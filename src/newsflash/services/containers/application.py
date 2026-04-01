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
from ...use_cases.notification.notify_trade_executed_use_case import NotifyTradeExecutedUseCase
from ...use_cases.notification.notify_exit_trade_use_case import NotifyExitTradeUseCase
from ...use_cases.notification.notify_trade_failed_use_case import NotifyTradeFailedUseCase
from ...use_cases.brokerage import ExitTradeUseCase
from ...use_cases.storage import StoreArticleUseCase, StoreAuditLogUseCase
from ...use_cases.websocket import ProcessArticleUseCase
from ...use_cases.classification import ClassifyArticleUseCase
from ..brokerage.auto_trade import AutoTradeService
from ..lifecycle_manager import LifecycleManager
from ..websocket.feed_manager import FeedManager
from ..websocket.feed_health_monitor import FeedHealthMonitor
from ...infra.notification.fast_trade_notifier import create_fast_trade_notifier

# Import statistics engines
from ...infra.statistics.repository import StatisticsRepository
from ...shared.statistics.recall_engine import RecallStatsEngine
from ...shared.statistics.signal_engine import SignalStatsEngine
from ...shared.statistics.failed_trades_engine import FailedTradeStatsEngine
from ...shared.statistics.yahoo_finance_coordinator import YahooFinanceCoordinator
from ...infra.cache.metadata_cache import MetadataCache
from pathlib import Path
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
    
    # Sub-containers - instantiate directly
    config = ConfigurationContainer()
    shared = SharedContainer()
    
    # Configuration providers (automatically resolved from config container)
    telegram_config_1 = providers.Callable(config.telegram_config_1)
    telegram_config_2 = providers.Callable(config.telegram_config_2)
    
    # Expose event_bus directly from shared container for easy access
    event_bus = providers.Callable(shared.event_bus)
    
    # Expose metrics_service directly from shared container for easy access
    metrics_service = providers.Callable(shared.metrics_service)
    
    # Use case providers - define BEFORE microservices that use them
    # Storage use cases
    # StoreArticleUseCase - only needs event_bus
    store_article_use_case = providers.Factory(
        StoreArticleUseCase,
        event_bus=shared.event_bus,
    )
    
    # StoreAuditLogUseCase - needs event_bus and storage_query_service (will be overridden after storage is created)
    # Note: storage_query_service provider defined below, will be overridden in composition root
    storage_query_service = providers.Callable(
        lambda storage_ms: storage_ms.query_service
    )
    
    store_audit_log_use_case = providers.Factory(
        StoreAuditLogUseCase,
        event_bus=shared.event_bus,
        storage_query_service=storage_query_service,  # Will be overridden in composition root
    )
    
    # WebSocket use cases
    # ProcessArticleUseCase - only needs event_bus
    process_article_use_case = providers.Factory(
        ProcessArticleUseCase,
        event_bus=shared.event_bus,
    )
    
    # ClassifyArticleUseCase - only needs event_bus
    classify_article_use_case = providers.Factory(
        ClassifyArticleUseCase,
        event_bus=shared.event_bus,
    )
    
    # WebSocket services
    # FeedManager - only needs event_bus
    feed_manager = providers.Factory(
        FeedManager,
        event_bus=shared.event_bus,
    )
    
    # FeedHealthMonitor - needs event_bus and telegram_service
    # Note: telegram_service will be provided when feed_health_monitor is called in composition_root
    feed_health_monitor = providers.Factory(
        FeedHealthMonitor,
        event_bus=shared.event_bus,
        # telegram_service will be passed when feed_health_monitor is called in composition_root
    )
    
    # Factory providers - container automatically injects dependencies!
    # When called, container resolves event_bus and storage_config and passes them automatically
    storage_microservice = providers.Factory(
        initialize_storage_microservice,
        event_bus=shared.event_bus,
        storage_config=config.storage_config,
        store_article_use_case=store_article_use_case,  # ✅ Inject use case via DI
    )
    
    classification_microservice = providers.Factory(
        initialize_classification_microservice,
        event_bus=shared.event_bus,
        groq_api_key=config.groq_api_key,
        anthropic_api_key=config.anthropic_api_key,
        anthropic_model=config.anthropic_model,
        enabled=config.classification_enabled,
        metrics_service=shared.metrics_service,  # ✅ Inject metrics service
    )
    
    notification_microservice = providers.Factory(
        initialize_notification_microservice,
        event_bus=shared.event_bus,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
        metrics_service=shared.metrics_service,  # ✅ Inject metrics service
    )

    # Fast trade notifier for immediate Telegram notifications (bypasses event bus)
    fast_trade_notifier = providers.Singleton(
        create_fast_trade_notifier,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
    )

    brokerage_microservice = providers.Factory(
        initialize_brokerage_microservice,
        event_bus=shared.event_bus,
        paper_trading=config.paper_trading,
        metrics_service=shared.metrics_service,  # ✅ Inject metrics service
        fast_notifier=fast_trade_notifier,  # ✅ Inject fast trade notifier for immediate Telegram
    )
    
    # Trade handler factories - DI container manages instances
    # ✅ DI CONTAINER: Removed custom __new__ singleton pattern
    # Factory creates instances on demand (one per bot, managed by composition root)
    trade_handler_factory_1 = providers.Factory(
        get_telegram_trade_handler,
    )
    
    trade_handler_factory_2 = providers.Factory(
        get_telegram_trade_handler,
    )
    
    # Telegram service factory - trade handlers will be provided when called
    # Note: Bot instances will be created in composition_root and passed here
    telegram_service_factory = providers.Factory(
        TelegramNotifier,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
        # bot_1 and bot_2 will be passed when telegram_service_factory is called in composition_root
    )
    
    # WebSocket microservice factory - feed_health_monitor will be provided when called
    websocket_microservice_factory = providers.Factory(
        initialize_websocket_microservice,
        event_bus=shared.event_bus,
        benzinga_api_key=config.benzinga_api_key,
        benzinga_websocket_enabled=config.benzinga_websocket_enabled,
        metrics_service=shared.metrics_service,  # ✅ Inject metrics service
        process_article_use_case=process_article_use_case,  # ✅ Inject use case via DI
        classify_article_use_case=classify_article_use_case,  # ✅ Inject use case via DI
        feed_manager=feed_manager,  # ✅ Inject service via DI
        feed_health_monitor=None,  # Will be provided when websocket_microservice_factory is called in composition_root
    )
    
    # Cross-microservice dependencies
    # These providers will be called AFTER storage_microservice is awaited
    # storage_query_service is already defined above (used by store_audit_log_use_case)
    
    # Notification use case needs event_bus and storage_query_service
    notification_use_case = providers.Factory(
        NotifyImminentArticleUseCase,
        event_bus=shared.event_bus,
        storage_query_service=storage_query_service,
    )
    
    # AutoTrade service needs event_bus, storage_query_service, and auto-trade config
    # Uses confluence scoring system (spread + volume + price) for position sizing
    # Stop loss: 5% below actual entry price
    auto_trade_service = providers.Factory(
        AutoTradeService,
        event_bus=shared.event_bus,
        storage_query_service=storage_query_service,
        enabled=config.auto_trading_enabled,
        # market_data_client and quote_fetcher will be provided when called in composition_root
    )
    
    # Exit trade use case - only needs event_bus (no storage dependency)
    exit_trade_use_case = providers.Factory(
        ExitTradeUseCase,
        event_bus=shared.event_bus,
    )
    
    # Notify trade executed use case - needs event_bus, storage_query_service, and market_data_client
    # market_data_client will be passed when notify_trade_executed_use_case is called in composition_root
    notify_trade_executed_use_case = providers.Factory(
        NotifyTradeExecutedUseCase,
        event_bus=shared.event_bus,
        storage_query_service=storage_query_service,
        # market_data_client will be provided when called in composition_root
    )
    
    # Notify exit trade use case - only needs event_bus
    notify_exit_trade_use_case = providers.Factory(
        NotifyExitTradeUseCase,
        event_bus=shared.event_bus,
    )
    
    # Notify trade failed use case - needs event_bus and storage_query_service
    notify_trade_failed_use_case = providers.Factory(
        NotifyTradeFailedUseCase,
        event_bus=shared.event_bus,
        storage_query_service=storage_query_service,
    )
    
    # Lifecycle manager - orchestrates startup/shutdown
    lifecycle_manager = providers.Factory(
        LifecycleManager,
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
    )
    
    # Statistics engines - factories for dependency injection
    # StatisticsRepository - needs tmp_dir from storage_config
    statistics_repository = providers.Factory(
        StatisticsRepository,
        tmp_dir=providers.Callable(
            lambda storage_config: Path(storage_config["tmp_dir"]),
            storage_config=config.storage_config,
        ),
    )
    
    # MetadataCache - persistent cache for instant ticker lookups
    # Permanent cache: sector, industry (never changes)
    # Daily cache: market_cap (refreshed at 4am UK time)
    metadata_cache = providers.Singleton(
        MetadataCache,
    )

    # YahooFinanceCoordinator - shared singleton for all engines (replaces Finnhub)
    # Uses yfinance to fetch industry, sector, market_cap (no API key needed)
    # Wired with MetadataCache for instant lookups (only calls yfinance for cache misses)
    yahoo_finance_coordinator = providers.Singleton(
        YahooFinanceCoordinator,
        metadata_cache=metadata_cache,
    )
    
    # RecallStatsEngine - needs event_bus, repository, quote_fetcher, yahoo_finance_coordinator, market_data_client, trading_client
    recall_stats_engine = providers.Factory(
        RecallStatsEngine,
        event_bus=shared.event_bus,
        repository=statistics_repository,
        yahoo_finance_coordinator=yahoo_finance_coordinator,
        # quote_fetcher, market_data_client, trading_client will be passed when recall_stats_engine is called in composition_root
    )
    
    # SignalStatsEngine - needs event_bus, repository, yahoo_finance_coordinator, quote_fetcher, trading_client
    signal_stats_engine = providers.Factory(
        SignalStatsEngine,
        event_bus=shared.event_bus,
        repository=statistics_repository,
        yahoo_finance_coordinator=yahoo_finance_coordinator,
        # quote_fetcher and trading_client will be passed when signal_stats_engine is called in composition_root
    )
    
    # FailedTradeStatsEngine - needs event_bus, repository, quote_fetcher, yahoo_finance_coordinator, trading_client
    failed_trade_stats_engine = providers.Factory(
        FailedTradeStatsEngine,
        event_bus=shared.event_bus,
        repository=statistics_repository,
        yahoo_finance_coordinator=yahoo_finance_coordinator,
        # quote_fetcher and trading_client will be passed when failed_trade_stats_engine is called in composition_root
    )
