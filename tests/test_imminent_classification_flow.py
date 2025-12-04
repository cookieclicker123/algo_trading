"""
Test that verifies AutoTradeService and NotifyImminentArticleUseCase 
properly handle IMMINENT classification events.

This test mocks an ArticleClassified event and verifies:
1. AutoTradeService receives event and publishes trade request
2. NotifyImminentArticleUseCase receives event and publishes notification request
3. Both can fetch article from storage
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from newsflash.domain.classification.events import ArticleClassifiedDomainEvent
from newsflash.domain.classification.models import ClassificationResult, ClassificationCategory, ClassificationConfidence
from newsflash.domain.websocket.models import Article, ArticleSource
from newsflash.services.brokerage.auto_trade import AutoTradeService
from newsflash.services.storage.query_service import StorageQueryService
from newsflash.use_cases.notification.notify_imminent_article_use_case import NotifyImminentArticleUseCase
from newsflash.shared.event_bus import AsyncEventBus
from newsflash.shared.event_types import DomainEventType


@pytest.mark.asyncio
async def test_imminent_classification_triggers_auto_trade_and_notification():
    """
    Test that an IMMINENT classification triggers both auto-trade and notification.
    
    Flow:
    1. Create mock article in storage
    2. Create IMMINENT classification result
    3. Publish ArticleClassified event
    4. Verify AutoTradeService publishes trade request
    5. Verify NotifyImminentArticleUseCase publishes notification request
    """
    # Setup event bus
    event_bus = AsyncEventBus()
    
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
    
    # Mock storage query service to return article
    mock_storage_service = MagicMock(spec=StorageQueryService)
    mock_storage_service.fetch_article = AsyncMock(return_value=domain_article)
    
    # Track published events
    published_events = []
    
    async def track_publish(event_type: str, event_data: dict):
        published_events.append((event_type, event_data))
        # Call original publish
        await event_bus._publish(event_type, event_data)
    
    # Replace publish to track events
    original_publish = event_bus.publish
    event_bus.publish = track_publish
    
    # Create AutoTradeService
    auto_trade_service = AutoTradeService(
        event_bus=event_bus,
        storage_query_service=mock_storage_service,
        enabled=True,
        trade_amount_usd=Decimal("100.0")
    )
    await auto_trade_service.start()
    
    # Create NotifyImminentArticleUseCase
    notification_use_case = NotifyImminentArticleUseCase(
        event_bus=event_bus,
        storage_query_service=mock_storage_service
    )
    await notification_use_case.start()
    
    # Create IMMINENT classification result
    classification_result = ClassificationResult(
        article_id=article_id,
        classification=ClassificationCategory.IMMINENT,
        confidence=ClassificationConfidence.HIGH,
        reasoning="Major government contract announcement",
        classified_at=datetime.now(timezone.utc),
        latency_ms=250.0
    )
    
    # Create and publish ArticleClassified event
    classified_event = ArticleClassifiedDomainEvent(
        article_id=article_id,
        result=classification_result,
        classified_at=datetime.now(timezone.utc)
    )
    
    await event_bus.publish(DomainEventType.ARTICLE_CLASSIFIED, classified_event.model_dump())
    
    # Wait for async processing
    await asyncio.sleep(0.5)
    
    # Verify storage was queried (both services should fetch)
    assert mock_storage_service.fetch_article.call_count >= 2, "Both services should fetch article"
    
    # Verify trade request was published
    trade_events = [e for e in published_events if e[0] == DomainEventType.TRADE_REQUESTED]
    assert len(trade_events) > 0, "AutoTradeService should publish trade request"
    
    trade_event_data = trade_events[0][1]
    assert trade_event_data["trade_request"]["ticker"] == "AAPL"
    assert trade_event_data["article_id"] == article_id
    
    # Verify notification request was published
    notification_events = [e for e in published_events if e[0] == DomainEventType.NOTIFICATION_REQUESTED]
    assert len(notification_events) > 0, "NotifyImminentArticleUseCase should publish notification request"
    
    notification_event_data = notification_events[0][1]
    assert notification_event_data["message"]["article_id"] == article_id
    assert notification_event_data["message"]["classification"] == "imminent"
    
    # Cleanup
    await auto_trade_service.stop()
    await notification_use_case.stop()


@pytest.mark.asyncio
async def test_auto_trade_disabled_skips_trade():
    """Test that AutoTradeService skips trade when disabled."""
    event_bus = AsyncEventBus()
    mock_storage_service = MagicMock(spec=StorageQueryService)
    
    published_events = []
    
    async def track_publish(event_type: str, event_data: dict):
        published_events.append((event_type, event_data))
        await event_bus._publish(event_type, event_data)
    
    event_bus.publish = track_publish
    
    # Create AutoTradeService with enabled=False
    auto_trade_service = AutoTradeService(
        event_bus=event_bus,
        storage_query_service=mock_storage_service,
        enabled=False,  # Disabled
        trade_amount_usd=Decimal("100.0")
    )
    await auto_trade_service.start()
    
    # Create IMMINENT classification
    classification_result = ClassificationResult(
        article_id="test-123",
        classification=ClassificationCategory.IMMINENT,
        confidence=ClassificationConfidence.HIGH,
        reasoning="Test",
        classified_at=datetime.now(timezone.utc),
        latency_ms=250.0
    )
    
    classified_event = ArticleClassifiedDomainEvent(
        article_id="test-123",
        result=classification_result,
        classified_at=datetime.now(timezone.utc)
    )
    
    await event_bus.publish(DomainEventType.ARTICLE_CLASSIFIED, classified_event.model_dump())
    await asyncio.sleep(0.2)
    
    # Verify no trade request published
    trade_events = [e for e in published_events if e[0] == DomainEventType.TRADE_REQUESTED]
    assert len(trade_events) == 0, "AutoTradeService should not publish trade when disabled"
    
    await auto_trade_service.stop()


@pytest.mark.asyncio
async def test_non_imminent_classification_skipped():
    """Test that IGNORE classifications are skipped."""
    event_bus = AsyncEventBus()
    mock_storage_service = MagicMock(spec=StorageQueryService)
    
    published_events = []
    
    async def track_publish(event_type: str, event_data: dict):
        published_events.append((event_type, event_data))
        await event_bus._publish(event_type, event_data)
    
    event_bus.publish = track_publish
    
    auto_trade_service = AutoTradeService(
        event_bus=event_bus,
        storage_query_service=mock_storage_service,
        enabled=True,
        trade_amount_usd=Decimal("100.0")
    )
    await auto_trade_service.start()
    
    notification_use_case = NotifyImminentArticleUseCase(
        event_bus=event_bus,
        storage_query_service=mock_storage_service
    )
    await notification_use_case.start()
    
    # Create IGNORE classification
    classification_result = ClassificationResult(
        article_id="test-123",
        classification=ClassificationCategory.IGNORE,  # IGNORE, not IMMINENT
        confidence=ClassificationConfidence.HIGH,
        reasoning="Not relevant",
        classified_at=datetime.now(timezone.utc),
        latency_ms=250.0
    )
    
    classified_event = ArticleClassifiedDomainEvent(
        article_id="test-123",
        result=classification_result,
        classified_at=datetime.now(timezone.utc)
    )
    
    await event_bus.publish(DomainEventType.ARTICLE_CLASSIFIED, classified_event.model_dump())
    await asyncio.sleep(0.2)
    
    # Verify no trade or notification published
    trade_events = [e for e in published_events if e[0] == DomainEventType.TRADE_REQUESTED]
    notification_events = [e for e in published_events if e[0] == DomainEventType.NOTIFICATION_REQUESTED]
    
    assert len(trade_events) == 0, "Should not trade IGNORE classifications"
    assert len(notification_events) == 0, "Should not notify IGNORE classifications"
    
    await auto_trade_service.stop()
    await notification_use_case.stop()

