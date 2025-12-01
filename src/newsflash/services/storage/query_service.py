"""
Storage query service - provides article fetching operations.

Service subscribes to domain events and provides focused storage query operations.
"""
from typing import Optional, Dict, Any
from datetime import datetime

from ...utils.logging_config import get_logger
from ...shared.event_bus import get_event_bus
from ...shared.typed_event_bus import subscribe_typed
from ...domain.storage.events import ArticleFetchRequestedDomainEvent, ArticleFetchedDomainEvent
from ...domain.storage.factories import StoredArticleFactory
from ...domain.websocket.models import Article as DomainArticle

logger = get_logger(__name__)


class StorageQueryService:
    """
    Service for querying storage - fetches articles by ID.
    
    Responsibilities:
    - Provides fetch_article method for services/use cases
    - Publishes Domain.ArticleFetchRequested event
    - Subscribes to Domain.ArticleFetched event
    - Returns domain Article model
    
    Does NOT:
    - Know about infrastructure details
    - Know about file paths or JSON
    """
    
    def __init__(self):
        """Initialize storage query service."""
        self.event_bus = get_event_bus()
        self.stored_article_factory = StoredArticleFactory()
        
        # Subscribe to typed fetch results
        subscribe_typed(
            "Domain.ArticleFetched",
            ArticleFetchedDomainEvent,
            self._handle_article_fetched,
        )
        
        # Pending fetch requests: article_id -> (future, timestamp)
        self._pending_fetches: Dict[str, tuple] = {}
        
        logger.info("StorageQueryService initialized - provides article fetching operations")
    
    async def start(self) -> None:
        """Start the service (already subscribed in __init__)."""
        logger.info("StorageQueryService started")
    
    async def stop(self) -> None:
        """Stop the service."""
        self.event_bus.unsubscribe("Domain.ArticleFetched", self._handle_article_fetched)
        logger.info("StorageQueryService stopped")
    
    async def fetch_article(self, article_id: str, timeout_seconds: float = 5.0) -> Optional[DomainArticle]:
        """
        Fetch an article by ID from storage.
        
        Args:
            article_id: Article ID to fetch
            timeout_seconds: Maximum time to wait for response
            
        Returns:
            Domain Article model if found, None otherwise
        """
        import asyncio
        
        # Create future for this fetch
        future = asyncio.Future()
        self._pending_fetches[article_id] = (future, datetime.now())
        
        try:
            # Publish fetch request
            fetch_event = ArticleFetchRequestedDomainEvent(
                article_id=article_id,
                requested_at=datetime.now()
            )
            await self.event_bus.publish("Domain.ArticleFetchRequested", fetch_event.model_dump())
            
            logger.debug("StorageQueryService: Published article fetch request", article_id=article_id)
            
            # Wait for response with timeout
            try:
                stored_article = await asyncio.wait_for(future, timeout=timeout_seconds)
                
                if stored_article:
                    # Convert StoredArticle back to DomainArticle
                    # This is a reverse mapping - we need to reconstruct DomainArticle from StoredArticle
                    from ...domain.websocket.models import Article, ArticleSource
                    
                    return Article(
                        id=stored_article.article_id,
                        source=ArticleSource(stored_article.source),
                        source_id=stored_article.source_id,
                        title=stored_article.title,
                        content=stored_article.content,
                        summary=stored_article.summary,
                        author=stored_article.author,
                        published_at=stored_article.published_at,
                        updated_at=stored_article.updated_at,
                        url=stored_article.url,
                        tickers=stored_article.tickers,
                        tags=stored_article.tags,
                        categories=stored_article.categories
                    )
                else:
                    logger.debug("StorageQueryService: Article not found", article_id=article_id)
                    return None
                    
            except asyncio.TimeoutError:
                logger.warning("StorageQueryService: Fetch timeout", article_id=article_id, timeout=timeout_seconds)
                return None
                
        finally:
            # Clean up pending fetch
            self._pending_fetches.pop(article_id, None)
    
    async def _handle_article_fetched(
        self,
        domain_event: ArticleFetchedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleFetched event - resolve pending fetch.
        """
        try:
            article_id = domain_event.article_id
            
            # Check if we have a pending fetch for this article
            if article_id in self._pending_fetches:
                future, _ = self._pending_fetches[article_id]
                
                if not future.done():
                    # Set result (StoredArticle or None)
                    future.set_result(domain_event.article)
                    logger.debug("StorageQueryService: Resolved fetch request", article_id=article_id)
                else:
                    logger.warning("StorageQueryService: Fetch future already done", article_id=article_id)
            else:
                logger.debug("StorageQueryService: No pending fetch for article", article_id=article_id)
                
        except Exception as e:
            logger.error("StorageQueryService: Error handling article fetched event", error=str(e), exc_info=True)
            # Try to resolve any pending fetches with None
            if 'domain_event' in locals() and domain_event.article_id in self._pending_fetches:
                future, _ = self._pending_fetches[domain_event.article_id]
                if not future.done():
                    future.set_result(None)

