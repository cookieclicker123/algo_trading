"""
Process article use case - orchestrates article processing workflow.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases call service methods (services injected as dependencies, not imported as types)
- Services provide focused operations on domain models
"""
from ..utils.logging_config import get_logger
from ..shared.event_bus import get_event_bus
from ..shared.typed_event_bus import subscribe_typed
from ..domain.classification.events import ArticleClassifiedDomainEvent
from ..domain.classification.models import ClassificationCategory

logger = get_logger(__name__)


class ProcessArticleUseCase:
    """
    Use case for orchestrating article processing workflow.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events → Store article
    - Subscribe to Domain.ArticleClassified events → Trade if IMMINENT, Notify
    
    Classification is handled by ClassifyArticleUseCase (event-driven).
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self, notification_service):
        """
        Initialize process article use case with service dependencies.
        
        Services are injected - use case doesn't import them, just calls their methods.
        Use cases are also injected - one use case can orchestrate another use case.
        
        Args:
            auto_trade_use_case: Use case for orchestrating trading
            notification_service: Service for notifications
        """
        self.event_bus = get_event_bus()
        self.notification_service = notification_service
        
        # Use case subscribes to domain events and orchestrates
        # Storage is handled by StoreArticleUseCase (event-driven)
        subscribe_typed(
            "Domain.ArticleClassified",
            ArticleClassifiedDomainEvent,
            self._handle_article_classified,
        )
        
        logger.info(
            "ProcessArticleUseCase initialized - subscribes to domain events, orchestrates services",
            has_notification=self.notification_service is not None,
        )
    
    # Storage is now handled by StoreArticleUseCase (event-driven)
    # No need to handle ArticleReceived here anymore
    
    async def _handle_article_classified(
        self,
        domain_event: ArticleClassifiedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleClassified event - orchestrate trading and notification.
        
        This is called when classification is complete (event-driven from classification microservice).
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            classification_result = domain_event.result
            
            logger.info(
                "🎯 USE CASE: Orchestrating post-classification actions",
                article_id=classification_result.article_id,
                classification=classification_result.classification.value,
                confidence=classification_result.confidence.value
            )
            
            # Only process IMMINENT classifications
            if classification_result.classification != ClassificationCategory.IMMINENT:
                logger.debug(
                    "USE CASE: Skipping post-classification actions for non-IMMINENT",
                    article_id=classification_result.article_id,
                    classification=classification_result.classification.value
                )
                return
            
            article_id = classification_result.article_id
            
            # Notify if IMMINENT (orchestrate by calling service method)
            # Notification service would need article data - for now, notifications are handled separately
            # TODO: Add notification use case that fetches article from storage and sends notification
            if self.notification_service and hasattr(self.notification_service, 'send_notification'):
                logger.debug(
                    "USE CASE: Notification orchestration pending - will be handled by notification use case",
                    article_id=article_id
                )
            
            logger.info(
                "✅ USE CASE: Post-classification orchestration completed",
                article_id=article_id,
                classification=classification_result.classification.value
            )
            
        except Exception as e:
            logger.error(
                "❌ USE CASE: Error orchestrating post-classification actions",
                error=str(e),
                exc_info=True
            )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("ProcessArticleUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe("Domain.ArticleClassified", self._handle_article_classified)
        logger.info("ProcessArticleUseCase stopped")
