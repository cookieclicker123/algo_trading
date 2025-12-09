"""
Storage microservice - self-contained initialization.

This module initializes all storage-related components:
- Infrastructure service
- Domain listener (bridge)
- Services (pure functions + minimal classes)
- Use cases
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus

if TYPE_CHECKING:
    from ...infra.storage.types import StorageConfig

# Infrastructure layer
from ...infra.storage import StorageInfrastructureService

# Domain layer
from ...domain.storage.listener import StorageDomainListener

# Services layer
from .query_service import StorageQueryService

# Use cases layer
from ...use_cases.storage import StoreArticleUseCase, StoreAuditLogUseCase

logger = get_logger(__name__)


@dataclass
class StorageMicroservice:
    """
    Storage microservice container.
    
    Holds all storage-related components:
    - Infrastructure service
    - Domain listener (bridge)
    - Services (pure functions + minimal classes)
    - Use cases
    """
    infra: StorageInfrastructureService
    domain_listener: StorageDomainListener
    query_service: StorageQueryService
    store_article_use_case: StoreArticleUseCase
    store_audit_log_use_case: StoreAuditLogUseCase
    
    async def start(self) -> None:
        """Start all storage microservice components."""
        logger.info("Starting storage microservice...")
        
        # Start infrastructure FIRST
        await self.infra.start()
        logger.info("Storage infrastructure started")
        
        # Start domain listener
        await self.domain_listener.start()
        logger.info("Storage domain listener started")
        
        # Start services
        await self.query_service.start()
        logger.info("Storage query service started")
        
        # Start use cases
        await self.store_article_use_case.start()
        await self.store_audit_log_use_case.start()
        logger.info("Storage use cases started")
        
        logger.info("Storage microservice started")
    
    async def stop(self) -> None:
        """Stop all storage microservice components."""
        logger.info("Stopping storage microservice...")
        
        # Stop use cases first
        await self.store_audit_log_use_case.stop()
        await self.store_article_use_case.stop()
        
        # Stop services
        await self.query_service.stop()
        
        # Stop domain listener
        await self.domain_listener.stop()
        
        # Stop infrastructure last
        await self.infra.stop()
        
        logger.info("Storage microservice stopped")


async def initialize_storage_microservice(
    event_bus: AsyncEventBus,
    storage_config: "StorageConfig",
    store_article_use_case: StoreArticleUseCase,
    store_audit_log_use_case: StoreAuditLogUseCase | None = None,
) -> StorageMicroservice:
    """
    Initialize storage microservice independently.
    
    This function knows ONLY about storage microservice.
    It doesn't know about other microservices.
    
    Args:
        event_bus: Event bus instance (shared dependency)
        storage_config: Storage configuration dictionary (injected via DI)
        store_article_use_case: Store article use case (injected via DI)
        store_audit_log_use_case: Store audit log use case (injected via DI, optional - will be created if None)
        
    Returns:
        StorageMicroservice: Initialized storage microservice
    """
    logger.info("Initializing storage microservice...")
    
    # Step 1: Infrastructure layer
    infra = StorageInfrastructureService(
        event_bus=event_bus,
        storage_config=storage_config
    )
    logger.info("Storage infrastructure initialized")
    
    # Step 2: Domain listener (bridge infrastructure ↔ domain)
    from ...domain.storage.validators import StoredArticleValidator, AuditEntryValidator
    from ...domain.storage.mappers import ArticleStorageMapper, AuditLogMapper
    from ...domain.storage.factories import StoredArticleFactory
    
    domain_listener = StorageDomainListener(
        event_bus=event_bus,
        article_validator=StoredArticleValidator(),
        audit_validator=AuditEntryValidator(),
        article_mapper=ArticleStorageMapper(),
        audit_mapper=AuditLogMapper(),
        stored_article_factory=StoredArticleFactory()
    )
    logger.info("Storage domain listener initialized")
    
    # Step 3: Services layer
    fetch_timeout = storage_config.get("article_fetch_timeout_seconds", 5.0)
    query_service = StorageQueryService(
        event_bus=event_bus,
        article_repository=infra.article_repository,  # ✅ Internal dependency
        fetch_timeout_seconds=fetch_timeout
    )
    logger.info("Storage query service initialized", fetch_timeout_seconds=fetch_timeout)
    
    # Step 4: Use cases layer
    # store_article_use_case is injected via DI ✅
    # store_audit_log_use_case: use injected if provided, otherwise create (needs query_service)
    if store_audit_log_use_case is None:
        store_audit_log_use_case = StoreAuditLogUseCase(
            event_bus=event_bus,
            storage_query_service=query_service  # ✅ Internal dependency wired here
        )
        logger.info("Storage use cases initialized (store_audit_log_use_case created internally)")
    else:
        logger.info("Storage use cases initialized (store_audit_log_use_case injected via DI)")
    
    return StorageMicroservice(
        infra=infra,
        domain_listener=domain_listener,
        query_service=query_service,
        store_article_use_case=store_article_use_case,
        store_audit_log_use_case=store_audit_log_use_case
    )


# Export for backwards compatibility
from .query_service import StorageQueryService

__all__ = ["StorageQueryService", "StorageMicroservice", "initialize_storage_microservice"]
