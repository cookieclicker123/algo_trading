"""
Protocols for storage domain events.

These define the contracts for event publishers and subscribers.
"""
from typing import Protocol, Awaitable
from datetime import datetime
from .models import StoredArticle, AuditEntry


class DomainStorageEventPublisher(Protocol):
    """Protocol for publishing storage domain events."""
    
    async def publish_article_storage_requested(
        self,
        article: StoredArticle,
        requested_at: datetime
    ) -> Awaitable[None]:
        """Publish ArticleStorageRequested domain event."""
        ...
    
    async def publish_article_stored(
        self,
        article_id: str,
        stored_at: datetime,
        file_path: str,
        is_archived: bool = False
    ) -> Awaitable[None]:
        """Publish ArticleStored domain event."""
        ...
    
    async def publish_article_storage_failed(
        self,
        article_id: str,
        error: str,
        failed_at: datetime
    ) -> Awaitable[None]:
        """Publish ArticleStorageFailed domain event."""
        ...
    
    async def publish_audit_log_requested(
        self,
        entry: AuditEntry,
        requested_at: datetime
    ) -> Awaitable[None]:
        """Publish AuditLogRequested domain event."""
        ...
    
    async def publish_audit_logged(
        self,
        article_id: str,
        logged_at: datetime,
        file_path: str
    ) -> Awaitable[None]:
        """Publish AuditLogged domain event."""
        ...
    
    async def publish_audit_log_storage_failed(
        self,
        article_id: str,
        error: str,
        failed_at: datetime
    ) -> Awaitable[None]:
        """Publish AuditLogStorageFailed domain event."""
        ...


class DomainStorageRequestEventSubscriber(Protocol):
    """Protocol for subscribing to storage domain request events."""
    
    async def handle_article_storage_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> Awaitable[None]:
        """Handle ArticleStorageRequested domain event."""
        ...
    
    async def handle_audit_log_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> Awaitable[None]:
        """Handle AuditLogRequested domain event."""
        ...

