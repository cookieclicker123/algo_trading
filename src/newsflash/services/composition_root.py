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

    # Start TickerBlacklist (auto-blacklist after 3 consecutive FPs)
    from .brokerage.ticker_blacklist import get_ticker_blacklist
    ticker_blacklist = get_ticker_blacklist()
    await ticker_blacklist.start()
    logger.info("TickerBlacklist started", stats=ticker_blacklist.get_stats())

    # Start SectorTracker (track FPs per sector - hot sector detection)
    from .brokerage.sector_tracker import get_sector_tracker
    sector_tracker = get_sector_tracker()
    await sector_tracker.start()
    logger.info("SectorTracker started")

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

    # Create PositionManager for stop loss protection + let winners run
    # Uses WebSocket for real-time price monitoring with 500ms REST polling fallback
    from .brokerage.position_manager import PositionManager

    # Get stream_manager from connection_manager (it's not exposed directly on BrokerageService)
    stream_manager = None
    if hasattr(brokerage.infra, 'connection_manager') and brokerage.infra.connection_manager:
        stream_manager = getattr(brokerage.infra.connection_manager, 'stream_manager', None)

    if stream_manager:
        logger.info("PositionManager will use WebSocket stream for real-time stop loss monitoring (SIP feed)")
    else:
        logger.warning("⚠️ PositionManager has NO WebSocket stream - stop loss relies on 500ms REST polling only!")

    # Get fast_notifier for immediate Telegram on stop loss exits
    fast_notifier = container.fast_trade_notifier()

    position_manager = PositionManager(
        event_bus=event_bus,
        quote_fetcher=brokerage.quote_fetcher,
        stream_manager=stream_manager,
        fast_notifier=fast_notifier,
        poll_interval=0.5,  # 500ms fallback
        enabled=True,
    )
    brokerage.position_manager = position_manager

    # Subscribe to TradeExecuted events to automatically track new positions
    from .brokerage.position_manager import ConvictionLevel
    from .brokerage.auto_trade import register_active_position, unregister_active_position

    async def _on_trade_executed(event_type: str, event_data: dict):
        """Handle TradeExecuted event - add position if BUY order filled."""
        try:
            trade_request = event_data.get("trade_request", {})
            action = trade_request.get("action", "")
            ticker = trade_request.get("ticker")

            # Track BUY trades (entries) - add position or update for scale-in
            if action.upper() == "BUY" and event_data.get("success"):
                fill_price = event_data.get("fill_price")
                shares = event_data.get("shares")
                article_id = trade_request.get("article_id")

                # Extract metadata (defensive: handle None from serialization)
                metadata = event_data.get("metadata") or {}

                # Check if this is a scale-in fill (adding to existing position)
                is_scale_in = metadata.get("scale_in", False)

                if is_scale_in and ticker and fill_price and shares:
                    # Scale-in fill: Update existing position, don't create new one
                    updated = await position_manager.update_scale_in(
                        ticker=ticker,
                        fill_price=fill_price,
                        shares_added=int(shares),
                    )
                    if updated:
                        logger.info(
                            "📈 Scale-in fill processed - position updated",
                            ticker=ticker,
                            fill_price=fill_price,
                            shares_added=int(shares),
                        )
                    else:
                        logger.warning(
                            "Scale-in fill but no position found - creating new position",
                            ticker=ticker,
                            fill_price=fill_price,
                            shares=shares,
                        )
                        # Fall through to create new position
                        is_scale_in = False

                if not is_scale_in:
                    # Regular entry: Create new position
                    conviction_str = metadata.get("conviction", "standard")
                    try:
                        conviction = ConvictionLevel(conviction_str)
                    except ValueError:
                        conviction = ConvictionLevel.STANDARD

                    # Extract initial_nbbo_mid for analytics/logging
                    initial_nbbo_mid = metadata.get("initial_nbbo_mid")

                    # Extract scale-in parameters for no_volume entries
                    awaiting_confirmation = metadata.get("awaiting_confirmation", False)
                    target_full_shares = metadata.get("target_full_shares", 0.0)

                    # Extract mega trade, high-conviction, and clinical breakthrough flags
                    is_mega_trade = metadata.get("is_mega_trade", False)
                    is_high_conviction = metadata.get("is_high_conviction", False)
                    is_clinical_breakthrough = metadata.get("is_clinical_breakthrough", False)
                    is_auto_tp_eligible = metadata.get("is_auto_tp_eligible", False)

                    # Safety check: HC-sized trade but flag missing = metadata race condition
                    # Normal max is $2,000 — anything above that without HC flag is a bug
                    total_cost = fill_price * shares if fill_price and shares else 0
                    if total_cost > 2500 and not is_high_conviction and not is_mega_trade and not is_clinical_breakthrough:
                        logger.error(
                            "🚨 METADATA BUG: Large position ($%.0f) but is_high_conviction=False! "
                            "Forcing is_high_conviction=True to prevent wrong stop loss",
                            total_cost,
                            ticker=ticker,
                            metadata_keys=list(metadata.keys()) if metadata else [],
                        )
                        is_high_conviction = True

                    if ticker and fill_price and shares:
                        # Register active position for duplicate guard
                        register_active_position(ticker)

                        await position_manager.add_position(
                            ticker=ticker,
                            entry_price=fill_price,
                            shares=shares,
                            article_id=article_id or "",
                            conviction=conviction,
                            initial_nbbo_mid=initial_nbbo_mid,
                            awaiting_confirmation=awaiting_confirmation,
                            target_full_shares=target_full_shares,
                            is_mega_trade=is_mega_trade,
                            is_high_conviction=is_high_conviction,
                            is_clinical_breakthrough=is_clinical_breakthrough,
                            is_auto_tp_eligible=is_auto_tp_eligible,
                        )
                        if is_mega_trade:
                            logger.info(
                                "MEGA TRADE: Auto-exits disabled — use /exit or /hold for manual control",
                                ticker=ticker,
                                entry_price=fill_price,
                                shares=shares,
                                conviction=conviction.value,
                            )
                        elif awaiting_confirmation:
                            logger.info(
                                "📊 Position added (AWAITING SCALE-IN CONFIRMATION)",
                                ticker=ticker,
                                entry_price=fill_price,
                                shares=shares,
                                target_full_shares=target_full_shares,
                                scale_in_shares=target_full_shares - shares,
                                conviction=conviction.value,
                            )
                        else:
                            stop_pct = 0.12 if (is_high_conviction or is_clinical_breakthrough) else 0.05
                            logger.info(
                                "Position added from TradeExecuted event (stop loss + let winners run)",
                                ticker=ticker,
                                entry_price=fill_price,
                                shares=shares,
                                conviction=conviction.value,
                                is_high_conviction=is_high_conviction,
                                stop_loss_pct=f"{stop_pct*100:.0f}%",
                                stop_loss_price=round(fill_price * (1 - stop_pct), 4) if fill_price else None,
                            )

            # Track SELL trades (exits) - unregister position and start cooldown
            elif action.upper() == "SELL" and event_data.get("success") and ticker:
                # Extract profit info from metadata for dynamic cooldown
                metadata = event_data.get("metadata", {}) or {}
                profit_pct = metadata.get("profit_pct")
                was_profitable = profit_pct is not None and profit_pct > 0
                unregister_active_position(ticker, was_profitable=was_profitable)

                # CRITICAL: Also remove from PositionManager to prevent ghost exits.
                # Without this, ExitTradeUseCase exits leave stale positions in
                # PositionManager, which then triggers force-exit-before-session-end
                # or other exit logic hours later — creating accidental shorts.
                await position_manager.remove_position(ticker)

                logger.info(
                    "Position unregistered from TradeExecuted SELL (dynamic cooldown started)",
                    ticker=ticker,
                    was_profitable=was_profitable,
                    profit_pct=f"{profit_pct*100:.1f}%" if profit_pct else "unknown",
                )

                # Record outcome for ticker blacklist (auto-blacklist after 3 FPs)
                from .brokerage.ticker_blacklist import record_trade_outcome
                try:
                    newly_blacklisted = await record_trade_outcome(ticker, was_profitable)
                    if newly_blacklisted:
                        logger.warning(f"Ticker {ticker} has been auto-blacklisted after 3 consecutive FPs")
                except Exception as bl_error:
                    logger.debug(f"Error recording blacklist outcome: {bl_error}")

                # Record outcome for sector tracking
                from .brokerage.sector_tracker import record_sector_outcome
                try:
                    # Get sector from metadata cache if available
                    sector = None
                    if brokerage.metadata_cache:
                        ticker_meta = await brokerage.metadata_cache.get_permanent(ticker)
                        if ticker_meta:
                            sector = ticker_meta.get("sector")
                    await record_sector_outcome(sector, was_profitable)
                except Exception as st_error:
                    logger.debug(f"Error recording sector outcome: {st_error}")

        except Exception as e:
            logger.error(f"Error handling TradeExecuted for PositionManager: {e}", exc_info=True)

    event_bus.subscribe("TradeExecuted", _on_trade_executed)
    await position_manager.start()
    logger.info("PositionManager created and started (5% stop loss, let winners run)")
    
    # Give position_manager access to exit_trade_use_case so it can cancel
    # scheduled 10-min exits when position exits early (stop loss, breakeven, etc.)
    position_manager.exit_trade_use_case = exit_trade_use_case

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
    
    # Build RetrospectiveClassifier — used by PriceMonitor after the 10-min
    # hold to run triage + HC/sector classification on articles whose mid
    # excursion was >=10% but were rejected by prefilter (false-negative capture).
    from ..shared.statistics.headline_classifier import get_headline_classifier
    from ..shared.statistics.retrospective_classifier import RetrospectiveClassifier
    retrospective_classifier = RetrospectiveClassifier(
        headline_classifier=get_headline_classifier(),
        sector_classifier=classification.infra.sector_classifier,
    )
    logger.info("RetrospectiveClassifier wired (triage + HC bypass + sector)")

    # Create recall engine via DI container factory (quote_fetcher, yahoo_finance_coordinator, market_data_client, trading_client passed as dependencies)
    # Note: yahoo_finance_coordinator is already started above (shared with MarketDataValidator)
    recall_engine = container.recall_stats_engine(
        quote_fetcher=brokerage.quote_fetcher,  # Use property (not .infra directly)
        yahoo_finance_coordinator=yahoo_finance_coordinator,  # Use already-started shared singleton
        market_data_client=brokerage.infra.connection_manager.market_data_client if brokerage else None,
        trading_client=brokerage.infra.connection_manager.trading_client if brokerage else None,
        metadata_cache=metadata_cache,  # For float-normalized volume calculations
        retrospective_classifier=retrospective_classifier,
    )
    await recall_engine.start()
    logger.info("RecallStatsEngine started - tracking missed opportunities (with volume stats, float normalization)")

    # Set recall engine reference in auto_trade for recording post-AI skips
    from .brokerage.auto_trade import set_recall_engine
    set_recall_engine(recall_engine)

    # Create signal engine via DI container factory (yahoo_finance_coordinator, quote_fetcher, trading_client)
    # Note: yahoo_finance_coordinator is already started above (shared with MarketDataValidator)
    signal_engine = container.signal_stats_engine(
        yahoo_finance_coordinator=yahoo_finance_coordinator,  # Use already-started shared singleton
        quote_fetcher=brokerage.quote_fetcher if brokerage else None,
        trading_client=brokerage.infra.connection_manager.trading_client if brokerage else None,
        metadata_cache=metadata_cache,  # For float-normalized volume calculations
    )
    await signal_engine.start()
    logger.info("SignalStatsEngine started - tracking trade executions (with float normalization)")
    
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

