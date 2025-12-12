"""
Integration tests for RecallStatsEngine.

Integration Test Strategy:
---------------------------
These tests verify end-to-end event flow and file I/O operations.

External Dependencies (REAL):
- Event bus: Real AsyncEventBus to test event subscription and publishing
- Repository: Real StatisticsRepository to test actual file I/O operations

External Dependencies (MOCKED):
- Quote fetcher: Mocked to avoid real API calls
- asyncio.sleep: Mocked to skip 5-minute monitoring delays (tests should be fast)
- Market session detector: Mocked to control test scenarios

What These Tests Prove:
- Events flow correctly through the system
- Records are created and updated in files correctly
- Monitoring tasks are created and cancelled correctly
- Summary statistics are calculated correctly
- Multiple event types interact correctly (article received, classified, trade executed)

Note: We mock asyncio.sleep to avoid waiting 5 minutes in tests. The actual 5-minute
monitoring behavior is tested in unit tests with proper mocking.
"""
import asyncio
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
from newsflash.infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
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
    tmpdir = tempfile.mkdtemp(prefix="recall_engine_integration_test_")
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
def mock_quote_fetcher():
    """Create mocked quote fetcher."""
    quote_fetcher = AsyncMock(spec=AlpacaQuoteFetcher)
    quote_fetcher.get_nbbo_snapshot = AsyncMock(return_value=None)
    return quote_fetcher


@pytest.fixture
def recall_engine(event_bus, repository, mock_quote_fetcher):
    """Create recall engine instance for tests."""
    return RecallStatsEngine(
        event_bus=event_bus,
        repository=repository,
        quote_fetcher=mock_quote_fetcher
    )


@pytest.fixture
def sample_article():
    """Sample article for testing."""
    return Article(
        id="integration-test-article-123",
        source=ArticleSource.BENZINGA,
        source_id="123",
        title="Integration Test Article",
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


class TestEndToEndRecallWorkflow:
    """Test complete end-to-end recall tracking workflows."""
    
    @pytest.mark.asyncio
    async def test_complete_recall_workflow_with_missed_opportunity(
        self, recall_engine, repository, sample_article, mock_quote_fetcher
    ):
        """
        Integration test goal: Verify event flow → record creation → price monitoring → update.
        
        External dependencies:
        - Real event bus (test event flow)
        - Real repository (test file I/O)
        - Mocked quote fetcher (no real API calls)
        - Mocked asyncio.sleep (skip 5-minute wait)
        """
        await recall_engine.start()
        
        # Mock NBBO snapshots
        initial_nbbo = {
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        }
        
        # Final NBBO after 5 minutes (1%+ move)
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
        
        received_at = datetime.now()
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session, \
             patch('newsflash.shared.statistics.recall_engine.asyncio.sleep', return_value=None) as mock_sleep:  # Skip 5-minute wait
            
            mock_session.return_value = ("premarket", True)
            
            # Step 1: Publish article received event
            article_event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=received_at
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                article_event.model_dump()
            )
            
            # Wait for initial record creation (with retry for async file write)
            file_path = repository._get_session_file_path("recall", "premarket", received_at)
            max_retries = 20
            data = None
            for i in range(max_retries):
                await asyncio.sleep(0.15)  # Wait a bit longer
                if file_path.exists():
                    try:
                        async with aiofiles.open(file_path, 'r') as f:
                            content = await f.read()
                            if content.strip():  # File has content
                                data = json.loads(content)
                                if len(data.get("records", [])) > 0:
                                    break
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        if i == max_retries - 1:
                            raise AssertionError(f"Failed to read file after {max_retries} retries: {e}")
                        continue
            
            assert file_path.exists(), "Recall file should exist"
            assert data is not None, f"File should have content. File exists: {file_path.exists()}, Path: {file_path}"
            
            assert len(data["records"]) == 1
            record = data["records"][0]
            assert record["article_id"] == "integration-test-article-123"
            assert record["initial_nbbo"]["mid"] == 175.525
            assert data["summary"]["total_articles_tracked"] == 1
            
            # Step 2: Wait for monitoring task to complete (with mocked sleep, it should be fast)
            # Get the monitoring task and wait for it
            async with recall_engine._monitoring_lock:
                task = recall_engine._monitoring_tasks.get("integration-test-article-123")
            
            if task:
                # Wait for task to complete (sleep is mocked, so it should finish quickly)
                await asyncio.wait_for(task, timeout=2.0)
            
            # Step 3: Verify record was updated with price check
            await asyncio.sleep(0.2)  # Give file write time to complete
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                
                record = data["records"][0]
                assert record["price_check_5min"] is not None
                assert record["price_check_5min"]["moved_1_percent"] is True
                assert record["price_check_5min"]["percent_change"] > 1.0
                assert data["summary"]["articles_with_1_percent_move"] == 1
        
        await recall_engine.stop()
    
    @pytest.mark.asyncio
    async def test_recall_workflow_with_classification_filter(
        self, recall_engine, repository, sample_article, mock_quote_fetcher
    ):
        """
        Test workflow: article received → classified as IGNORE → filter reason added.
        """
        await recall_engine.start()
        
        # Mock NBBO available
        mock_quote_fetcher.get_nbbo_snapshot.return_value = {
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        }
        
        received_at = datetime.now()
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session, \
             patch('newsflash.shared.statistics.recall_engine.get_market_session_from_timestamp') as mock_session_from_ts:
            
            mock_session.return_value = ("premarket", True)
            mock_session_from_ts.return_value = ("premarket", True)
            
            # Step 1: Publish article received event
            article_event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=received_at
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                article_event.model_dump()
            )
            
            await asyncio.sleep(0.3)
            
            # Step 2: Publish classification event (IGNORE)
            classification_result = ClassificationResult(
                article_id="integration-test-article-123",
                classification=ClassificationCategory.IGNORE,
                confidence=ClassificationConfidence.MEDIUM,
                reasoning="Not significant",
                classified_at=datetime.now(),
                latency_ms=50.0
            )
            
            classified_event = ArticleClassifiedDomainEvent(
                article_id="integration-test-article-123",
                result=classification_result,
                classified_at=datetime.now()
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_CLASSIFIED,
                classified_event.model_dump()
            )
            
            await asyncio.sleep(0.3)
            
            # Step 3: Verify filter reason was added
            file_path = repository._get_session_file_path("recall", "premarket", received_at)
            assert file_path.exists()
            
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                
                record = data["records"][0]
                assert "not_classified_ignore" in record.get("filter_reasons", [])
                assert data["summary"]["filter_breakdown"].get("not_classified_ignore", 0) >= 1
        
        await recall_engine.stop()
    
    @pytest.mark.asyncio
    async def test_recall_workflow_with_trade_execution(
        self, recall_engine, repository, sample_article, mock_quote_fetcher
    ):
        """
        Test workflow: article received → monitoring started → trade executed → monitoring cancelled.
        """
        await recall_engine.start()
        
        # Mock NBBO available
        mock_quote_fetcher.get_nbbo_snapshot.return_value = {
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        }
        
        received_at = datetime.now()
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            # Step 1: Publish article received event
            article_event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=received_at
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                article_event.model_dump()
            )
            
            await asyncio.sleep(0.3)
            
            # Verify monitoring task was created
            async with recall_engine._monitoring_lock:
                assert "integration-test-article-123" in recall_engine._monitoring_tasks
                task = recall_engine._monitoring_tasks["integration-test-article-123"]
                assert not task.done()
            
            # Step 2: Publish trade executed event
            trade_result = TradeResult(
                trade_request={
                    "ticker": "AAPL",
                    "article_id": "integration-test-article-123",
                    "action": "BUY"
                },
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
            
            await asyncio.sleep(0.3)
            
            # Step 3: Verify monitoring task was cancelled/removed
            async with recall_engine._monitoring_lock:
                assert "integration-test-article-123" not in recall_engine._monitoring_tasks
                # Task might be cancelled or might have finished (if it checked traded_articles first)
                assert task.done(), "Task should be done (either cancelled or finished)"
            
            # Verify article marked as traded
            async with recall_engine._traded_lock:
                assert "integration-test-article-123" in recall_engine._traded_articles
        
        await recall_engine.stop()
    
    @pytest.mark.asyncio
    async def test_recall_workflow_multiple_tickers(
        self, recall_engine, repository, mock_quote_fetcher
    ):
        """
        Test workflow with article containing multiple tradable tickers.
        """
        await recall_engine.start()
        
        article = Article(
            id="multi-ticker-test",
            source=ArticleSource.BENZINGA,
            source_id="456",
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
        
        received_at = datetime.now()
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            article_event = ArticleReceivedDomainEvent(
                article=article,
                received_at=received_at
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                article_event.model_dump()
            )
            
            await asyncio.sleep(0.3)
            
            # Verify all tickers were checked
            assert mock_quote_fetcher.get_nbbo_snapshot.call_count >= 3
            
            # Verify record was created with all tradable tickers
            file_path = repository._get_session_file_path("recall", "premarket", received_at)
            if file_path.exists():
                async with aiofiles.open(file_path, 'r') as f:
                    content = await f.read()
                    data = json.loads(content)
                    
                    record = next((r for r in data["records"] if r["article_id"] == "multi-ticker-test"), None)
                    if record:
                        assert "AAPL" in record["tickers"]
                        assert "TSLA" in record["tickers"]
                        assert "NVDA" in record["tickers"]
        
        await recall_engine.stop()
    
    @pytest.mark.asyncio
    async def test_recall_workflow_no_nbbo_skipped(
        self, recall_engine, repository, sample_article, mock_quote_fetcher
    ):
        """
        Test that articles with no NBBO (non-tradable) are skipped.
        """
        await recall_engine.start()
        
        # Mock NBBO unavailable
        mock_quote_fetcher.get_nbbo_snapshot.return_value = None
        
        received_at = datetime.now()
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session:
            mock_session.return_value = ("premarket", True)
            
            article_event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=received_at
            )
            
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                article_event.model_dump()
            )
            
            await asyncio.sleep(0.3)
            
            # Verify no record was created
            file_path = repository._get_session_file_path("recall", "premarket", received_at)
            assert not file_path.exists(), "No record should be created for non-tradable ticker"
            
            # Verify no monitoring task was created
            async with recall_engine._monitoring_lock:
                assert "integration-test-article-123" not in recall_engine._monitoring_tasks
        
        await recall_engine.stop()
    
    @pytest.mark.asyncio
    async def test_recall_workflow_complete_with_all_updates(
        self, recall_engine, repository, sample_article, mock_quote_fetcher
    ):
        """
        Integration test goal: Verify complete event flow with all update types.
        
        Tests: article received → classified → monitoring → price check → summary updates.
        Uses mocked sleep to avoid 5-minute wait.
        """
        await recall_engine.start()
        
        initial_nbbo = {"bid": 175.50, "ask": 175.55, "spread": 0.05, "mid": 175.525}
        final_nbbo = {"bid": 177.50, "ask": 177.55, "spread": 0.05, "mid": 177.525}
        
        mock_quote_fetcher.get_nbbo_snapshot.side_effect = [initial_nbbo, final_nbbo]
        
        received_at = datetime.now()
        
        with patch('newsflash.shared.statistics.recall_engine.get_market_session') as mock_session, \
             patch('newsflash.shared.statistics.recall_engine.get_market_session_from_timestamp') as mock_session_from_ts, \
             patch('newsflash.shared.statistics.recall_engine.asyncio.sleep', return_value=None) as mock_sleep:  # Skip 5-minute wait
            
            mock_session.return_value = ("premarket", True)
            mock_session_from_ts.return_value = ("premarket", True)
            
            # Step 1: Article received
            article_event = ArticleReceivedDomainEvent(
                article=sample_article,
                received_at=received_at
            )
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_RECEIVED,
                article_event.model_dump()
            )
            await asyncio.sleep(0.2)
            
            # Step 2: Classified as IGNORE
            classification_result = ClassificationResult(
                article_id="integration-test-article-123",
                classification=ClassificationCategory.IGNORE,
                confidence=ClassificationConfidence.LOW,
                reasoning="Not significant",
                classified_at=datetime.now(),
                latency_ms=50.0
            )
            classified_event = ArticleClassifiedDomainEvent(
                article_id="integration-test-article-123",
                result=classification_result,
                classified_at=datetime.now()
            )
            await recall_engine.event_bus.publish(
                DomainEventType.ARTICLE_CLASSIFIED,
                classified_event.model_dump()
            )
            await asyncio.sleep(0.2)
            
            # Step 3: Wait for monitoring task to complete (sleep is mocked, so it should finish quickly)
            async with recall_engine._monitoring_lock:
                task = recall_engine._monitoring_tasks.get("integration-test-article-123")
            
            if task:
                # Wait for task to complete (sleep is mocked, so it should finish quickly)
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except asyncio.TimeoutError:
                    pass  # Task might have already completed
            
            # Give file write time to complete
            await asyncio.sleep(0.2)
            
            # Step 4: Verify final state
            file_path = repository._get_session_file_path("recall", "premarket", received_at)
            assert file_path.exists()
            
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                
                assert len(data["records"]) == 1
                record = data["records"][0]
                
                # Verify all updates
                assert record["initial_nbbo"] is not None
                assert "not_classified_ignore" in record.get("filter_reasons", [])
                assert record["price_check_5min"] is not None, "Price check should be completed"
                assert record["price_check_5min"]["moved_1_percent"] is True
                
                # Verify summary
                summary = data["summary"]
                assert summary["total_articles_tracked"] == 1
                assert summary["articles_with_1_percent_move"] == 1
                assert summary["missed_opportunities"] == 1  # Had filter reason and moved 1%+
                assert summary["filter_breakdown"].get("not_classified_ignore", 0) >= 1
        
        await recall_engine.stop()
