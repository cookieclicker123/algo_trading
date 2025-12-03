"""
WebSocket microservice - self-contained initialization.

This module initializes all websocket-related components:
- Infrastructure service
- Domain listener (bridge)
- Services (feed manager, health monitor)
- Use cases (process article)
"""
import asyncio
from dataclasses import dataclass, field
from typing import Optional

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...config.settings import BENZINGA_API_KEY, BENZINGA_WEBSOCKET_ENABLED

# Infrastructure layer
from ...infra.websocket.service import BenzingaWebSocketMicroservice

# Domain layer
from ...domain.websocket.listener import WebSocketDomainListener

# Services layer
from .feed_manager import FeedManager
from .feed_health_monitor import FeedHealthMonitor

# Use cases layer
from ...use_cases.websocket import ProcessArticleUseCase

# Notification service (shared dependency)
from ..notification.notification import TelegramNotifier

logger = get_logger(__name__)


@dataclass
class WebSocketMicroservice:
    """
    WebSocket microservice container.
    
    Holds all websocket-related components:
    - Infrastructure service
    - Domain listener (bridge)
    - Services (feed manager, health monitor)
    - Use cases (process article)
    """
    infra: Optional[BenzingaWebSocketMicroservice]
    domain_listener: WebSocketDomainListener
    feed_manager: FeedManager
    health_monitor: FeedHealthMonitor
    process_article_use_case: ProcessArticleUseCase
    _health_monitor_task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)
    
    async def start(self) -> None:
        """Start all websocket microservice components."""
        logger.info("Starting websocket microservice...")
        
        # Start infrastructure FIRST (if enabled)
        if self.infra:
            self.infra.start()
            logger.info("Benzinga WebSocket microservice started")
        
        # Start domain listener
        await self.domain_listener.start()
        logger.info("WebSocket domain listener started")
        
        # Start process article use case
        await self.process_article_use_case.start()
        logger.info("Process article use case started")
        
        # Start feed manager (non-blocking, event subscription only)
        asyncio.create_task(self.feed_manager.start_all_feeds())
        logger.info("Feed manager started")
        
        # Start health monitor as background task
        self._health_monitor_task = asyncio.create_task(
            self.health_monitor.start()
        )
        logger.info("Feed health monitor started")
        
        logger.info("WebSocket microservice started")
    
    async def stop(self) -> None:
        """Stop all websocket microservice components."""
        logger.info("Stopping websocket microservice...")
        
        # Stop health monitor first
        if self.health_monitor:
            await self.health_monitor.stop()
            if self._health_monitor_task:
                self._health_monitor_task.cancel()
                try:
                    await self._health_monitor_task
                except asyncio.CancelledError:
                    pass
            logger.info("Feed health monitor stopped")
        
        # Stop feed manager
        if self.feed_manager:
            await self.feed_manager.stop_all_feeds()
            logger.info("Feed manager stopped")
        
        # Stop process article use case
        if self.process_article_use_case:
            await self.process_article_use_case.stop()
            logger.info("Process article use case stopped")
        
        # Stop domain listener
        await self.domain_listener.stop()
        logger.info("WebSocket domain listener stopped")
        
        # Stop infrastructure last
        if self.infra:
            self.infra.stop()
            logger.info("Benzinga WebSocket service stopped")
        
        logger.info("WebSocket microservice stopped")


async def initialize_websocket_microservice(
    event_bus: AsyncEventBus,
    telegram_service: Optional[TelegramNotifier] = None
) -> WebSocketMicroservice:
    """
    Initialize websocket microservice independently.
    
    This function knows ONLY about websocket microservice.
    It doesn't know about other microservices.
    
    Args:
        event_bus: Event bus instance (shared dependency)
        telegram_service: Telegram service (shared dependency for health monitoring)
        
    Returns:
        WebSocketMicroservice: Initialized websocket microservice
    """
    logger.info("Initializing websocket microservice...")
    
    # Step 1: Infrastructure layer
    if BENZINGA_WEBSOCKET_ENABLED and BENZINGA_API_KEY:
        infra = BenzingaWebSocketMicroservice(event_bus=event_bus, token=BENZINGA_API_KEY)
        logger.info("WebSocket infrastructure initialized")
    else:
        infra = None
        logger.info("WebSocket infrastructure disabled")
    
    # Step 2: Domain listener (bridge infrastructure ↔ domain)
    from ...domain.websocket.validators import ArticleValidator
    from ...domain.websocket.factories import ArticleFactory
    
    domain_listener = WebSocketDomainListener(
        event_bus=event_bus,
        validator=ArticleValidator(),
        factory=ArticleFactory(),
    )
    logger.info("WebSocket domain listener initialized")
    
    # Step 3: Services layer
    feed_manager = FeedManager(event_bus=event_bus)
    logger.info("Feed manager initialized")
    
    health_monitor = FeedHealthMonitor(
        event_bus=event_bus,
        telegram_service=telegram_service
    )
    logger.info("Feed health monitor initialized")
    
    # Step 4: Use cases layer
    process_article_use_case = ProcessArticleUseCase(event_bus=event_bus)
    logger.info("Process article use case initialized")
    
    return WebSocketMicroservice(
        infra=infra,
        domain_listener=domain_listener,
        feed_manager=feed_manager,
        health_monitor=health_monitor,
        process_article_use_case=process_article_use_case,
    )


__all__ = ["WebSocketMicroservice", "initialize_websocket_microservice"]
