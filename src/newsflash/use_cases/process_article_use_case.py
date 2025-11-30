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

logger = get_logger(__name__)


class ProcessArticleUseCase:
    """
    Use case for orchestrating article processing workflow.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events
    - Orchestrate workflow by calling service methods:
      → Store article
      → Classify article  
      → Trade if IMMINENT
      → Notify
    
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self, storage_service=None, classification_service=None, auto_trade_use_case=None, notification_service=None):
        """
        Initialize process article use case with service dependencies.
        
        Services are injected - use case doesn't import them, just calls their methods.
        Use cases are also injected - one use case can orchestrate another use case.
        
        Args:
            storage_service: Service for storing articles
            classification_service: Service for classifying articles  
            auto_trade_use_case: Use case for orchestrating trading
            notification_service: Service for notifications
        """
        self.event_bus = get_event_bus()
        self.storage_service = storage_service
        self.classification_service = classification_service
        self.auto_trade_use_case = auto_trade_use_case
        self.notification_service = notification_service
        
        # Use case subscribes to domain events and orchestrates
        self.event_bus.subscribe("Domain.ArticleReceived", self._handle_article_received)
        
        logger.info(
            "ProcessArticleUseCase initialized - subscribes to domain events, orchestrates services",
            has_storage=self.storage_service is not None,
            has_classification=self.classification_service is not None,
            has_auto_trade_use_case=self.auto_trade_use_case is not None,
            has_notification=self.notification_service is not None
        )
    
    async def _handle_article_received(self, event_type: str, event_data: dict) -> None:
        """
        Handle domain event and orchestrate by calling service methods.
        
        Use cases work with domain models - they orchestrate domain workflows.
        Services are called with domain models - use case orchestrates the flow.
        """
        try:
            # Reconstruct domain event (use cases work with domain models)
            domain_event = ArticleReceivedDomainEvent(**event_data)
            domain_article = domain_event.article
            
            logger.info(
                "🎯 USE CASE: Orchestrating article processing",
                article_id=domain_article.id,
                title=domain_article.title[:100] if domain_article.title else ""
            )
            
            # Step 1: Store article (orchestrate by calling service method)
            if self.storage_service and hasattr(self.storage_service, '_convert_domain_article_to_standardized'):
                standardized_article = self.storage_service._convert_domain_article_to_standardized(domain_article)
                if standardized_article and hasattr(self.storage_service, 'store_articles'):
                    await self.storage_service.store_articles([standardized_article])
            
            # Step 2: Classify article (orchestrate by calling service method)
            classification = None
            if self.classification_service and hasattr(self.classification_service, '_convert_domain_article_to_standardized'):
                standardized_article = self.classification_service._convert_domain_article_to_standardized(domain_article)
                if standardized_article and hasattr(self.classification_service, 'classifier'):
                    classification = await self.classification_service.classifier.classify_article(standardized_article)
            
            # Step 3: Trade if IMMINENT (orchestrate by calling auto-trade use case)
            if classification and classification.classification.value.lower() == "imminent":
                if self.auto_trade_use_case:
                    if self.storage_service and hasattr(self.storage_service, '_convert_domain_article_to_standardized'):
                        standardized_article = self.storage_service._convert_domain_article_to_standardized(domain_article)
                        if standardized_article:
                            await self.auto_trade_use_case.execute_trade_for_imminent_article(standardized_article, classification)
            
            # Step 4: Notify if IMMINENT (orchestrate by calling service method)
            if classification and classification.classification.value.lower() == "imminent":
                if self.notification_service and hasattr(self.notification_service, 'send_notification'):
                    if self.storage_service and hasattr(self.storage_service, '_convert_domain_article_to_standardized'):
                        standardized_article = self.storage_service._convert_domain_article_to_standardized(domain_article)
                        if standardized_article:
                            await self.notification_service.send_notification(standardized_article, classification)
            
            logger.info(
                "✅ USE CASE: Orchestration completed",
                article_id=domain_article.id,
                classification=classification.classification.value if classification else None
            )
            
        except Exception as e:
            logger.error(
                "❌ USE CASE: Error orchestrating article processing",
                error=str(e),
                exc_info=True
            )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("ProcessArticleUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe("Domain.ArticleReceived", self._handle_article_received)
        logger.info("ProcessArticleUseCase stopped")
