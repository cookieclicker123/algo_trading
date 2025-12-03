"""
Composition Root - wires microservices together.

This is the ONLY place that knows about cross-microservice dependencies.
All microservices initialize themselves independently.
"""
from ..utils.logging_config import get_logger
from ..shared.event_bus import AsyncEventBus
from ..config.settings import (
    get_telegram_config,
    get_telegram_config_2,
)

# Import microservice initializers
from .storage import initialize_storage_microservice
from .notification import initialize_notification_microservice
from .classification import initialize_classification_microservice
from .brokerage import initialize_brokerage_microservice
from .websocket import initialize_websocket_microservice

# Import shared services
from .notification.notification import TelegramNotifier
from .notification.trade_handler import get_telegram_trade_handler

# Import Services container
from .service_initialization import Services

logger = get_logger(__name__)


async def initialize_services() -> Services:
    """
    Composition Root - wires microservices together.
    
    This is the ONLY place that knows about cross-microservice dependencies.
    All microservices initialize themselves independently.
    
    Returns:
        Services: Composed services container
    """
    logger.info("Initializing services...")
    
    # Step 1: Create shared dependencies
    event_bus = AsyncEventBus()
    logger.info("Event bus created")
    
    # Step 2: Initialize microservices independently
    # Order doesn't matter - they're independent!
    storage = await initialize_storage_microservice(event_bus)
    logger.info("Storage microservice initialized")
    
    classification = await initialize_classification_microservice(event_bus)
    logger.info("Classification microservice initialized")
    
    notification = await initialize_notification_microservice(event_bus)
    logger.info("Notification microservice initialized")
    
    brokerage = await initialize_brokerage_microservice(event_bus)
    logger.info("Brokerage microservice initialized")
    
    # Step 3: Initialize shared services (used by multiple microservices)
    telegram_config_1 = get_telegram_config()
    telegram_config_2 = get_telegram_config_2()
    
    # Initialize Telegram trade handlers (still using brokerage service)
    trade_handler = None
    trade_handler_2 = None
    
    bot_token_1 = telegram_config_1.get("bot_token", "")
    bot_token_2 = telegram_config_2.get("bot_token", "")
    
    if telegram_config_1.get("enabled") and bot_token_1:
        trade_handler = get_telegram_trade_handler(
            bot_token=bot_token_1,
            trading_service=brokerage.infra
        )
    
    if telegram_config_2.get("enabled") and bot_token_2:
        trade_handler_2 = get_telegram_trade_handler(
            bot_token=bot_token_2,
            trading_service=brokerage.infra
        )
    
    telegram = TelegramNotifier(
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
        trade_handler=trade_handler,
        trade_handler_2=trade_handler_2
    )
    logger.info("Telegram notifier initialized")
    
    # Step 4: Initialize websocket (needs telegram for health monitoring)
    websocket = await initialize_websocket_microservice(
        event_bus=event_bus,
        telegram_service=telegram
    )
    logger.info("WebSocket microservice initialized")
    
    # Step 5: Create services with cross-microservice dependencies (minimal, explicit)
    # This is the ONLY place cross-microservice dependencies are wired!
    
    # Create notification use case with storage query service
    from ..use_cases.notification import NotifyImminentArticleUseCase
    notification_use_case = NotifyImminentArticleUseCase(
        event_bus=event_bus,
        storage_query_service=storage.query_service
    )
    notification.use_case = notification_use_case
    logger.info("Notification use case created with storage query service")
    
    # Create brokerage auto-trade service with storage query service
    from .brokerage.auto_trade import AutoTradeService
    auto_trade_service = AutoTradeService(
        event_bus=event_bus,
        storage_query_service=storage.query_service
    )
    brokerage.auto_trade_service = auto_trade_service
    logger.info("Auto-trade service created with storage query service")
    
    logger.info("Cross-microservice dependencies wired")
    
    return Services(
        storage=storage,
        notification=notification,
        classification=classification,
        brokerage=brokerage,
        websocket=websocket,
        telegram=telegram,
        trade_handler=trade_handler,
        trade_handler_2=trade_handler_2,
        event_bus=event_bus,
    )

