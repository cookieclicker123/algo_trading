"""
Event protocols for WebSocket domain events.

Protocols define the CONTRACT using typed domain models - ensuring type safety.
"""
from typing import Protocol
from datetime import datetime

from .models import Article
from .events import ArticleReceivedDomainEvent, ArticleValidationFailedDomainEvent


class DomainArticleEventPublisher(Protocol):
    """
    Protocol for publishing ArticleReceived domain events.
    
    Ensures domain events use typed domain models.
    """
    
    async def publish_article_received(self, article: Article, received_at: datetime) -> None:
        """
        Publish ArticleReceived domain event.
        
        Args:
            article: Typed domain Article model (validated, immutable)
            received_at: When article was received
        """
        ...


class DomainArticleEventSubscriber(Protocol):
    """
    Protocol for subscribing to ArticleReceived domain events.
    
    Services implement this to receive typed domain events.
    """
    
    async def handle_article_received(self, event: ArticleReceivedDomainEvent) -> None:
        """
        Handle ArticleReceived domain event.
        
        Args:
            event: Typed domain event model (contains validated Article domain model)
        """
        ...
    
    async def handle_article_validation_failed(self, event: ArticleValidationFailedDomainEvent) -> None:
        """
        Handle ArticleValidationFailed domain event.
        
        Args:
            event: Typed domain event model with validation errors
        """
        ...
