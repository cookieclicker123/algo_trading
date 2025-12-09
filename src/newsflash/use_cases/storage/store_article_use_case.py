"""
Store article use case - orchestrates article storage workflow.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...domain.storage.events import ArticleStorageRequestedDomainEvent
from ...domain.storage.factories import StoredArticleFactory

logger = get_logger(__name__)


class StoreArticleUseCase:
    """
    Use case for orchestrating article storage workflow.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events
    - Create storage request from domain Article
    - Publish Domain.ArticleStorageRequested event
    - (Domain listener → Infrastructure → Repository → Domain.ArticleStored)
    
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize store article use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
        """
        self.event_bus = event_bus
        self.stored_article_factory = StoredArticleFactory()
        
        # Subscribe to typed Domain.ArticleReceived events
        # Store wrapper for unsubscribe
        self._article_received_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_RECEIVED,
            ArticleReceivedDomainEvent,
            self._handle_article_received,
        )
        
        logger.info("StoreArticleUseCase initialized - subscribes to Domain.ArticleReceived events")
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("StoreArticleUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe(DomainEventType.ARTICLE_RECEIVED, self._article_received_wrapper)
        logger.info("StoreArticleUseCase stopped")
    
    async def _handle_article_received(
        self,
        domain_event: ArticleReceivedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleReceived event and request storage.
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            domain_article = domain_event.article
            
            logger.info(
                "🎯 STORE USE CASE: Orchestrating article storage request",
                article_id=domain_article.id,
                title=domain_article.title[:100] if domain_article.title else ""
            )
            
            # Create StoredArticle domain model from domain Article using factory
            # Pass websocket_received_at from event to preserve timing information
            stored_article = self.stored_article_factory.create_from_domain_article(
                domain_article, 
                websocket_received_at=domain_event.received_at
            )
            
            if not stored_article:
                logger.warning(
                    "StoreArticleUseCase: Failed to create StoredArticle",
                    article_id=domain_article.id
                )
                return
            
            # Publish typed domain event (domain listener will forward to infrastructure)
            domain_storage_event = ArticleStorageRequestedDomainEvent(
                article=stored_article,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish(DomainEventType.ARTICLE_STORAGE_REQUESTED, domain_storage_event.model_dump())
            
            logger.info(
                "✅ STORE USE CASE: Published article storage request",
                article_id=domain_article.id
            )
            
        except Exception as e:
            logger.error(
                "❌ STORE USE CASE: Error orchestrating article storage",
                error=str(e),
                exc_info=True
            )

