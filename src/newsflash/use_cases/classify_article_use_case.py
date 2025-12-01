"""
Classify article use case - orchestrates classification workflow.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime

from ..utils.logging_config import get_logger
from ..shared.event_bus import get_event_bus
from ..domain.websocket.events import ArticleReceivedDomainEvent
from ..domain.classification.events import ClassificationRequestedDomainEvent
from ..domain.classification.factories import ClassificationRequestFactory

logger = get_logger(__name__)


class ClassifyArticleUseCase:
    """
    Use case for orchestrating article classification workflow.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events
    - Create ClassificationRequest from domain Article
    - Publish Domain.ClassificationRequested event
    - (Domain listener → Infrastructure → Groq API → Domain.ArticleClassified)
    
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self):
        """Initialize classify article use case."""
        self.event_bus = get_event_bus()
        self.classification_request_factory = ClassificationRequestFactory()
        
        # Subscribe to domain ArticleReceived events
        self.event_bus.subscribe("Domain.ArticleReceived", self._handle_article_received)
        
        logger.info("ClassifyArticleUseCase initialized - subscribes to Domain.ArticleReceived events")
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("ClassifyArticleUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe("Domain.ArticleReceived", self._handle_article_received)
        logger.info("ClassifyArticleUseCase stopped")
    
    async def _handle_article_received(self, event_type: str, event_data: dict) -> None:
        """
        Handle Domain.ArticleReceived event and request classification.
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            # Reconstruct domain event (use cases work with domain models)
            domain_event = ArticleReceivedDomainEvent(**event_data)
            domain_article = domain_event.article
            
            logger.info(
                "🎯 CLASSIFY USE CASE: Orchestrating classification request",
                article_id=domain_article.id,
                title=domain_article.title[:100] if domain_article.title else ""
            )
            
            # Create classification request from domain Article using factory
            classification_request = self.classification_request_factory.create_from_article(domain_article)
            
            if not classification_request:
                logger.warning(
                    "ClassifyArticleUseCase: Failed to create classification request",
                    article_id=domain_article.id
                )
                return
            
            # Publish typed domain event (domain listener will forward to infrastructure)
            domain_classification_event = ClassificationRequestedDomainEvent(
                request=classification_request,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish("Domain.ClassificationRequested", domain_classification_event.model_dump())
            
            logger.info(
                "✅ CLASSIFY USE CASE: Published classification request",
                article_id=domain_article.id
            )
            
        except Exception as e:
            logger.error(
                "❌ CLASSIFY USE CASE: Error orchestrating classification",
                error=str(e),
                exc_info=True
            )

