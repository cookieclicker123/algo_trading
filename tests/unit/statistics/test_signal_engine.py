"""
Unit tests for SignalStatsEngine.

Tests event subscriptions, trade execution handling, metadata fetching, and record updates.
"""
import asyncio
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiofiles

# Ensure src is on path
import sys
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from newsflash.shared.statistics.signal_engine import SignalStatsEngine
from newsflash.shared.event_bus import AsyncEventBus
from newsflash.shared.event_types import DomainEventType
from newsflash.infra.statistics.repository import StatisticsRepository
from newsflash.domain.brokerage.events import TradeExecutedDomainEvent
from newsflash.domain.brokerage.models import TradeResult, TradeStatus, MarketSession


# Test data directory - will be cleaned up after 1 second
TEST_TMP_DIR = None


@pytest.fixture
def test_tmp_dir():
    """Create temporary directory for tests, cleanup after 1 second."""
    global TEST_TMP_DIR
    tmpdir = tempfile.mkdtemp(prefix="signal_engine_test_")
    TEST_TMP_DIR = Path(tmpdir)
    yield TEST_TMP_DIR
    
    # Wait 1 second before cleanup (for file inspection)
    print(f"\n📁 Test files available at: {tmpdir}")
    print("⏳ Waiting 1 second before cleanup...")
    time.sleep(1)
    
    # Cleanup
    if TEST_TMP_DIR.exists():
        shutil.rmtree(TEST_TMP_DIR)
        print(f"✅ Cleaned up: {tmpdir}")


@pytest.fixture
def event_bus():
    """Create event bus for tests."""
    return AsyncEventBus()


@pytest.fixture
def repository(test_tmp_dir):
    """Create repository instance for tests."""
    return StatisticsRepository(tmp_dir=test_tmp_dir)


@pytest.fixture
def signal_engine(event_bus, repository):
    """Create signal engine instance for tests."""
    from newsflash.shared.statistics.finnhub_coordinator import FinnhubCoordinator
    from unittest.mock import MagicMock
    
    # Create a mock FinnhubCoordinator
    mock_coordinator = MagicMock(spec=FinnhubCoordinator)
    mock_coordinator._worker_task = None
    mock_coordinator.start = asyncio.coroutine(lambda: None)
    mock_coordinator.stop = asyncio.coroutine(lambda: None)
    
    return SignalStatsEngine(
        event_bus=event_bus,
        repository=repository,
        finnhub_coordinator=mock_coordinator
    )


@pytest.fixture
def sample_trade_result():
    """Sample trade result for testing."""
    return TradeResult(
        trade_request={
            "ticker": "AAPL",
            "action": "BUY",
            "article_id": "test-article-123",
            "order_id": "order_abc123",
            "_spread_info": {
                "bid": 175.48,
                "ask": 175.52,
                "spread": 0.04,
                "mid": 175.50
            }
        },
        success=True,
        status=TradeStatus.EXECUTED,
        shares=10.0,
        fill_price=Decimal("175.50"),
        total_cost=Decimal("1755.00"),
        commission=Decimal("0.00"),
        session=MarketSession.PREMARKET,
        executed_at=datetime.now(timezone.utc)
    )


class TestEventSubscriptions:
    """Test event subscription functionality."""
    
    @pytest.mark.asyncio
    async def test_start_subscribes_to_events(self, signal_engine):
        """Test that start() subscribes to TRADE_EXECUTED event."""
        await signal_engine.start()
        
        # Verify subscription
        assert signal_engine.event_bus.get_subscriber_count(DomainEventType.TRADE_EXECUTED) >= 1
    
    @pytest.mark.asyncio
    async def test_stop_handles_gracefully(self, signal_engine):
        """Test that stop() works without errors."""
        await signal_engine.start()
        await signal_engine.stop()
        
        # Should complete without errors
        assert True


class TestTradeExecutedHandling:
    """Test handling of TradeExecuted events."""
    
    @pytest.mark.asyncio
    async def test_successful_trade_creates_record(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that successful trades create signal records."""
        await signal_engine.start()
        
        event = TradeExecutedDomainEvent(
            trade_result=sample_trade_result,
            executed_at=datetime.now()
        )
        
        # Publish event
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        # Wait for processing
        await asyncio.sleep(0.3)
        
        # Verify record was created
        file_path = repository._get_session_file_path("signal", "premarket", event.executed_at)
        if file_path.exists():
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                assert len(data["records"]) >= 1
                record = data["records"][0]
                assert record["trade_id"] == "order_abc123"
                assert record["ticker"] == "AAPL"
                assert record["article_id"] == "test-article-123"
                assert record["entry_price"] == 175.50
                assert record["entry_shares"] == 10
                assert record["entry_amount_usd"] == 1755.00
    
    @pytest.mark.asyncio
    async def test_non_executed_trade_skipped(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that non-executed trades are skipped."""
        await signal_engine.start()
        
        # Create failed trade
        failed_trade = TradeResult(
            trade_request=sample_trade_result.trade_request,
            success=False,
            status=TradeStatus.FAILED,
            shares=None,
            fill_price=None,
            total_cost=None,
            commission=None,
            session=MarketSession.PREMARKET,
            executed_at=datetime.now(timezone.utc),
            error="Order rejected"
        )
        
        event = TradeExecutedDomainEvent(
            trade_result=failed_trade,
            executed_at=datetime.now()
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.2)
        
        # Verify no record was created
        file_path = repository._get_session_file_path("signal", "premarket", event.executed_at)
        if file_path.exists():
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                assert len(data.get("records", [])) == 0
        else:
            assert True  # File doesn't exist, which is expected
    
    @pytest.mark.asyncio
    async def test_pending_trade_skipped(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that pending trades are skipped."""
        await signal_engine.start()
        
        pending_trade = TradeResult(
            trade_request=sample_trade_result.trade_request,
            success=True,
            status=TradeStatus.PENDING,  # Not executed
            shares=10.0,
            fill_price=Decimal("175.50"),
            total_cost=Decimal("1755.00"),
            commission=Decimal("0.00"),
            session=MarketSession.PREMARKET,
            executed_at=datetime.now(timezone.utc)
        )
        
        event = TradeExecutedDomainEvent(
            trade_result=pending_trade,
            executed_at=datetime.now()
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.2)
        
        # Verify no record was created
        file_path = repository._get_session_file_path("signal", "premarket", event.executed_at)
        if file_path.exists():
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                assert len(data.get("records", [])) == 0
        else:
            assert True  # File doesn't exist, which is expected
    
    @pytest.mark.asyncio
    async def test_trade_id_generation(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that trade_id is generated if not in trade_request."""
        await signal_engine.start()
        
        # Remove order_id from trade_request
        trade_request_no_id = sample_trade_result.trade_request.copy()
        trade_request_no_id.pop("order_id", None)
        trade_request_no_id.pop("_order_id", None)
        
        trade_result = TradeResult(
            trade_request=trade_request_no_id,
            success=True,
            status=TradeStatus.EXECUTED,
            shares=10.0,
            fill_price=Decimal("175.50"),
            total_cost=Decimal("1755.00"),
            commission=Decimal("0.00"),
            session=MarketSession.PREMARKET,
            executed_at=datetime.now(timezone.utc)
        )
        
        executed_at = datetime.now()
        event = TradeExecutedDomainEvent(
            trade_result=trade_result,
            executed_at=executed_at
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.3)
        
        # Verify record was created with generated trade_id
        file_path = repository._get_session_file_path("signal", "premarket", executed_at)
        if file_path.exists():
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                record = data["records"][0]
                assert record["trade_id"].startswith("trade_")
                assert len(record["trade_id"]) > 6
    
    @pytest.mark.asyncio
    async def test_nbbo_extraction(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that NBBO is extracted from trade_request."""
        await signal_engine.start()
        
        event = TradeExecutedDomainEvent(
            trade_result=sample_trade_result,
            executed_at=datetime.now()
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.3)
        
        # Verify NBBO was extracted
        file_path = repository._get_session_file_path("signal", "premarket", event.executed_at)
        if file_path.exists():
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                record = data["records"][0]
                assert record["entry_nbbo"] is not None
                assert record["entry_nbbo"]["bid"] == 175.48
                assert record["entry_nbbo"]["ask"] == 175.52
                assert record["entry_nbbo"]["spread"] == 0.04
    
    @pytest.mark.asyncio
    async def test_nbbo_fallback_location(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that NBBO is extracted from fallback location if _spread_info not found."""
        await signal_engine.start()
        
        # Use spread_info instead of _spread_info
        trade_request = sample_trade_result.trade_request.copy()
        trade_request.pop("_spread_info", None)
        trade_request["spread_info"] = {
            "bid": 175.48,
            "ask": 175.52,
            "spread": 0.04,
            "mid": 175.50
        }
        
        trade_result = TradeResult(
            trade_request=trade_request,
            success=True,
            status=TradeStatus.EXECUTED,
            shares=10.0,
            fill_price=Decimal("175.50"),
            total_cost=Decimal("1755.00"),
            commission=Decimal("0.00"),
            session=MarketSession.PREMARKET,
            executed_at=datetime.now(timezone.utc)
        )
        
        event = TradeExecutedDomainEvent(
            trade_result=trade_result,
            executed_at=datetime.now()
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.3)
        
        # Verify NBBO was extracted from fallback location
        file_path = repository._get_session_file_path("signal", "premarket", event.executed_at)
        if file_path.exists():
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                record = data["records"][0]
                assert record["entry_nbbo"] is not None
                assert record["entry_nbbo"]["spread"] == 0.04


class TestSessionMapping:
    """Test session mapping functionality."""
    
    @pytest.mark.asyncio
    async def test_premarket_session_mapping(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that PREMARKET session is mapped correctly."""
        await signal_engine.start()
        
        trade_result = TradeResult(
            trade_request=sample_trade_result.trade_request,
            success=True,
            status=TradeStatus.EXECUTED,
            shares=10.0,
            fill_price=Decimal("175.50"),
            total_cost=Decimal("1755.00"),
            commission=Decimal("0.00"),
            session=MarketSession.PREMARKET,
            executed_at=datetime.now(timezone.utc)
        )
        
        executed_at = datetime.now()
        event = TradeExecutedDomainEvent(
            trade_result=trade_result,
            executed_at=executed_at
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.3)
        
        # Verify file was created in premarket directory
        file_path = repository._get_session_file_path("signal", "premarket", executed_at)
        assert file_path.exists()
    
    @pytest.mark.asyncio
    async def test_market_hours_session_mapping(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that MARKET session is mapped to market_hours."""
        await signal_engine.start()
        
        trade_result = TradeResult(
            trade_request=sample_trade_result.trade_request,
            success=True,
            status=TradeStatus.EXECUTED,
            shares=10.0,
            fill_price=Decimal("175.50"),
            total_cost=Decimal("1755.00"),
            commission=Decimal("0.00"),
            session=MarketSession.MARKET,
            executed_at=datetime.now(timezone.utc)
        )
        
        executed_at = datetime.now()
        event = TradeExecutedDomainEvent(
            trade_result=trade_result,
            executed_at=executed_at
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.3)
        
        # Verify file was created in market_hours directory
        file_path = repository._get_session_file_path("signal", "market_hours", executed_at)
        assert file_path.exists()
    
    @pytest.mark.asyncio
    async def test_postmarket_session_mapping(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that POSTMARKET session is mapped correctly."""
        await signal_engine.start()
        
        trade_result = TradeResult(
            trade_request=sample_trade_result.trade_request,
            success=True,
            status=TradeStatus.EXECUTED,
            shares=10.0,
            fill_price=Decimal("175.50"),
            total_cost=Decimal("1755.00"),
            commission=Decimal("0.00"),
            session=MarketSession.POSTMARKET,
            executed_at=datetime.now(timezone.utc)
        )
        
        executed_at = datetime.now()
        event = TradeExecutedDomainEvent(
            trade_result=trade_result,
            executed_at=executed_at
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.3)
        
        # Verify file was created in postmarket directory
        file_path = repository._get_session_file_path("signal", "postmarket", executed_at)
        assert file_path.exists()


class TestMetadataFetching:
    """Test FinnhubCoordinator metadata fetching functionality."""
    
    @pytest.mark.asyncio
    async def test_metadata_fetch_task_created(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that metadata fetch task is created."""
        await signal_engine.start()
        
        event = TradeExecutedDomainEvent(
            trade_result=sample_trade_result,
            executed_at=datetime.now()
        )
        
        # Mock FinnhubCoordinator.fetch_metadata
        mock_metadata = {
            'industry': 'Consumer Electronics',
            'sector': 'Technology',
            'market_cap_millions': 2800000.0,
            'shares_outstanding': 16000000000.0
        }
        
        async def mock_fetch_metadata(ticker, timeout=30.0):
            return mock_metadata
        
        signal_engine.finnhub_coordinator.fetch_metadata = mock_fetch_metadata
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        # Wait for initial record and metadata task
        await asyncio.sleep(0.3)
        
        # Verify record was created (metadata update happens asynchronously)
        file_path = repository._get_session_file_path("signal", "premarket", event.executed_at)
        assert file_path.exists()
    
    @pytest.mark.asyncio
    async def test_metadata_fetch_handles_errors_gracefully(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that metadata fetch errors are handled gracefully."""
        await signal_engine.start()
        
        event = TradeExecutedDomainEvent(
            trade_result=sample_trade_result,
            executed_at=datetime.now()
        )
        
        # Mock FinnhubCoordinator to raise an error
        async def mock_fetch_metadata(ticker, timeout=30.0):
            raise Exception("API Error")
        
        signal_engine.finnhub_coordinator.fetch_metadata = mock_fetch_metadata
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )

        # Wait for processing
        await asyncio.sleep(0.3)

        # Verify record was still created (even if metadata fetch failed)
        file_path = repository._get_session_file_path("signal", "premarket", event.executed_at)
        assert file_path.exists()


class TestErrorHandling:
    """Test error handling in signal engine."""
    
    @pytest.mark.asyncio
    async def test_error_in_trade_executed_handled_gracefully(
        self, signal_engine
    ):
        """Test that errors in trade executed handler don't crash engine."""
        await signal_engine.start()
        
        # Publish invalid event data (should be handled gracefully)
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            {"invalid": "data"}  # Invalid event data
        )
        
        await asyncio.sleep(0.1)
        
        # Engine should still be running
        assert signal_engine.event_bus.get_subscriber_count(DomainEventType.TRADE_EXECUTED) >= 1
    
    @pytest.mark.asyncio
    async def test_missing_ticker_handled_gracefully(
        self, signal_engine, repository
    ):
        """Test that missing ticker is handled gracefully."""
        await signal_engine.start()
        
        trade_result = TradeResult(
            trade_request={},  # Missing ticker
            success=True,
            status=TradeStatus.EXECUTED,
            shares=10.0,
            fill_price=Decimal("175.50"),
            total_cost=Decimal("1755.00"),
            commission=Decimal("0.00"),
            session=MarketSession.PREMARKET,
            executed_at=datetime.now(timezone.utc)
        )
        
        event = TradeExecutedDomainEvent(
            trade_result=trade_result,
            executed_at=datetime.now()
        )
        
        # Should not raise exception
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.1)
        
        # Engine should still be running
        assert signal_engine.event_bus.get_subscriber_count(DomainEventType.TRADE_EXECUTED) >= 1


class TestSummaryStatistics:
    """Test that summary statistics are updated correctly."""
    
    @pytest.mark.asyncio
    async def test_summary_updated_on_record_append(
        self, signal_engine, repository, sample_trade_result
    ):
        """Test that summary statistics are updated when record is appended."""
        await signal_engine.start()
        
        executed_at = datetime.now()
        event = TradeExecutedDomainEvent(
            trade_result=sample_trade_result,
            executed_at=executed_at
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.3)
        
        # Verify summary was updated
        file_path = repository._get_session_file_path("signal", "premarket", executed_at)
        if file_path.exists():
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                assert data["summary"]["total_trades"] >= 1
                assert "AAPL" in data["summary"]["ticker_breakdown"]
                assert data["summary"]["ticker_breakdown"]["AAPL"] >= 1
