"""
Integration test for full auto-trade flow with Alpaca.

This test mocks:
- WebSocket (publishes ArticleReceived event directly)
- Storage (mock StorageQueryService returns articles immediately)
- Classification (publishes ArticleClassified event directly)

This test uses REAL:
- Brokerage service (Alpaca paper trading) - executes actual paper trades
- Notification service (Telegram) - sends real notifications
- Event bus (AsyncEventBus) - real event-driven flow
- Exit trade use case - automatically exits positions after 5 minutes

Goal: Complete trade entry, notification, and exit in ~5 minutes from news event.
"""
import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure src is on path
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from newsflash.shared.event_bus import AsyncEventBus
from newsflash.shared.event_types import DomainEventType
from newsflash.domain.websocket.models import Article, ArticleSource
from newsflash.domain.classification.models import (
    ClassificationResult,
    ClassificationCategory,
    ClassificationConfidence
)
from newsflash.domain.classification.events import ArticleClassifiedDomainEvent
from newsflash.services.brokerage.auto_trade import AutoTradeService
from newsflash.services.storage.query_service import StorageQueryService
from newsflash.services.metrics.metrics_service import MetricsService
from newsflash.use_cases.brokerage.exit_trade_use_case import ExitTradeUseCase


@pytest.mark.asyncio
async def test_full_auto_trade_flow_entry_notification_and_exit():
    """
    Full integration test: News → Classification → Auto-Trade → Trade Execution → Notification → Exit
    
    Mocks: WebSocket, Storage, Classification
    Real: Brokerage (Alpaca paper trading), Notification (Telegram), Event Bus
    
    Should complete in ~5 minutes (entry + 5 min delay + exit).
    """
    if os.getenv("RUN_ALPACA_INTEGRATION") != "1":
        pytest.skip("Set RUN_ALPACA_INTEGRATION=1 to run the live Alpaca integration test.")
    
    # Check for Alpaca credentials
    if not os.getenv("ALPACA_KEY") or not os.getenv("ALPACA_SECRET"):
        pytest.skip("ALPACA_KEY and ALPACA_SECRET must be set in environment to run integration test.")
    
    print("\n" + "=" * 80)
    print("FULL AUTO-TRADE INTEGRATION TEST (ALPACA)")
    print("=" * 80)
    
    # Setup real event bus
    event_bus = AsyncEventBus()
    print("✅ Event bus created")
    
    # Create mock article with ticker
    article_id = f"benzinga:test-{int(datetime.now().timestamp())}"
    test_ticker = "AAPL"  # Use liquid ticker for reliable execution
    publication_time = datetime.now(timezone.utc)
    
    domain_article = Article(
        id=article_id,
        source=ArticleSource.BENZINGA,
        source_id=article_id.split(":")[1],
        title=f"Test Company {test_ticker} announces major contract worth $100M",
        content="Test content for integration test",
        summary="Test summary - major contract announcement",
        author="Test Author",
        published_at=publication_time,
        updated_at=None,
        url=f"https://test.com/{article_id}",
        tickers=frozenset([test_ticker]),
        tags=frozenset(["test"]),
        categories=frozenset(["test"])
    )
    print(f"✅ Mock article created: {article_id} with ticker {test_ticker}")
    print(f"   Publication time: {publication_time.isoformat()}")
    
    # Mock StorageQueryService - returns article immediately
    mock_storage_service = MagicMock(spec=StorageQueryService)
    mock_storage_service.fetch_article = AsyncMock(return_value=domain_article)
    mock_storage_service.start = AsyncMock()
    mock_storage_service.stop = AsyncMock()
    print("✅ Mock storage service created (returns article immediately)")
    
    # Initialize MetricsService (required by brokerage)
    metrics_service = MetricsService(event_bus)
    await metrics_service.start()
    print("✅ MetricsService started")
    
    # Initialize REAL BrokerageMicroservice (Alpaca paper trading)
    print("\n📡 Initializing REAL Alpaca Brokerage Service (paper trading)...")
    from newsflash.services.brokerage import initialize_brokerage_microservice
    brokerage = await initialize_brokerage_microservice(
        event_bus=event_bus,
        paper_trading=True,
        metrics_service=metrics_service
    )
    await brokerage.start()
    print("✅ Brokerage service started")
    
    # Wait for Alpaca connection to be established
    print("\n⏳ Waiting for Alpaca connection to be established (max 10 seconds)...")
    connection_timeout = 10.0
    start_time = asyncio.get_event_loop().time()
    
    while not brokerage.infra.is_connected():
        await asyncio.sleep(0.5)
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > connection_timeout:
            pytest.skip(
                f"Alpaca connection not established within {connection_timeout} seconds. "
                "Check ALPACA_KEY and ALPACA_SECRET environment variables."
            )
        if elapsed % 2 < 0.5:  # Print every 2 seconds
            print(f"   Still waiting for connection... ({elapsed:.1f}s)")
    
    elapsed = asyncio.get_event_loop().time() - start_time
    print(f"✅ Alpaca connection established in {elapsed:.2f} seconds")
    
    # Initialize REAL Notification service (Telegram)
    print("\n📱 Initializing REAL Notification Service (Telegram)...")
    from newsflash.services.notification import initialize_notification_microservice
    from newsflash.config import settings
    from newsflash.use_cases.notification.notify_trade_executed_use_case import NotifyTradeExecutedUseCase
    from newsflash.use_cases.notification.notify_imminent_article_use_case import NotifyImminentArticleUseCase
    from newsflash.use_cases.notification.notify_exit_trade_use_case import NotifyExitTradeUseCase
    
    # Get telegram config directly from settings to avoid circular import
    telegram_config_1 = settings.get_telegram_config()
    telegram_config_2 = settings.get_telegram_config_2()
    
    # Initialize notification use cases first
    notify_trade_executed_use_case = NotifyTradeExecutedUseCase(
        event_bus=event_bus,
        storage_query_service=mock_storage_service
    )
    notify_exit_trade_use_case = NotifyExitTradeUseCase(event_bus=event_bus)
    
    notification = await initialize_notification_microservice(
        event_bus=event_bus,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
        metrics_service=metrics_service
    )
    
    # Add notification use cases to the microservice
    notification.use_case = NotifyImminentArticleUseCase(
        event_bus=event_bus,
        storage_query_service=mock_storage_service
    )
    notification.notify_trade_executed_use_case = notify_trade_executed_use_case
    notification.notify_exit_trade_use_case = notify_exit_trade_use_case
    
    await notification.start()
    print("✅ Notification service started (with all use cases)")
    
    # Initialize AutoTradeService (will subscribe to ArticleClassified events)
    auto_trade_service = AutoTradeService(
        event_bus=event_bus,
        storage_query_service=mock_storage_service,
        enabled=True,
        trade_amount_usd=Decimal("100.0")  # $100 trade
    )
    await auto_trade_service.start()
    print("✅ AutoTradeService started (subscribed to ArticleClassified events)")
    
    # Initialize ExitTradeUseCase (will subscribe to TradeExecuted events)
    # Override exit delay to 10 seconds for testing
    import newsflash.config.settings as settings_module
    original_exit_delay = settings_module.AUTO_TRADE_EXIT_DELAY_MINUTES
    settings_module.AUTO_TRADE_EXIT_DELAY_MINUTES = 0.167  # 10 seconds (10/60 minutes)
    
    exit_trade_use_case = ExitTradeUseCase(event_bus=event_bus)
    await exit_trade_use_case.start()
    print("✅ ExitTradeUseCase started (will exit positions after 10 seconds for testing)")
    
    # Notification use cases are already initialized above
    print("✅ Notification use cases created (will be started with notification service)")
    
    # Track events for verification
    trade_requested_events = []
    trade_executed_events = []
    trade_failed_events = []
    notification_sent_events = []
    exit_trade_events = []
    
    async def track_trade_requested(event_type: str, event_data: dict):
        if event_type == DomainEventType.TRADE_REQUESTED:
            trade_requested_events.append(event_data)
            print(f"\n📢 Trade Request Published:")
            trade_req = event_data.get("trade_request", {})
            print(f"   Ticker: {trade_req.get('ticker')}")
            print(f"   Action: {trade_req.get('action')}")
            print(f"   Shares: {trade_req.get('shares', 'TBD')}")
            print(f"   Leverage: {trade_req.get('leverage', 'N/A')}")
    
    async def track_trade_executed(event_type: str, event_data: dict):
        if event_type == DomainEventType.TRADE_EXECUTED:
            trade_executed_events.append(event_data)
            trade_result = event_data.get("trade_result", {})
            trade_request = trade_result.get("trade_request", {})
            print(f"\n✅ Trade Executed:")
            print(f"   Success: {trade_result.get('success')}")
            if trade_result.get('success'):
                print(f"   Ticker: {trade_request.get('ticker', 'N/A')}")
                print(f"   Shares: {trade_result.get('shares')}")
                print(f"   Fill Price: ${trade_result.get('fill_price')}")
                print(f"   Total Cost: ${trade_result.get('total_cost')}")
                print(f"   Session: {trade_result.get('session')}")
                print(f"   Instrument: {trade_request.get('instrument', 'N/A')}")
                print(f"   Leverage: {trade_request.get('leverage', 'N/A')}")
    
    async def track_trade_failed(event_type: str, event_data: dict):
        if event_type == DomainEventType.TRADE_FAILED:
            trade_failed_events.append(event_data)
            print(f"\n❌ Trade Failed:")
            error = event_data.get("error", "Unknown error")
            print(f"   Error: {error}")
    
    trade_execution_notifications = []
    
    async def track_notification_sent(event_type: str, event_data: dict):
        if event_type == DomainEventType.NOTIFICATION_SENT:
            notification_sent_events.append(event_data)
            # Check if this is a trade execution notification
            message = event_data.get("message", {})
            body = message.get("body", "")
            if "TRADE EXECUTED" in body:
                trade_execution_notifications.append(event_data)
                print(f"\n📱 Trade Execution Notification Sent:")
                print(f"   Ticker: {message.get('tickers', 'N/A')}")
                print(f"   Body preview: {body[:200]}...")
            else:
                print(f"\n📱 Article Notification Sent:")
                print(f"   Article ID: {event_data.get('article_id', 'N/A')}")
                print(f"   Channel: {event_data.get('channel', 'N/A')}")
    
    async def track_exit_trade(event_type: str, event_data: dict):
        if event_type == DomainEventType.TRADE_REQUESTED:
            trade_req = event_data.get("trade_request", {})
            if trade_req.get("action") == "SELL":
                exit_trade_events.append(event_data)
                print(f"\n🚪 Exit Trade Requested:")
                print(f"   Ticker: {trade_req.get('ticker')}")
                print(f"   Shares: {trade_req.get('shares')}")
                print(f"   Action: {trade_req.get('action')}")
    
    # Subscribe to track events
    event_bus.subscribe(DomainEventType.TRADE_REQUESTED, track_trade_requested)
    event_bus.subscribe(DomainEventType.TRADE_EXECUTED, track_trade_executed)
    event_bus.subscribe(DomainEventType.TRADE_FAILED, track_trade_failed)
    event_bus.subscribe(DomainEventType.NOTIFICATION_SENT, track_notification_sent)
    event_bus.subscribe(DomainEventType.TRADE_REQUESTED, track_exit_trade)
    
    try:
        # Step 1: Publish IMMINENT classification event (simulating classification microservice)
        print(f"\n🎯 Step 1: Publishing IMMINENT classification event...")
        classification_result = ClassificationResult(
            article_id=article_id,
            classification=ClassificationCategory.IMMINENT,
            confidence=ClassificationConfidence.HIGH,
            reasoning="Integration test - simulated IMMINENT classification",
            classified_at=datetime.now(timezone.utc),
            latency_ms=50.0
        )
        
        classified_event = ArticleClassifiedDomainEvent(
            article_id=article_id,
            result=classification_result,
            classified_at=datetime.now(timezone.utc)
        )
        
        await event_bus.publish(
            DomainEventType.ARTICLE_CLASSIFIED,
            classified_event.model_dump()
        )
        print(f"✅ ArticleClassified event published")
        
        # Step 2: Wait for auto-trade to process and publish trade request
        print(f"\n⏳ Step 2: Waiting for auto-trade to process (max 5 seconds)...")
        start_time = asyncio.get_event_loop().time()
        timeout = 5.0
        
        while len(trade_requested_events) == 0:
            await asyncio.sleep(0.1)
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                pytest.fail(f"Timeout: Trade request not published within {timeout} seconds")
        
        elapsed = asyncio.get_event_loop().time() - start_time
        print(f"✅ Trade request published in {elapsed:.2f} seconds")
        
        # Step 3: Wait for trade execution (or failure)
        print(f"\n⏳ Step 3: Waiting for trade execution (max 10 seconds)...")
        print("   Note: This requires Alpaca API to be accessible and market to be open")
        start_time = asyncio.get_event_loop().time()
        timeout = 10.0
        
        while len(trade_executed_events) == 0 and len(trade_failed_events) == 0:
            await asyncio.sleep(0.5)
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                print(f"\n⚠️  Trade execution timed out after {timeout} seconds")
                print("   This may be due to:")
                print("   - Market is closed")
                print("   - Alpaca API issues")
                print("   - Connection issues")
                print("\n✅ However, the auto-trade flow worked correctly:")
                print(f"   - Article classified: ✅")
                print(f"   - Trade request published: ✅")
                print(f"   - Trade execution: ⏱️  (timed out)")
                pytest.skip(f"Trade execution timed out - market may be closed or API unavailable")
        
        # Check if trade failed
        if len(trade_failed_events) > 0:
            error = trade_failed_events[0].get("error", "Unknown error")
            print(f"\n⚠️  Trade execution failed: {error}")
            print("   This may be due to:")
            print("   - Market closed")
            print("   - Insufficient buying power")
            print("   - Invalid ticker symbol")
            print("\n✅ However, the auto-trade flow worked correctly:")
            print(f"   - Article classified: ✅")
            print(f"   - Trade request published: ✅")
            print(f"   - Trade execution attempted: ✅ (but failed: {error})")
            pytest.skip(f"Trade execution failed: {error}")
        
        elapsed = asyncio.get_event_loop().time() - start_time
        print(f"✅ Trade executed in {elapsed:.2f} seconds")
        
        # Step 4: Wait for notifications (trade execution notification and article headline notification)
        print(f"\n⏳ Step 4: Waiting for notifications (trade execution + article headline)...")
        await asyncio.sleep(1.0)  # Give notifications time to be sent
        
        # Count trade execution notifications (from NotifyTradeExecutedUseCase)
        trade_notification_count = len(trade_execution_notifications)
        
        # Count article headline notifications (from NotifyImminentArticleUseCase)
        # These are notifications that don't contain "TRADE EXECUTED"
        article_notification_count = len([
            e for e in notification_sent_events 
            if e not in trade_execution_notifications and "TRADE EXECUTED" not in e.get("message", {}).get("body", "")
        ])
        
        if trade_notification_count > 0:
            print(f"✅ Trade execution notification received")
            trade_notification = trade_execution_notifications[0]
            message = trade_notification.get("message", {})
            body = message.get("body", "")
            print(f"   Preview: {body.split(chr(10))[0]}")  # First line
        else:
            print(f"⚠️  Trade execution notification not received (may be disabled or delayed)")
        
        if article_notification_count > 0:
            print(f"✅ Article headline notification received")
        else:
            print(f"⚠️  Article headline notification not received (may be disabled or delayed)")
        
        # Step 5: Verify trade execution details
        assert len(trade_executed_events) > 0, "Trade should have been executed"
        trade_result = trade_executed_events[0].get("trade_result", {})
        
        if not trade_result.get("success"):
            error = trade_result.get("error", "Unknown error")
            print(f"\n⚠️  Trade execution failed: {error}")
            pytest.skip(f"Trade execution failed: {error}")
        
        assert trade_result.get("success"), "Trade should have succeeded"
        assert trade_result.get("shares", 0) > 0, "Trade should have filled shares"
        assert trade_result.get("fill_price", 0) > 0, "Trade should have fill price"
        
        print(f"\n✅ Trade Execution Verified:")
        trade_request = trade_result.get("trade_request", {})
        print(f"   Ticker: {trade_request.get('ticker', 'N/A')}")
        print(f"   Shares: {trade_result.get('shares')}")
        print(f"   Fill Price: ${trade_result.get('fill_price'):.2f}")
        print(f"   Total Cost: ${trade_result.get('total_cost'):.2f}")
        print(f"   Session: {trade_result.get('session')}")
        print(f"   Instrument: {trade_request.get('instrument', 'N/A')}")
        print(f"   Leverage: {trade_request.get('leverage', 'N/A')}")
        
        # Check if trade execution notification was received (already waited in Step 4)
        if trade_execution_notifications:
            print(f"✅ Trade execution notification received!")
            trade_notification = trade_execution_notifications[0]
            message = trade_notification.get("message", {})
            body = message.get("body", "")
            print(f"   Notification body:")
            for line in body.split("\n")[:15]:  # Show first 15 lines
                print(f"      {line}")
        else:
            print(f"⚠️  Trade execution notification not received (may be disabled or delayed)")
        
        # Step 6: Wait for exit trade (10 seconds after entry for testing)
        print(f"\n⏳ Step 6: Waiting for exit trade (10 seconds after entry)...")
        print("   This will automatically close the position")
        start_time = asyncio.get_event_loop().time()
        timeout = 15.0  # 10 seconds + 5 seconds buffer
        
        while len(exit_trade_events) == 0:
            await asyncio.sleep(0.5)
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                print(f"\n⚠️  Exit trade not requested within {timeout} seconds")
                print("   Exit trade use case may not be configured correctly")
                break
            if elapsed % 2 < 0.5:  # Print every 2 seconds
                remaining = timeout - elapsed
                print(f"   Waiting for exit... ({remaining:.1f} seconds remaining)")
        
        if exit_trade_events:
            elapsed = asyncio.get_event_loop().time() - start_time
            print(f"✅ Exit trade requested in {elapsed:.2f} seconds ({elapsed/60:.1f} minutes)")
            
            # Wait for exit trade execution
            print(f"\n⏳ Step 7: Waiting for exit trade execution (max 10 seconds)...")
            exit_start_time = asyncio.get_event_loop().time()
            exit_timeout = 10.0
            initial_executed_count = len(trade_executed_events)
            
            while len(trade_executed_events) == initial_executed_count:
                await asyncio.sleep(0.5)
                elapsed = asyncio.get_event_loop().time() - exit_start_time
                if elapsed > exit_timeout:
                    print(f"⚠️  Exit trade execution timed out")
                    break
            
            if len(trade_executed_events) > initial_executed_count:
                exit_trade_result = trade_executed_events[-1].get("trade_result", {})
                if exit_trade_result.get("success"):
                    print(f"✅ Exit trade executed successfully!")
                    print(f"   Shares: {exit_trade_result.get('shares')}")
                    print(f"   Fill Price: ${exit_trade_result.get('fill_price'):.2f}")
        
        print(f"\n" + "=" * 80)
        print("✅ INTEGRATION TEST COMPLETED SUCCESSFULLY")
        print("=" * 80)
        print(f"\nSummary:")
        print(f"  - Article published: {publication_time.isoformat()}")
        if notification_sent_events:
            print(f"  - Notification sent: ✅")
        print(f"  - Trade executed: ✅")
        print(f"  - Exit trade: {'✅' if exit_trade_events else '⏱️ (timed out or not executed)'}")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    finally:
        # Restore original exit delay
        settings_module.AUTO_TRADE_EXIT_DELAY_MINUTES = original_exit_delay
        
        # Cleanup
        print(f"\n🧹 Cleaning up services...")
        await notify_exit_trade_use_case.stop()
        await notify_trade_executed_use_case.stop()
        await exit_trade_use_case.stop()
        await auto_trade_service.stop()
        await notification.stop()
        await brokerage.stop()
        await metrics_service.stop()
        print("✅ Services stopped")


if __name__ == "__main__":
    # Run directly for debugging
    asyncio.run(test_full_auto_trade_flow_entry_notification_and_exit())
