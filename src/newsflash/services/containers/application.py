"""
Main application container - provides all dependencies via dependency injection.

This container defines the dependency graph. When services need dependencies,
the container automatically resolves and injects them.
"""
from dependency_injector import containers, providers

from .shared import SharedContainer
from .configuration import ConfigurationContainer

# Import initialization functions
from ..storage import initialize_storage_microservice
from ..classification import initialize_classification_microservice
from ..notification import initialize_notification_microservice
from ..brokerage import initialize_brokerage_microservice
from ..websocket import initialize_websocket_microservice

# Import services
from ..notification.notification import TelegramNotifier
from ..notification.trade_handler import get_telegram_trade_handler
from ...use_cases.notification import NotifyImminentArticleUseCase
from ..brokerage.auto_trade import AutoTradeService
from ..lifecycle_manager import LifecycleManager


class ApplicationContainer(containers.DeclarativeContainer):
    """
    Main application container - manages all dependencies via DI.
    
    DEPENDENCY INJECTION EXPLANATION:
    
    This container provides services with automatic dependency resolution:
    
    1. event_bus is a singleton - created once, shared everywhere
    2. When you ask for storage_microservice:
       - Container sees it needs event_bus
       - Container gets event_bus from shared container (or creates it if first time)
       - Container passes event_bus to initialize_storage_microservice()
       - You get fully wired storage_microservice!
    
    3. Dependencies flow in (are injected) rather than being created internally
    
    This is TRUE dependency injection because:
    - Services don't create their own dependencies
    - Dependencies are provided from outside (container)
    - Container manages the dependency graph automatically
    """
    
    # Sub-containers
    config = containers.Container(ConfigurationContainer)
    shared = containers.Container(SharedContainer)
    
    # Configuration providers (automatically resolved from config container)
    telegram_config_1 = providers.Callable(config.telegram_config_1)
    telegram_config_2 = providers.Callable(config.telegram_config_2)
    
    # Factory providers - container automatically injects dependencies!
    # When called, container resolves event_bus and storage_config and passes them automatically
    storage_microservice = providers.Factory(
        initialize_storage_microservice,
        event_bus=shared.event_bus,
        storage_config=config.storage_config,
    )
    
    classification_microservice = providers.Factory(
        initialize_classification_microservice,
        event_bus=shared.event_bus,
        api_key=config.groq_api_key,
        model=config.groq_model,
        enabled=config.classification_enabled,
    )
    
    notification_microservice = providers.Factory(
        initialize_notification_microservice,
        event_bus=shared.event_bus,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
    )
    
    brokerage_microservice = providers.Factory(
        initialize_brokerage_microservice,
        event_bus=shared.event_bus,
        paper_trading=config.ibkr_paper_trading,
        client_id=config.ibkr_client_id,
    )
    
    # Trade handler factory functions - need brokerage_microservice which is async
    # These will be called from composition_root after brokerage is initialized
    trade_handler_factory_1 = providers.Factory(
        get_telegram_trade_handler,
    )
    
    trade_handler_factory_2 = providers.Factory(
        get_telegram_trade_handler,
    )
    
    # Telegram service factory - trade handlers will be provided when called
    telegram_service_factory = providers.Factory(
        TelegramNotifier,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
    )
    
    # WebSocket microservice factory - telegram_service will be provided when called
    websocket_microservice_factory = providers.Factory(
        initialize_websocket_microservice,
        event_bus=shared.event_bus,
        benzinga_api_key=config.benzinga_api_key,
        benzinga_websocket_enabled=config.benzinga_websocket_enabled,
    )
    
    # Cross-microservice dependencies
    # Notification use case needs event_bus and storage_query_service
    notification_use_case = providers.Factory(
        NotifyImminentArticleUseCase,
        event_bus=shared.event_bus,
        storage_query_service=providers.Factory(
            lambda ms: ms.query_service,
            storage_microservice,
        ),
    )
    
    # AutoTrade service needs event_bus, storage_query_service, and auto-trade config
    auto_trade_service = providers.Factory(
        AutoTradeService,
        event_bus=shared.event_bus,
        storage_query_service=providers.Factory(
            lambda ms: ms.query_service,
            storage_microservice,
        ),
        enabled=config.auto_trading_enabled,
        trade_amount_usd=config.auto_trade_amount_usd,
    )
    
    # Lifecycle manager - orchestrates startup/shutdown
    lifecycle_manager = providers.Factory(
        LifecycleManager,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
    )
