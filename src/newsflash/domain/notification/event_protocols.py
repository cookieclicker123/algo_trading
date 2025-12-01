"""
Protocols for notification domain events.

These define the contracts for event publishers and subscribers.
"""
from typing import Protocol, Awaitable
from datetime import datetime
from .models import NotificationMessage, NotificationChannel


class DomainNotificationEventPublisher(Protocol):
    """Protocol for publishing notification domain events."""
    
    async def publish_notification_requested(
        self,
        message: NotificationMessage,
        requested_at: datetime
    ) -> Awaitable[None]:
        """Publish NotificationRequested domain event."""
        ...
    
    async def publish_notification_sent(
        self,
        message: NotificationMessage,
        channel: NotificationChannel,
        sent_at: datetime
    ) -> Awaitable[None]:
        """Publish NotificationSent domain event."""
        ...
    
    async def publish_notification_failed(
        self,
        message: NotificationMessage,
        channel: NotificationChannel,
        error: str,
        failed_at: datetime
    ) -> Awaitable[None]:
        """Publish NotificationFailed domain event."""
        ...


class DomainNotificationRequestEventSubscriber(Protocol):
    """Protocol for subscribing to notification domain request events."""
    
    async def handle_notification_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> Awaitable[None]:
        """Handle NotificationRequested domain event."""
        ...

