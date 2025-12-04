"""
Notification infrastructure microservice - handles external notification APIs.

Pure infrastructure - handles Telegram API calls, publishes events.
All stateful code related to external notification APIs lives here.
"""
from datetime import datetime
from typing import Dict, Any, Optional

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import InfrastructureEventType
from .infrastructure_models import (
    NotificationSendRequestData,
    NotificationSentInfrastructureEvent,
    NotificationFailedInfrastructureEvent,
)
from .event_protocols import InfrastructureNotificationRequestEventSubscriber
from .telegram_client import TelegramNotificationClient
from ...domain.notification.models import NotificationChannel

logger = get_logger(__name__)


class NotificationInfrastructureService(InfrastructureNotificationRequestEventSubscriber):
    """
    Notification infrastructure microservice for external APIs.
    
    Responsibilities:
    - Manage notification clients (Telegram, etc.) (stateful)
    - Subscribe to notification request events
    - Call external APIs to send notifications
    - Publish infrastructure events when notifications are sent/failed
    
    Does NOT:
    - Know about business logic
    - Return results directly (publishes events instead)
    - Know about domain models
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        telegram_config_1: dict,
        telegram_config_2: dict,
        metrics_service,  # Required - injected via DI
        enabled: bool = True,
    ):
        """
        Initialize notification infrastructure service.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            telegram_config_1: Configuration dict for primary Telegram bot
            telegram_config_2: Configuration dict for secondary Telegram bot
            enabled: Whether notifications are enabled
            metrics_service: Optional metrics service for statistics (injected via DI)
        """
        self.enabled = enabled
        self.metrics_service = metrics_service  # ✅ Injected metrics service
        
        # Stateful: Notification clients (initialized once) - inject config
        self.telegram_client = TelegramNotificationClient(
            telegram_config_1=telegram_config_1,
            telegram_config_2=telegram_config_2,
            enabled=enabled
        )
        
        # Event bus for publishing events
        self.event_bus = event_bus
        
        # ✅ No stats dictionary - MetricsService aggregates from events!
        
        logger.info(
            "NotificationInfrastructureService initialized",
            enabled=enabled
        )
    
    async def start(self) -> None:
        """
        Start the notification infrastructure service.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        logger.info("🚀 Starting Notification Infrastructure Service")
        # ✅ No stats mutation - MetricsService tracks from events
        
        # Subscribe to notification requests from domain layer
        # Domain listener will publish NotificationSendRequestedInfrastructureEvent
        # Event bus automatically prevents duplicate subscriptions
        self.event_bus.subscribe(InfrastructureEventType.NOTIFICATION_SEND_REQUESTED, self.handle_notification_send_requested)
        
        logger.info("NotificationInfrastructureService: Subscribed to notification request events")
        logger.info("✅ Notification Infrastructure Service started")
    
    async def stop(self) -> None:
        """
        Stop the notification infrastructure service.
        
        Idempotent: Safe to call multiple times. Unsubscribing when not subscribed is safe.
        """
        logger.info("Stopping Notification Infrastructure Service")
        # ✅ No stats mutation - MetricsService tracks from events
        
        # Unsubscribe from events (safe even if not subscribed)
        self.event_bus.unsubscribe(InfrastructureEventType.NOTIFICATION_SEND_REQUESTED, self.handle_notification_send_requested)
        
        logger.info("NotificationInfrastructureService stopped")
    
    async def handle_notification_send_requested(
        self,
        event_type: str,
        event_data: Dict[str, Any]
    ) -> None:
        """
        Handle NotificationSendRequested infrastructure event.
        
        Args:
            event_type: Event type (should be "NotificationSendRequested")
            event_data: Event data dictionary
        """
        try:
            # Reconstruct typed infrastructure request
            request_data = NotificationSendRequestData(**event_data)
            
            # ✅ No stats mutation - MetricsService subscribes to NotificationSendRequested event
            
            logger.info(
                "NotificationInfrastructureService: Processing notification request",
                article_id=request_data.payload.get("article_id", "unknown"),
                channel=request_data.channel
            )
            
            # Check if notifications are enabled
            if not self.enabled:
                error = "Notifications are disabled"
                await self._publish_notification_failed(request_data, error)
                return
            
            # Route to appropriate channel client
            channel = request_data.channel.lower()
            
            if channel == NotificationChannel.TELEGRAM.value:
                success, error = await self._send_telegram_notification(request_data)
            elif channel == NotificationChannel.CONSOLE.value:
                # Console notifications - just log
                logger.info(
                    "NotificationInfrastructureService: Console notification",
                    body=request_data.payload.get("body", "")
                )
                success = True
                error = None
            else:
                error = f"Unsupported notification channel: {channel}"
                success = False
            
            if success:
                await self._publish_notification_sent(request_data)
            else:
                await self._publish_notification_failed(request_data, error or "Unknown error")
                
        except Exception as e:
            logger.error(
                "NotificationInfrastructureService: Error handling notification request",
                error=str(e),
                exc_info=True
            )
            # Try to extract request_data for error event
            try:
                request_data = NotificationSendRequestData(**event_data)
                await self._publish_notification_failed(
                    request_data,
                    f"Error handling notification request: {e}"
                )
            except:
                # If we can't reconstruct request_data, log and continue
                logger.error(
                    "NotificationInfrastructureService: Could not publish failed event",
                    error=str(e)
                )
    
    async def _send_telegram_notification(
        self,
        request_data: NotificationSendRequestData
    ) -> tuple[bool, Optional[str]]:
        """
        Send notification via Telegram.
        
        Args:
            request_data: Notification request data
            
        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Extract message body from payload
            body = request_data.payload.get("body", "")
            
            if not body:
                return False, "Notification body is empty"
            
            # Send via Telegram client
            success, error = await self.telegram_client.send_message(text=body)
            
            if success:
                logger.info(
                    "NotificationInfrastructureService: Telegram notification sent",
                    article_id=request_data.payload.get("article_id", "unknown")
                )
            else:
                logger.warning(
                    "NotificationInfrastructureService: Telegram notification failed",
                    article_id=request_data.payload.get("article_id", "unknown"),
                    error=error
                )
            
            return success, error
            
        except Exception as e:
            logger.error(
                "NotificationInfrastructureService: Exception sending Telegram notification",
                error=str(e),
                exc_info=True
            )
            return False, str(e)
    
    async def _publish_notification_sent(self, request_data: NotificationSendRequestData) -> None:
        """Publish NotificationSent infrastructure event."""
        try:
            # ✅ No stats mutation - MetricsService subscribes to NotificationSent event
            
            event = NotificationSentInfrastructureEvent(
                request_data=request_data,
                sent_at=datetime.now()
            )
            
            await self.event_bus.publish(InfrastructureEventType.NOTIFICATION_SENT, event.model_dump())
            
            logger.info(
                "NotificationInfrastructureService: Published notification sent event",
                article_id=request_data.payload.get("article_id", "unknown")
            )
            
        except Exception as e:
            logger.error(
                "NotificationInfrastructureService: Error publishing notification sent event",
                error=str(e),
                exc_info=True
            )
    
    async def _publish_notification_failed(
        self,
        request_data: NotificationSendRequestData,
        error: str
    ) -> None:
        """Publish NotificationFailed infrastructure event."""
        try:
            # ✅ No stats mutation - MetricsService subscribes to NotificationFailed event
            
            event = NotificationFailedInfrastructureEvent(
                request_data=request_data,
                error=error,
                failed_at=datetime.now()
            )
            
            await self.event_bus.publish(InfrastructureEventType.NOTIFICATION_FAILED, event.model_dump())
            
            logger.warning(
                "NotificationInfrastructureService: Published notification failed event",
                article_id=request_data.payload.get("article_id", "unknown"),
                error=error
            )
            
        except Exception as e:
            logger.error(
                "NotificationInfrastructureService: Error publishing notification failed event",
                error=str(e),
                exc_info=True
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        # ✅ Delegate to MetricsService - statistics aggregated from events
        return self.metrics_service.get_notification_stats(enabled=self.enabled)

