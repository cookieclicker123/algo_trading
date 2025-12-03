"""
Notification microservice - self-contained initialization.

This module initializes all notification-related components:
- Infrastructure service
- Domain listener (bridge)
- Use cases
"""
from dataclasses import dataclass
from typing import Optional

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...config.settings import get_telegram_config, get_telegram_config_2, TELEGRAM_ENABLED, TELEGRAM_ENABLED_2

# Infrastructure layer
from ...infra.notification import NotificationInfrastructureService

# Domain layer
from ...domain.notification.listener import NotificationDomainListener

# Use cases layer
from ...use_cases.notification import NotifyImminentArticleUseCase

logger = get_logger(__name__)


@dataclass
class NotificationMicroservice:
    """
    Notification microservice container.
    
    Holds all notification-related components:
    - Infrastructure service
    - Domain listener (bridge)
    - Use cases
    """
    infra: NotificationInfrastructureService
    domain_listener: NotificationDomainListener
    use_case: Optional[NotifyImminentArticleUseCase]  # Will be created in composition root after dependencies wired
    
    async def start(self) -> None:
        """Start all notification microservice components."""
        logger.info("Starting notification microservice...")
        
        # Start infrastructure FIRST
        await self.infra.start()
        logger.info("Notification infrastructure started")
        
        # Start domain listener
        await self.domain_listener.start()
        logger.info("Notification domain listener started")
        
        # Start use cases
        if self.use_case:
            await self.use_case.start()
            logger.info("Notification use case started")
        
        logger.info("Notification microservice started")
    
    async def stop(self) -> None:
        """Stop all notification microservice components."""
        logger.info("Stopping notification microservice...")
        
        # Stop use cases first
        if self.use_case:
            await self.use_case.stop()
        
        # Stop domain listener
        await self.domain_listener.stop()
        
        # Stop infrastructure last
        await self.infra.stop()
        
        logger.info("Notification microservice stopped")


async def initialize_notification_microservice(event_bus: AsyncEventBus) -> NotificationMicroservice:
    """
    Initialize notification microservice independently.
    
    This function knows ONLY about notification microservice.
    It doesn't know about other microservices.
    
    Note: storage_query_service dependency will be wired in composition root.
    
    Args:
        event_bus: Event bus instance (shared dependency)
        
    Returns:
        NotificationMicroservice: Initialized notification microservice
    """
    logger.info("Initializing notification microservice...")
    
    # Step 1: Infrastructure layer
    telegram_config_1 = get_telegram_config()
    telegram_config_2 = get_telegram_config_2()
    notification_enabled = TELEGRAM_ENABLED or TELEGRAM_ENABLED_2
    
    infra = NotificationInfrastructureService(
        event_bus=event_bus,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
        enabled=notification_enabled
    )
    logger.info("Notification infrastructure initialized", enabled=notification_enabled)
    
    # Step 2: Domain listener (bridge infrastructure ↔ domain)
    from ...domain.notification.validators import NotificationMessageValidator
    from ...domain.notification.mappers import NotificationMapper
    
    domain_listener = NotificationDomainListener(
        event_bus=event_bus,
        message_validator=NotificationMessageValidator(),
        notification_mapper=NotificationMapper(),
    )
    logger.info("Notification domain listener initialized")
    
    # Step 3: Use cases layer
    # Note: Use case will be created in composition root after storage_query_service is available
    # (cross-microservice dependency)
    
    return NotificationMicroservice(
        infra=infra,
        domain_listener=domain_listener,
        use_case=None,  # Will be created in composition root
    )


# Export notification services
from .notification import TelegramNotifier

__all__ = ["NotificationMicroservice", "initialize_notification_microservice", "TelegramNotifier"]
