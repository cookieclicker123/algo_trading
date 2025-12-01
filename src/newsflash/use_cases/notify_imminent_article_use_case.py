"""
Notify imminent article use case - orchestrates notification workflow.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime
from typing import Final

from ..utils.logging_config import get_logger
from ..shared.event_bus import AsyncEventBus
from ..shared.typed_event_bus import subscribe_typed
from ..shared.event_types import DomainEventType
from ..domain.classification.events import ArticleClassifiedDomainEvent
from ..domain.classification.models import ClassificationCategory
from ..domain.notification.events import NotificationRequestedDomainEvent
from ..domain.notification.factories import NotificationMessageFactory
from ..domain.notification.models import NotificationChannel
from ..services.storage import StorageQueryService

logger = get_logger(__name__)


class NotifyImminentArticleUseCase:
    """
    Use case for orchestrating notification workflow for IMMINENT articles.
    
    Responsibilities:
    - Subscribe to Domain.ArticleClassified events
    - Filter for IMMINENT classifications
    - Fetch article from storage
    - Create notification message from article + classification
    - Publish Domain.NotificationRequested event
    - (Domain listener → Infrastructure → Telegram API → Domain.NotificationSent)
    
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self, event_bus: AsyncEventBus, storage_query_service: StorageQueryService):
        """
        Initialize notify imminent article use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Storage query service for fetching articles
        """
        self.event_bus = event_bus
        self.notification_factory = NotificationMessageFactory()
        self.storage_query_service: Final[StorageQueryService] = storage_query_service
        
        # Subscribe to typed Domain.ArticleClassified events
        # Store wrapper for unsubscribe
        self._article_classified_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_CLASSIFIED,
            ArticleClassifiedDomainEvent,
            self._handle_article_classified,
        )
        
        logger.info(
            "NotifyImminentArticleUseCase initialized - subscribes to Domain.ArticleClassified events",
            has_storage_query=self.storage_query_service is not None,
        )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("NotifyImminentArticleUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe("Domain.ArticleClassified", self._article_classified_wrapper)
        logger.info("NotifyImminentArticleUseCase stopped")
    
    async def _handle_article_classified(
        self,
        domain_event: ArticleClassifiedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleClassified event and request notification.
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            classification_result = domain_event.result
            
            logger.info(
                "🎯 NOTIFY USE CASE: Orchestrating notification request",
                article_id=classification_result.article_id,
                classification=classification_result.classification.value
            )
            
            # Only notify for IMMINENT classifications
            if classification_result.classification != ClassificationCategory.IMMINENT:
                logger.debug(
                    "NotifyImminentArticleUseCase: Skipping notification for non-IMMINENT classification",
                    article_id=classification_result.article_id,
                    classification=classification_result.classification.value
                )
                return
            
            # Fetch article from storage on demand via StorageQueryService
            domain_article = await self.storage_query_service.fetch_article(
                classification_result.article_id
            )
            if not domain_article:
                logger.warning(
                    "NotifyImminentArticleUseCase: Article not found in storage for notification, skipping",
                    article_id=classification_result.article_id
                )
                return
            
            # Create notification message from article and classification using factory
            # Default to Telegram channel
            channels = frozenset([NotificationChannel.TELEGRAM])
            notification_message = self.notification_factory.create_from_article_and_classification(
                article=domain_article,
                classification_result=classification_result,
                channels=channels,
                body=None  # Factory will generate body
            )
            
            if not notification_message:
                logger.warning(
                    "NotifyImminentArticleUseCase: Failed to create notification message",
                    article_id=classification_result.article_id
                )
                return
            
            # Publish typed domain event with NotificationMessage domain model (domain listener will forward to infrastructure)
            domain_notification_event = NotificationRequestedDomainEvent(
                message=notification_message,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish(DomainEventType.NOTIFICATION_REQUESTED, domain_notification_event.model_dump())
            
            logger.info(
                "✅ NOTIFY USE CASE: Published notification request",
                article_id=classification_result.article_id,
                channels=[c.value for c in notification_message.channels]
            )
            
        except Exception as e:
            logger.error(
                "❌ NOTIFY USE CASE: Error orchestrating notification",
                error=str(e),
                exc_info=True
            )

