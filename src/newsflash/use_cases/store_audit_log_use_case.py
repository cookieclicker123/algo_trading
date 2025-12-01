"""
Store audit log use case - orchestrates audit log storage workflow.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime
from typing import Final

from ..utils.logging_config import get_logger
from ..shared.event_bus import AsyncEventBus
from ..shared.typed_event_bus import subscribe_typed
from ..shared.event_types import DomainEventType
from ..domain.classification.events import ArticleClassifiedDomainEvent
from ..domain.classification.models import ClassificationCategory
from ..domain.storage.events import AuditLogRequestedDomainEvent
from ..domain.storage.factories import AuditEntryFactory
from ..services.storage import StorageQueryService

logger = get_logger(__name__)


class StoreAuditLogUseCase:
    """
    Use case for orchestrating audit log storage workflow.
    
    Responsibilities:
    - Subscribe to Domain.ArticleClassified events
    - Filter for IMMINENT classifications
    - Create audit entry from classification result
    - Publish Domain.AuditLogRequested event
    - (Domain listener → Infrastructure → Repository → Domain.AuditLogged)
    
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self, event_bus: AsyncEventBus, storage_query_service: StorageQueryService):
        """
        Initialize store audit log use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Storage query service for fetching articles
        """
        self.event_bus = event_bus
        self.audit_factory = AuditEntryFactory()
        self.storage_query_service: Final[StorageQueryService] = storage_query_service
        
        # Subscribe to typed Domain.ArticleClassified events
        # Store wrapper for unsubscribe
        self._article_classified_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_CLASSIFIED,
            ArticleClassifiedDomainEvent,
            self._handle_article_classified,
        )
        
        logger.info(
            "StoreAuditLogUseCase initialized - subscribes to Domain.ArticleClassified events",
            has_storage_query=self.storage_query_service is not None,
        )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("StoreAuditLogUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe("Domain.ArticleClassified", self._article_classified_wrapper)
        logger.info("StoreAuditLogUseCase stopped")
    
    async def _handle_article_classified(
        self,
        domain_event: ArticleClassifiedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleClassified event and request audit log storage.
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            classification_result = domain_event.result
            
            # Only log IMMINENT classifications
            if classification_result.classification != ClassificationCategory.IMMINENT:
                logger.debug(
                    "StoreAuditLogUseCase: Skipping audit log for non-IMMINENT classification",
                    article_id=classification_result.article_id,
                    classification=classification_result.classification.value
                )
                return
            
            logger.info(
                "🎯 AUDIT USE CASE: Orchestrating audit log storage request",
                article_id=classification_result.article_id,
                classification=classification_result.classification.value
            )
            
            # Fetch article from storage on demand via StorageQueryService
            domain_article = await self.storage_query_service.fetch_article(
                classification_result.article_id
            )
            if not domain_article:
                logger.warning(
                    "StoreAuditLogUseCase: Article not found in storage for audit log, skipping",
                    article_id=classification_result.article_id
                )
                return
            
            # Create audit entry from classification result using factory
            audit_entry = self.audit_factory.create_from_classification(
                article=domain_article,
                classification_result=classification_result,
                news_received_at=domain_article.published_at,  # Use article's published_at as news_received_at
                metadata={}  # Metadata can be added later
            )
            
            if not audit_entry:
                logger.warning(
                    "StoreAuditLogUseCase: Failed to create audit entry",
                    article_id=classification_result.article_id
                )
                return
            
            # Publish typed domain event with AuditEntry domain model (domain listener will forward to infrastructure)
            domain_audit_event = AuditLogRequestedDomainEvent(
                entry=audit_entry,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish(DomainEventType.AUDIT_LOG_STORAGE_REQUESTED, domain_audit_event.model_dump())
            
            logger.info(
                "✅ AUDIT USE CASE: Published audit log storage request",
                article_id=classification_result.article_id
            )
            
        except Exception as e:
            logger.error(
                "❌ AUDIT USE CASE: Error orchestrating audit log storage",
                error=str(e),
                exc_info=True
            )

