"""
Storage query service - provides article fetching operations.

Service subscribes to domain events and provides focused storage query operations.
"""
from typing import Optional, Dict, List
from datetime import datetime

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.storage.events import ArticleFetchRequestedDomainEvent, ArticleFetchedDomainEvent
from ...domain.storage.factories import StoredArticleFactory
from ...domain.storage.models import StoredArticle, ArchiveStatistics
from ...domain.websocket.models import Article as DomainArticle
from ...infra.storage.article_repository import ArticleRepository
from .article_query import (
    convert_stored_article_to_domain_article,
    query_recent_articles,
    query_archived_articles,
    create_empty_archive_stats,
)

logger = get_logger(__name__)


class StorageQueryService:
    """
    Service for querying storage - fetches articles by ID and provides query operations.
    
    Responsibilities:
    - Provides fetch_article method for services/use cases
    - Provides query methods (get_recent_articles, get_archived_articles, get_archive_stats)
    - Publishes Domain.ArticleFetchRequested event
    - Subscribes to Domain.ArticleFetched event
    - Returns domain Article model
    
    Does NOT:
    - Know about infrastructure details
    - Know about file paths or JSON (delegates to repository)
    """
    
    def __init__(
        self, 
        event_bus: AsyncEventBus, 
        article_repository: ArticleRepository,
        fetch_timeout_seconds: float
    ):
        """
        Initialize storage query service.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            article_repository: Article repository for direct queries
            fetch_timeout_seconds: Default timeout for article fetch operations (from config)
        """
        self.event_bus = event_bus
        self.article_repository = article_repository
        self.fetch_timeout_seconds = fetch_timeout_seconds
        self.stored_article_factory = StoredArticleFactory()
        
        # Subscribe to typed fetch results
        # Store wrapper for unsubscribe
        self._article_fetched_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_FETCHED,
            ArticleFetchedDomainEvent,
            self._handle_article_fetched,
        )
        
        # Pending fetch requests: article_id -> (future, timestamp)
        self._pending_fetches: Dict[str, tuple] = {}
        
        logger.info(
            "StorageQueryService initialized - provides article fetching operations",
            fetch_timeout_seconds=fetch_timeout_seconds
        )
    
    async def start(self) -> None:
        """Start the service (already subscribed in __init__)."""
        logger.info("StorageQueryService started")
    
    async def stop(self) -> None:
        """Stop the service."""
        self.event_bus.unsubscribe(DomainEventType.ARTICLE_FETCHED, self._article_fetched_wrapper)
        
        # Clean up any remaining pending fetches
        for article_id, (future, _) in list(self._pending_fetches.items()):
            if not future.done():
                future.cancel()
            self._pending_fetches.pop(article_id, None)
        
        logger.info("StorageQueryService stopped")
    
    async def fetch_article(self, article_id: str, timeout_seconds: Optional[float] = None) -> Optional[DomainArticle]:
        """
        Fetch an article by ID from storage.
        
        Args:
            article_id: Article ID to fetch
            timeout_seconds: Maximum time to wait for response (defaults to config value if None)
            
        Returns:
            Domain Article model if found, None otherwise
        """
        import asyncio
        
        # Use configured timeout if not specified
        timeout = timeout_seconds if timeout_seconds is not None else self.fetch_timeout_seconds
        
        # Create future for this fetch
        future = asyncio.Future()
        self._pending_fetches[article_id] = (future, datetime.now())
        
        try:
            # Publish fetch request
            fetch_event = ArticleFetchRequestedDomainEvent(
                article_id=article_id,
                requested_at=datetime.now()
            )
            await self.event_bus.publish(DomainEventType.ARTICLE_FETCH_REQUESTED, fetch_event.model_dump())
            
            logger.debug("StorageQueryService: Published article fetch request", article_id=article_id)
            
            # Wait for response with timeout
            try:
                stored_article = await asyncio.wait_for(future, timeout=timeout)
                
                if stored_article:
                    # Convert StoredArticle back to DomainArticle using pure function
                    return convert_stored_article_to_domain_article(stored_article)
                else:
                    logger.debug("StorageQueryService: Article not found", article_id=article_id)
                    return None
                    
            except asyncio.TimeoutError:
                logger.warning("StorageQueryService: Fetch timeout", article_id=article_id, timeout=timeout)
                # Cancel the future if it's still pending
                if not future.done():
                    future.cancel()
                return None
                
        finally:
            # Clean up pending fetch (already handled in timeout case, but ensure cleanup)
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
                    future.set_result(domain_event.article)
                    logger.debug("StorageQueryService: Resolved fetch request", article_id=article_id)
                else:
                    logger.warning("StorageQueryService: Fetch future already done", article_id=article_id)
                
                # Clean up after resolving
                self._pending_fetches.pop(article_id, None)
            else:
                logger.debug("StorageQueryService: No pending fetch for article", article_id=article_id)
                
        except Exception as e:
            logger.error("StorageQueryService: Error handling article fetched event", error=str(e), exc_info=True)
            # Try to resolve any pending fetches with None
            if 'domain_event' in locals() and domain_event.article_id in self._pending_fetches:
                future, _ = self._pending_fetches[domain_event.article_id]
                if not future.done():
                    future.set_result(None)
                self._pending_fetches.pop(domain_event.article_id, None)
    
    async def get_recent_articles(self, hours: int) -> List[StoredArticle]:
        """
        Get articles from the last N hours.
        
        Args:
            hours: Number of hours to look back
            
        Returns:
            List of StoredArticle domain models
        """
        return await query_recent_articles(
            self.article_repository,
            hours,
            self.stored_article_factory
        )
    
    async def get_archived_articles(self, date: str) -> List[StoredArticle]:
        """
        Get archived articles for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format
            
        Returns:
            List of StoredArticle domain models
        """
        return await query_archived_articles(
            self.article_repository,
            date,
            self.stored_article_factory
        )
    
    async def get_archive_stats(self) -> ArchiveStatistics:
        """
        Get statistics about archived articles.
        
        Returns:
            ArchiveStatistics domain model with archive statistics
        """
        return create_empty_archive_stats()

