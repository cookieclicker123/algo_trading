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
from ..domain.websocket.events import ArticleReceivedDomainEvent
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
    
    def __init__(self, storage_service=None, auto_trade_use_case=None, notification_service=None):
        """
        Initialize process article use case with service dependencies.
        
        Services are injected - use case doesn't import them, just calls their methods.
        Use cases are also injected - one use case can orchestrate another use case.
        
        Args:
            storage_service: Service for storing articles
            auto_trade_use_case: Use case for orchestrating trading
            notification_service: Service for notifications
        """
        self.event_bus = get_event_bus()
        self.storage_service = storage_service
        self.auto_trade_use_case = auto_trade_use_case
        self.notification_service = notification_service
        
        # Use case subscribes to domain events and orchestrates
        self.event_bus.subscribe("Domain.ArticleReceived", self._handle_article_received)
        self.event_bus.subscribe("Domain.ArticleClassified", self._handle_article_classified)
        
        logger.info(
            "ProcessArticleUseCase initialized - subscribes to domain events, orchestrates services",
            has_storage=self.storage_service is not None,
            has_auto_trade_use_case=self.auto_trade_use_case is not None,
            has_notification=self.notification_service is not None
        )
    
    async def _handle_article_received(self, event_type: str, event_data: dict) -> None:
        """
        Handle Domain.ArticleReceived event - store article.
        
        Classification is handled separately by ClassifyArticleUseCase (event-driven).
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            # Reconstruct domain event (use cases work with domain models)
            domain_event = ArticleReceivedDomainEvent(**event_data)
            domain_article = domain_event.article
            
            logger.info(
                "🎯 USE CASE: Orchestrating article storage",
                article_id=domain_article.id,
                title=domain_article.title[:100] if domain_article.title else ""
            )
            
            # Step 1: Store article (orchestrate by calling service method)
            # Classification will happen via ClassifyArticleUseCase subscribing to same event
            if self.storage_service and hasattr(self.storage_service, '_convert_domain_article_to_standardized'):
                standardized_article = self.storage_service._convert_domain_article_to_standardized(domain_article)
                if standardized_article and hasattr(self.storage_service, 'store_articles'):
                    await self.storage_service.store_articles([standardized_article])
                    logger.info(
                        "✅ USE CASE: Article stored",
                        article_id=domain_article.id
                    )
            
        except Exception as e:
            logger.error(
                "❌ USE CASE: Error orchestrating article storage",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_article_classified(self, event_type: str, event_data: dict) -> None:
        """
        Handle Domain.ArticleClassified event - orchestrate trading and notification.
        
        This is called when classification is complete (event-driven from classification microservice).
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            # Reconstruct domain event (use cases work with domain models)
            domain_event = ArticleClassifiedDomainEvent(**event_data)
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
            
            # Get article - we need to fetch it or have it cached
            # For now, we'll need to get it from storage or reconstruct
            # TODO: Store article reference or fetch from storage (will be fixed in storage microservice)
            article_id = classification_result.article_id
            
            # Step 1: Trade if IMMINENT (orchestrate by calling auto-trade use case)
            if self.auto_trade_use_case:
                # We need article data - for now, we'll pass article_id and let service handle it
                # TODO: Fetch article from storage (will be fixed in storage microservice)
                logger.warning(
                    "USE CASE: Trading orchestration needs article data - will be fixed in storage microservice",
                    article_id=article_id
                )
                # For now, we can't call execute_trade_for_imminent_article without article
                # This will be fixed when we have storage microservice to fetch articles
            
            # Step 2: Notify if IMMINENT (orchestrate by calling service method)
            if self.notification_service and hasattr(self.notification_service, 'send_notification'):
                # We need article data - for now, we'll skip
                # TODO: Fetch article from storage and send notification (will be fixed in storage microservice)
                logger.warning(
                    "USE CASE: Notification orchestration needs article data - will be fixed in storage microservice",
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
        self.event_bus.unsubscribe("Domain.ArticleReceived", self._handle_article_received)
        self.event_bus.unsubscribe("Domain.ArticleClassified", self._handle_article_classified)
        logger.info("ProcessArticleUseCase stopped")
