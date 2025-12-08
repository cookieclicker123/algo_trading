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
    # Order doesn't matter - container handles dependency resolution!
    storage = await container.storage_microservice()
    logger.info("Storage microservice initialized")
    
    classification = await container.classification_microservice()
    logger.info("Classification microservice initialized")
    
    notification = await container.notification_microservice()
    logger.info("Notification microservice initialized")
    
    brokerage = await container.brokerage_microservice()
    logger.info("Brokerage microservice initialized")
    
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
    
    # Telegram service - container factory, trade handlers injected
    telegram = container.telegram_service_factory(
        trade_handler=trade_handler,
        trade_handler_2=trade_handler_2
    )
    logger.info("Telegram notifier initialized")
    
    # Step 4: Initialize websocket via container (telegram_service injected)
    websocket = await container.websocket_microservice_factory(
        telegram_service=telegram
    )
    logger.info("WebSocket microservice initialized")
    
    # Step 5: Wire cross-microservice dependencies using DI container
    # ✅ DI CONTAINER: Use container providers instead of manual instantiation
    # After storage is awaited, we can use providers that depend on it
    
    # Override storage_query_service provider with the awaited storage instance
    # This allows other providers to use it
    container.storage_query_service.override(
        providers.Callable(lambda: storage.query_service)
    )
    
    # Now use container providers - they will automatically get storage_query_service
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

