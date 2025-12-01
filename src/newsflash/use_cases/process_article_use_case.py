"""
Process article use case - orchestrates article processing workflow.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- All orchestration happens via events - no direct service calls

All post-classification actions are now handled by dedicated use cases:
- Storage: StoreArticleUseCase (event-driven)
- Classification: ClassifyArticleUseCase (event-driven)
- Notifications: NotifyImminentArticleUseCase (event-driven)
- Trading: AutoTradeService (event-driven)
- Audit: StoreAuditLogUseCase (event-driven)

This use case logs that classification occurred.
"""
from ..utils.logging_config import get_logger
from ..shared.event_bus import AsyncEventBus
from ..shared.typed_event_bus import subscribe_typed
from ..shared.event_types import DomainEventType
from ..domain.classification.events import ArticleClassifiedDomainEvent

logger = get_logger(__name__)


class ProcessArticleUseCase:
    """
    Use case for orchestrating article processing workflow.
    
    Responsibilities:
    - Subscribe to Domain.ArticleClassified events
    - Log classification results
    
    All actual processing is handled by dedicated use cases:
    - Storage: StoreArticleUseCase
    - Notifications: NotifyImminentArticleUseCase
    - Trading: AutoTradeService
    - Audit: StoreAuditLogUseCase
    
    """
    
    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize process article use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
        """
        self.event_bus = event_bus
        
        # Use case subscribes to domain events
        # All processing is handled by dedicated use cases (event-driven)
        # Store wrapper for unsubscribe
        self._article_classified_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_CLASSIFIED,
            ArticleClassifiedDomainEvent,
            self._handle_article_classified,
        )
        
        logger.info(
            "ProcessArticleUseCase initialized - subscribes to Domain.ArticleClassified events",
            note="All processing handled by dedicated use cases (event-driven)"
        )
    
    async def _handle_article_classified(
        self,
        domain_event: ArticleClassifiedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleClassified event - log classification result.
        
        All actual processing is handled by dedicated use cases:
        - NotifyImminentArticleUseCase (notifications)
        - AutoTradeService (trading)
        - StoreAuditLogUseCase (audit logging)
        
        This handler just logs that classification occurred.
        """
        try:
            classification_result = domain_event.result
            
            logger.info(
                "🎯 PROCESS USE CASE: Article classified",
                article_id=classification_result.article_id,
                classification=classification_result.classification.value,
                confidence=classification_result.confidence.value,
                note="All processing handled by dedicated use cases (event-driven)"
            )
            
            # All processing is handled by dedicated use cases via events:
            # - NotifyImminentArticleUseCase subscribes to Domain.ArticleClassified
            # - AutoTradeService subscribes to Domain.ArticleClassified
            # - StoreAuditLogUseCase subscribes to Domain.ArticleClassified
            
        except Exception as e:
            logger.error(
                "❌ PROCESS USE CASE: Error handling article classified event",
                error=str(e),
                exc_info=True
            )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("ProcessArticleUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe("Domain.ArticleClassified", self._article_classified_wrapper)
        logger.info("ProcessArticleUseCase stopped")
