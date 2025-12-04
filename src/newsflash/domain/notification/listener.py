"""
Domain listener for notification - subscribes to infrastructure events, publishes domain events.

This bridges infrastructure ↔ domain for notification operations.
"""
from datetime import datetime
from typing import Dict, Any

from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType, InfrastructureEventType
from ...infra.notification.infrastructure_models import (
    NotificationSentInfrastructureEvent,
    NotificationFailedInfrastructureEvent,
)
from ...utils.logging_config import get_logger
from .validators import NotificationMessageValidator
from .mappers import NotificationMapper
from .events import (
    NotificationRequestedDomainEvent,
    NotificationSentDomainEvent,
    NotificationFailedDomainEvent,
)
from .event_protocols import DomainNotificationEventPublisher, DomainNotificationRequestEventSubscriber
from .models import NotificationChannel

logger = get_logger(__name__)


class NotificationDomainListener(
    DomainNotificationRequestEventSubscriber,
    DomainNotificationEventPublisher
):
    """
    Listens to notification infrastructure events and publishes domain events.
    
    Also listens to domain notification requests and forwards them to infrastructure.
    
    Responsibilities:
    - Subscribe to Domain.NotificationRequested (from use cases) → Publish NotificationSendRequested (to infrastructure)
    - Subscribe to NotificationSent (from infrastructure) → Publish Domain.NotificationSent (to services)
    - Subscribe to NotificationFailed (from infrastructure) → Publish Domain.NotificationFailed (to services)
    
    Standard Domain Layer Pattern:
    - Validators: Validate domain models (protocol contracts)
    - Mappers: Transform domain ↔ infrastructure (bidirectional flow)
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        message_validator: NotificationMessageValidator,
        notification_mapper: NotificationMapper,
    ):
        """
        Initialize notification domain listener.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            message_validator: Validator for NotificationMessage domain models
            notification_mapper: Mapper for notification domain ↔ infrastructure transformation
        """
        self.event_bus = event_bus
        self.message_validator = message_validator
        self.notification_mapper = notification_mapper
    
    async def start(self) -> None:
        """
        Start listening to events.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        # Subscribe to domain notification requests (use cases → infrastructure)
        self.event_bus.subscribe(DomainEventType.NOTIFICATION_REQUESTED, self._handle_domain_notification_request)
        
        self.event_bus.subscribe(InfrastructureEventType.NOTIFICATION_SENT, self._handle_infra_notification_sent_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.NOTIFICATION_FAILED, self._handle_infra_notification_failed_from_bus)
        
        logger.info("NotificationDomainListener started - listening to domain and infrastructure events")
    
    async def stop(self) -> None:
        """
        Stop listening to events.
        
        Idempotent: Safe to call multiple times. Unsubscribing when not subscribed is safe.
        """
        # Unsubscribe from events
        self.event_bus.unsubscribe(DomainEventType.NOTIFICATION_REQUESTED, self._handle_domain_notification_request)
        self.event_bus.unsubscribe(InfrastructureEventType.NOTIFICATION_SENT, self._handle_infra_notification_sent_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.NOTIFICATION_FAILED, self._handle_infra_notification_failed_from_bus)
        
        logger.info("NotificationDomainListener stopped")
    
    async def _handle_domain_notification_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain notification request event (from use cases).
        
        Flow: Validate → Map → Publish (for each channel)
        """
        try:
            logger.debug(
                "NotificationDomainListener: Received domain notification request event",
                event_type=event_type
            )
            
            # Step 1: VALIDATE domain event (reconstruct typed event - Pydantic validates)
            domain_event = NotificationRequestedDomainEvent(**event_data)
            
            # Extract domain model
            notification_message = domain_event.message
            
            # Step 2: VALIDATE domain model
            is_valid, error = self.message_validator.validate(notification_message)
            if not is_valid:
                logger.warning(
                    "NotificationDomainListener: Invalid notification message",
                    error=error,
                    article_id=notification_message.article_id
                )
                return
            
            # Step 3: MAP and PUBLISH for each channel
            for channel in notification_message.channels:
                # Map domain model → infrastructure format
                infra_request_data = self.notification_mapper.to_infrastructure_request(
                    message=notification_message,
                    channel=channel,
                    requested_at=domain_event.requested_at
                )
                
                # Publish typed infrastructure event
                await self.event_bus.publish(InfrastructureEventType.NOTIFICATION_SEND_REQUESTED, infra_request_data.model_dump())
                
                logger.info(
                    "NotificationDomainListener: Published infrastructure notification request",
                    article_id=notification_message.article_id,
                    channel=channel.value
                )
            
        except Exception as e:
            logger.error(
                "NotificationDomainListener: Error handling domain notification request",
                error=str(e),
                exc_info=True
            )
            # Attempt to extract article_id for logging
            article_id = "unknown"
            if 'domain_event' in locals() and domain_event.message:
                article_id = domain_event.message.article_id
                # Publish failed event with the actual message
                await self.publish_notification_failed(
                    message=domain_event.message,
                    channel=NotificationChannel.TELEGRAM,  # Default
                    error=f"Error handling domain notification request: {e}",
                    failed_at=datetime.now()
                )
            else:
                # Can't publish failed event without a message - just log
                logger.error(
                    "NotificationDomainListener: Cannot publish failed event - no message available",
                    error=str(e)
                )
    
    async def _handle_infra_notification_sent_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle NotificationSent infrastructure event and publish domain event."""
        try:
            # Reconstruct typed infrastructure event
            infra_event = NotificationSentInfrastructureEvent(**event_data)
            
            # Reconstruct notification message from payload
            notification_message = self.notification_mapper.from_infrastructure_dict(infra_event.request_data.payload)
            
            # Convert channel string to enum
            channel = NotificationChannel(infra_event.request_data.channel)
            
            # Publish typed domain event
            await self.publish_notification_sent(
                message=notification_message,
                channel=channel,
                sent_at=infra_event.sent_at
            )
            
            logger.info(
                "NotificationDomainListener: Published domain notification sent event",
                article_id=notification_message.article_id,
                channel=channel.value
            )
            
        except Exception as e:
            logger.error(
                "NotificationDomainListener: Error handling infrastructure notification sent event",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_infra_notification_failed_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle NotificationFailed infrastructure event and publish domain event."""
        try:
            # Reconstruct typed infrastructure event
            infra_event = NotificationFailedInfrastructureEvent(**event_data)
            
            # Reconstruct notification message from payload
            notification_message = self.notification_mapper.from_infrastructure_dict(infra_event.request_data.payload)
            
            # Convert channel string to enum
            channel = NotificationChannel(infra_event.request_data.channel)
            
            # Publish typed domain event
            await self.publish_notification_failed(
                message=notification_message,
                channel=channel,
                error=infra_event.error,
                failed_at=infra_event.failed_at
            )
            
            logger.warning(
                "NotificationDomainListener: Published domain notification failed event",
                article_id=notification_message.article_id,
                channel=channel.value,
                error=infra_event.error
            )
            
        except Exception as e:
            logger.error(
                "NotificationDomainListener: Error handling infrastructure notification failed event",
                error=str(e),
                exc_info=True
            )
    
    # Protocol implementations
    async def handle_notification_requested(self, event_type: str, event_data: dict) -> None:
        """Handle NotificationRequested domain event (implements DomainNotificationRequestEventSubscriber)."""
        await self._handle_domain_notification_request(event_type, event_data)
    
    async def publish_notification_requested(self, message, requested_at: datetime) -> None:
        """Publish NotificationRequested domain event (implements DomainNotificationEventPublisher)."""
        event = NotificationRequestedDomainEvent(
            message=message,
            requested_at=requested_at
        )
        await self.event_bus.publish(DomainEventType.NOTIFICATION_REQUESTED, event.model_dump())
    
    async def publish_notification_sent(
        self,
        message,
        channel: NotificationChannel,
        sent_at: datetime
    ) -> None:
        """Publish NotificationSent domain event (implements DomainNotificationEventPublisher)."""
        event = NotificationSentDomainEvent(
            message=message,
            channel=channel,
            sent_at=sent_at
        )
        await self.event_bus.publish(DomainEventType.NOTIFICATION_SENT, event.model_dump())
    
    async def publish_notification_failed(
        self,
        message,
        channel: NotificationChannel,
        error: str,
        failed_at: datetime
    ) -> None:
        """Publish NotificationFailed domain event (implements DomainNotificationEventPublisher)."""
        # Handle case where message might be None
        if message is None:
            # Create a minimal message for error reporting
            from .models import NotificationMessage
            message = NotificationMessage(
                article_id="unknown",
                title="Unknown",
                tickers=frozenset(),
                classification="",
                confidence="",
                reasoning="",
                body="",
                channels=frozenset([channel]),
                created_at=datetime.now()
            )
        
        event = NotificationFailedDomainEvent(
            message=message,
            channel=channel,
            error=error,
            failed_at=failed_at
        )
        await self.event_bus.publish(DomainEventType.NOTIFICATION_FAILED, event.model_dump())

