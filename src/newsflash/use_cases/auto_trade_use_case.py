"""
Auto-trade use case - orchestrates automatic trading for IMMINENT articles.

USE CASES ORCHESTRATE SERVICES:
- Use cases can subscribe to domain events (reactive orchestration)
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases call service methods (services injected as dependencies)
- Services provide focused operations on domain models
"""
from ..utils.logging_config import get_logger
from ..shared.event_bus import get_event_bus

logger = get_logger(__name__)


class AutoTradeUseCase:
    """
    Use case for orchestrating automatic trading workflows.
    
    Responsibilities:
    - Orchestrate trading service for IMMINENT articles
    - Coordinate trading workflow
    
    This use case orchestrates the AutoTradeService to handle trading logic.
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self, trading_service=None, subscribe_to_events=False):
        """
        Initialize auto-trade use case with service dependencies.
        
        Services are injected - use case doesn't import service types, just calls methods.
        
        Args:
            trading_service: Service for trading (injected dependency)
            subscribe_to_events: If True, subscribes to domain events. If False, uses method call orchestration.
        """
        self.trading_service = trading_service
        self.event_bus = get_event_bus() if subscribe_to_events else None
        self.subscribe_to_events = subscribe_to_events
        
        # If event-driven, subscribe to domain events
        if subscribe_to_events:
            # Note: Currently there's no Domain.ArticleClassified event
            # For now, this use case is orchestrated via method calls
            # Future: Subscribe to Domain.ArticleClassified (IMMINENT) when that event exists
            logger.info("AutoTradeUseCase: Event subscription mode enabled (but no events to subscribe to yet)")
        
        logger.info(
            "AutoTradeUseCase initialized",
            has_trading_service=self.trading_service is not None,
            subscribe_to_events=subscribe_to_events
        )
    
    async def execute_trade_for_imminent_article(
        self,
        article,  # Domain Article or StandardizedArticle
        classification_result
    ) -> None:
        """
        Orchestrate trading for an IMMINENT article.
        
        This method is called by ProcessArticleUseCase when an article is classified as IMMINENT.
        The use case orchestrates the trading service to handle the trade request.
        
        Args:
            article: Article (domain or standardized)
            classification_result: Classification result confirming IMMINENT
        """
        if not self.trading_service:
            logger.warning("AutoTradeUseCase: No trading service available")
            return
        
        if not hasattr(self.trading_service, 'process_imminent_article'):
            logger.warning("AutoTradeUseCase: Trading service doesn't have process_imminent_article method")
            return
        
        try:
            logger.info(
                "🎯 AUTO-TRADE USE CASE: Orchestrating trade for IMMINENT article",
                article_id=getattr(article, 'id', getattr(article, 'source_id', 'unknown')),
                classification=classification_result.classification.value if classification_result else None
            )
            
            # Orchestrate by calling service method
            await self.trading_service.process_imminent_article(article, classification_result)
            
            logger.info(
                "✅ AUTO-TRADE USE CASE: Trade orchestration completed",
                article_id=getattr(article, 'id', getattr(article, 'source_id', 'unknown'))
            )
            
        except Exception as e:
            logger.error(
                "❌ AUTO-TRADE USE CASE: Error orchestrating trade",
                error=str(e),
                article_id=getattr(article, 'id', getattr(article, 'source_id', 'unknown')),
                exc_info=True
            )
    
    async def start(self) -> None:
        """Start the use case."""
        # Future: If subscribe_to_events, subscribe to Domain.ArticleClassified here
        logger.info("AutoTradeUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        # Future: If subscribe_to_events, unsubscribe from domain events here
        logger.info("AutoTradeUseCase stopped")
