"""
Domain listener for storage - subscribes to infrastructure events, publishes domain events.

This bridges infrastructure ↔ domain for storage operations.
"""
from datetime import datetime
from typing import Dict, Any

from newsflash.domain.storage.models import AuditEntry, StoredArticle

from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType, InfrastructureEventType
from ...infra.storage.infrastructure_models import (
    ArticleStoredInfrastructureEvent,
    ArticleStorageFailedInfrastructureEvent,
    AuditLoggedInfrastructureEvent,
    AuditLogStorageFailedInfrastructureEvent,
    ArticleFetchedInfrastructureEvent
)
from ...utils.logging_config import get_logger
from ...shared.decorators import handle_errors
from ..base_listener import BaseDomainListener
from .validators import StoredArticleValidator, AuditEntryValidator
from .mappers import ArticleStorageMapper, AuditLogMapper
from .factories import StoredArticleFactory
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
    BaseDomainListener,
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
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        article_validator: StoredArticleValidator,
        audit_validator: AuditEntryValidator,
        article_mapper: ArticleStorageMapper,
        audit_mapper: AuditLogMapper,
        stored_article_factory: StoredArticleFactory,
    ):
        """
        Initialize storage domain listener.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            article_validator: Validator for StoredArticle domain models
            audit_validator: Validator for AuditEntry domain models
            article_mapper: Mapper for article domain ↔ infrastructure transformation
            audit_mapper: Mapper for audit entry domain ↔ infrastructure transformation
            stored_article_factory: Factory for creating StoredArticle domain models
        """
        super().__init__(event_bus, "StorageDomainListener")
        self.article_validator = article_validator
        self.audit_validator = audit_validator
        self.article_mapper = article_mapper
        self.audit_mapper = audit_mapper
        self.stored_article_factory = stored_article_factory
    
    async def start(self) -> None:
        """
        Start listening to events.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        # Subscribe to domain storage requests (use cases → infrastructure)
        self.event_bus.subscribe(DomainEventType.ARTICLE_STORAGE_REQUESTED, self._handle_domain_article_storage_request)
        self.event_bus.subscribe(DomainEventType.AUDIT_LOG_STORAGE_REQUESTED, self._handle_domain_audit_log_request)
        self.event_bus.subscribe(DomainEventType.ARTICLE_FETCH_REQUESTED, self._handle_domain_article_fetch_request)
        
        self.event_bus.subscribe(InfrastructureEventType.ARTICLE_STORED, self._handle_infra_article_stored_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.ARTICLE_STORAGE_FAILED, self._handle_infra_article_storage_failed_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.AUDIT_LOG_STORED, self._handle_infra_audit_logged_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.AUDIT_LOG_STORAGE_FAILED, self._handle_infra_audit_log_storage_failed_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.ARTICLE_FETCHED, self._handle_infra_article_fetched_from_bus)
        
        logger.info("StorageDomainListener started - listening to domain and infrastructure events")
    
    async def stop(self) -> None:
        """
        Stop listening to events.
        
        Idempotent: Safe to call multiple times.
        """
        
        # Unsubscribe from domain storage requests
        self.event_bus.unsubscribe(DomainEventType.ARTICLE_STORAGE_REQUESTED, self._handle_domain_article_storage_request)
        self.event_bus.unsubscribe(DomainEventType.AUDIT_LOG_STORAGE_REQUESTED, self._handle_domain_audit_log_request)
        self.event_bus.unsubscribe(DomainEventType.ARTICLE_FETCH_REQUESTED, self._handle_domain_article_fetch_request)
        
        # Unsubscribe from infrastructure events
        self.event_bus.unsubscribe(InfrastructureEventType.ARTICLE_STORED, self._handle_infra_article_stored_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.ARTICLE_STORAGE_FAILED, self._handle_infra_article_storage_failed_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.AUDIT_LOG_STORED, self._handle_infra_audit_logged_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.AUDIT_LOG_STORAGE_FAILED, self._handle_infra_audit_log_storage_failed_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.ARTICLE_FETCHED, self._handle_infra_article_fetched_from_bus)
        
        logger.info("StorageDomainListener stopped")
    
    @handle_errors(log_context="StorageDomainListener: Error handling domain article storage request")
    async def _handle_domain_article_storage_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain article storage request event (from use cases).
        
        Flow: Validate → Map → Publish
        """
        self.log_debug("Received domain article storage request event", event_type=event_type)
        
        # Step 1: VALIDATE domain event (using base class helper)
        domain_event = self.validate_domain_event(
            event_type, event_data, ArticleStorageRequestedDomainEvent
        )
        if not domain_event:
            return
        
        # Extract domain model
        stored_article = domain_event.article
        
        # Step 2: MAP domain model → infrastructure format
        article_data = self.article_mapper.from_domain_article(stored_article)
        infra_request_data = self.article_mapper.to_infrastructure_request(
            article_data=article_data,
            article_id=stored_article.article_id
        )
        
        # Step 3: PUBLISH typed infrastructure event (using base class helper)
        await self.publish_infrastructure_event(
            InfrastructureEventType.ARTICLE_STORAGE_REQUESTED,
            infra_request_data,
            log_context=f"Published infrastructure article storage request (article_id={stored_article.article_id})"
        )
    
    @handle_errors(log_context="StorageDomainListener: Error handling domain audit log request")
    async def _handle_domain_audit_log_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain audit log request event (from use cases).
        
        Flow: Validate → Map → Publish
        """
        self.log_debug("Received domain audit log request event", event_type=event_type)
        
        # Step 1: VALIDATE domain event (using base class helper)
        domain_event = self.validate_domain_event(
            event_type, event_data, AuditLogRequestedDomainEvent
        )
        if not domain_event:
            return
        
        # Extract domain model
        audit_entry = domain_event.entry
        
        # Step 2: MAP domain model → infrastructure format
        audit_data = self.audit_mapper.from_domain_audit_entry(audit_entry)
        infra_request_data = self.audit_mapper.to_infrastructure_request(
            audit_data=audit_data,
            article_id=audit_entry.article_id
        )
        
        # Step 3: PUBLISH typed infrastructure event (using base class helper)
        await self.publish_infrastructure_event(
            InfrastructureEventType.AUDIT_LOG_STORAGE_REQUESTED,
            infra_request_data,
            log_context=f"Published infrastructure audit log request (article_id={audit_entry.article_id})"
        )
    
    @handle_errors(log_context="StorageDomainListener: Error handling domain article fetch request")
    async def _handle_domain_article_fetch_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain article fetch request event (from use cases).
        
        Flow: Publish infrastructure event
        """
        self.log_debug("Received domain article fetch request event", event_type=event_type)
        
        # Reconstruct typed domain event (using base class helper)
        domain_event = self.validate_domain_event(
            event_type, event_data, ArticleFetchRequestedDomainEvent
        )
        if not domain_event:
            return
        
        # Publish infrastructure event
        from ...infra.storage.infrastructure_models import ArticleFetchRequestData
        infra_request = ArticleFetchRequestData(
            article_id=domain_event.article_id,
            requested_at=domain_event.requested_at
        )
        
        await self.publish_infrastructure_event(
            InfrastructureEventType.ARTICLE_FETCH_REQUESTED,
            infra_request,
            log_context=f"Published infrastructure article fetch request (article_id={domain_event.article_id})"
        )
    
    @handle_errors(log_context="StorageDomainListener: Error handling infrastructure article stored event")
    @handle_errors(log_context="StorageDomainListener: Error handling infrastructure article stored event")
    async def _handle_infra_article_stored_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle ArticleStored infrastructure event and publish domain event."""
        # Validate infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, ArticleStoredInfrastructureEvent
        )
        if not infra_event:
            return
        
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
    
    @handle_errors(log_context="StorageDomainListener: Error handling infrastructure article storage failed event")
    async def _handle_infra_article_storage_failed_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle ArticleStorageFailed infrastructure event and publish domain event."""
        # Validate infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, ArticleStorageFailedInfrastructureEvent
        )
        if not infra_event:
            return
        
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
    
    @handle_errors(log_context="StorageDomainListener: Error handling infrastructure audit logged event")
    async def _handle_infra_audit_logged_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle AuditLogged infrastructure event and publish domain event."""
        # Validate infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, AuditLoggedInfrastructureEvent
        )
        if not infra_event:
            return
        
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
    
    @handle_errors(log_context="StorageDomainListener: Error handling infrastructure audit log storage failed event")
    async def _handle_infra_audit_log_storage_failed_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle AuditLogStorageFailed infrastructure event and publish domain event."""
        # Validate infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, AuditLogStorageFailedInfrastructureEvent
        )
        if not infra_event:
            return
        
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
    
    @handle_errors(log_context="StorageDomainListener: Error handling infrastructure article fetched event")
    async def _handle_infra_article_fetched_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle ArticleFetched infrastructure event and publish domain event."""
        # Validate infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, ArticleFetchedInfrastructureEvent
        )
        if not infra_event:
            return
        
        # Convert article_data dict to StoredArticle domain model if found
        stored_article = None
        if infra_event.article_data:
            stored_article = self.stored_article_factory.create_from_dict(infra_event.article_data)
        
        # Publish typed domain event
        domain_event = ArticleFetchedDomainEvent(
            article_id=infra_event.request_data.article_id,
            article=stored_article,
            fetched_at=infra_event.fetched_at
        )
        await self.event_bus.publish(DomainEventType.ARTICLE_FETCHED, domain_event.model_dump())
        
        logger.debug(
            "StorageDomainListener: Published domain article fetched event",
            article_id=infra_event.request_data.article_id,
            found=infra_event.article_data is not None
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
        await self.event_bus.publish(DomainEventType.ARTICLE_STORAGE_REQUESTED, event.model_dump())
    
    async def publish_article_stored(self, article_id: str, stored_at: datetime, file_path: str, is_archived: bool = False) -> None:
        """Publish ArticleStored domain event (implements DomainStorageEventPublisher)."""
        event = ArticleStoredDomainEvent(
            article_id=article_id,
            stored_at=stored_at,
            file_path=file_path,
            is_archived=is_archived
        )
        await self.event_bus.publish(DomainEventType.ARTICLE_STORED, event.model_dump())
    
    async def publish_article_storage_failed(self, article_id: str, error: str, failed_at: datetime) -> None:
        """Publish ArticleStorageFailed domain event (implements DomainStorageEventPublisher)."""
        event = ArticleStorageFailedDomainEvent(
            article_id=article_id,
            error=error,
            failed_at=failed_at
        )
        await self.event_bus.publish(DomainEventType.ARTICLE_STORAGE_FAILED, event.model_dump())
    
    async def publish_audit_log_requested(self, entry: AuditEntry, requested_at: datetime) -> None:
        """Publish AuditLogRequested domain event (implements DomainStorageEventPublisher)."""
        event = AuditLogRequestedDomainEvent(
            entry=entry,
            requested_at=requested_at
        )
        await self.event_bus.publish(DomainEventType.AUDIT_LOG_STORAGE_REQUESTED, event.model_dump())
    
    async def publish_audit_logged(self, article_id: str, logged_at: datetime, file_path: str) -> None:
        """Publish AuditLogged domain event (implements DomainStorageEventPublisher)."""
        event = AuditLoggedDomainEvent(
            article_id=article_id,
            logged_at=logged_at,
            file_path=file_path
        )
        await self.event_bus.publish(DomainEventType.AUDIT_LOG_STORED, event.model_dump())
    
    async def publish_audit_log_storage_failed(self, article_id: str, error: str, failed_at: datetime) -> None:
        """Publish AuditLogStorageFailed domain event (implements DomainStorageEventPublisher)."""
        event = AuditLogStorageFailedDomainEvent(
            article_id=article_id,
            error=error,
            failed_at=failed_at
        )
        await self.event_bus.publish(DomainEventType.AUDIT_LOG_STORAGE_FAILED, event.model_dump())

