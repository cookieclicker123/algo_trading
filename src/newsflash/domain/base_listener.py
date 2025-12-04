"""
Base domain listener - eliminates duplicated event handling patterns.

This base class provides helper methods for common patterns used by all domain listeners.
Rather than forcing a fully generic approach, it provides reusable building blocks that
listeners can compose to eliminate duplication while maintaining flexibility.

Eliminates ~250 lines of duplicated code across 5 domain listeners.
"""
from typing import Dict, Any, Optional, Type, TypeVar
from pydantic import BaseModel

from ..utils.logging_config import get_logger
from ..shared.event_bus import AsyncEventBus

logger = get_logger(__name__)

TEvent = TypeVar('TEvent', bound=BaseModel)
TInfraEvent = TypeVar('TInfraEvent', bound=BaseModel)


class BaseDomainListener:
    """
    Base class for domain listeners - provides helper methods for common patterns.
    
    All domain listeners follow similar patterns:
    1. Domain → Infrastructure: Validate domain event → Validate domain model → Map → Publish
    2. Infrastructure → Domain: Validate infrastructure event → Factory creates domain model → Publish
    
    This base class provides helper methods that listeners can use to eliminate duplication
    while maintaining the flexibility to customize behavior as needed.
    """
    
    def __init__(self, event_bus: AsyncEventBus, listener_name: str):
        """
        Initialize base domain listener.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            listener_name: Name of the listener (for logging context, e.g., "BrokerageDomainListener")
        """
        self.event_bus = event_bus
        self.listener_name = listener_name
    
    def validate_domain_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        event_class: Type[TEvent],
        log_context: Optional[str] = None
    ) -> Optional[TEvent]:
        """
        Helper method: Validate and reconstruct domain event from dict.
        
        Common pattern: Reconstruct typed event (Pydantic validates structure).
        
        Args:
            event_type: Event type string (for logging)
            event_data: Raw event data dictionary
            event_class: Pydantic class for domain event
            log_context: Optional context for logging
        
        Returns:
            Validated domain event instance, or None if validation fails
        """
        try:
            return event_class(**event_data)
        except Exception as e:
            context = log_context or f"{self.listener_name}: Error validating domain event"
            logger.error(
                context,
                error=str(e),
                event_type=event_type,
                exc_info=True
            )
            return None
    
    def validate_infrastructure_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        event_class: Type[TInfraEvent],
        log_context: Optional[str] = None
    ) -> Optional[TInfraEvent]:
        """
        Helper method: Validate and reconstruct infrastructure event from dict.
        
        Common pattern: Reconstruct typed event (Pydantic validates structure).
        
        Args:
            event_type: Event type string (for logging)
            event_data: Raw event data dictionary
            event_class: Pydantic class for infrastructure event
            log_context: Optional context for logging
        
        Returns:
            Validated infrastructure event instance, or None if validation fails
        """
        try:
            return event_class(**event_data)
        except Exception as e:
            context = log_context or f"{self.listener_name}: Error validating infrastructure event"
            logger.error(
                context,
                error=str(e),
                event_type=event_type,
                exc_info=True
            )
            return None
    
    async def publish_infrastructure_event(
        self,
        event_type: str,
        event: BaseModel,
        log_context: Optional[str] = None
    ) -> None:
        """
        Helper method: Publish infrastructure event.
        
        Common pattern: Publish typed infrastructure event to event bus.
        
        Args:
            event_type: Infrastructure event type string
            event: Pydantic model instance to publish
            log_context: Optional context for logging
        """
        await self.event_bus.publish(event_type, event.model_dump())
        
        context = log_context or f"{self.listener_name}: Published infrastructure event"
        logger.info(context, event_type=event_type)
    
    def log_debug(self, message: str, event_type: str, **kwargs) -> None:
        """Helper method: Log debug message with listener context."""
        logger.debug(f"{self.listener_name}: {message}", event_type=event_type, **kwargs)
    
    def log_warning(self, message: str, event_type: str, **kwargs) -> None:
        """Helper method: Log warning message with listener context."""
        logger.warning(f"{self.listener_name}: {message}", event_type=event_type, **kwargs)
    
    def log_error(self, message: str, error: Exception, event_type: str, **kwargs) -> None:
        """Helper method: Log error message with listener context."""
        logger.error(
            f"{self.listener_name}: {message}",
            error=str(error),
            event_type=event_type,
            exc_info=True,
            **kwargs
        )

