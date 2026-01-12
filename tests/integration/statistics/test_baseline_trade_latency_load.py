"""
Load Test for Trade Latency Measurement.

This test simulates high load scenarios (12pm/1pm bulk delivery) by sending
multiple articles in quick succession to test system behavior under stress.

Purpose:
--------
- Verify system performance under high load (20+ articles)
- Measure latency degradation during bulk delivery
- Identify bottlenecks (Yahoo Finance rate limiting, repository locking, etc.)
- Compare against baseline single-article test

Test Strategy:
--------------
1. Send 20 articles in quick succession (simulating bulk delivery)
2. Measure latency for each trade
3. Compare average latency against baseline (3.31s)
4. Identify which articles were delayed and why

Expected Behavior:
------------------
- First few articles: ~3.31s (baseline)
- Later articles: 5-10s (due to rate limiting, file locking, etc.)
- Some articles may timeout or fail metadata fetching

High Load Bottlenecks:
----------------------
1. Yahoo Finance rate limiting: 1-5s delays
2. Repository file locking: 0.5-2s delays
3. Event bus congestion: 0.1-0.5s delays
4. Alpaca API rate limiting: 0.5-2s delays
"""
import asyncio
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

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
from newsflash.domain.websocket.events import ArticleReceivedDomainEvent
from newsflash.domain.websocket.models import Article, ArticleSource
from newsflash.shared.statistics.yahoo_finance_coordinator import YahooFinanceCoordinator
from newsflash.infra.brokerage.service import BrokerageService
from newsflash.services.metrics.metrics_service import MetricsService


# Test configuration
NUM_ARTICLES = 20
ARTICLES_PER_SECOND = 5  # Simulate bulk delivery rate

# OSRH Trade Data (same as baseline test)
OSRH_ARTICLE_DATA = {
    "id": "benzinga:test_osrh_article",
    "source": ArticleSource.BENZINGA,
    "source_id": "test_osrh_article",
    "title": "OSRH Strategic Investment News",
    "content": None,
    "summary": None,
    "author": None,
    "published_at": datetime(2026, 1, 12, 13, 5, 0, tzinfo=timezone.utc),
    "updated_at": None,
    "url": None,
    "tickers": frozenset(["OSRH"]),
    "tags": frozenset(),
    "categories": frozenset()
}


@pytest.fixture
def test_tmp_dir():
    """Create temporary directory for tests."""
    tmpdir = tempfile.mkdtemp(prefix="baseline_latency_load_test_")
    test_dir = Path(tmpdir)
    yield test_dir
    
    # Cleanup
    if test_dir.exists():
        shutil.rmtree(test_dir)


@pytest.mark.asyncio
async def test_baseline_osrh_trade_latency_under_load(test_tmp_dir):
    """
    Load test for OSRH trade latency - EXACT SAME SCENARIO as baseline test.
    
    Sends 20 OSRH articles in quick succession to simulate high load scenarios
    (12pm/1pm bulk delivery). Uses the EXACT SAME article data as the baseline
    test to enable direct comparison.
    
    This allows us to:
    - Compare baseline (single article) vs load (20 articles)
    - Measure latency degradation under load
    - Identify bottlenecks (Yahoo Finance rate limiting, repository locking, etc.)
    """
    # Check for Alpaca credentials
    if not os.getenv("ALPACA_KEY") or not os.getenv("ALPACA_SECRET"):
        pytest.skip("ALPACA_KEY and ALPACA_SECRET must be set in .env file for load test")
    
    # Create REAL event bus
    event_bus = AsyncEventBus()
    
    # Create REAL repository
    repository = StatisticsRepository(tmp_dir=test_tmp_dir)
    
    # Create REAL Yahoo Finance coordinator
    yahoo_finance_coordinator = YahooFinanceCoordinator(num_workers=10)
    await yahoo_finance_coordinator.start()
    
    # Create metrics service (required by brokerage)
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
    
    # Create REAL BrokerageDomainListener (bridges Domain.TradeRequested → Infrastructure.TradeExecutionRequested)
    from newsflash.domain.brokerage.listener import BrokerageDomainListener
    from newsflash.domain.brokerage.validators import TradeRequestValidator, TradeResultValidator
    from newsflash.domain.brokerage.factories import TradeRequestFactory, TradeResultFactory, QuoteFactory
    from newsflash.domain.brokerage.mappers import TradeRequestMapper
    
    brokerage_domain_listener = BrokerageDomainListener(
        event_bus=event_bus,
        request_validator=TradeRequestValidator(),
        result_validator=TradeResultValidator(),
        request_factory=TradeRequestFactory(),
        result_factory=TradeResultFactory(),
        quote_factory=QuoteFactory(),
        request_mapper=TradeRequestMapper(),
    )
    await brokerage_domain_listener.start()
    
    # Create REAL recall engine
    recall_engine = RecallStatsEngine(
        event_bus=event_bus,
        repository=repository,
        quote_fetcher=brokerage.quote_fetcher,
        yahoo_finance_coordinator=yahoo_finance_coordinator,
        market_data_client=brokerage.connection_manager.market_data_client,
        trading_client=brokerage.connection_manager.trading_client
    )
    await recall_engine.start()
    
    # Track trade events (both executed and failed, since we want to measure latency even if execution fails)
    trade_results: List[Dict[str, Any]] = []
    trade_requested_times: Dict[str, float] = {}  # article_id -> timestamp
    
    async def capture_trade_executed(event_type: str, event_data: Dict[str, Any]) -> None:
        """Capture trade executed event and timestamp."""
        if event_type == DomainEventType.TRADE_EXECUTED or "TradeExecuted" in event_type:
            trade_result = event_data.get("trade_result", {})
            trade_request = trade_result.get("trade_request", {})
            article_id = event_data.get("article_id") or trade_request.get("article_id")
            trade_results.append({
                "article_id": article_id,
                "ticker": trade_result.get("ticker") or trade_request.get("ticker"),
                "executed_at": time.time(),
                "status": "executed",
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
    
    # Create multiple articles using EXACT SAME OSRH scenario (simulating bulk delivery)
    # This allows us to compare baseline (single) vs load (20x same scenario)
    articles = []
    base_time = datetime(2026, 1, 12, 13, 5, 0, tzinfo=timezone.utc)
    
    for i in range(NUM_ARTICLES):
        # Use same OSRH article data, just different IDs
        article = Article(
            id=f"benzinga:load_test_osrh_{i}",
            source=OSRH_ARTICLE_DATA["source"],
            source_id=f"load_test_osrh_{i}",
            title=OSRH_ARTICLE_DATA["title"],
            content=OSRH_ARTICLE_DATA["content"],
            summary=OSRH_ARTICLE_DATA["summary"],
            author=OSRH_ARTICLE_DATA["author"],
            published_at=OSRH_ARTICLE_DATA["published_at"],
            updated_at=OSRH_ARTICLE_DATA["updated_at"],
            url=OSRH_ARTICLE_DATA["url"],
            tickers=OSRH_ARTICLE_DATA["tickers"],  # Same ticker: OSRH
            tags=OSRH_ARTICLE_DATA["tags"],
            categories=OSRH_ARTICLE_DATA["categories"]
        )
        articles.append(article)
    
    # Send articles in quick succession (simulating bulk delivery)
    print(f"\n📊 LOAD TEST: Sending {NUM_ARTICLES} articles at {ARTICLES_PER_SECOND} articles/second")
    
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
    
    # Wait for all trades to complete (executed or failed) (with timeout)
    max_wait_time = 60.0  # 60 seconds max for all trades
    wait_start = time.time()
    
    while len(trade_results) < NUM_ARTICLES:
        await asyncio.sleep(0.5)
        if time.time() - wait_start > max_wait_time:
            print(f"\n⚠️  Timeout: Only {len(trade_results)}/{NUM_ARTICLES} trades completed (executed or failed)")
            break
    
    # Calculate latencies
    latencies = []
    # Match trade results to articles by ticker (since we're all using OSRH)
    # We'll match them in order since they should execute roughly in order
    for i, result in enumerate(trade_results):
        # Try to match by article_id first, then by index
        article_id = result.get("article_id")
        if article_id and article_id in start_times:
            latency = result["executed_at"] - start_times[article_id]
        else:
            # Fallback: match by order (first trade = first article, etc.)
            if i < len(articles):
                article_id = articles[i].id
                if article_id in start_times:
                    latency = result["executed_at"] - start_times[article_id]
                else:
                    continue  # Skip if we can't match
            else:
                continue  # Skip if index out of range
        
        latencies.append(latency)
        result["latency"] = latency
        result["matched_article_id"] = article_id
    
    # Separate executed vs failed trades
    executed_trades = [r for r in trade_results if r.get("status") == "executed"]
    failed_trades = [r for r in trade_results if r.get("status") == "failed"]
    
    # Print results
    print(f"\n📊 LOAD TEST RESULTS:")
    print(f"   Articles sent: {NUM_ARTICLES}")
    print(f"   Trades completed: {len(trade_results)} (executed: {len(executed_trades)}, failed: {len(failed_trades)})")
    print(f"   Completion rate: {len(trade_results)/NUM_ARTICLES*100:.1f}%")
    
    if failed_trades:
        print(f"\n   ⚠️  Note: {len(failed_trades)} trades failed (likely insufficient buying power)")
        print(f"      This is expected in load tests - measuring latency to trade request/completion")
    
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        
        print(f"\n   📈 LATENCY METRICS (Article Received → Trade Completed):")
        print(f"      Average latency: {avg_latency:.3f} seconds")
        print(f"      Min latency: {min_latency:.3f} seconds")
        print(f"      Max latency: {max_latency:.3f} seconds")
        print(f"      Baseline (single article): 3.31 seconds")
        print(f"      Degradation: {avg_latency - 3.31:.3f} seconds")
        
        # Analyze latency distribution
        first_5_avg = sum(latencies[:5]) / min(5, len(latencies))
        last_5_avg = sum(latencies[-5:]) / min(5, len(latencies))
        
        print(f"\n   📊 LATENCY DISTRIBUTION:")
        print(f"      First 5 articles avg: {first_5_avg:.3f} seconds")
        print(f"      Last 5 articles avg: {last_5_avg:.3f} seconds")
        print(f"      Latency increase: {last_5_avg - first_5_avg:.3f} seconds")
        
        # Calculate trade request latency (if available)
        if trade_requested_times:
            request_latencies = []
            for result in trade_results:
                article_id = result.get("article_id")
                if article_id and article_id in start_times and article_id in trade_requested_times:
                    request_latency = trade_requested_times[article_id] - start_times[article_id]
                    request_latencies.append(request_latency)
            
            if request_latencies:
                avg_request_latency = sum(request_latencies) / len(request_latencies)
                print(f"\n   ⚡ SURGE DETECTION → TRADE REQUEST LATENCY:")
                print(f"      Average: {avg_request_latency:.3f} seconds")
                print(f"      (This measures how fast surge detection triggers trade requests)")
        
        # Verify results
        assert len(trade_results) > 0, "At least one trade should complete (executed or failed)"
        # Note: We measure latency even for failed trades, as the system still processed them
        if avg_latency > 30.0:
            print(f"\n   ⚠️  WARNING: Average latency {avg_latency:.3f}s is very high")
        else:
            print(f"\n   ✅ LOAD TEST PASSED: System handled {len(trade_results)} trades under load")
    else:
        pytest.fail("No trades completed during load test")
    
    # Cleanup
    await recall_engine.stop()
    await brokerage_domain_listener.stop()
    await brokerage.stop()
    await metrics_service.stop()
    await yahoo_finance_coordinator.stop()
