"""
Classify article use case - orchestrates classification workflow.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime
from typing import Final, Optional, Any

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...domain.classification.events import ClassificationRequestedDomainEvent
from ...domain.classification.factories import ClassificationRequestFactory

logger = get_logger(__name__)


class ClassifyArticleUseCase:
    """
    Use case for orchestrating classification workflow.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events
    - Create classification request from article
    - Publish Domain.ClassificationRequested event
    - (Domain listener → Infrastructure → Groq API → Domain.ArticleClassified)
    
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize classify article use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
        """
        self.event_bus: Final[AsyncEventBus] = event_bus
        self.request_factory = ClassificationRequestFactory()
        
        # Track wrapper for unsubscribe
        self._article_received_wrapper: Optional[Any] = None
        
        logger.info(
            "ClassifyArticleUseCase initialized - ready to start subscriptions"
        )
    
    async def start(self) -> None:
        """Start the use case - subscribe to domain events."""
        if self._article_received_wrapper:
            logger.debug("ClassifyArticleUseCase already started")
            return

        # Subscribe to typed Domain.ArticleReceived events
        self._article_received_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_RECEIVED,
            ArticleReceivedDomainEvent,
            self._handle_article_received,
        )
        logger.info("ClassifyArticleUseCase started - subscribed to Domain.ArticleReceived events")
    
    async def stop(self) -> None:
        """Stop the use case - unsubscribe from domain events."""
        if self._article_received_wrapper:
            self.event_bus.unsubscribe(DomainEventType.ARTICLE_RECEIVED, self._article_received_wrapper)
            self._article_received_wrapper = None
            logger.info("ClassifyArticleUseCase stopped")
    
    async def _handle_article_received(
        self,
        domain_event: ArticleReceivedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleReceived event and request classification.
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            article = domain_event.article
            
            logger.info(
                "🎯 CLASSIFY USE CASE: Orchestrating classification request",
                article_id=article.id,
                title=article.title or "",
                has_tickers=len(article.tickers) > 0
            )
            
            # Create classification request from article using factory
            classification_request = self.request_factory.create_from_article(article)
            
            if not classification_request:
                logger.warning(
                    "ClassifyArticleUseCase: Failed to create classification request",
                    article_id=article.id
                )
                return
            
            # Publish typed domain event with ClassificationRequest domain model
            # (Domain listener will forward to infrastructure)
            domain_classification_event = ClassificationRequestedDomainEvent(
                request=classification_request,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish(DomainEventType.CLASSIFICATION_REQUESTED, domain_classification_event.model_dump())
            
            logger.info(
                "✅ CLASSIFY USE CASE: Published classification request",
                article_id=article.id,
                title=article.title or ""
            )
            
        except Exception as e:
            logger.error(
                "❌ CLASSIFY USE CASE: Error orchestrating classification",
                error=str(e),
                exc_info=True
            )

