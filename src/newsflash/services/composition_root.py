"""
Composition Root - wires microservices together using dependency injection.

This is the ONLY place that knows about cross-microservice dependencies.
All microservices initialize themselves independently, but dependencies are
provided via the DI container.
"""
from typing import Tuple
from dependency_injector import providers

from ..utils.logging_config import get_logger

# Import Services container
from .service_initialization import Services

# Import DI container
from .containers.application import ApplicationContainer

logger = get_logger(__name__)


def _create_trade_handler_if_enabled(
    container: ApplicationContainer,
    telegram_config: dict,
    brokerage_infra,
    factory_attr: str,
) -> object:
    """
    Helper function to conditionally create trade handler if enabled.
    
    Args:
        container: Application container
        telegram_config: Telegram configuration dict
        brokerage_infra: Brokerage infrastructure service
        factory_attr: Attribute name of factory on container
        
    Returns:
        Trade handler instance or None
    """
    if telegram_config.get("enabled") and telegram_config.get("bot_token"):
        factory = getattr(container, factory_attr)
        return factory(
            bot_token=telegram_config["bot_token"],
            trading_service=brokerage_infra
        )
    return None


async def initialize_services() -> Tuple[Services, ApplicationContainer]:
    """
    Composition Root - wires microservices together using DI container.
    
    This function uses the dependency injection container to:
    1. Create all microservices with their dependencies automatically resolved
    2. Wire cross-microservice dependencies
    3. Return a Services container with all initialized services and the DI container
    
    Returns:
        Tuple of (Services, ApplicationContainer): Composed services container and DI container
    """
    logger.info("Initializing services using dependency injection container...")
    
    # Step 1: Create and configure the DI container
    container = ApplicationContainer()
    
    # Wire container for automatic dependency injection in route handlers
    # This enables @inject decorator usage in route handlers
    container.wire(
        modules=[
            "newsflash.api.routes.health",
            "newsflash.api.routes.storage.articles",
            "newsflash.api.routes.websocket.feeds",
        ]
    )
    logger.info("DI container created and wired")
    
    # Step 1.5: Initialize and start MetricsService (needs to be running before other services)
    # MetricsService subscribes to events, so it must start before services that publish events
    metrics_service = container.metrics_service()
    await metrics_service.start()
    logger.info("MetricsService started - subscribing to events")
    
    # Step 2: Initialize microservices via container (dependencies auto-resolved)
    # Note: Classification needs TickerValidator which requires brokerage TradingClient
    # So we initialize brokerage first, then create TickerValidator, then pass it to classification
    storage = await container.storage_microservice()
    logger.info("Storage microservice initialized")
    
    # Initialize classification first (without TickerValidator - will be injected later)
    classification = await container.classification_microservice()
    logger.info("Classification microservice initialized (TickerValidator will be injected)")
    
    notification = await container.notification_microservice()
    logger.info("Notification microservice initialized")
    
    brokerage = await container.brokerage_microservice()
    logger.info("Brokerage microservice initialized")
    
    # Step 2.5: Create TickerValidator (needs TradingClient from brokerage)
    # This must happen after brokerage is initialized
    from ..infra.brokerage.ticker_validator import TickerValidator
    ticker_validator = TickerValidator(
        trading_client=brokerage.infra.connection_manager.trading_client
    )
    logger.info("TickerValidator created")
    
    # Step 2.6: Inject TickerValidator into classification infrastructure (before starting)
    classification.infra.ticker_validator = ticker_validator
    logger.info("TickerValidator injected into ClassificationInfrastructureService")
    
    # Step 3: Initialize shared services via container (using helper functions)
    # Get configs from container (DI-managed)
    telegram_config_1 = container.telegram_config_1()
    telegram_config_2 = container.telegram_config_2()
    
    # Create trade handlers conditionally using helper function (cleaner approach)
    trade_handler = _create_trade_handler_if_enabled(
        container, telegram_config_1, brokerage.infra, "trade_handler_factory_1"
    )
    trade_handler_2 = _create_trade_handler_if_enabled(
        container, telegram_config_2, brokerage.infra, "trade_handler_factory_2"
    )
    
    if trade_handler:
        logger.info("Trade handler 1 initialized")
    if trade_handler_2:
        logger.info("Trade handler 2 initialized")
    
    # Create Bot instances conditionally (only if enabled and token present)
    bot_1 = None
    bot_2 = None
    
    if telegram_config_1.get("enabled") and telegram_config_1.get("bot_token"):
        from telegram import Bot
        bot_1 = Bot(token=telegram_config_1["bot_token"])
        logger.info("Bot 1 created via DI")
    
    if telegram_config_2.get("enabled") and telegram_config_2.get("bot_token"):
        from telegram import Bot
        bot_2 = Bot(token=telegram_config_2["bot_token"])
        logger.info("Bot 2 created via DI")
    
    # Telegram service - container factory, trade handlers and bots injected
    telegram = container.telegram_service_factory(
        trade_handler=trade_handler,
        trade_handler_2=trade_handler_2,
        bot_1=bot_1,  # ✅ Inject Bot instance via DI
        bot_2=bot_2,  # ✅ Inject Bot instance via DI
    )
    logger.info("Telegram notifier initialized")
    
    # Step 4: Create use cases that need storage_query_service (after storage is created)
    # Override storage_query_service provider with the awaited storage instance
    container.storage_query_service.override(
        providers.Callable(lambda: storage.query_service)
    )
    
    # Create store_audit_log_use_case (needs storage_query_service)
    store_audit_log_use_case = container.store_audit_log_use_case()
    # Update storage microservice to use injected use case
    # Note: Use case will be started when storage.start() is called in lifecycle_manager
    storage.store_audit_log_use_case = store_audit_log_use_case
    logger.info("Store audit log use case created via DI container (will be started with storage microservice)")
    
    # Step 5: Create FeedHealthMonitor via container (needs telegram_service)
    feed_health_monitor = container.feed_health_monitor(
        telegram_service=telegram  # ✅ Pass telegram_service when creating FeedHealthMonitor
    )
    logger.info("Feed health monitor created via DI container")
    
    # Step 6: Initialize websocket via container (use cases and services injected)
    websocket = await container.websocket_microservice_factory(
        feed_health_monitor=feed_health_monitor  # ✅ Inject FeedHealthMonitor via DI
    )
    logger.info("WebSocket microservice initialized")
    
    # Step 6: Wire cross-microservice dependencies using DI container
    # ✅ DI CONTAINER: Use container providers instead of manual instantiation
    # storage_query_service is already overridden above, so providers can use it
    notification_use_case = container.notification_use_case()
    notification.use_case = notification_use_case
    await notification_use_case.start()
    logger.info("Notification use case created and started via DI container")
    
    auto_trade_service = container.auto_trade_service()
    brokerage.auto_trade_service = auto_trade_service
    await auto_trade_service.start()
    logger.info("Auto-trade service created and started via DI container")
    
    exit_trade_use_case = container.exit_trade_use_case()
    brokerage.exit_trade_use_case = exit_trade_use_case
    await exit_trade_use_case.start()
    logger.info("Exit trade use case created and started via DI container")
    
    # Notify trade executed use case - sends notifications when trades execute
    notify_trade_executed_use_case = container.notify_trade_executed_use_case()
    notification.notify_trade_executed_use_case = notify_trade_executed_use_case
    await notify_trade_executed_use_case.start()
    logger.info("Notify trade executed use case created and started via DI container")
    
    # Notify exit trade use case - sends notifications when exit trades execute
    notify_exit_trade_use_case = container.notify_exit_trade_use_case()
    notification.notify_exit_trade_use_case = notify_exit_trade_use_case
    await notify_exit_trade_use_case.start()
    logger.info("Notify exit trade use case created and started via DI container")
    
    # Get event bus from container (needed for Services container)
    event_bus = container.event_bus()
    
    logger.info("All services initialized via DI container")
    
    services = Services(
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
    
    return services, container

