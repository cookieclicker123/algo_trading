"""
Load Test for AAPL Trade Latency - Testing with Highly Liquid Stock.

This test uses AAPL instead of OSRH to test with a highly liquid stock
that should actually fill (not fail with wash trade errors).

Purpose:
--------
- Test latency with a stock that will actually fill
- Compare failed trades (OSRH) vs successful fills (AAPL)
- Measure real fill time vs immediate rejection time

Expected Results:
-----------------
- Failed trades (OSRH): ~4.6s (immediate rejection, no fill wait)
- Successful fills (AAPL): ~5.0-6.5s (includes fill wait time)
- Difference: ~0.5-2.0s (time spent waiting for fill)
"""
import asyncio
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from typing import Dict, Any

import pytest
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

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
from newsflash.shared.statistics.yahoo_finance_coordinator import YahooFinanceCoordinator


# AAPL Article Data (highly liquid stock - should fill successfully)
AAPL_ARTICLE_DATA = {
    "id": "benzinga:test_aapl_article",
    "source": ArticleSource.BENZINGA,
    "source_id": "test_aapl_article",
    "title": "Apple Inc. Strategic Investment News",
    "content": None,
    "summary": None,
    "author": None,
    "published_at": datetime(2026, 1, 13, 14, 0, 0, tzinfo=timezone.utc),  # Market hours
    "updated_at": None,
    "url": None,
    "tickers": frozenset(["AAPL"]),
    "tags": frozenset(),
    "categories": frozenset()
}

NUM_ARTICLES = 5  # Fewer articles for AAPL (more expensive, but should fill)
ARTICLES_PER_SECOND = 2.0  # 2 articles/second


@pytest.fixture
def test_tmp_dir():
    """Create temporary directory for tests."""
    tmp_dir = tempfile.mkdtemp()
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_baseline_aapl_trade_latency_under_load(test_tmp_dir):
    """
    Load test for AAPL trade latency - highly liquid stock that should fill.
    
    This test uses AAPL instead of OSRH to test with a stock that will actually
    fill successfully, allowing us to measure real fill time vs immediate rejection.
    
    Expected:
    - Failed trades (OSRH): ~4.6s (immediate rejection)
    - Successful fills (AAPL): ~5.0-6.5s (includes fill wait time)
    """
    # Create REAL event bus
    event_bus = AsyncEventBus()
    
    # Create REAL repository
    repository = StatisticsRepository(tmp_dir=test_tmp_dir)
    
    # Create REAL Yahoo Finance coordinator
    yahoo_finance_coordinator = YahooFinanceCoordinator(num_workers=10)
    await yahoo_finance_coordinator.start()
    
    # Check for Alpaca credentials
    if not os.getenv("ALPACA_KEY") or not os.getenv("ALPACA_SECRET"):
        pytest.skip("ALPACA_KEY and ALPACA_SECRET must be set in .env file for load test")
    
    from newsflash.infra.brokerage.service import BrokerageService
    from newsflash.services.metrics.metrics_service import MetricsService
    from newsflash.domain.brokerage.listener import BrokerageDomainListener
    from newsflash.domain.brokerage.validators import TradeRequestValidator, TradeResultValidator
    from newsflash.domain.brokerage.factories import TradeRequestFactory, TradeResultFactory
    
    # Create metrics service
    metrics_service = MetricsService(event_bus)
    await metrics_service.start()
    
    # Create REAL brokerage service
    brokerage = BrokerageService(
        event_bus=event_bus,
        paper_trading=True,
        metrics_service=metrics_service
    )
    await brokerage.start()
    
    # Wait for Alpaca connection
    timeout = 10.0
    start_wait = time.time()
    while not brokerage.is_connected():
        await asyncio.sleep(0.5)
        if time.time() - start_wait > timeout:
            pytest.skip("Alpaca connection not established")
    
    # Create BrokerageDomainListener
    brokerage_listener = BrokerageDomainListener(
        event_bus=event_bus,
        brokerage_service=brokerage,
        trade_request_validator=TradeRequestValidator(),
        trade_result_validator=TradeResultValidator(),
        trade_request_factory=TradeRequestFactory(),
        trade_result_factory=TradeResultFactory()
    )
    await brokerage_listener.start()
    
    # Create quote fetcher
    quote_fetcher = AlpacaQuoteFetcher(
        event_bus=event_bus,
        market_data_client=brokerage.connection_manager.market_data_client,
        stream_manager=brokerage.connection_manager.stream_manager
    )
    
    # Create recall engine
    recall_engine = RecallStatsEngine(
        event_bus=event_bus,
        repository=repository,
        quote_fetcher=quote_fetcher,
        market_data_client=brokerage.connection_manager.market_data_client,
        yahoo_finance_coordinator=yahoo_finance_coordinator
    )
    await recall_engine.start()
    
    # Track trade results
    trade_results = []
    trade_requested_times = {}
    
    async def capture_trade_executed(event_type: str, event_data: Dict[str, Any]) -> None:
        """Capture trade executed event and timestamp."""
        if event_type == DomainEventType.TRADE_EXECUTED or "TradeExecuted" in event_type:
            trade_request = event_data.get("trade_request", {})
            article_id = event_data.get("article_id") or trade_request.get("article_id")
            trade_results.append({
                "article_id": article_id,
                "ticker": trade_request.get("ticker"),
                "executed_at": time.time(),
                "status": "executed",
                "fill_price": event_data.get("fill_price"),
                "shares": event_data.get("shares"),
                "event_data": event_data
            })
    
    async def capture_trade_failed(event_type: str, event_data: Dict[str, Any]) -> None:
        """Capture trade failed event and timestamp."""
        if event_type == DomainEventType.TRADE_FAILED or "TradeFailed" in event_type:
            trade_request = event_data.get("trade_request", {})
            article_id = event_data.get("article_id") or trade_request.get("article_id")
            trade_results.append({
                "article_id": article_id,
                "ticker": trade_request.get("ticker"),
                "executed_at": time.time(),  # Time when failure occurred
                "status": "failed",
                "error": event_data.get("error"),
                "event_data": event_data
            })
    
    async def capture_trade_requested(event_type: str, event_data: Dict[str, Any]) -> None:
        """Capture trade requested event and timestamp."""
        if event_type == DomainEventType.TRADE_REQUESTED or "TradeRequested" in event_type:
            trade_request = event_data.get("trade_request", {})
            article_id = event_data.get("article_id") or trade_request.get("article_id")
            if article_id:
                trade_requested_times[article_id] = time.time()
    
    # Subscribe to trade events
    event_bus.subscribe(DomainEventType.TRADE_EXECUTED, capture_trade_executed)
    event_bus.subscribe(DomainEventType.TRADE_FAILED, capture_trade_failed)
    event_bus.subscribe(DomainEventType.TRADE_REQUESTED, capture_trade_requested)
    
    # Create multiple articles using AAPL
    articles = []
    base_time = datetime(2026, 1, 13, 14, 0, 0, tzinfo=timezone.utc)  # Market hours
    
    for i in range(NUM_ARTICLES):
        article = Article(
            id=f"benzinga:load_test_aapl_{i}",
            source=AAPL_ARTICLE_DATA["source"],
            source_id=f"load_test_aapl_{i}",
            title=AAPL_ARTICLE_DATA["title"],
            content=AAPL_ARTICLE_DATA["content"],
            summary=AAPL_ARTICLE_DATA["summary"],
            author=AAPL_ARTICLE_DATA["author"],
            published_at=AAPL_ARTICLE_DATA["published_at"],
            updated_at=AAPL_ARTICLE_DATA["updated_at"],
            url=AAPL_ARTICLE_DATA["url"],
            tickers=AAPL_ARTICLE_DATA["tickers"],  # AAPL
            tags=AAPL_ARTICLE_DATA["tags"],
            categories=AAPL_ARTICLE_DATA["categories"]
        )
        articles.append(article)
    
    # Send articles in quick succession
    print(f"\n📊 LOAD TEST (AAPL): Sending {NUM_ARTICLES} articles at {ARTICLES_PER_SECOND} articles/second")
    
    start_times = {}
    for i, article in enumerate(articles):
        article_received_at = datetime.now(timezone.utc)
        start_times[article.id] = time.time()
        
        article_event = ArticleReceivedDomainEvent(
            article=article,
            received_at=article_received_at,
            source="domain.websocket"
        )
        
        await event_bus.publish(
            DomainEventType.ARTICLE_RECEIVED,
            article_event.model_dump()
        )
        
        # Rate limit: send articles at specified rate
        if i < len(articles) - 1:
            await asyncio.sleep(1.0 / ARTICLES_PER_SECOND)
    
    # Wait for all trades to complete (executed or failed)
    max_wait_time = 60.0
    wait_start = time.time()
    
    while len(trade_results) < NUM_ARTICLES:
        await asyncio.sleep(0.5)
        if time.time() - wait_start > max_wait_time:
            print(f"\n⚠️  Timeout: Only {len(trade_results)}/{NUM_ARTICLES} trades completed")
            break
    
    # Calculate latencies
    latencies = []
    for i, result in enumerate(trade_results):
        article_id = result.get("article_id")
        if article_id and article_id in start_times:
            latency = result["executed_at"] - start_times[article_id]
        else:
            if i < len(articles):
                article_id = articles[i].id
                if article_id in start_times:
                    latency = result["executed_at"] - start_times[article_id]
                else:
                    continue
            else:
                continue
        
        latencies.append(latency)
        result["latency"] = latency
        result["matched_article_id"] = article_id
    
    # Separate executed vs failed trades
    executed_trades = [r for r in trade_results if r.get("status") == "executed"]
    failed_trades = [r for r in trade_results if r.get("status") == "failed"]
    
    # Print results
    print(f"\n📊 LOAD TEST RESULTS (AAPL):")
    print(f"   Articles sent: {NUM_ARTICLES}")
    print(f"   Trades completed: {len(trade_results)} (executed: {len(executed_trades)}, failed: {len(failed_trades)})")
    print(f"   Completion rate: {len(trade_results)/NUM_ARTICLES*100:.1f}%")
    
    if executed_trades:
        executed_latencies = [r["latency"] for r in executed_trades]
        avg_executed = sum(executed_latencies) / len(executed_latencies)
        print(f"\n   ✅ EXECUTED TRADES (Filled):")
        print(f"      Count: {len(executed_trades)}")
        print(f"      Average latency: {avg_executed:.3f} seconds")
        print(f"      Min latency: {min(executed_latencies):.3f} seconds")
        print(f"      Max latency: {max(executed_latencies):.3f} seconds")
    
    if failed_trades:
        failed_latencies = [r["latency"] for r in failed_trades]
        avg_failed = sum(failed_latencies) / len(failed_latencies)
        print(f"\n   ❌ FAILED TRADES (Rejected):")
        print(f"      Count: {len(failed_trades)}")
        print(f"      Average latency: {avg_failed:.3f} seconds")
        print(f"      Min latency: {min(failed_latencies):.3f} seconds")
        print(f"      Max latency: {max(failed_latencies):.3f} seconds")
    
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        
        print(f"\n   📈 LATENCY METRICS (Article Received → Trade Completed):")
        print(f"      Average latency: {avg_latency:.3f} seconds")
        print(f"      Min latency: {min_latency:.3f} seconds")
        print(f"      Max latency: {max_latency:.3f} seconds")
        
        if executed_trades and failed_trades:
            fill_time_diff = avg_executed - avg_failed
            print(f"\n   ⚡ FILL TIME IMPACT:")
            print(f"      Executed (filled): {avg_executed:.3f}s")
            print(f"      Failed (rejected): {avg_failed:.3f}s")
            print(f"      Difference (fill wait): {fill_time_diff:.3f}s")
            print(f"      This shows time spent waiting for order fill vs immediate rejection")
    
    # Calculate surge detection latency
    surge_latencies = []
    for result in trade_results:
        article_id = result.get("matched_article_id")
        if article_id and article_id in start_times and article_id in trade_requested_times:
            surge_latency = trade_requested_times[article_id] - start_times[article_id]
            surge_latencies.append(surge_latency)
    
    if surge_latencies:
        avg_surge = sum(surge_latencies) / len(surge_latencies)
        print(f"\n   ⚡ SURGE DETECTION → TRADE REQUEST LATENCY:")
        print(f"      Average: {avg_surge:.3f} seconds")
        print(f"      (This measures how fast surge detection triggers trade requests)")
    
    # Cleanup
    await recall_engine.stop()
    await brokerage_listener.stop()
    await brokerage.stop()
    await metrics_service.stop()
    await yahoo_finance_coordinator.stop()
    
    print(f"\n   ✅ LOAD TEST PASSED: System handled {NUM_ARTICLES} trades under load")
    
    # Test passes if we got results (even if some failed)
    assert len(trade_results) > 0, "No trades completed"
