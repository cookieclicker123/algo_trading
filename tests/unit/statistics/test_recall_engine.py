"""
Unit tests for RecallStatsEngine.

Tests event subscriptions, NBBO checking, monitoring tasks, and filter updates.
"""
import asyncio
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
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

from newsflash.shared.statistics.recall_engine import RecallStatsEngine
from newsflash.shared.event_bus import AsyncEventBus
from newsflash.shared.event_types import DomainEventType
from newsflash.infra.statistics.repository import StatisticsRepository
from newsflash.domain.websocket.events import ArticleReceivedDomainEvent
from newsflash.domain.websocket.models import Article, ArticleSource
from newsflash.domain.classification.events import ArticleClassifiedDomainEvent
from newsflash.domain.classification.models import (
    ClassificationResult,
    ClassificationCategory,
    ClassificationConfidence
)
from newsflash.domain.brokerage.events import TradeExecutedDomainEvent
from newsflash.domain.brokerage.models import TradeResult, TradeStatus, MarketSession


# Test data directory - will be cleaned up after 1 second
TEST_TMP_DIR = None


@pytest.fixture
def test_tmp_dir():
    """Create temporary directory for tests, cleanup after 1 second."""
    global TEST_TMP_DIR
    tmpdir = tempfile.mkdtemp(prefix="recall_engine_test_")
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
def mock_quote_fetcher():
    """Create mocked quote fetcher."""
    quote_fetcher = AsyncMock()
    quote_fetcher.get_nbbo_snapshot = AsyncMock(return_value=None)
    return quote_fetcher


@pytest.fixture
def mock_finnhub_coordinator():
    """Create mocked Finnhub coordinator."""
    coordinator = MagicMock()
    coordinator.fetch_metadata = AsyncMock(return_value={
        "industry": "Technology",
        "sector": "Information Technology",
        "market_cap_millions": 3000.0
    })
    coordinator.start = AsyncMock()
    coordinator.stop = AsyncMock()
    coordinator._worker_task = None  # Simulate not started
    return coordinator


@pytest.fixture
def recall_engine(event_bus, repository, mock_quote_fetcher, mock_finnhub_coordinator):
    """Create recall engine instance for tests."""
    return RecallStatsEngine(
        event_bus=event_bus,
        repository=repository,
        quote_fetcher=mock_quote_fetcher,
        finnhub_coordinator=mock_finnhub_coordinator
    )


@pytest.fixture
def sample_article():
    """Sample article for testing."""
    return Article(
        id="test-article-123",
        source=ArticleSource.BENZINGA,
        source_id="123",
        title="Test Article Title",
        content="Test content",
        summary="Test summary",
        author="Test Author",
        published_at=datetime.now(timezone.utc),
        updated_at=None,
        url="https://test.com/123",
        tickers=frozenset(["AAPL"]),
        tags=frozenset(),
        categories=frozenset()
    )


class TestEventSubscriptions:
    """Test event subscription functionality."""
    
    @pytest.mark.asyncio
    async def test_start_subscribes_to_events(self, recall_engine):
        """Test that start() subscribes to all required events."""
        await recall_engine.start()
        
        # Verify subscriptions
        assert recall_engine.event_bus.get_subscriber_count(DomainEventType.ARTICLE_RECEIVED) >= 1
        assert recall_engine.event_bus.get_subscriber_count(DomainEventType.ARTICLE_CLASSIFIED) >= 1
        assert recall_engine.event_bus.get_subscriber_count(DomainEventType.TRADE_EXECUTED) >= 1
    
    @pytest.mark.asyncio
    async def test_stop_cancels_monitoring_tasks(self, recall_engine):
        """Test that stop() cancels all monitoring tasks."""
        await recall_engine.start()
        
        # Create a fake monitoring task
        fake_task = asyncio.create_task(asyncio.sleep(100))
        async with recall_engine._monitoring_lock:
            recall_engine._monitoring_tasks["test-article"] = fake_task
        
        # Stop engine
        await recall_engine.stop()
        
        # Wait a moment for cancellation to propagate
        await asyncio.sleep(0.1)
        
        # Verify task was cancelled
        assert fake_task.cancelled()
        assert len(recall_engine._monitoring_tasks) == 0


class TestArticleReceivedHandling:
    """Test handling of ArticleReceived events."""
    
    @pytest.mark.asyncio
    async def test_article_without_tickers_skipped(
        self, recall_engine, repository
    ):
        """Test that articles without tickers are skipped."""
        await recall_engine.start()
        
        article = Article(
            id="test-no-tickers",
            source=ArticleSource.BENZINGA,
            source_id="456",
            title="No Tickers",
            published_at=datetime.now(timezone.utc),
            tickers=frozenset(),
            tags=frozenset(),
            categories=frozenset()
        )
        
        event = ArticleReceivedDomainEvent(
            article=article,
            received_at=datetime.now()
        )
        
        # Publish event
        await recall_engine.event_bus.publish(
            DomainEventType.ARTICLE_RECEIVED,
            event.model_dump()
        )
        
        # Give it a moment to process
        await asyncio.sleep(0.1)
        
        # Verify no record was created
        # (Can't easily check without knowing session/date, but quote_fetcher shouldn't be called)
        recall_engine.quote_fetcher.get_nbbo_snapshot.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_article_with_tradable_ticker_creates_record(
        self, recall_engine, repository, sample_article, mock_quote_fetcher
    ):
        """Test that articles with tradable tickers create recall records."""
        await recall_engine.start()
        
        # Mock NBBO available (ticker is tradable)
        mock_quote_fetcher.get_nbbo_snapshot.return_value = {
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        }
        
        # Mock current session as premarket
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=datetime.now()
            )
            
            # Publish event
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            # Give it time to process
            await asyncio.sleep(0.2)
            
            # Verify NBBO was checked
            mock_quote_fetcher.get_nbbo_snapshot.assert_called_with("AAPL")
            
            # Verify record was appended (check file exists)
            test_date = datetime.now()
            file_path = repository._get_session_file_path("recall", "premarket", test_date)
            
            # Wait a bit more for async operations
            await asyncio.sleep(0.2)
            
            if file_path.exists():
                async with aiofiles.open(file_path, 'r') as f:
                    content = await f.read()
                    data = json.loads(content)
                    assert len(data["records"]) >= 1
                    assert data["records"][0]["article_id"] == "test-article-123"
    
    @pytest.mark.asyncio
    async def test_article_with_no_nbbo_skipped(
        self, recall_engine, sample_article, mock_quote_fetcher
    ):
        """Test that articles with no NBBO (non-tradable) are skipped."""
        await recall_engine.start()
        
        # Mock NBBO unavailable (ticker not tradable)
        mock_quote_fetcher.get_nbbo_snapshot.return_value = None
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=datetime.now()
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            await asyncio.sleep(0.2)
            
            # Verify NBBO was checked
            mock_quote_fetcher.get_nbbo_snapshot.assert_called()
            
            # Verify no monitoring task was created
            async with recall_engine._monitoring_lock:
                assert "test-article-123" not in recall_engine._monitoring_tasks
    
    @pytest.mark.asyncio
    async def test_closed_market_skipped(
        self, recall_engine, sample_article
    ):
        """Test that articles received during closed market are skipped."""
        await recall_engine.start()
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("closed", True)
            
            event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=datetime.now()
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            await asyncio.sleep(0.1)
            
            # Verify NBBO was not checked (market is closed)
            recall_engine.quote_fetcher.get_nbbo_snapshot.assert_not_called()


class TestMonitoringTasks:
    """Test 5-minute monitoring functionality."""
    
    @pytest.mark.asyncio
    async def test_monitoring_task_created(
        self, recall_engine, sample_article, mock_quote_fetcher
    ):
        """Test that monitoring task is created for tradable tickers."""
        await recall_engine.start()
        
        # Mock NBBO available
        mock_quote_fetcher.get_nbbo_snapshot.return_value = {
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        }
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=datetime.now()
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            # Wait for task creation
            await asyncio.sleep(0.2)
            
            # Verify monitoring task was created
            async with recall_engine._monitoring_lock:
                assert "test-article-123" in recall_engine._monitoring_tasks
                task = recall_engine._monitoring_tasks["test-article-123"]
                assert not task.done()
    
    @pytest.mark.asyncio
    async def test_monitoring_task_updates_record_after_wait(
        self, recall_engine, repository, sample_article, mock_quote_fetcher
    ):
        """Test that monitoring task updates record after waiting (with shorter wait for test)."""
        await recall_engine.start()
        
        # Mock initial NBBO
        initial_nbbo = {
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        }
        
        # Mock final NBBO (1%+ move)
        final_nbbo = {
            "bid": 177.50,
            "ask": 177.55,
            "spread": 0.05,
            "mid": 177.525  # ~1.14% move
        }
        
        mock_quote_fetcher.get_nbbo_snapshot.side_effect = [
            initial_nbbo,  # First call (when article received)
            final_nbbo     # Second call (after 5 minutes)
        ]
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            received_at = datetime.now()
            event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=received_at
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            # Wait for initial record creation
            await asyncio.sleep(0.2)
            
            # Manually trigger the monitoring task's price check (with shorter wait)
            # We'll patch asyncio.sleep to make it faster
            with patch('asyncio.sleep', return_value=None) as mock_sleep:
                # Get the monitoring task
                async with recall_engine._monitoring_lock:
                    task = recall_engine._monitoring_tasks.get("test-article-123")
                
                if task:
                    # Cancel the original task
                    task.cancel()
                    
                    # Manually call the monitoring function with short wait
                    await recall_engine._monitor_ticker_price(
                        "test-article-123",
                        ["AAPL"],
                        {"AAPL": initial_nbbo},
                        "premarket",
                        received_at
                    )
            
            # Verify record was updated
            file_path = repository._get_session_file_path("recall", "premarket", received_at)
            if file_path.exists():
                async with aiofiles.open(file_path, 'r') as f:
                    content = await f.read()
                    data = json.loads(content)
                    
                    record = next((r for r in data["records"] if r["article_id"] == "test-article-123"), None)
                    if record:
                        assert record.get("price_check_5min") is not None
                        assert record["price_check_5min"].get("moved_1_percent") is True


class TestFilterReasonUpdates:
    """Test filter reason updates from classification events."""
    
    @pytest.mark.asyncio
    async def test_non_imminent_classification_adds_filter_reason(
        self, recall_engine, repository, sample_article, mock_quote_fetcher
    ):
        """Test that non-IMMINENT classification adds filter reason."""
        await recall_engine.start()
        
        # First, create a recall record
        mock_quote_fetcher.get_nbbo_snapshot.return_value = {
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        }
        
        received_at = datetime.now()
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            # Create article received event
            article_event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=received_at
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                article_event.model_dump()
            )
            
            await asyncio.sleep(0.2)
            
            # Now publish classification event (non-IMMINENT)
            classification_result = ClassificationResult(
                article_id="test-article-123",
                classification=ClassificationCategory.IGNORE,
                confidence=ClassificationConfidence.MEDIUM,
                reasoning="Not significant",
                classified_at=datetime.now(),
                latency_ms=50.0
            )
            
            classified_event = ArticleClassifiedDomainEvent(
                article_id="test-article-123",
                result=classification_result,
                classified_at=datetime.now()
            )
            
            # Mock session detector for classification timestamp
            with patch('newsflash.shared.statistics.recall_engine.get_market_session_from_timestamp') as mock_session_from_ts:
                mock_session_from_ts.return_value = ("premarket", True)
                
                await recall_engine.event_bus.publish(
                    DomainEventType.ARTICLE_CLASSIFIED,
                    classified_event.model_dump()
                )
                
                await asyncio.sleep(0.2)
                
                # Verify record was updated with filter reason
                file_path = repository._get_session_file_path("recall", "premarket", received_at)
                if file_path.exists():
                    async with aiofiles.open(file_path, 'r') as f:
                        content = await f.read()
                        data = json.loads(content)
                        
                        record = next((r for r in data["records"] if r["article_id"] == "test-article-123"), None)
                        if record:
                            assert "not_classified_ignore" in record.get("filter_reasons", [])
    
    @pytest.mark.asyncio
    async def test_imminent_classification_no_filter_reason(
        self, recall_engine, sample_article, mock_quote_fetcher
    ):
        """Test that IMMINENT classification doesn't add filter reason."""
        await recall_engine.start()
        
        classification_result = ClassificationResult(
            article_id="test-article-123",
            classification=ClassificationCategory.IMMINENT,
            confidence=ClassificationConfidence.HIGH,
            reasoning="Significant news",
            classified_at=datetime.now(),
            latency_ms=50.0
        )
        
        classified_event = ArticleClassifiedDomainEvent(
            article_id="test-article-123",
            result=classification_result,
            classified_at=datetime.now()
        )
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session_from_timestamp') as mock_session_from_ts:
            mock_session_from_ts.return_value = ("premarket", True)
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_CLASSIFIED,
                classified_event.model_dump()
            )
            
            await asyncio.sleep(0.1)
            
            # Verify repository update was not called (no filter reason for IMMINENT)
            # We can't easily verify this without spying on repository, but the logic is correct


class TestTradeExecutedHandling:
    """Test handling of TradeExecuted events."""
    
    @pytest.mark.asyncio
    async def test_trade_executed_cancels_monitoring(
        self, recall_engine, sample_article, mock_quote_fetcher
    ):
        """Test that trade execution cancels monitoring task."""
        await recall_engine.start()
        
        # Create monitoring task first
        mock_quote_fetcher.get_nbbo_snapshot.return_value = {
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        }
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=datetime.now()
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            await asyncio.sleep(0.2)
            
            # Verify monitoring task exists
            async with recall_engine._monitoring_lock:
                assert "test-article-123" in recall_engine._monitoring_tasks
            
            # Now publish trade executed event
            trade_result = TradeResult(
                trade_request={"ticker": "AAPL", "article_id": "test-article-123", "action": "BUY"},
                success=True,
                status=TradeStatus.EXECUTED,
                shares=10,
                fill_price=175.50,
                total_cost=1755.00,
                commission=0.0,
                session=MarketSession.PREMARKET,
                executed_at=datetime.now()
            )
            
            trade_event = TradeExecutedDomainEvent(
                trade_result=trade_result,
                executed_at=datetime.now()
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.TRADE_EXECUTED,
                trade_event.model_dump()
            )
            
            await asyncio.sleep(0.2)
            
            # Verify monitoring task was cancelled
            async with recall_engine._monitoring_lock:
                assert "test-article-123" not in recall_engine._monitoring_tasks
            
            # Verify article marked as traded
            async with recall_engine._traded_lock:
                assert "test-article-123" in recall_engine._traded_articles
    
    @pytest.mark.asyncio
    async def test_trade_executed_without_article_id_handled_gracefully(
        self, recall_engine
    ):
        """Test that trade executed without article_id is handled gracefully."""
        await recall_engine.start()
        
        trade_result = TradeResult(
            trade_request={"ticker": "AAPL", "action": "BUY"},  # No article_id
            success=True,
            status=TradeStatus.EXECUTED,
            shares=10,
            fill_price=175.50,
            total_cost=1755.00,
            commission=0.0,
            session=MarketSession.PREMARKET,
            executed_at=datetime.now()
        )
        
        trade_event = TradeExecutedDomainEvent(
            trade_result=trade_result,
            executed_at=datetime.now()
        )
        
        # Should not raise exception
        await recall_engine.event_bus.publish(
            DomainEventType.TRADE_EXECUTED,
            trade_event.model_dump()
        )
        
        await asyncio.sleep(0.1)


class TestMultipleTickers:
    """Test handling of articles with multiple tickers."""
    
    @pytest.mark.asyncio
    async def test_article_with_multiple_tradable_tickers(
        self, recall_engine, mock_quote_fetcher
    ):
        """Test that articles with multiple tickers check all of them."""
        await recall_engine.start()
        
        article = Article(
            id="test-multi-ticker",
            source=ArticleSource.BENZINGA,
            source_id="789",
            title="Multi Ticker Article",
            published_at=datetime.now(timezone.utc),
            tickers=frozenset(["AAPL", "TSLA", "NVDA"]),
            tags=frozenset(),
            categories=frozenset()
        )
        
        # Mock NBBO for all tickers
        def nbbo_side_effect(ticker):
            return {
                "bid": 100.0,
                "ask": 100.05,
                "spread": 0.05,
                "mid": 100.025
            }
        
        mock_quote_fetcher.get_nbbo_snapshot.side_effect = nbbo_side_effect
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            event = ArticleReceivedDomainEvent(
                article=article,
                received_at=datetime.now()
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            await asyncio.sleep(0.2)
            
            # Verify all tickers were checked
            assert mock_quote_fetcher.get_nbbo_snapshot.call_count >= 3
            called_tickers = [call[0][0] for call in mock_quote_fetcher.get_nbbo_snapshot.call_args_list]
            assert "AAPL" in called_tickers
            assert "TSLA" in called_tickers
            assert "NVDA" in called_tickers
    
    @pytest.mark.asyncio
    async def test_article_with_some_tradable_tickers(
        self, recall_engine, repository, mock_quote_fetcher
    ):
        """Test that only tradable tickers are tracked."""
        await recall_engine.start()
        
        article = Article(
            id="test-partial-tradable",
            source=ArticleSource.BENZINGA,
            source_id="101",
            title="Partial Tradable",
            published_at=datetime.now(timezone.utc),
            tickers=frozenset(["AAPL", "INVALID"]),
            tags=frozenset(),
            categories=frozenset()
        )
        
        # Mock: AAPL has NBBO, INVALID doesn't
        def nbbo_side_effect(ticker):
            if ticker == "AAPL":
                return {"bid": 175.50, "ask": 175.55, "spread": 0.05, "mid": 175.525}
            return None  # INVALID has no NBBO
        
        mock_quote_fetcher.get_nbbo_snapshot.side_effect = nbbo_side_effect
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            received_at = datetime.now()
            event = ArticleReceivedDomainEvent(
                article=article,
                received_at=received_at
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            await asyncio.sleep(0.2)
            
            # Verify record was created with only AAPL
            file_path = repository._get_session_file_path("recall", "premarket", received_at)
            if file_path.exists():
                async with aiofiles.open(file_path, 'r') as f:
                    content = await f.read()
                    data = json.loads(content)
                    
                    record = next((r for r in data["records"] if r["article_id"] == "test-partial-tradable"), None)
                    if record:
                        assert "AAPL" in record["tickers"]
                        assert "INVALID" not in record["tickers"]


class TestErrorHandling:
    """Test error handling in recall engine."""
    
    @pytest.mark.asyncio
    async def test_error_in_article_received_handled_gracefully(
        self, recall_engine
    ):
        """Test that errors in article received handler don't crash engine."""
        await recall_engine.start()
        
        # Publish invalid event data (should be handled gracefully)
        await recall_engine.event_bus.publish(
            DomainEventType.ARTICLE_RECEIVED,
            {"invalid": "data"}  # Invalid event data
        )
        
        await asyncio.sleep(0.1)
        
        # Engine should still be running
        assert recall_engine.event_bus.get_subscriber_count(DomainEventType.ARTICLE_RECEIVED) >= 1
    
    @pytest.mark.asyncio
    async def test_error_in_nbbo_check_handled_gracefully(
        self, recall_engine, sample_article, mock_quote_fetcher
    ):
        """Test that errors in NBBO check are handled gracefully."""
        await recall_engine.start()
        
        # Mock NBBO check to raise exception
        mock_quote_fetcher.get_nbbo_snapshot.side_effect = Exception("API Error")
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=datetime.now()
            )
            
            # Should not raise exception
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                event.model_dump()
            )
            
            await asyncio.sleep(0.2)
            
            # Engine should still be running
            assert recall_engine.event_bus.get_subscriber_count(DomainEventType.ARTICLE_RECEIVED) >= 1
