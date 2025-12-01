"""
Event protocols for classification infrastructure events.

Protocols define the CONTRACT using typed models - ensuring type safety.
All protocols use typed infrastructure models, not Dict[str, Any].
"""
from typing import Protocol

from .infrastructure_models import (
    ClassificationRequestedInfrastructureEvent,
    ClassificationCompletedInfrastructureEvent,
    ClassificationFailedInfrastructureEvent
)


class InfrastructureClassificationRequestEventPublisher(Protocol):
    """
    Protocol for publishing ClassificationRequest infrastructure events.
    
    Ensures infrastructure events match the typed model contract.
    """
    
    async def publish_classification_requested(self, event: ClassificationRequestedInfrastructureEvent) -> None:
        """
        Publish ClassificationRequested infrastructure event.
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        ...


class InfrastructureClassificationRequestEventSubscriber(Protocol):
    """
    Protocol for subscribing to ClassificationRequest infrastructure events.
    
    Infrastructure services implement this to receive typed infrastructure events.
    """
    
    async def handle_classification_requested(self, event: ClassificationRequestedInfrastructureEvent) -> None:
        """
        Handle ClassificationRequested infrastructure event.
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        ...


class InfrastructureClassificationCompletedEventPublisher(Protocol):
    """Protocol for publishing ClassificationCompleted infrastructure events."""
    
    async def publish_classification_completed(self, event: ClassificationCompletedInfrastructureEvent) -> None:
        """Publish ClassificationCompleted infrastructure event."""
        ...


class InfrastructureClassificationFailedEventPublisher(Protocol):
    """Protocol for publishing ClassificationFailed infrastructure events."""
    
    async def publish_classification_failed(self, event: ClassificationFailedInfrastructureEvent) -> None:
        """Publish ClassificationFailed infrastructure event."""
        ...

