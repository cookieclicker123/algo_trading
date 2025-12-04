"""
Manual test script to verify IMMINENT classification flow.
Run this to test AutoTradeService and NotifyImminentArticleUseCase.

Usage: python tests/test_imminent_flow_manual.py
"""
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.domain.classification.events import ArticleClassifiedDomainEvent
from newsflash.domain.classification.models import ClassificationResult, ClassificationCategory, ClassificationConfidence
from newsflash.domain.websocket.models import Article, ArticleSource
from newsflash.services.brokerage.auto_trade import AutoTradeService
from newsflash.services.storage.query_service import StorageQueryService
from newsflash.use_cases.notification.notify_imminent_article_use_case import NotifyImminentArticleUseCase
from newsflash.shared.event_bus import AsyncEventBus
from newsflash.shared.event_types import DomainEventType


async def test_imminent_flow():
    """Test IMMINENT classification triggers both auto-trade and notification."""
    print("=" * 60)
    print("Testing IMMINENT Classification Flow")
    print("=" * 60)
    
    # Setup event bus
    event_bus = AsyncEventBus()
    
    # Track published events
    published_events = []
    
    async def track_publish(event_type: str, event_data: dict):
        published_events.append((event_type, event_data))
        print(f"📢 Published: {event_type}")
        if event_type == DomainEventType.TRADE_REQUESTED:
            print(f"   Trade: {event_data.get('trade_request', {}).get('ticker', 'N/A')}")
        elif event_type == DomainEventType.NOTIFICATION_REQUESTED:
            print(f"   Notification: {event_data.get('message', {}).get('title', 'N/A')[:50]}")
    
    # Replace publish to track
    original_publish = event_bus.publish
    event_bus.publish = track_publish
    
    # Create mock article
    article_id = "benzinga:test-12345"
    domain_article = Article(
        id=article_id,
        source=ArticleSource.BENZINGA,
        source_id="test-12345",
        title="Test Company announces major government contract worth $100M",
        content="",
        summary="",
        author=None,
        published_at=datetime.now(timezone.utc),
        updated_at=None,
        url=None,
        tickers=frozenset(["AAPL"]),
        tags=frozenset(),
        categories=frozenset()
    )
    
    # Mock storage service
    from unittest.mock import AsyncMock, MagicMock
    mock_storage_service = MagicMock(spec=StorageQueryService)
    mock_storage_service.fetch_article = AsyncMock(return_value=domain_article)
    
    print(f"\n✅ Mock storage service created (returns article: {article_id})")
    
    # Create AutoTradeService
    auto_trade_service = AutoTradeService(
        event_bus=event_bus,
        storage_query_service=mock_storage_service,
        enabled=True,
        trade_amount_usd=Decimal("100.0")
    )
    await auto_trade_service.start()
    print("✅ AutoTradeService started")
    
    # Create NotifyImminentArticleUseCase
    notification_use_case = NotifyImminentArticleUseCase(
        event_bus=event_bus,
        storage_query_service=mock_storage_service
    )
    await notification_use_case.start()
    print("✅ NotifyImminentArticleUseCase started")
    
    # Create IMMINENT classification
    classification_result = ClassificationResult(
        article_id=article_id,
        classification=ClassificationCategory.IMMINENT,
        confidence=ClassificationConfidence.HIGH,
        reasoning="Major government contract announcement",
        classified_at=datetime.now(timezone.utc),
        latency_ms=250.0
    )
    
    print(f"\n📋 Created IMMINENT classification for article: {article_id}")
    
    # Create and publish ArticleClassified event
    classified_event = ArticleClassifiedDomainEvent(
        article_id=article_id,
        result=classification_result,
        classified_at=datetime.now(timezone.utc)
    )
    
    print(f"\n🚀 Publishing ArticleClassified event...")
    await original_publish(DomainEventType.ARTICLE_CLASSIFIED, classified_event.model_dump())
    
    # Wait for async processing
    await asyncio.sleep(1.0)
    
    print(f"\n📊 Results:")
    print(f"   Storage fetch calls: {mock_storage_service.fetch_article.call_count}")
    
    # Check results
    trade_events = [e for e in published_events if e[0] == DomainEventType.TRADE_REQUESTED]
    notification_events = [e for e in published_events if e[0] == DomainEventType.NOTIFICATION_REQUESTED]
    
    print(f"   Trade requests published: {len(trade_events)}")
    print(f"   Notification requests published: {len(notification_events)}")
    
    if len(trade_events) > 0:
        print(f"\n✅ SUCCESS: AutoTradeService published trade request")
        trade_data = trade_events[0][1]
        print(f"   Ticker: {trade_data.get('trade_request', {}).get('ticker')}")
        print(f"   Amount: ${trade_data.get('trade_request', {}).get('amount_usd')}")
    else:
        print(f"\n❌ FAILED: AutoTradeService did NOT publish trade request")
    
    if len(notification_events) > 0:
        print(f"\n✅ SUCCESS: NotifyImminentArticleUseCase published notification request")
        notif_data = notification_events[0][1]
        print(f"   Article ID: {notif_data.get('message', {}).get('article_id')}")
        print(f"   Classification: {notif_data.get('message', {}).get('classification')}")
    else:
        print(f"\n❌ FAILED: NotifyImminentArticleUseCase did NOT publish notification request")
    
    # Cleanup
    await auto_trade_service.stop()
    await notification_use_case.stop()
    
    print(f"\n{'=' * 60}")
    if len(trade_events) > 0 and len(notification_events) > 0:
        print("✅ TEST PASSED: Both services responded correctly")
    else:
        print("❌ TEST FAILED: One or both services did not respond")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(test_imminent_flow())

