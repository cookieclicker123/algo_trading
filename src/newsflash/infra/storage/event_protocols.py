"""
Protocols for storage infrastructure events.

These define the contracts for event publishers and subscribers.
"""
from typing import Protocol, Awaitable
from .infrastructure_models import (
    ArticleStorageRequestData,
    AuditLogStorageRequestData,
    ArticleFetchRequestData
)


class InfrastructureArticleStorageRequestEventSubscriber(Protocol):
    """Protocol for subscribing to article storage requests."""
    
    async def handle_article_storage_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> Awaitable[None]:
        """Handle ArticleStorageRequested infrastructure event."""
        ...


class InfrastructureAuditLogStorageRequestEventSubscriber(Protocol):
    """Protocol for subscribing to audit log storage requests."""
    
    async def handle_audit_log_storage_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> Awaitable[None]:
        """Handle AuditLogStorageRequested infrastructure event."""
        ...


class InfrastructureArticleFetchRequestEventSubscriber(Protocol):
    """Protocol for subscribing to article fetch requests."""
    
    async def handle_article_fetch_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> Awaitable[None]:
        """Handle ArticleFetchRequested infrastructure event."""
        ...


class InfrastructureStorageEventPublisher(Protocol):
    """Protocol for publishing storage infrastructure events."""
    
    async def publish_article_stored(
        self,
        request_data: ArticleStorageRequestData,
        file_path: str,
        stored_at: str,
        is_archived: bool = False
    ) -> Awaitable[None]:
        """Publish ArticleStored infrastructure event."""
        ...
    
    async def publish_article_storage_failed(
        self,
        request_data: ArticleStorageRequestData,
        error: str,
        failed_at: str
    ) -> Awaitable[None]:
        """Publish ArticleStorageFailed infrastructure event."""
        ...
    
    async def publish_audit_logged(
        self,
        request_data: AuditLogStorageRequestData,
        file_path: str,
        logged_at: str
    ) -> Awaitable[None]:
        """Publish AuditLogged infrastructure event."""
        ...
    
    async def publish_audit_log_storage_failed(
        self,
        request_data: AuditLogStorageRequestData,
        error: str,
        failed_at: str
    ) -> Awaitable[None]:
        """Publish AuditLogStorageFailed infrastructure event."""
        ...
    
    async def publish_article_fetched(
        self,
        request_data: ArticleFetchRequestData,
        article_data: dict | None,
        fetched_at: str
    ) -> Awaitable[None]:
        """Publish ArticleFetched infrastructure event."""
        ...

