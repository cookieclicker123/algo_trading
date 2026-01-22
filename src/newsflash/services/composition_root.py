"""
Composition Root - wires microservices together using dependency injection.

This is the ONLY place that knows about cross-microservice dependencies.
All microservices initialize themselves independently, but dependencies are
provided via the DI container.
"""
import asyncio
from typing import Tuple, Any
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
            brokerage_service=brokerage_infra,
            exit_trade_use_case=None  # Will be set later after exit_trade_use_case is created
        )
    return None


async def initialize_services() -> Tuple[Services, ApplicationContainer, Any, Any, Any, Any, Any]:
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
    
    # Step 2.6: Create MarketDataValidator (needs TradingClient, MarketDataClient, and shared YahooFinanceCoordinator)
    from ..infra.brokerage.market_data_validator import MarketDataValidator

    # Start MetadataCache first (loads permanent + daily caches from disk)
    # Provides instant lookups for sector, industry, market_cap (~0ms vs 200-2000ms from yfinance)
    # IMPORTANT: Must be fully loaded before classification starts to prevent race condition
    # where SCYX/SGHC-type tickers get "unknown sector" because cache isn't ready yet
    metadata_cache = container.metadata_cache()
    await metadata_cache.start()

    # Verify cache is populated before proceeding (prevents race condition)
    stats = metadata_cache.get_stats()
    if stats["permanent_tickers"] == 0:
        logger.warning(
            "MetadataCache started but has no permanent tickers - check permanent_metadata.json",
            permanent_path=str(metadata_cache.permanent_path) if hasattr(metadata_cache, 'permanent_path') else "unknown"
        )
    else:
        logger.info(
            "MetadataCache verified ready",
            permanent_tickers=stats["permanent_tickers"],
            daily_tickers=stats["daily_tickers"]
        )

    # Small delay to ensure async file I/O is fully complete
    # This prevents race conditions where articles arrive before cache is fully loaded
    await asyncio.sleep(0.1)

    # Get shared YahooFinanceCoordinator from container (singleton - shared with stats engines)
    # Uses MetadataCache for instant lookups, only calls yfinance for cache misses
    yahoo_finance_coordinator = container.yahoo_finance_coordinator()
    await yahoo_finance_coordinator.start()  # Start coordinator early so it's ready for all services
    logger.info("YahooFinanceCoordinator started (shared across MarketDataValidator and stats engines, backed by MetadataCache)")
    
    market_data_validator = MarketDataValidator(
        trading_client=brokerage.infra.connection_manager.trading_client,
        market_data_client=brokerage.infra.connection_manager.market_data_client,
        yahoo_finance_coordinator=yahoo_finance_coordinator  # Shared singleton - single API call per ticker
    )
    logger.info("MarketDataValidator created (using shared YahooFinanceCoordinator)")
    
    # Step 2.7: Inject validators, quote_fetcher, and metadata_cache into classification infrastructure (before starting)
    classification.infra.ticker_validator = ticker_validator
    classification.infra.market_data_validator = market_data_validator
    classification.infra.quote_fetcher = brokerage.infra.quote_fetcher
    classification.infra.metadata_cache = metadata_cache  # ✅ Enables Healthcare classifier
    logger.info("TickerValidator, MarketDataValidator, QuoteFetcher, and MetadataCache injected into ClassificationInfrastructureService")
    
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
    
    auto_trade_service = container.auto_trade_service(
        market_data_client=brokerage.infra.connection_manager.market_data_client if brokerage else None,
        quote_fetcher=brokerage.quote_fetcher if brokerage else None,
        metadata_cache=metadata_cache,  # For market cap filter (<$10M = SKIP)
    )
    brokerage.auto_trade_service = auto_trade_service
    await auto_trade_service.start()
    logger.info("Auto-trade service created and started via DI container (with Confluence Scoring)")
    
    exit_trade_use_case = container.exit_trade_use_case()
    brokerage.exit_trade_use_case = exit_trade_use_case
    await exit_trade_use_case.start()
    logger.info("Exit trade use case created and started via DI container")

    # Get event bus from container (needed for PositionManager and Services container)
    event_bus = container.event_bus()

    # Create PositionManager for tiered exit strategy (10%/15%/20% profit targets)
    # Uses WebSocket for real-time price monitoring with 500ms REST polling fallback
    from .brokerage.position_manager import PositionManager
    position_manager = PositionManager(
        event_bus=event_bus,
        quote_fetcher=brokerage.quote_fetcher,
        stream_manager=brokerage.infra.stream_manager if hasattr(brokerage.infra, 'stream_manager') else None,
        poll_interval=0.5,  # 500ms fallback
        enabled=True,
    )
    brokerage.position_manager = position_manager

    # Subscribe to TradeExecuted events to automatically track new positions
    from .brokerage.position_manager import ConvictionLevel

    async def _on_trade_executed(event_type: str, event_data: dict):
        """Handle TradeExecuted event - add position if BUY order filled."""
        try:
            trade_request = event_data.get("trade_request", {})
            action = trade_request.get("action", "")

            # Only track BUY trades (not SELL exits)
            if action.upper() == "BUY" and event_data.get("success"):
                ticker = trade_request.get("ticker")
                fill_price = event_data.get("fill_price")
                shares = event_data.get("shares")
                article_id = trade_request.get("article_id")

                # Extract conviction from event metadata (set by auto_trade.py)
                metadata = event_data.get("metadata", {})
                conviction_str = metadata.get("conviction", "standard")
                try:
                    conviction = ConvictionLevel(conviction_str)
                except ValueError:
                    conviction = ConvictionLevel.STANDARD

                # Extract initial_nbbo_mid for stop loss tracking
                # Stop loss is 5% below initial NBBO mid (not entry price)
                initial_nbbo_mid = metadata.get("initial_nbbo_mid")

                if ticker and fill_price and shares:
                    await position_manager.add_position(
                        ticker=ticker,
                        entry_price=fill_price,
                        shares=shares,
                        article_id=article_id or "",
                        conviction=conviction,
                        initial_nbbo_mid=initial_nbbo_mid,
                    )
                    strategy = "AGGRESSIVE" if conviction not in (ConvictionLevel.STANDARD, ConvictionLevel.MINIMUM) else "STANDARD"
                    logger.info(
                        f"Position added from TradeExecuted event ({strategy} exits)",
                        ticker=ticker,
                        entry_price=fill_price,
                        shares=shares,
                        conviction=conviction.value,
                        initial_nbbo_mid=initial_nbbo_mid,
                        stop_loss_price=initial_nbbo_mid * 0.95 if initial_nbbo_mid else None
                    )
        except Exception as e:
            logger.error(f"Error handling TradeExecuted for PositionManager: {e}", exc_info=True)

    event_bus.subscribe("TradeExecuted", _on_trade_executed)
    await position_manager.start()
    logger.info("PositionManager created and started (tiered exits + 5% stop loss below initial NBBO)")
    
    # Update trade handlers with exit_trade_use_case and position_manager
    if trade_handler:
        trade_handler.exit_trade_use_case = exit_trade_use_case
        trade_handler.brokerage_service = brokerage.infra
        trade_handler.position_manager = position_manager
    if trade_handler_2:
        trade_handler_2.exit_trade_use_case = exit_trade_use_case
        trade_handler_2.brokerage_service = brokerage.infra
        trade_handler_2.position_manager = position_manager
    
    # Notify trade executed use case - sends notifications when trades execute
    # Pass market_data_client for volume stats analysis
    notify_trade_executed_use_case = container.notify_trade_executed_use_case(
        market_data_client=brokerage.infra.connection_manager.market_data_client if brokerage else None
    )
    notification.notify_trade_executed_use_case = notify_trade_executed_use_case
    await notify_trade_executed_use_case.start()
    logger.info("Notify trade executed use case created and started via DI container (with volume stats)")
    
    # Notify exit trade use case - sends notifications when exit trades execute
    notify_exit_trade_use_case = container.notify_exit_trade_use_case()
    notification.notify_exit_trade_use_case = notify_exit_trade_use_case
    await notify_exit_trade_use_case.start()
    logger.info("Notify exit trade use case created and started via DI container")
    
    # Notify trade failed use case - sends notifications when trades fail for imminent articles
    notify_trade_failed_use_case = container.notify_trade_failed_use_case()
    notification.notify_trade_failed_use_case = notify_trade_failed_use_case
    await notify_trade_failed_use_case.start()
    logger.info("Notify trade failed use case created and started via DI container")

    # Step 7: Initialize statistics engines (runs alongside main trading system)
    # Use DI container factories - all dependencies injected via container
    statistics_repository = container.statistics_repository()
    logger.info("StatisticsRepository initialized via DI container")
    
    # Create recall engine via DI container factory (quote_fetcher, yahoo_finance_coordinator, market_data_client, trading_client passed as dependencies)
    # Note: yahoo_finance_coordinator is already started above (shared with MarketDataValidator)
    recall_engine = container.recall_stats_engine(
        quote_fetcher=brokerage.quote_fetcher,  # Use property (not .infra directly)
        yahoo_finance_coordinator=yahoo_finance_coordinator,  # Use already-started shared singleton
        market_data_client=brokerage.infra.connection_manager.market_data_client if brokerage else None,
        trading_client=brokerage.infra.connection_manager.trading_client if brokerage else None
    )
    await recall_engine.start()
    logger.info("RecallStatsEngine started - tracking missed opportunities (with volume stats)")
    
    # Create signal engine via DI container factory (yahoo_finance_coordinator, quote_fetcher, trading_client)
    # Note: yahoo_finance_coordinator is already started above (shared with MarketDataValidator)
    signal_engine = container.signal_stats_engine(
        yahoo_finance_coordinator=yahoo_finance_coordinator,  # Use already-started shared singleton
        quote_fetcher=brokerage.quote_fetcher if brokerage else None,
        trading_client=brokerage.infra.connection_manager.trading_client if brokerage else None
    )
    await signal_engine.start()
    logger.info("SignalStatsEngine started - tracking trade executions")
    
    # Create failed trades engine via DI container factory (yahoo_finance_coordinator, quote_fetcher, trading_client)
    # Note: yahoo_finance_coordinator is already started above (shared with MarketDataValidator)
    failed_trades_engine = container.failed_trade_stats_engine(
        yahoo_finance_coordinator=yahoo_finance_coordinator,  # Use already-started shared singleton
        quote_fetcher=brokerage.quote_fetcher,  # Use property (not .infra directly)
        trading_client=brokerage.infra.connection_manager.trading_client if brokerage else None
    )
    await failed_trades_engine.start()
    logger.info("FailedTradeStatsEngine started - tracking failed trades")
    
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
    
    # Initialize Market Hours Scheduler (manages websocket shutdown/startup during off-hours)
    # NOTE: Don't start yet - start after lifecycle_manager starts websocket
    from ..services.scheduler import MarketHoursScheduler
    scheduler = MarketHoursScheduler(services=services, telegram_notifier=telegram)
    # scheduler.start() is called by lifespan after lifecycle_manager.start_services()
    logger.info("MarketHoursScheduler initialized (will start after websocket)")
    
    # Store statistics engines, scheduler, and metadata_cache for shutdown (not in Services container - they're background services)
    # They'll be stopped in lifespan shutdown handler
    return services, container, recall_engine, signal_engine, failed_trades_engine, scheduler, metadata_cache

