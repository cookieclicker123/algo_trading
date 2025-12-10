"""
Quick diagnostic test for notification workflow.

This test can be run quickly to verify the notification pipeline works correctly.
Run with: python3 tests/integration/test_notification_diagnostic.py
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from newsflash.shared.event_bus import AsyncEventBus
from newsflash.shared.event_types import DomainEventType, InfrastructureEventType
from newsflash.domain.notification.events import NotificationRequestedDomainEvent
from newsflash.domain.notification.models import NotificationMessage, NotificationChannel
from newsflash.domain.notification.listener import NotificationDomainListener
from newsflash.domain.notification.validators import NotificationMessageValidator
from newsflash.domain.notification.mappers import NotificationMapper
from newsflash.infra.notification.service import NotificationInfrastructureService
from newsflash.services.metrics.metrics_service import MetricsService


async def test_notification_flow():
    """Test the complete notification flow."""
    print("\n" + "=" * 80)
    print("NOTIFICATION WORKFLOW DIAGNOSTIC TEST")
    print("=" * 80)
    
    # Setup event bus
    event_bus = AsyncEventBus()
    print("✅ Event bus created")
    
    # Track events
    domain_events = []
    infra_events = []
    telegram_calls = []
    
    async def track_domain(event_type: str, event_data: dict):
        if event_type == DomainEventType.NOTIFICATION_REQUESTED:
            domain_events.append(event_data)
            print(f"📨 Domain event received: {event_type}")
    
    async def track_infra(event_type: str, event_data: dict):
        if event_type == InfrastructureEventType.NOTIFICATION_SEND_REQUESTED:
            infra_events.append(event_data)
            print(f"📬 Infrastructure event received: {event_type}")
            print(f"   Channel: {event_data.get('channel')}")
            print(f"   Body preview: {event_data.get('payload', {}).get('body', '')[:50]}...")
    
    event_bus.subscribe(DomainEventType.NOTIFICATION_REQUESTED, track_domain)
    event_bus.subscribe(InfrastructureEventType.NOTIFICATION_SEND_REQUESTED, track_infra)
    
    # Create mock telegram client
    mock_telegram_client = AsyncMock()
    mock_telegram_client.send_message = AsyncMock(return_value=(True, None))
    mock_telegram_client.is_enabled = True
    
    # Track telegram calls
    original_send = mock_telegram_client.send_message
    
    async def tracked_send(*args, **kwargs):
        telegram_calls.append({"args": args, "kwargs": kwargs})
        print(f"📱 Telegram send_message called")
        print(f"   Text: {kwargs.get('text', '')[:100]}...")
        return await original_send(*args, **kwargs)
    
    mock_telegram_client.send_message = tracked_send
    
    # Initialize components
    print("\n🔧 Initializing components...")
    
    # Domain listener
    domain_listener = NotificationDomainListener(
        event_bus=event_bus,
        message_validator=NotificationMessageValidator(),
        notification_mapper=NotificationMapper()
    )
    await domain_listener.start()
    print("✅ Domain listener started")
    
    # Infrastructure service (with mocked telegram client)
    with patch('newsflash.infra.notification.service.TelegramNotificationClient') as mock_client_class:
        mock_client_class.return_value = mock_telegram_client
        
        metrics_service = MetricsService(event_bus)
        await metrics_service.start()
        
        infra_service = NotificationInfrastructureService(
            event_bus=event_bus,
            telegram_config_1={"enabled": True, "bot_token": "test_token", "chat_id": "test_chat"},
            telegram_config_2={"enabled": False, "bot_token": "", "chat_id": ""},
            enabled=True,
            metrics_service=metrics_service
        )
        await infra_service.start()
        print("✅ Infrastructure service started")
        
        # Create notification message
        print("\n📝 Creating notification message...")
        notification_message = NotificationMessage(
            article_id="test-123",
            title="Test Article Title",
            tickers=frozenset(["AAPL"]),
            classification="IMMINENT",
            confidence="HIGH",
            reasoning="Test reasoning",
            body="This is a test notification body to verify the complete workflow.",
            channels=frozenset([NotificationChannel.TELEGRAM]),
            created_at=datetime.now()
        )
        print("✅ Notification message created")
        
        # Create domain event
        domain_event = NotificationRequestedDomainEvent(
            message=notification_message,
            requested_at=datetime.now()
        )
        print("✅ Domain event created")
        
        # Publish domain event (simulating use case)
        print("\n🚀 Publishing domain event...")
        await event_bus.publish(
            DomainEventType.NOTIFICATION_REQUESTED,
            domain_event.model_dump()
        )
        print("✅ Domain event published")
        
        # Wait for processing
        print("\n⏳ Waiting for event processing...")
        await asyncio.sleep(0.5)
        
        # Verify results
        print("\n" + "=" * 80)
        print("RESULTS")
        print("=" * 80)
        print(f"Domain events received: {len(domain_events)}")
        print(f"Infrastructure events published: {len(infra_events)}")
        print(f"Telegram calls made: {len(telegram_calls)}")
        
        # Check each step
        issues = []
        
        if len(domain_events) == 0:
            issues.append("❌ Domain event not received by tracker")
        else:
            print("✅ Domain event received")
        
        if len(infra_events) == 0:
            issues.append("❌ Infrastructure event not published (domain listener may have failed)")
        else:
            print("✅ Infrastructure event published")
            infra_event = infra_events[0]
            if infra_event.get("payload", {}).get("body") != notification_message.body:
                issues.append("❌ Notification body mismatch")
            else:
                print("✅ Notification body matches")
        
        if len(telegram_calls) == 0:
            issues.append("❌ Telegram client not called (infrastructure service may have failed)")
        else:
            print("✅ Telegram client called")
            if telegram_calls[0]["kwargs"].get("text") != notification_message.body:
                issues.append("❌ Telegram message text mismatch")
            else:
                print("✅ Telegram message text matches")
        
        if issues:
            print("\n⚠️  ISSUES FOUND:")
            for issue in issues:
                print(f"   {issue}")
            return False
        else:
            print("\n✅ ALL CHECKS PASSED - Notification workflow is working correctly!")
            return True
        
        # Cleanup
        await infra_service.stop()
        await domain_listener.stop()
        await metrics_service.stop()


if __name__ == "__main__":
    success = asyncio.run(test_notification_flow())
    sys.exit(0 if success else 1)
