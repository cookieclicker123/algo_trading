"""
Domain listener for storage - subscribes to infrastructure events, publishes domain events.

This bridges infrastructure ↔ domain for storage operations.
"""
from datetime import datetime
from typing import Dict, Any

from newsflash.domain.storage.models import AuditEntry, StoredArticle

from ...shared.event_bus import get_event_bus
from ...infra.storage.infrastructure_models import (
    ArticleStoredInfrastructureEvent,
    ArticleStorageFailedInfrastructureEvent,
    AuditLoggedInfrastructureEvent,
    AuditLogStorageFailedInfrastructureEvent,
    ArticleFetchedInfrastructureEvent
)
from ...utils.logging_config import get_logger
from .validators import StoredArticleValidator, AuditEntryValidator
from .mappers import ArticleStorageMapper, AuditLogMapper
from .events import (
    ArticleFetchRequestedDomainEvent,
    ArticleStorageRequestedDomainEvent,
    ArticleStoredDomainEvent,
    ArticleStorageFailedDomainEvent,
    AuditLogRequestedDomainEvent,
    AuditLoggedDomainEvent,
    AuditLogStorageFailedDomainEvent,
    ArticleFetchedDomainEvent
)
from .event_protocols import DomainStorageEventPublisher, DomainStorageRequestEventSubscriber

logger = get_logger(__name__)


class StorageDomainListener(
    DomainStorageRequestEventSubscriber,
    DomainStorageEventPublisher
):
    """
    Listens to storage infrastructure events and publishes domain events.
    
    Also listens to domain storage requests and forwards them to infrastructure.
    
    Responsibilities:
    - Subscribe to Domain.ArticleStorageRequested (from use cases) → Publish ArticleStorageRequested (to infrastructure)
    - Subscribe to Domain.AuditLogRequested (from use cases) → Publish AuditLogStorageRequested (to infrastructure)
    - Subscribe to ArticleStored (from infrastructure) → Publish Domain.ArticleStored (to services)
    - Subscribe to AuditLogged (from infrastructure) → Publish Domain.AuditLogged (to services)
    - Subscribe to ArticleFetched (from infrastructure) → Publish Domain.ArticleFetched (to services)
    
    Standard Domain Layer Pattern:
    - Validators: Validate domain models (protocol contracts)
    - Mappers: Transform domain ↔ infrastructure (bidirectional flow)
    """
    
    def __init__(self):
        self.event_bus = get_event_bus()
        # Validators: Validate domain models
        self.article_validator = StoredArticleValidator()
        self.audit_validator = AuditEntryValidator()
        # Mappers: Bidirectional mapping (domain ↔ infra)
        self.article_mapper = ArticleStorageMapper()
        self.audit_mapper = AuditLogMapper()
        self.is_running = False
    
    async def start(self) -> None:
        """Start listening to events."""
        if self.is_running:
            logger.warning("StorageDomainListener already running")
            return
        
        self.is_running = True
        
        # Subscribe to domain storage requests (use cases → infrastructure)
        self.event_bus.subscribe("Domain.ArticleStorageRequested", self._handle_domain_article_storage_request)
        self.event_bus.subscribe("Domain.AuditLogRequested", self._handle_domain_audit_log_request)
        self.event_bus.subscribe("Domain.ArticleFetchRequested", self._handle_domain_article_fetch_request)
        
        # Subscribe to infrastructure storage results (infrastructure → services)
        self.event_bus.subscribe("ArticleStored", self._handle_infra_article_stored_from_bus)
        self.event_bus.subscribe("ArticleStorageFailed", self._handle_infra_article_storage_failed_from_bus)
        self.event_bus.subscribe("AuditLogged", self._handle_infra_audit_logged_from_bus)
        self.event_bus.subscribe("AuditLogStorageFailed", self._handle_infra_audit_log_storage_failed_from_bus)
        self.event_bus.subscribe("ArticleFetched", self._handle_infra_article_fetched_from_bus)
        
        logger.info("StorageDomainListener started - listening to domain and infrastructure events")
    
    async def stop(self) -> None:
        """Stop listening to events."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # Unsubscribe from events
        self.event_bus.unsubscribe("Domain.ArticleStorageRequested", self._handle_domain_article_storage_request)
        self.event_bus.unsubscribe("Domain.AuditLogRequested", self._handle_domain_audit_log_request)
        self.event_bus.unsubscribe("Domain.ArticleFetchRequested", self._handle_domain_article_fetch_request)
        self.event_bus.unsubscribe("ArticleStored", self._handle_infra_article_stored_from_bus)
        self.event_bus.unsubscribe("ArticleStorageFailed", self._handle_infra_article_storage_failed_from_bus)
        self.event_bus.unsubscribe("AuditLogged", self._handle_infra_audit_logged_from_bus)
        self.event_bus.unsubscribe("AuditLogStorageFailed", self._handle_infra_audit_log_storage_failed_from_bus)
        self.event_bus.unsubscribe("ArticleFetched", self._handle_infra_article_fetched_from_bus)
        
        logger.info("StorageDomainListener stopped")
    
    async def _handle_domain_article_storage_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain article storage request event (from use cases).
        
        Flow: Validate → Map → Publish
        """
        try:
            logger.debug(
                "StorageDomainListener: Received domain article storage request event",
                event_type=event_type
            )
            
            # Step 1: VALIDATE domain event (reconstruct typed event - Pydantic validates)
            domain_event = ArticleStorageRequestedDomainEvent(**event_data)
            
            # Extract domain model
            stored_article = domain_event.article
            
            # Step 2: MAP domain model → infrastructure format
            article_data = self.article_mapper.from_domain_article(stored_article)
            infra_request_data = self.article_mapper.to_infrastructure_request(
                article_data=article_data,
                article_id=stored_article.article_id
            )
            
            # Step 3: PUBLISH typed infrastructure event
            await self.event_bus.publish("ArticleStorageRequested", infra_request_data.model_dump())
            
            logger.info(
                "StorageDomainListener: Published infrastructure article storage request",
                article_id=stored_article.article_id
            )
            
        except Exception as e:
            logger.error(
                "StorageDomainListener: Error handling domain article storage request",
                error=str(e),
                exc_info=True
            )
            # Attempt to extract article_id for the failed event
            article_id = "unknown"
            if 'domain_event' in locals() and domain_event.article:
                article_id = domain_event.article.article_id
            await self.publish_article_storage_failed(
                article_id=article_id,
                error=f"Error handling domain article storage request: {e}",
                failed_at=datetime.now()
            )
    
    async def _handle_domain_audit_log_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain audit log request event (from use cases).
        
        Flow: Validate → Map → Publish
        """
        try:
            logger.debug(
                "StorageDomainListener: Received domain audit log request event",
                event_type=event_type
            )
            
            # Step 1: VALIDATE domain event (reconstruct typed event - Pydantic validates)
            domain_event = AuditLogRequestedDomainEvent(**event_data)
            
            # Extract domain model
            audit_entry = domain_event.entry
            
            # Step 2: MAP domain model → infrastructure format
            audit_data = self.audit_mapper.from_domain_audit_entry(audit_entry)
            infra_request_data = self.audit_mapper.to_infrastructure_request(
                audit_data=audit_data,
                article_id=audit_entry.article_id
            )
            
            # Step 3: PUBLISH typed infrastructure event
            await self.event_bus.publish("AuditLogStorageRequested", infra_request_data.model_dump())
            
            logger.info(
                "StorageDomainListener: Published infrastructure audit log request",
                article_id=audit_entry.article_id
            )
            
        except Exception as e:
            logger.error(
                "StorageDomainListener: Error handling domain audit log request",
                error=str(e),
                exc_info=True
            )
            # Attempt to extract article_id for the failed event
            article_id = "unknown"
            if 'domain_event' in locals() and domain_event.entry:
                article_id = domain_event.entry.article_id
            await self.publish_audit_log_storage_failed(
                article_id=article_id,
                error=f"Error handling domain audit log request: {e}",
                failed_at=datetime.now()
            )
    
    async def _handle_domain_article_fetch_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain article fetch request event (from use cases).
        
        Flow: Publish infrastructure event
        """
        try:
            logger.debug(
                "StorageDomainListener: Received domain article fetch request event",
                event_type=event_type
            )
            
            # Reconstruct typed domain event
            domain_event = ArticleFetchRequestedDomainEvent(**event_data)
            
            # Publish infrastructure event
            from ...infra.storage.infrastructure_models import ArticleFetchRequestData
            infra_request = ArticleFetchRequestData(
                article_id=domain_event.article_id,
                requested_at=domain_event.requested_at
            )
            
            await self.event_bus.publish("ArticleFetchRequested", infra_request.model_dump())
            
            logger.info(
                "StorageDomainListener: Published infrastructure article fetch request",
                article_id=domain_event.article_id
            )
            
        except Exception as e:
            logger.error(
                "StorageDomainListener: Error handling domain article fetch request",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_infra_article_stored_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle ArticleStored infrastructure event and publish domain event."""
        try:
            # Reconstruct typed infrastructure event
            infra_event = ArticleStoredInfrastructureEvent(**event_data)
            
            # Publish typed domain event
            await self.publish_article_stored(
                article_id=infra_event.request_data.article_id,
                stored_at=infra_event.stored_at,
                file_path=infra_event.file_path,
                is_archived=infra_event.is_archived
            )
            
            logger.info(
                "StorageDomainListener: Published domain article stored event",
                article_id=infra_event.request_data.article_id
            )
            
        except Exception as e:
            logger.error(
                "StorageDomainListener: Error handling infrastructure article stored event",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_infra_article_storage_failed_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle ArticleStorageFailed infrastructure event and publish domain event."""
        try:
            # Reconstruct typed infrastructure event
            infra_event = ArticleStorageFailedInfrastructureEvent(**event_data)
            
            # Publish typed domain event
            await self.publish_article_storage_failed(
                article_id=infra_event.request_data.article_id,
                error=infra_event.error,
                failed_at=infra_event.failed_at
            )
            
            logger.warning(
                "StorageDomainListener: Published domain article storage failed event",
                article_id=infra_event.request_data.article_id,
                error=infra_event.error
            )
            
        except Exception as e:
            logger.error(
                "StorageDomainListener: Error handling infrastructure article storage failed event",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_infra_audit_logged_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle AuditLogged infrastructure event and publish domain event."""
        try:
            # Reconstruct typed infrastructure event
            infra_event = AuditLoggedInfrastructureEvent(**event_data)
            
            # Publish typed domain event
            await self.publish_audit_logged(
                article_id=infra_event.request_data.article_id,
                logged_at=infra_event.logged_at,
                file_path=infra_event.file_path
            )
            
            logger.info(
                "StorageDomainListener: Published domain audit logged event",
                article_id=infra_event.request_data.article_id
            )
            
        except Exception as e:
            logger.error(
                "StorageDomainListener: Error handling infrastructure audit logged event",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_infra_audit_log_storage_failed_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle AuditLogStorageFailed infrastructure event and publish domain event."""
        try:
            # Reconstruct typed infrastructure event
            infra_event = AuditLogStorageFailedInfrastructureEvent(**event_data)
            
            # Publish typed domain event
            await self.publish_audit_log_storage_failed(
                article_id=infra_event.request_data.article_id,
                error=infra_event.error,
                failed_at=infra_event.failed_at
            )
            
            logger.warning(
                "StorageDomainListener: Published domain audit log storage failed event",
                article_id=infra_event.request_data.article_id,
                error=infra_event.error
            )
            
        except Exception as e:
            logger.error(
                "StorageDomainListener: Error handling infrastructure audit log storage failed event",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_infra_article_fetched_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle ArticleFetched infrastructure event and publish domain event."""
        try:
            # Reconstruct typed infrastructure event
            infra_event = ArticleFetchedInfrastructureEvent(**event_data)
            
            # Convert article_data dict to StoredArticle domain model if found
            stored_article = None
            if infra_event.article_data:
                from .factories import StoredArticleFactory
                stored_article = StoredArticleFactory.create_from_dict(infra_event.article_data)
            
            # Publish typed domain event
            domain_event = ArticleFetchedDomainEvent(
                article_id=infra_event.request_data.article_id,
                article=stored_article,
                fetched_at=infra_event.fetched_at
            )
            await self.event_bus.publish("Domain.ArticleFetched", domain_event.model_dump())
            
            logger.debug(
                "StorageDomainListener: Published domain article fetched event",
                article_id=infra_event.request_data.article_id,
                found=infra_event.article_data is not None
            )
            
        except Exception as e:
            logger.error(
                "StorageDomainListener: Error handling infrastructure article fetched event",
                error=str(e),
                exc_info=True
            )
    
    # Protocol implementations
    async def handle_article_storage_requested(self, event_type: str, event_data: dict) -> None:
        """Handle ArticleStorageRequested domain event (implements DomainStorageRequestEventSubscriber)."""
        await self._handle_domain_article_storage_request(event_type, event_data)
    
    async def handle_audit_log_requested(self, event_type: str, event_data: dict) -> None:
        """Handle AuditLogRequested domain event (implements DomainStorageRequestEventSubscriber)."""
        await self._handle_domain_audit_log_request(event_type, event_data)
    
    async def publish_article_storage_requested(self, article: StoredArticle, requested_at: datetime) -> None:
        """Publish ArticleStorageRequested domain event (implements DomainStorageEventPublisher)."""
        event = ArticleStorageRequestedDomainEvent(
            article=article,
            requested_at=requested_at
        )
        await self.event_bus.publish("Domain.ArticleStorageRequested", event.model_dump())
    
    async def publish_article_stored(self, article_id: str, stored_at: datetime, file_path: str, is_archived: bool = False) -> None:
        """Publish ArticleStored domain event (implements DomainStorageEventPublisher)."""
        event = ArticleStoredDomainEvent(
            article_id=article_id,
            stored_at=stored_at,
            file_path=file_path,
            is_archived=is_archived
        )
        await self.event_bus.publish("Domain.ArticleStored", event.model_dump())
    
    async def publish_article_storage_failed(self, article_id: str, error: str, failed_at: datetime) -> None:
        """Publish ArticleStorageFailed domain event (implements DomainStorageEventPublisher)."""
        event = ArticleStorageFailedDomainEvent(
            article_id=article_id,
            error=error,
            failed_at=failed_at
        )
        await self.event_bus.publish("Domain.ArticleStorageFailed", event.model_dump())
    
    async def publish_audit_log_requested(self, entry: AuditEntry, requested_at: datetime) -> None:
        """Publish AuditLogRequested domain event (implements DomainStorageEventPublisher)."""
        event = AuditLogRequestedDomainEvent(
            entry=entry,
            requested_at=requested_at
        )
        await self.event_bus.publish("Domain.AuditLogRequested", event.model_dump())
    
    async def publish_audit_logged(self, article_id: str, logged_at: datetime, file_path: str) -> None:
        """Publish AuditLogged domain event (implements DomainStorageEventPublisher)."""
        event = AuditLoggedDomainEvent(
            article_id=article_id,
            logged_at=logged_at,
            file_path=file_path
        )
        await self.event_bus.publish("Domain.AuditLogged", event.model_dump())
    
    async def publish_audit_log_storage_failed(self, article_id: str, error: str, failed_at: datetime) -> None:
        """Publish AuditLogStorageFailed domain event (implements DomainStorageEventPublisher)."""
        event = AuditLogStorageFailedDomainEvent(
            article_id=article_id,
            error=error,
            failed_at=failed_at
        )
        await self.event_bus.publish("Domain.AuditLogStorageFailed", event.model_dump())

