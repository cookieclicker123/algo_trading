"""
Integration tests for SignalStatsEngine.

Integration Test Strategy:
---------------------------
These tests verify end-to-end event flow and file I/O operations.

External Dependencies (REAL):
- Event bus: Real AsyncEventBus to test event subscription and publishing
- Repository: Real StatisticsRepository to test actual file I/O operations

External Dependencies (MOCKED):
- FinnhubCoordinator: Mocked to avoid real API calls (tests should be fast)

What These Tests Prove:
- Events flow correctly through the system
- Records are created and updated in files correctly
- Metadata fetching works (with mocked FinnhubCoordinator)
- Summary statistics are calculated correctly
- Session mapping works correctly

Note: We mock FinnhubCoordinator to avoid real API calls. The actual metadata fetching
behavior is tested in unit tests with proper mocking.
"""
import asyncio
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch, MagicMock

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
    tmpdir = tempfile.mkdtemp(prefix="signal_engine_integration_test_")
    TEST_TMP_DIR = Path(tmpdir)
    yield TEST_TMP_DIR
    
    # Wait 1 second before cleanup (for file inspection)
    print(f"\n📁 Integration test files available at: {tmpdir}")
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
            "article_id": "integration-test-article-123",
            "order_id": "order_integration_123",
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


class TestEndToEndSignalWorkflow:
    """Test complete end-to-end signal tracking workflows."""
    
    @pytest.mark.asyncio
    async def test_complete_signal_workflow_with_metadata(
        self, signal_engine, repository, sample_trade_result
    ):
        """
        Integration test goal: Verify event flow → record creation → metadata update.
        
        External dependencies:
        - Real event bus (test event flow)
        - Real repository (test file I/O)
        - Mocked FinnhubCoordinator (no real API calls)
        """
        await signal_engine.start()
        
        executed_at = datetime.now()
        event = TradeExecutedDomainEvent(
            trade_result=sample_trade_result,
            executed_at=executed_at
        )
        
        # Mock FinnhubCoordinator to return metadata
        # FinnhubCoordinator.fetch_metadata returns dict with:
        #   - industry, sector, market_cap_millions (already in millions)
        mock_metadata = {
            'industry': 'Consumer Electronics',
            'sector': 'Technology',
            'market_cap_millions': 2800000.0,  # Already in millions
            'shares_outstanding': 16000000000.0
        }
        
        # Mock FinnhubCoordinator.fetch_metadata to return our mock data
        async def mock_fetch_metadata(ticker, timeout=30.0):
            return mock_metadata
        
        signal_engine.finnhub_coordinator.fetch_metadata = mock_fetch_metadata

        # Step 1: Publish trade executed event
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )

        # Wait for initial record creation
        await asyncio.sleep(0.3)

        # Step 2: Verify record was created
        file_path = repository._get_session_file_path("signal", "premarket", executed_at)
        assert file_path.exists(), "Signal file should exist"

        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            assert content.strip(), "File should have content"
            data = json.loads(content)

            assert len(data["records"]) == 1
            record = data["records"][0]
            assert record["trade_id"] == "order_integration_123"
            assert record["ticker"] == "AAPL"
            assert record["article_id"] == "integration-test-article-123"
            assert record["entry_price"] == 175.50
            assert record["entry_shares"] == 10
            assert record["entry_amount_usd"] == 1755.00
            assert record["entry_nbbo"]["spread"] == 0.04
            assert data["summary"]["total_trades"] == 1

        # Step 3: Wait for metadata update
        await asyncio.sleep(0.5)  # Give metadata fetch time to complete

        # Step 4: Verify record was updated with metadata
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)

            record = data["records"][0]
            assert record["ticker_metadata"] is not None
            assert record["ticker_metadata"]["industry"] == "Consumer Electronics"
            assert record["ticker_metadata"]["sector"] == "Technology"
            assert record["ticker_metadata"]["market_cap_millions"] == 2800000.0
            assert record["ticker_metadata"]["price"] == 175.50
            assert record["ticker_metadata"]["exchange"] == "NASDAQ"

            # Verify summary includes industry/sector breakdown
            assert data["summary"]["industry_breakdown"].get("Consumer Electronics", 0) >= 1
            assert data["summary"]["sector_breakdown"].get("Technology", 0) >= 1

        await signal_engine.stop()
    
    @pytest.mark.asyncio
    async def test_signal_workflow_multiple_trades(
        self, signal_engine, repository
    ):
        """
        Test workflow with multiple trades in same session.
        """
        await signal_engine.start()
        
        executed_at = datetime.now()
        
        # Create multiple trades
        trades = []
        for i in range(3):
            trade_result = TradeResult(
                trade_request={
                    "ticker": f"TICK{i}",
                    "action": "BUY",
                    "article_id": f"article-{i}",
                    "order_id": f"order_{i}",
                    "_spread_info": {
                        "bid": 100.0 - i,
                        "ask": 100.0 + i,
                        "spread": float(i * 0.02),
                        "mid": 100.0
                    }
                },
                success=True,
                status=TradeStatus.EXECUTED,
                shares=10.0,
                fill_price=Decimal("100.00"),
                total_cost=Decimal("1000.00"),
                commission=Decimal("0.00"),
                session=MarketSession.MARKET,
                executed_at=executed_at
            )
            
            event = TradeExecutedDomainEvent(
                trade_result=trade_result,
                executed_at=executed_at
            )
            
            await signal_engine.event_bus.publish(
                DomainEventType.TRADE_EXECUTED,
                event.model_dump()
            )
            
            trades.append((trade_result, event))
            await asyncio.sleep(0.1)  # Small delay between trades
        
        # Wait for all records to be created
        await asyncio.sleep(0.3)
        
        # Verify all records were created
        file_path = repository._get_session_file_path("signal", "market_hours", executed_at)
        assert file_path.exists()
        
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert len(data["records"]) == 3
            assert data["summary"]["total_trades"] == 3
            
            # Verify all tickers are in breakdown
            for i in range(3):
                assert f"TICK{i}" in data["summary"]["ticker_breakdown"]
                assert data["summary"]["ticker_breakdown"][f"TICK{i}"] == 1
        
        await signal_engine.stop()
    
    @pytest.mark.asyncio
    async def test_signal_workflow_different_sessions(
        self, signal_engine, repository
    ):
        """
        Test workflow with trades in different sessions.
        """
        await signal_engine.start()
        
        base_time = datetime.now()
        
        # Create trades for different sessions
        sessions = [
            (MarketSession.PREMARKET, "premarket"),
            (MarketSession.MARKET, "market_hours"),
            (MarketSession.POSTMARKET, "postmarket")
        ]
        
        for session_enum, session_str in sessions:
            trade_result = TradeResult(
                trade_request={
                    "ticker": "AAPL",
                    "action": "BUY",
                    "article_id": f"article-{session_str}",
                    "order_id": f"order_{session_str}",
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
                session=session_enum,
                executed_at=base_time
            )
            
            event = TradeExecutedDomainEvent(
                trade_result=trade_result,
                executed_at=base_time
            )
            
            await signal_engine.event_bus.publish(
                DomainEventType.TRADE_EXECUTED,
                event.model_dump()
            )
            
            await asyncio.sleep(0.1)
        
        # Wait for all records
        await asyncio.sleep(0.3)
        
        # Verify records in each session file
        for session_enum, session_str in sessions:
            file_path = repository._get_session_file_path("signal", session_str, base_time)
            assert file_path.exists(), f"File should exist for {session_str}"
            
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                assert len(data["records"]) == 1
                assert data["records"][0]["trade_id"] == f"order_{session_str}"
        
        await signal_engine.stop()
    
    @pytest.mark.asyncio
    async def test_signal_workflow_non_executed_skipped(
        self, signal_engine, repository, sample_trade_result
    ):
        """
        Test that non-executed trades are skipped.
        """
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
        
        executed_at = datetime.now()
        event = TradeExecutedDomainEvent(
            trade_result=failed_trade,
            executed_at=executed_at
        )
        
        await signal_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            event.model_dump()
        )
        
        await asyncio.sleep(0.3)
        
        # Verify no record was created
        file_path = repository._get_session_file_path("signal", "premarket", executed_at)
        if file_path.exists():
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                assert len(data.get("records", [])) == 0
        else:
            assert True  # File doesn't exist, which is expected
        
        await signal_engine.stop()
    
    @pytest.mark.asyncio
    async def test_signal_workflow_metadata_update_with_timestamp(
        self, signal_engine, repository, sample_trade_result
    ):
        """
        Test that metadata update uses timestamp to determine session (stateless).
        """
        await signal_engine.start()
        
        executed_at = datetime.now()
        event = TradeExecutedDomainEvent(
            trade_result=sample_trade_result,
            executed_at=executed_at
        )
        
        # Mock FinnhubCoordinator
        mock_ticker = MagicMock()
        mock_ticker.info = {
            'industry': 'Test Industry',
            'sector': 'Test Sector',
            'marketCap': 1000000000,
            'currentPrice': 175.50,
            'exchange': 'NYSE'
        }
        
        with patch('newsflash.shared.statistics.signal_engine.yf') as mock_yf, \
             patch('newsflash.shared.statistics.signal_engine.get_market_session_from_timestamp') as mock_session_from_ts:
            
            # Mock FinnhubCoordinator.fetch_metadata
            async def mock_fetch_metadata(ticker, timeout=30.0):
                return {
                    'industry': 'Consumer Electronics',
                    'sector': 'Technology',
                    'market_cap_millions': 2800000.0,
                    'shares_outstanding': 16000000000.0
                }
            signal_engine.finnhub_coordinator.fetch_metadata = mock_fetch_metadata
            mock_session_from_ts.return_value = ("premarket", True)
            
            await signal_engine.event_bus.publish(
                DomainEventType.TRADE_EXECUTED,
                event.model_dump()
            )
            
            # Wait for initial record
            await asyncio.sleep(0.3)
            
            # Wait for metadata update
            await asyncio.sleep(0.5)
            
            # Verify metadata was updated using timestamp-based session detection
            file_path = repository._get_session_file_path("signal", "premarket", executed_at)
            assert file_path.exists()
            
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                
                record = data["records"][0]
                assert record["ticker_metadata"] is not None
                assert record["ticker_metadata"]["industry"] == "Test Industry"
                
                # Verify get_market_session_from_timestamp was called
                mock_session_from_ts.assert_called()
        
        await signal_engine.stop()
    
    @pytest.mark.asyncio
    async def test_signal_workflow_summary_statistics(
        self, signal_engine, repository
    ):
        """
        Test that summary statistics are calculated correctly.
        """
        await signal_engine.start()
        
        executed_at = datetime.now()
        
        # Create trades with different spreads
        spreads = [0.02, 0.04, 0.06]
        for i, spread in enumerate(spreads):
            trade_result = TradeResult(
                trade_request={
                    "ticker": "AAPL",
                    "action": "BUY",
                    "article_id": f"article-{i}",
                    "order_id": f"order_{i}",
                    "_spread_info": {
                        "bid": 175.50 - spread/2,
                        "ask": 175.50 + spread/2,
                        "spread": spread,
                        "mid": 175.50
                    }
                },
                success=True,
                status=TradeStatus.EXECUTED,
                shares=10.0,
                fill_price=Decimal("175.50"),
                total_cost=Decimal("1755.00"),
                commission=Decimal("0.00"),
                session=MarketSession.MARKET,
                executed_at=executed_at
            )
            
            event = TradeExecutedDomainEvent(
                trade_result=trade_result,
                executed_at=executed_at
            )
            
            await signal_engine.event_bus.publish(
                DomainEventType.TRADE_EXECUTED,
                event.model_dump()
            )
            
            await asyncio.sleep(0.1)
        
        # Wait for all records
        await asyncio.sleep(0.3)
        
        # Verify summary statistics
        file_path = repository._get_session_file_path("signal", "market_hours", executed_at)
        assert file_path.exists()
        
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            summary = data["summary"]
            assert summary["total_trades"] == 3
            assert summary["average_spread_at_entry"] == sum(spreads) / len(spreads)
            assert summary["ticker_breakdown"]["AAPL"] == 3
        
        await signal_engine.stop()
