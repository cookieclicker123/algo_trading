"""
Event protocols for classification domain events.

Protocols define the CONTRACT using typed domain models - ensuring type safety.
All protocols use typed domain models, not Dict[str, Any].
"""
from typing import Protocol

from .events import (
    ClassificationRequestedDomainEvent,
    ArticleClassifiedDomainEvent,
    ClassificationFailedDomainEvent
)


class DomainClassificationEventPublisher(Protocol):
    """
    Protocol for publishing classification domain events.
    
    Ensures domain events match the typed model contract.
    """
    
    async def publish_classification_requested(self, event: ClassificationRequestedDomainEvent) -> None:
        """
        Publish ClassificationRequested domain event.
        
        Args:
            event: Typed domain event model (validated)
        """
        ...
    
    async def publish_article_classified(self, event: ArticleClassifiedDomainEvent) -> None:
        """
        Publish ArticleClassified domain event.
        
        Args:
            event: Typed domain event model (validated)
        """
        ...
    
    async def publish_classification_failed(self, event: ClassificationFailedDomainEvent) -> None:
        """
        Publish ClassificationFailed domain event.
        
        Args:
            event: Typed domain event model (validated)
        """
        ...


class DomainClassificationEventSubscriber(Protocol):
    """
    Protocol for subscribing to classification domain events.
    
    Services implement this to receive typed domain events.
    """
    
    async def handle_classification_requested(self, event_type: str, event_data: dict) -> None:
        """
        Handle ClassificationRequested domain event.
        
        Args:
            event_type: Event type string
            event_data: Event data dictionary (will be validated to typed model)
        """
        ...
    
    async def handle_article_classified(self, event_type: str, event_data: dict) -> None:
        """
        Handle ArticleClassified domain event.
        
        Args:
            event_type: Event type string
            event_data: Event data dictionary (will be validated to typed model)
        """
        ...
    
    async def handle_classification_failed(self, event_type: str, event_data: dict) -> None:
        """
        Handle ClassificationFailed domain event.
        
        Args:
            event_type: Event type string
            event_data: Event data dictionary (will be validated to typed model)
        """
        ...

