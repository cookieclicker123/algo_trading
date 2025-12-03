"""
Services container and lifecycle management.
"""
from typing import Dict, Any, Optional

from ..utils.bot_conflict_resolver import resolve_bot_conflicts
from ..utils.logging_config import get_logger
from ..config.settings import (
    get_telegram_config,
    get_telegram_config_2,
)

# Import microservice types
from .storage import StorageMicroservice
from .notification import NotificationMicroservice
from .classification import ClassificationMicroservice
from .brokerage import BrokerageMicroservice
from .websocket import WebSocketMicroservice

# Type hints
from ..shared.event_bus import AsyncEventBus
from .notification.notification import TelegramNotifier

logger = get_logger(__name__)


class Services:
    """
    Services container - holds all microservices.
    """
    
    def __init__(
        self,
        storage: StorageMicroservice,
        notification: NotificationMicroservice,
        classification: ClassificationMicroservice,
        brokerage: BrokerageMicroservice,
        websocket: WebSocketMicroservice,
        telegram: Optional[TelegramNotifier] = None,
        trade_handler=None,
        trade_handler_2=None,
        event_bus: Optional[AsyncEventBus] = None,
    ):
        """Initialize services container with microservices."""
        self.storage = storage
        self.notification = notification
        self.classification = classification
        self.brokerage = brokerage
        self.websocket = websocket
        self.telegram = telegram
        self.trade_handler = trade_handler
        self.trade_handler_2 = trade_handler_2
        self.event_bus = event_bus




async def start_services(services: Services) -> None:
    """Start all services."""
    logger.info("Starting all services...")
    
    try:
        # Resolve bot conflicts (shared concern)
        telegram_config_1 = get_telegram_config()
        telegram_config_2 = get_telegram_config_2()
        
        bot_tokens = []
        if telegram_config_1.get("enabled") and telegram_config_1.get("bot_token"):
            bot_tokens.append(telegram_config_1.get("bot_token"))
        if telegram_config_2.get("enabled") and telegram_config_2.get("bot_token"):
            bot_tokens.append(telegram_config_2.get("bot_token"))
        
        if bot_tokens:
            conflict_resolved = await resolve_bot_conflicts(bot_tokens, aggressive=True)
            if not conflict_resolved:
                logger.warning("Bot conflicts detected but not resolved - services may fail to start")
        else:
            logger.info("No enabled bots found, skipping conflict resolution")
        
        # Start Telegram trade handlers (shared services)
        if services.trade_handler and telegram_config_1.get("enabled"):
            await services.trade_handler.start()
            logger.info("Telegram trade handler 1 started")
        
        if services.trade_handler_2 and telegram_config_2.get("enabled"):
            await services.trade_handler_2.start()
            logger.info("Telegram trade handler 2 started")
        
        # Start each microservice (they manage their own lifecycle!)
        await services.storage.start()
        await services.classification.start()
        await services.notification.start()
        await services.brokerage.start()
        await services.websocket.start()
        
        logger.info("All services started successfully")
        
    except Exception as e:
        logger.error("Failed to start services", error=str(e))
        raise


async def stop_services(services: Services) -> None:
    """Stop all services."""
    logger.info("Stopping all services...")
    
    try:
        # Stop microservices in reverse order (they manage their own lifecycle!)
        await services.websocket.stop()
        await services.brokerage.stop()
        await services.notification.stop()
        await services.classification.stop()
        await services.storage.stop()
        
        # Stop shared services
        if services.trade_handler:
            await services.trade_handler.stop()
            logger.info("Telegram trade handler 1 stopped")
        
        if services.trade_handler_2:
            await services.trade_handler_2.stop()
            logger.info("Telegram trade handler 2 stopped")
        
        logger.info("All services stopped successfully")
        
    except Exception as e:
        logger.error("Failed to stop services", error=str(e))
        raise


async def get_stats(services: Services) -> Dict[str, Any]:
    """Get statistics from all services."""
    try:
        stats = {
            "feed_manager": services.websocket.feed_manager.get_stats() if services.websocket.feed_manager else {},
            "storage_query_service": "Available" if services.storage.query_service else "Not available",
            "telegram": {
                "enabled_1": services.telegram.enabled_1 if services.telegram else False,
                "enabled_2": services.telegram.enabled_2 if services.telegram else False,
                "test_mode": services.telegram.test_mode if services.telegram else False,
            },
            "classification_infra": services.classification.infra.get_stats() if services.classification.infra else {},
            "storage_infra": await services.storage.infra.get_stats() if services.storage.infra else {},
            "notification_infra": services.notification.infra.get_stats() if services.notification.infra else {},
            "brokerage": services.brokerage.infra.get_stats() if services.brokerage.infra else {},
        }
        return stats
    except Exception as e:
        logger.error("Failed to get service stats", error=str(e))
        return {"error": str(e)}


def is_healthy(services: Services) -> bool:
    """Check if all services are healthy."""
    try:
        if services.websocket.feed_manager:
            return services.websocket.feed_manager.is_healthy()
        return False
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return False
