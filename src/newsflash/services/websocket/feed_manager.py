"""
Feed manager - WebSocket service that manages article feed processing.

Pure event subscription - subscribes to domain events only.
No direct coupling to classification/article processing.
"""

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType

logger = get_logger(__name__)


class FeedManager:
    """
    WebSocket service for managing article feeds.
    
    Responsibilities:
    - Subscribes to Domain.ArticleReceived events
    - Logs article reception
    - Provides feed statistics
    
    Does NOT:
    - Process articles (use case layer does that)
    - Classify articles (classification microservice does that)
    - Create/manage WebSocket connections (infrastructure does that)
    - Access WebSocket state
    - Know about infrastructure details
    - Know about classification logic
    """
    
    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize the feed manager.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
        """
        self.is_running = False
        self.articles_received_count = 0
        self.event_bus = event_bus
        
        # Subscribe to domain ArticleReceived events only
        self.event_bus.subscribe(DomainEventType.ARTICLE_RECEIVED, self._handle_domain_article_received)
        logger.info("FeedManager subscribed to Domain.ArticleReceived events")
    
    async def start_all_feeds(self) -> None:
        """Start feed manager (event-driven, no blocking loop needed)."""
        logger.info("Starting feed manager")
        self.is_running = True
        logger.info("FeedManager started - listening for Domain.ArticleReceived events")
    
    async def _handle_domain_article_received(self, event_type: str, event_data: dict) -> None:
        """
        Handle Domain.ArticleReceived event - receives typed domain Article model.
        
        This service just logs and tracks stats.
        Article processing is handled by ProcessArticleUseCase.
        """
        try:
            # Reconstruct typed domain event
            from ...domain.websocket.events import ArticleReceivedDomainEvent
            domain_event = ArticleReceivedDomainEvent(**event_data)
            
            # Extract typed domain Article model
            article = domain_event.article
            
            # Log article reception
            self.articles_received_count += 1
            logger.info(
                "FeedManager: Article received from domain",
                article_id=article.id,
                tickers=list(article.tickers) if article.tickers else [],
                total_received=self.articles_received_count
            )
        
        except Exception as e:
            logger.error(
                "FeedManager: Error handling Domain.ArticleReceived event",
                error=str(e),
                event_type=event_type,
                exc_info=True
            )
    
    async def stop_all_feeds(self) -> None:
        """Stop feed manager."""
        logger.info("Stopping feed manager")
        self.is_running = False
        
        # Unsubscribe from events
        self.event_bus.unsubscribe(DomainEventType.ARTICLE_RECEIVED, self._handle_domain_article_received)
        logger.info("FeedManager stopped")
    
    def get_stats(self) -> dict:
        """Get current feed statistics."""
        return {
            "is_running": self.is_running,
            "articles_received": self.articles_received_count
        }
    
    def is_healthy(self) -> bool:
        """Check if feed manager is healthy."""
        return self.is_running

