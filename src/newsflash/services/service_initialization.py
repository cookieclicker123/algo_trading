"""
Services container - holds all initialized microservices.

Note: Lifecycle management (start/stop) has been moved to LifecycleManager
which is created via dependency injection.
"""
from typing import Dict, Any, Optional

from ..utils.logging_config import get_logger

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




# Note: start_services and stop_services have been moved to LifecycleManager
# which is created via dependency injection. This ensures all dependencies
# (like config) are properly injected rather than called directly.


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
