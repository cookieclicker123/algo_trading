"""
Storage infrastructure microservice - handles file I/O operations.

Pure infrastructure - handles JSON file operations, publishes events.
All stateful code related to file I/O lives here.
"""
from datetime import datetime
from typing import Dict, Any

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import InfrastructureEventType
from .infrastructure_models import (
    ArticleStorageRequestData,
    ArticleStoredInfrastructureEvent,
    ArticleStorageFailedInfrastructureEvent,
    AuditLogStorageRequestData,
    AuditLoggedInfrastructureEvent,
    AuditLogStorageFailedInfrastructureEvent,
    ArticleFetchRequestData,
    ArticleFetchedInfrastructureEvent
)
from .event_protocols import (
    InfrastructureArticleStorageRequestEventSubscriber,
    InfrastructureAuditLogStorageRequestEventSubscriber,
    InfrastructureArticleFetchRequestEventSubscriber
)
from .article_repository import ArticleRepository
from .audit_repository import AuditRepository

logger = get_logger(__name__)


class StorageInfrastructureService(
    InfrastructureArticleStorageRequestEventSubscriber,
    InfrastructureAuditLogStorageRequestEventSubscriber,
    InfrastructureArticleFetchRequestEventSubscriber
):
    """
    Storage infrastructure microservice for file I/O operations.
    
    Responsibilities:
    - Manage repositories (stateful)
    - Subscribe to storage request events
    - Call repositories to persist data
    - Publish infrastructure events when data is persisted
    
    Does NOT:
    - Know about business logic
    - Return results directly (publishes events instead)
    - Know about domain models
    """
    
    def __init__(self, event_bus: AsyncEventBus, storage_config: dict):
        """
        Initialize storage infrastructure service.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_config: Storage configuration dictionary
        """
        # Stateful: Repositories (initialized once) - inject config
        self.article_repository = ArticleRepository(storage_config=storage_config)
        self.audit_repository = AuditRepository()
        
        # Event bus for publishing events
        self.event_bus = event_bus
        
        logger.info("StorageInfrastructureService initialized")
    
    async def start(self) -> None:
        """
        Start the storage infrastructure service.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        logger.info("🚀 Starting Storage Infrastructure Service")
        
        # Subscribe to storage requests from domain layer
        # Domain listener will publish ArticleStorageRequestedInfrastructureEvent
        # Event bus automatically prevents duplicate subscriptions
        self.event_bus.subscribe(InfrastructureEventType.ARTICLE_STORAGE_REQUESTED, self.handle_article_storage_requested)
        self.event_bus.subscribe(InfrastructureEventType.AUDIT_LOG_STORAGE_REQUESTED, self.handle_audit_log_storage_requested)
        self.event_bus.subscribe(InfrastructureEventType.ARTICLE_FETCH_REQUESTED, self.handle_article_fetch_requested)
        
        logger.info("StorageInfrastructureService: Subscribed to storage request events")
        logger.info("✅ Storage Infrastructure Service started")
    
    async def stop(self) -> None:
        """
        Stop the storage infrastructure service.
        
        Idempotent: Safe to call multiple times. Unsubscribing when not subscribed is safe.
        """
        logger.info("Stopping Storage Infrastructure Service")
        
        # Unsubscribe from events (safe even if not subscribed)
        self.event_bus.unsubscribe(InfrastructureEventType.ARTICLE_STORAGE_REQUESTED, self.handle_article_storage_requested)
        self.event_bus.unsubscribe(InfrastructureEventType.AUDIT_LOG_STORAGE_REQUESTED, self.handle_audit_log_storage_requested)
        self.event_bus.unsubscribe(InfrastructureEventType.ARTICLE_FETCH_REQUESTED, self.handle_article_fetch_requested)
        
        logger.info("✅ Storage Infrastructure Service stopped")
    
    async def handle_article_storage_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """
        Handle ArticleStorageRequested infrastructure event.
        
        Implements InfrastructureArticleStorageRequestEventSubscriber protocol.
        Receives typed infrastructure event, calls repository, publishes result.
        """
        try:
            # Reconstruct typed infrastructure event
            request_data = ArticleStorageRequestData(**event_data)
            
            # Store via repository
            file_path, is_archived = await self.article_repository.store_article(
                article_id=request_data.article_id,
                article_data=request_data.article_data
            )
            
            # Publish success event
            stored_event = ArticleStoredInfrastructureEvent(
                request_data=request_data,
                file_path=file_path,
                stored_at=datetime.now(),
                is_archived=is_archived
            )
            await self.event_bus.publish(InfrastructureEventType.ARTICLE_STORED, stored_event.model_dump())
            
            logger.info(
                "StorageInfrastructureService: Article stored",
                article_id=request_data.article_id,
                file_path=file_path,
                is_archived=is_archived
            )
            
        except Exception as e:
            logger.error(
                "StorageInfrastructureService: Error storing article",
                error=str(e),
                exc_info=True
            )
            
            # Publish failure event
            request_data = ArticleStorageRequestData(**event_data) if 'request_data' not in locals() else event_data
            failed_event = ArticleStorageFailedInfrastructureEvent(
                request_data=request_data if isinstance(request_data, ArticleStorageRequestData) else ArticleStorageRequestData(**event_data),
                error=str(e),
                failed_at=datetime.now()
            )
            await self.event_bus.publish(InfrastructureEventType.ARTICLE_STORAGE_FAILED, failed_event.model_dump())
    
    async def handle_audit_log_storage_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """
        Handle AuditLogStorageRequested infrastructure event.
        
        Implements InfrastructureAuditLogStorageRequestEventSubscriber protocol.
        Receives typed infrastructure event, calls repository, publishes result.
        """
        try:
            # Reconstruct typed infrastructure event
            request_data = AuditLogStorageRequestData(**event_data)
            
            # Store via repository
            file_path = await self.audit_repository.store_audit_entry(
                article_id=request_data.article_id,
                audit_data=request_data.audit_data,
                logged_at=request_data.logged_at
            )
            
            # Publish success event
            logged_event = AuditLoggedInfrastructureEvent(
                request_data=request_data,
                file_path=file_path,
                logged_at=datetime.now()
            )
            await self.event_bus.publish(InfrastructureEventType.AUDIT_LOG_STORED, logged_event.model_dump())
            
            logger.info(
                "StorageInfrastructureService: Audit entry stored",
                article_id=request_data.article_id,
                file_path=file_path
            )
            
        except Exception as e:
            logger.error(
                "StorageInfrastructureService: Error storing audit entry",
                error=str(e),
                exc_info=True
            )
            
            # Publish failure event
            request_data = AuditLogStorageRequestData(**event_data) if 'request_data' not in locals() else event_data
            failed_event = AuditLogStorageFailedInfrastructureEvent(
                request_data=request_data if isinstance(request_data, AuditLogStorageRequestData) else AuditLogStorageRequestData(**event_data),
                error=str(e),
                failed_at=datetime.now()
            )
            await self.event_bus.publish(InfrastructureEventType.AUDIT_LOG_STORAGE_FAILED, failed_event.model_dump())
    
    async def handle_article_fetch_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """
        Handle ArticleFetchRequested infrastructure event.
        
        Implements InfrastructureArticleFetchRequestEventSubscriber protocol.
        Receives typed infrastructure event, calls repository, publishes result.
        """
        try:
            # Reconstruct typed infrastructure event
            request_data = ArticleFetchRequestData(**event_data)
            
            # Fetch via repository
            article_data = await self.article_repository.fetch_article(request_data.article_id)
            
            # Publish result event
            fetched_event = ArticleFetchedInfrastructureEvent(
                request_data=request_data,
                article_data=article_data,
                fetched_at=datetime.now()
            )
            await self.event_bus.publish(InfrastructureEventType.ARTICLE_FETCHED, fetched_event.model_dump())
            
            logger.debug(
                "StorageInfrastructureService: Article fetched",
                article_id=request_data.article_id,
                found=article_data is not None
            )
            
        except Exception as e:
            logger.error(
                "StorageInfrastructureService: Error fetching article",
                error=str(e),
                exc_info=True
            )
            
            # Publish result event with None (not found)
            request_data = ArticleFetchRequestData(**event_data) if 'request_data' not in locals() else event_data
            fetched_event = ArticleFetchedInfrastructureEvent(
                request_data=request_data if isinstance(request_data, ArticleFetchRequestData) else ArticleFetchRequestData(**event_data),
                article_data=None,
                fetched_at=datetime.now()
            )
            await self.event_bus.publish(InfrastructureEventType.ARTICLE_FETCHED, fetched_event.model_dump())
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get storage service statistics (calculated on demand - stateless)."""
        # Count articles from file system (stateless)
        # Load all articles from rolling window file
        articles = await self.article_repository._load_articles()
        articles_stored = len(articles)
        
        # Count audit entries from file system (stateless)
        # Note: This is approximate - counts today's entries only
        from datetime import datetime
        today_file = self.audit_repository._get_daily_file_path(datetime.now())
        audit_entries = await self.audit_repository._load_daily_classifications(today_file)
        audit_entries_stored = len(audit_entries)
        
        return {
            "articles_stored": articles_stored,
            "audit_entries_stored": audit_entries_stored,
            # Note: Running state is tracked by LifecycleManager, not individual services
        }

