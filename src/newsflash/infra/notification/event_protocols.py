"""
Protocols for notification infrastructure events.
"""
from typing import Protocol, Awaitable


class InfrastructureNotificationRequestEventSubscriber(Protocol):
    """Protocol for subscribing to notification infrastructure request events."""
    
    async def handle_notification_send_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> Awaitable[None]:
        """Handle NotificationSendRequested infrastructure event."""
        ...

