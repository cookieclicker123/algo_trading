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

This use case is now minimal - it just logs that classification occurred.
It can be removed if no additional orchestration is needed.
"""
from ..utils.logging_config import get_logger
from ..shared.event_bus import get_event_bus
from ..shared.typed_event_bus import subscribe_typed
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
    
    This use case can be removed if no additional orchestration is needed.
    """
    
    def __init__(self):
        """
        Initialize process article use case.
        
        No dependencies needed - all orchestration is event-driven.
        """
        self.event_bus = get_event_bus()
        
        # Use case subscribes to domain events
        # All processing is handled by dedicated use cases (event-driven)
        subscribe_typed(
            "Domain.ArticleClassified",
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
        self.event_bus.unsubscribe("Domain.ArticleClassified", self._handle_article_classified)
        logger.info("ProcessArticleUseCase stopped")
