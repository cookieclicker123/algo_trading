"""
Storage query service - provides article fetching operations.

Service subscribes to domain events and provides focused storage query operations.

Design Note:
- Uses asyncio.Event for coordinating multiple concurrent fetches of the same article
- Avoids mutable state by using proper async primitives
- Each fetch creates its own Event, multiple waiters share the same Event per article_id
"""
import asyncio
from typing import Any, Optional, Dict, List
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
        
        # Pending fetch coordination: article_id -> (asyncio.Event, result, timestamp)
        # Uses asyncio.Event for proper async coordination - multiple waiters share the same Event
        # This is operational state (coordination), not business state
        self._pending_fetches: Dict[str, tuple[asyncio.Event, Optional[StoredArticle], datetime]] = {}
        self._fetch_lock = asyncio.Lock()  # Protects _pending_fetches dict
        
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
        # Set all Events to notify waiters (they'll get None result)
        async with self._fetch_lock:
            for article_id, (fetch_event, _, _) in list(self._pending_fetches.items()):
                if not fetch_event.is_set():
                    # Set Event with None to wake up any waiters
                    self._pending_fetches[article_id] = (fetch_event, None, datetime.now())
                    fetch_event.set()
            self._pending_fetches.clear()
        
        logger.info("StorageQueryService stopped")
    
    async def fetch_article(self, article_id: str, timeout_seconds: Optional[float] = None) -> Optional[DomainArticle]:
        """
        Fetch an article by ID from storage.
        
        Optimized: Tries direct repository query first (fast path), then falls back to event-driven fetch.
        Since articles persist in ~8ms and classification takes ~300ms, direct query should succeed immediately.
        
        Args:
            article_id: Article ID to fetch
            timeout_seconds: Maximum time to wait for response (defaults to config value if None)
            
        Returns:
            Domain Article model if found, None otherwise
        """
        import asyncio
        
        # FAST PATH: Try direct repository query first (articles persist in ~8ms, classification takes ~300ms)
        # By the time we fetch, article should already be stored
        try:
            logger.info("StorageQueryService: Attempting direct repository query", article_id=article_id)
            article_data = await self.article_repository.fetch_article(article_id)
            if article_data:
                # Convert dict to StoredArticle domain model
                stored_article = self.stored_article_factory.create_from_dict(article_data)
                if stored_article:
                    # Convert StoredArticle to DomainArticle
                    domain_article = convert_stored_article_to_domain_article(stored_article)
                    if domain_article:
                        tickers_list = list(domain_article.tickers) if domain_article.tickers else []
                        logger.info(
                            "✅ StorageQueryService: Article found via direct repository query",
                            article_id=article_id,
                            tickers=tickers_list,
                            has_tickers=len(tickers_list) > 0
                        )
                        return domain_article
                    else:
                        logger.info("StorageQueryService: Article data found but failed to convert to DomainArticle", 
                                   article_id=article_id)
                else:
                    logger.info("StorageQueryService: Article data found but failed to convert to StoredArticle", 
                               article_id=article_id)
            else:
                logger.info("StorageQueryService: Article not found in repository (returned None), falling back to event-driven fetch",
                           article_id=article_id)
        except Exception as e:
            logger.info("StorageQueryService: Direct repository query failed with exception, falling back to event-driven fetch", 
                        article_id=article_id, error=str(e), exc_info=True)
        
        # FALLBACK PATH: Event-driven fetch (for articles that might not be stored yet)
        # Use configured timeout if not specified
        timeout = timeout_seconds if timeout_seconds is not None else self.fetch_timeout_seconds
        
        # Use asyncio.Event for proper async coordination
        # Multiple concurrent fetches for the same article share the same Event
        async with self._fetch_lock:
            if article_id not in self._pending_fetches:
                # First fetch for this article - create Event and publish request
                fetch_event_obj = asyncio.Event()
                fetch_timestamp = datetime.now()
                self._pending_fetches[article_id] = (fetch_event_obj, None, fetch_timestamp)
                
                # Publish fetch request
                fetch_event = ArticleFetchRequestedDomainEvent(
                    article_id=article_id,
                    requested_at=fetch_timestamp
                )
                await self.event_bus.publish(DomainEventType.ARTICLE_FETCH_REQUESTED, fetch_event.model_dump())
                logger.info("StorageQueryService: Published article fetch request", article_id=article_id)
            else:
                # Another fetch already in progress - reuse the Event
                fetch_event_obj, _, _ = self._pending_fetches[article_id]
                logger.info("StorageQueryService: Reusing existing fetch request", article_id=article_id)
        
        # Wait for the Event to be set (when ArticleFetched arrives)
        try:
            await asyncio.wait_for(fetch_event_obj.wait(), timeout=timeout)
            
            # Event was set - get the result
            async with self._fetch_lock:
                _, stored_article, _ = self._pending_fetches.get(article_id, (None, None, None))
            
            if stored_article:
                # Convert StoredArticle back to DomainArticle using pure function
                domain_article = convert_stored_article_to_domain_article(stored_article)
                if domain_article:
                    tickers_list = list[str](domain_article.tickers) if domain_article.tickers else []
                    logger.info(
                        "✅ StorageQueryService: Article found via event-driven fetch",
                        article_id=article_id,
                        tickers=tickers_list,
                        has_tickers=len(tickers_list) > 0
                    )
                return domain_article
            else:
                logger.info("StorageQueryService: Article not found via event-driven fetch", article_id=article_id)
                return None
                
        except asyncio.TimeoutError:
            logger.warning("StorageQueryService: Fetch timeout", article_id=article_id, timeout=timeout)
            return None
    
    async def _handle_article_fetched(
        self,
        domain_event: ArticleFetchedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleFetched event - notify all waiters for this article.
        
        Uses asyncio.Event for proper async coordination - all waiters are automatically
        notified when the Event is set. No mutable state mutation needed.
        """
        try:
            article_id = domain_event.article_id
            
            async with self._fetch_lock:
                if article_id in self._pending_fetches:
                    fetch_event, _, _ = self._pending_fetches[article_id]
                    
                    # Store result and set Event (notifies all waiters)
                    self._pending_fetches[article_id] = (
                        fetch_event,
                        domain_event.article,
                        datetime.now()
                    )
                    
                    # Set the Event - all waiters will be notified
                    fetch_event.set()
                    
                    logger.debug(
                        "StorageQueryService: Notified waiters for article fetch",
                        article_id=article_id,
                        found=domain_event.article is not None
                    )
                    
                    # Clean up after a short delay (allows waiters to read result)
                    # Note: We don't pop immediately because waiters need to read the result
                    # The cleanup happens when waiters finish or timeout
                else:
                    logger.debug("StorageQueryService: No pending fetch for article", article_id=article_id)
                
        except Exception as e:
            logger.error("StorageQueryService: Error handling article fetched event", error=str(e), exc_info=True)
            # Set Event with None result on error
            if 'domain_event' in locals() and domain_event.article_id in self._pending_fetches:
                async with self._fetch_lock:
                    fetch_event, _, _ = self._pending_fetches.get(domain_event.article_id, (None, None, None))
                    if fetch_event:
                        self._pending_fetches[domain_event.article_id] = (fetch_event, None, datetime.now())
                        fetch_event.set()
    
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

