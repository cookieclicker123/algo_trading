"""
Recall statistics engine - tracks all articles with tradable tickers.
Event-driven, stateless, runs alongside main trading system.

REFACTORED: Core engine now orchestrates specialized modules:
- TradeTrigger: Trade execution on surge detection
- SurgeMonitor: 2-minute surge detection monitoring
- PriceMonitor: 10-minute price tracking
- RecordManager: Metadata and record updates
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Set

try:
    from alpaca.data import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient
except ImportError:
    StockHistoricalDataClient = None
    TradingClient = None

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType, InfrastructureEventType
from ...shared.statistics.models import RecallRecord
from ...shared.statistics.volume_analyzer import analyze_volume_around_event
from ...infra.statistics.repository import StatisticsRepository
from ...infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
from ...utils.brokerage.session_detector import get_market_session
from .yahoo_finance_coordinator import YahooFinanceCoordinator
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.brokerage.events import TradeExecutedDomainEvent, TradeFailedDomainEvent
from ...domain.brokerage.models import MarketSession
from ...infra.classification.infrastructure_models import ClassificationSkippedInfrastructureEvent
from ...services.brokerage.auto_trade import check_confluence_signals

# Extracted modules
from .trade_trigger import TradeTrigger
from .surge_monitor import SurgeMonitor
from .price_monitor import PriceMonitor
from .record_manager import RecordManager
logger = get_logger(__name__)


class RecallStatsEngine:
    """
    Recall statistics engine - tracks missed trading opportunities.

    Orchestrates specialized modules for:
    - Trade execution (TradeTrigger)
    - Surge detection (SurgeMonitor)
    - Price tracking (PriceMonitor)
    - Record management (RecordManager)

    Stateless: All state in repository (files), no in-memory storage.
    """

    def __init__(
        self,
        event_bus: AsyncEventBus,
        repository: StatisticsRepository,
        quote_fetcher: AlpacaQuoteFetcher,
        yahoo_finance_coordinator: YahooFinanceCoordinator,
        market_data_client: Optional["StockHistoricalDataClient"] = None,
        trading_client: Optional["TradingClient"] = None,
        metadata_cache: Optional[Any] = None,  # MetadataCache for float shares
        retrospective_classifier: Optional[Any] = None,  # RetrospectiveClassifier
    ):
        """Initialize recall statistics engine with all dependencies."""
        self.event_bus = event_bus
        self.repository = repository
        self.quote_fetcher = quote_fetcher
        self.yahoo_finance_coordinator = yahoo_finance_coordinator
        self.market_data_client = market_data_client
        self.trading_client = trading_client
        self.metadata_cache = metadata_cache
        self.retrospective_classifier = retrospective_classifier

        # Shared state
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_lock = asyncio.Lock()
        self._traded_articles: Set[str] = set()
        self._traded_lock = asyncio.Lock()
        # Timestamps for TTL eviction of _traded_articles (prevents unbounded growth)
        self._traded_article_times: Dict[str, datetime] = {}
        self._TRADED_ARTICLES_TTL = timedelta(hours=2)

        # Initialize modules with shared state
        self.trade_trigger = TradeTrigger(
            event_bus=event_bus,
            quote_fetcher=quote_fetcher,
            traded_articles=self._traded_articles,
            traded_lock=self._traded_lock
        )

        self.surge_monitor = SurgeMonitor(
            market_data_client=market_data_client,
            quote_fetcher=quote_fetcher,
            metadata_fetcher=yahoo_finance_coordinator,
            repository=repository,
            traded_articles=self._traded_articles,
            traded_lock=self._traded_lock,
            monitoring_tasks=self._monitoring_tasks,
            monitoring_lock=self._monitoring_lock,
            on_surge_detected=self._on_surge_detected,
            on_monitoring_complete=self._on_monitoring_complete,
            metadata_cache=metadata_cache
        )

        self.price_monitor = PriceMonitor(
            market_data_client=market_data_client,
            quote_fetcher=quote_fetcher,
            repository=repository,
            monitoring_tasks=self._monitoring_tasks,
            monitoring_lock=self._monitoring_lock,
            retrospective_classifier=retrospective_classifier,
        )

        self.record_manager = RecordManager(
            repository=repository,
            quote_fetcher=quote_fetcher,
            metadata_fetcher=yahoo_finance_coordinator,
            trading_client=trading_client,
            metadata_cache=metadata_cache
        )

        # Event subscription wrappers
        self._article_received_wrapper: Optional[Any] = None
        self._article_classified_wrapper: Optional[Any] = None
        self._trade_executed_wrapper: Optional[Any] = None
        self._trade_failed_wrapper: Optional[Any] = None
        self._classification_skipped_wrapper: Optional[Any] = None

        logger.info("RecallStatsEngine initialized with modules")

    async def start(self) -> None:
        """Start engine - subscribe to events."""
        if self._article_received_wrapper:
            logger.warning("RecallStatsEngine already started")
            return

        # Subscribe to domain events (subscribe_typed is synchronous, returns wrapper)
        self._article_received_wrapper = subscribe_typed(
            self.event_bus, DomainEventType.ARTICLE_RECEIVED,
            ArticleReceivedDomainEvent, self._handle_article_received
        )
        self._article_classified_wrapper = subscribe_typed(
            self.event_bus, DomainEventType.ARTICLE_CLASSIFIED,
            ArticleClassifiedDomainEvent, self._handle_article_classified
        )
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus, DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent, self._handle_trade_executed
        )
        self._trade_failed_wrapper = subscribe_typed(
            self.event_bus, DomainEventType.TRADE_FAILED,
            TradeFailedDomainEvent, self._handle_trade_failed
        )
        self._classification_skipped_wrapper = self.event_bus.subscribe(
            InfrastructureEventType.CLASSIFICATION_SKIPPED,
            self._handle_classification_skipped
        )

        # Start record manager finalization loop
        await self.record_manager.start_finalization_loop()

        logger.info("RecallStatsEngine started")

    async def stop(self) -> None:
        """Stop engine - unsubscribe from events."""
        if self._article_received_wrapper:
            self.event_bus.unsubscribe(DomainEventType.ARTICLE_RECEIVED, self._article_received_wrapper)
        if self._article_classified_wrapper:
            self.event_bus.unsubscribe(DomainEventType.ARTICLE_CLASSIFIED, self._article_classified_wrapper)
        if self._trade_executed_wrapper:
            self.event_bus.unsubscribe(DomainEventType.TRADE_EXECUTED, self._trade_executed_wrapper)
        if self._trade_failed_wrapper:
            self.event_bus.unsubscribe(DomainEventType.TRADE_FAILED, self._trade_failed_wrapper)
        if self._classification_skipped_wrapper:
            self.event_bus.unsubscribe(InfrastructureEventType.CLASSIFICATION_SKIPPED, self._classification_skipped_wrapper)

        # Stop record manager
        await self.record_manager.stop_finalization_loop()

        # Cancel monitoring tasks
        async with self._monitoring_lock:
            for task in self._monitoring_tasks.values():
                task.cancel()
            self._monitoring_tasks.clear()

        self._article_received_wrapper = None
        logger.info("RecallStatsEngine stopped")

    async def _evict_stale_traded_articles(self) -> None:
        """Evict traded article IDs older than TTL to prevent unbounded set growth."""
        now = datetime.now()
        async with self._traded_lock:
            stale = [aid for aid, t in self._traded_article_times.items()
                     if now - t > self._TRADED_ARTICLES_TTL]
            for aid in stale:
                self._traded_articles.discard(aid)
                self._traded_article_times.pop(aid, None)
        if stale:
            logger.info(
                "RecallEngine: Evicted stale traded article IDs",
                evicted=len(stale),
                remaining=len(self._traded_articles),
            )

    # ==================== Surge Detection Callback ====================

    async def _on_surge_detected(self, article: Any, ticker: str) -> None:
        """
        Callback when SurgeMonitor detects a surge.

        NOTE: SURGE-based trade triggering is DISABLED.
        Trading is now controlled by Healthcare LLM classification in ClassificationInfrastructureService.
        SURGE detection continues for data collection and analysis purposes.
        """
        logger.info(
            "SURGE detected (trade trigger disabled - LLM controls trading)",
            article_id=article.id,
            ticker=ticker
        )
        # DISABLED: await self.trade_trigger.trigger_trade(article, ticker)

    async def _on_monitoring_complete(self, tickers: list[str], article_id: str, was_traded: bool) -> None:
        """
        Callback when SurgeMonitor finishes 2-minute monitoring.

        Unsubscribes from WebSocket quotes to reduce event loop load.
        Position manager maintains its own subscriptions via reference counting,
        so active positions will continue receiving quotes.
        """
        if not self.quote_fetcher:
            return

        for ticker in tickers:
            try:
                await self.quote_fetcher.unsubscribe_symbol(ticker)
                logger.debug(
                    "Unsubscribed from ticker after monitoring complete",
                    ticker=ticker,
                    article_id=article_id,
                    was_traded=was_traded
                )
            except Exception as e:
                logger.debug(
                    "Error unsubscribing from ticker",
                    ticker=ticker,
                    error=str(e)
                )

        logger.info(
            "Cleaned up WebSocket subscriptions after monitoring",
            article_id=article_id,
            tickers=tickers,
            was_traded=was_traded
        )

    # ==================== Event Handlers ====================

    async def _handle_article_received(self, event: ArticleReceivedDomainEvent) -> None:
        """Handle Domain.ArticleReceived event."""
        try:
            article = event.article
            if not article.tickers:
                return

            session, _ = get_market_session()
            if session == "closed":
                return

            asyncio.create_task(
                self._check_and_monitor_ticker(article, session, event.received_at)
            )
        except Exception as e:
            logger.error("Error handling article received", error=str(e), exc_info=True)

    async def _handle_article_classified(self, event: ArticleClassifiedDomainEvent) -> None:
        """Handle Domain.ArticleClassified event."""
        try:
            article_id = event.article_id
            classification = event.result.classification.value

            filter_reason = None
            if classification.upper() != "IMMINENT":
                filter_reason = f"ai_classification:{classification}"

            await self.record_manager.update_classification(
                article_id, classification, filter_reason
            )

            # Store headline_type from triage (already set by classification service)
            if event.headline_type:
                await self.record_manager.update_headline_type(article_id, event.headline_type)
        except Exception as e:
            logger.error("Error handling classification", error=str(e), exc_info=True)

    async def _handle_trade_executed(self, event: TradeExecutedDomainEvent) -> None:
        """Handle Domain.TradeExecuted event."""
        try:
            async with self._traded_lock:
                self._traded_articles.add(event.article_id)
                self._traded_article_times[event.article_id] = datetime.now()

            # Periodic eviction of stale traded article IDs
            await self._evict_stale_traded_articles()

            await self.record_manager.update_trade_executed(
                article_id=event.article_id,
                ticker=event.result.ticker,
                execution_data={
                    "order_id": event.result.order_id,
                    "qty": event.result.qty,
                    "side": event.result.side,
                    "executed_at": event.executed_at.isoformat() if event.executed_at else None
                }
            )
        except Exception as e:
            logger.error("Error handling trade executed", error=str(e), exc_info=True)

    async def _handle_trade_failed(self, event: TradeFailedDomainEvent) -> None:
        """Handle Domain.TradeFailed event."""
        try:
            # Get article_id from event (now available on the event itself)
            article_id = event.article_id
            if not article_id:
                logger.warning("TradeFailed event missing article_id, skipping recall tracking")
                return

            await self.record_manager.update_trade_failed(
                article_id=article_id,
                ticker=event.trade_request.ticker,
                error=event.error
            )
        except Exception as e:
            logger.error("Error handling trade failed", error=str(e), exc_info=True)

    async def _handle_classification_skipped(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle Infrastructure.ClassificationSkipped event."""
        try:
            event = ClassificationSkippedInfrastructureEvent(**event_data)
            filter_reason = f"prefilter_{event.reason}"
            # article_id is nested inside request_data
            await self.record_manager.update_classification(
                event.request_data.article_id, None, filter_reason
            )
            # Store headline_type if available (post-prefilter skips have it)
            if event.headline_type:
                await self.record_manager.update_headline_type(
                    event.request_data.article_id, event.headline_type
                )
        except Exception as e:
            logger.error("Error handling classification skipped", error=str(e), exc_info=True)

    async def record_postfilter_skip(self, article_id: str, reason: str) -> bool:
        """
        Record why an IMMINENT article was skipped by post-AI checks.

        Call this from auto_trade.py when skipping an IMMINENT article due to:
        - postfilter_no_surge: Score 0 and no qualifying surge
        - postfilter_low_volume: Window volume < 2000
        - postfilter_spread_too_wide: Spread > 10%
        - postfilter_spread_no_improvement: 5-10% spread without compression
        - postfilter_ask_moved: Ask moved > 3%
        - postfilter_chase: Ask moved > 7% from reception
        - postfilter_zero_volume: Dead market
        - postfilter_active_position: Already holding ticker
        - postfilter_cooldown: Ticker in cooldown

        Returns:
            True if updated, False if failed
        """
        return await self.record_manager.update_postfilter_reason(article_id, reason)

    # ==================== Article Processing ====================

    async def _check_and_monitor_ticker(
        self,
        article: Any,
        session: str,
        received_at: datetime
    ) -> None:
        """Check if ticker is tradable and start monitoring."""
        try:
            # Check if already traded
            async with self._traded_lock:
                if article.id in self._traded_articles:
                    return

            # Register record location early
            self.record_manager.register_record_location(article.id, session, received_at)

            # Get tradable tickers with NBBO
            tradable_tickers, initial_nbbos = await self._get_tradable_tickers(article)
            if not tradable_tickers:
                return

            # Fetch sector metadata
            sector_by_ticker = await self._fetch_sectors(tradable_tickers)

            # Analyze initial volume
            volume_stats = await self._analyze_initial_volume(
                article, tradable_tickers, initial_nbbos, sector_by_ticker, received_at
            )

            # Double-check not traded during processing
            async with self._traded_lock:
                if article.id in self._traded_articles:
                    return

            # Check for initial surge
            has_surge, surge_ticker = self._check_initial_surge(volume_stats, article.id)

            # Determine primary ticker
            primary_ticker = surge_ticker if has_surge else (tradable_tickers[0] if tradable_tickers else None)

            # Create and save record
            await self._create_and_save_record(
                article, tradable_tickers, session, received_at,
                initial_nbbos, volume_stats, primary_ticker, has_surge, surge_ticker
            )

            # Start monitoring tasks
            await self._start_monitoring_tasks(
                article, tradable_tickers, initial_nbbos, session,
                received_at, has_surge, surge_ticker, primary_ticker
            )

        except Exception as e:
            logger.error("Error checking ticker", article_id=article.id, error=str(e), exc_info=True)

    async def _get_tradable_tickers(self, article: Any) -> tuple[list[str], Dict[str, Dict]]:
        """Get tradable tickers with NBBO data."""
        tradable_tickers = []
        initial_nbbos = {}

        # Filter candidates
        raw_tickers = set(article.tickers)
        candidates = [
            t for t in article.tickers
            if not (t.endswith('W') and len(t) > 1 and t[:-1] in raw_tickers)
        ]

        # Filter non-US exchanges
        us_candidates = [
            t for t in candidates
            if not any(t.startswith(p) for p in ["TSX:", "TSXV:", "CSE:", "NEO:", "CBOE:"])
        ]

        # Fetch NBBO in parallel
        async def check_nbbo(ticker: str):
            try:
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                return (ticker, nbbo)
            except Exception:
                return (ticker, None)

        results = await asyncio.gather(*[check_nbbo(t) for t in us_candidates], return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                continue
            ticker, nbbo = result
            if nbbo:
                # No price filtering - let ML decide on penny stocks
                tradable_tickers.append(ticker)
                initial_nbbos[ticker] = nbbo

        return tradable_tickers, initial_nbbos

    async def _fetch_sectors(self, tickers: list[str]) -> Dict[str, Optional[str]]:
        """Fetch sector metadata for tickers."""
        sector_by_ticker = {}

        async def fetch_sector(ticker: str):
            try:
                meta = await self.yahoo_finance_coordinator.fetch_metadata(
                    ticker, timeout=2.0, queue_on_failure=True
                )
                return (ticker, meta.get("sector") if meta else None)
            except Exception:
                return (ticker, None)

        results = await asyncio.gather(*[fetch_sector(t) for t in tickers], return_exceptions=True)
        for result in results:
            if not isinstance(result, Exception):
                ticker, sector = result
                sector_by_ticker[ticker] = sector

        return sector_by_ticker

    async def _analyze_initial_volume(
        self,
        article: Any,
        tickers: list[str],
        initial_nbbos: Dict,
        sector_by_ticker: Dict,
        received_at: datetime
    ) -> Dict[str, Dict]:
        """Analyze initial volume for all tickers."""
        volume_stats = {}

        if not self.market_data_client or not article.published_at:
            return volume_stats

        async def analyze_ticker(ticker: str):
            try:
                # Get cached float_shares (instant, ~0ms) instead of calling yfinance
                cached_float = None
                if self.metadata_cache:
                    try:
                        cached_float = await self.metadata_cache.get_float(ticker)
                    except Exception:
                        pass

                analysis = await analyze_volume_around_event(
                    client=self.market_data_client,
                    symbol=ticker,
                    event_time=article.published_at,
                    received_at=received_at,
                    reference_nbbo=initial_nbbos.get(ticker),
                    stream_manager=self.quote_fetcher.stream_manager if self.quote_fetcher else None,
                    sector=sector_by_ticker.get(ticker),
                    float_shares=cached_float
                )
                return (ticker, analysis.to_dict() if analysis else {"error": "No analysis"})
            except Exception as e:
                return (ticker, {"error": str(e)})

        results = await asyncio.gather(*[analyze_ticker(t) for t in tickers], return_exceptions=True)
        for result in results:
            if not isinstance(result, Exception):
                ticker, stats = result
                volume_stats[ticker] = stats

        return volume_stats

    def _check_initial_surge(self, volume_stats: Dict, article_id: str) -> tuple[bool, Optional[str]]:
        """Check if any ticker shows initial SURGE."""
        for ticker, stats in volume_stats.items():
            if isinstance(stats, dict) and "error" not in stats and stats.get("move_type") == "SURGE":
                logger.info(
                    "Initial SURGE detected",
                    article_id=article_id,
                    ticker=ticker,
                    move_type=stats.get("move_type")
                )
                return True, ticker
        return False, None

    async def _create_and_save_record(
        self,
        article: Any,
        tickers: list[str],
        session: str,
        received_at: datetime,
        initial_nbbos: Dict,
        volume_stats: Dict,
        primary_ticker: Optional[str],
        has_surge: bool,
        surge_ticker: Optional[str]
    ) -> None:
        """Create RecallRecord and save to repository."""
        session_enum_map = {
            "premarket": MarketSession.PREMARKET,
            "market_hours": MarketSession.MARKET,
            "postmarket": MarketSession.POSTMARKET
        }
        session_enum = session_enum_map.get(session, MarketSession.MARKET)

        # Calculate confluence window data (0-2 seconds) for the primary ticker
        # This uses the same logic as auto_trade.py for apples-to-apples comparison
        confluence_data = {}
        if primary_ticker and article.published_at and self.market_data_client and self.quote_fetcher:
            try:
                _, confluence_metadata = await check_confluence_signals(
                    ticker=primary_ticker,
                    publication_time=article.published_at,
                    market_data_client=self.market_data_client,
                    quote_fetcher=self.quote_fetcher
                )
                if confluence_metadata and confluence_metadata.get("confluence_checked"):
                    confluence_data = confluence_metadata
                    logger.debug(
                        "Confluence data captured for recall",
                        article_id=article.id,
                        ticker=primary_ticker,
                        confluence_score=confluence_metadata.get("confluence_score"),
                        confluence_imbalance_ratio=confluence_metadata.get("confluence_imbalance_ratio")
                    )
            except Exception as e:
                logger.warning(
                    "Failed to capture confluence data for recall",
                    article_id=article.id,
                    ticker=primary_ticker,
                    error=str(e)
                )

        # === GAP/TRAP DETECTION: Fetch pub_time_ask for false negative analysis ===
        # Critical for understanding: did price run away before we could act?
        pub_time_ask = None
        recv_time_ask = None
        pub_to_recv_pct = None
        pub_to_recv_latency_ms = None

        if primary_ticker and article.published_at and self.market_data_client:
            try:
                from alpaca.data.requests import StockQuotesRequest
                from alpaca.data.enums import DataFeed
                from datetime import timedelta
                from ...utils.async_alpaca import run_sync_alpaca_call

                # Get recv_time_ask from initial_nbbo
                initial_nbbo = initial_nbbos.get(primary_ticker) if primary_ticker else None
                recv_time_ask = initial_nbbo.get("ask") if initial_nbbo else None

                # Fetch pub_time_ask from historical API (same as auto_trade.py)
                pub_quotes = await run_sync_alpaca_call(
                    self.market_data_client.get_stock_quotes,
                    StockQuotesRequest(
                        symbol_or_symbols=primary_ticker,
                        start=article.published_at - timedelta(seconds=1),
                        end=article.published_at + timedelta(seconds=1),
                        feed=DataFeed.SIP
                    )
                )

                if pub_quotes and pub_quotes.data and primary_ticker in pub_quotes.data:
                    quotes_list = pub_quotes.data[primary_ticker]
                    if quotes_list:
                        pub_time_ask = quotes_list[-1].ask_price

                # Calculate pub_to_recv percentage change
                if pub_time_ask and recv_time_ask and pub_time_ask > 0:
                    pub_to_recv_pct = round(((recv_time_ask - pub_time_ask) / pub_time_ask) * 100, 2)

                # Calculate latency
                if article.published_at and received_at:
                    pub_to_recv_latency_ms = round((received_at - article.published_at).total_seconds() * 1000, 1)

                logger.debug(
                    "Gap detection data captured for recall",
                    article_id=article.id,
                    ticker=primary_ticker,
                    pub_time_ask=pub_time_ask,
                    recv_time_ask=recv_time_ask,
                    pub_to_recv_pct=pub_to_recv_pct,
                    pub_to_recv_latency_ms=pub_to_recv_latency_ms
                )
            except Exception as e:
                logger.debug(f"Could not fetch pub_time_ask for recall: {e}")

        # === FLOAT-NORMALIZED VOLUME (Phase 1: Data Collection) ===
        # Get float_shares from metadata_cache for float-normalized volume calculations
        float_shares = None
        confluence_volume_float_pct = None
        surge_volume_float_pct = None

        if primary_ticker and self.metadata_cache:
            try:
                float_shares = await self.metadata_cache.get_float(primary_ticker)
                if float_shares:
                    confluence_volume = confluence_data.get("confluence_volume")
                    if confluence_volume:
                        confluence_volume_float_pct = round((confluence_volume / float_shares) * 100, 4)

                    # Get surge volume from volume_stats if available
                    primary_volume_stats = volume_stats.get(primary_ticker, {}) if primary_ticker else {}
                    surge_volume = primary_volume_stats.get("surge_volume")
                    if surge_volume:
                        surge_volume_float_pct = round((surge_volume / float_shares) * 100, 4)
            except Exception as e:
                logger.debug(f"Could not get float_shares for {primary_ticker}: {e}")

        record = RecallRecord(
            article_id=article.id,
            title=article.title,
            tickers=tickers,
            session=session_enum,
            published_at=article.published_at,
            received_at=received_at,
            initial_nbbo=initial_nbbos.get(primary_ticker) if primary_ticker else None,
            volume_stats=volume_stats,
            # Confluence window data (0-2 seconds) - aligned with trade decision point
            confluence_score=confluence_data.get("confluence_score"),
            confluence_volume=confluence_data.get("confluence_volume"),
            confluence_trade_count=confluence_data.get("confluence_trade_count"),
            confluence_buy_volume=confluence_data.get("confluence_buy_volume"),
            confluence_sell_volume=confluence_data.get("confluence_sell_volume"),
            confluence_buying_pressure_pct=confluence_data.get("confluence_buying_pressure_pct"),
            confluence_imbalance_ratio=confluence_data.get("confluence_imbalance_ratio"),
            confluence_price_excursion_pct=confluence_data.get("confluence_price_excursion_pct"),
            confluence_first_price=confluence_data.get("confluence_first_price"),
            confluence_max_price=confluence_data.get("confluence_max_price"),
            confluence_min_price=confluence_data.get("confluence_min_price"),
            confluence_vwap=confluence_data.get("confluence_vwap"),
            confluence_initial_spread=confluence_data.get("confluence_initial_spread"),
            confluence_final_spread=confluence_data.get("confluence_final_spread"),
            confluence_spread_compression_pct=confluence_data.get("confluence_spread_compression_pct"),
            confluence_first_trade_latency_ms=confluence_data.get("confluence_first_trade_latency_ms"),
            confluence_avg_trade_size=confluence_data.get("confluence_avg_trade_size"),
            confluence_max_trade_gap_ms=confluence_data.get("confluence_max_trade_gap_ms"),
            confluence_has_volume_surge=confluence_data.get("confluence_has_volume_surge"),
            confluence_has_price_excursion=confluence_data.get("confluence_has_price_excursion"),
            confluence_has_buying_pressure=confluence_data.get("confluence_has_buying_pressure"),
            confluence_last_price=confluence_data.get("confluence_last_price"),
            confluence_price_direction=confluence_data.get("confluence_price_direction"),
            confluence_dollar_volume=confluence_data.get("confluence_dollar_volume"),
            confluence_max_single_trade=confluence_data.get("confluence_max_single_trade"),
            confluence_median_trade_size=confluence_data.get("confluence_median_trade_size"),
            confluence_large_trade_pct=confluence_data.get("confluence_large_trade_pct"),
            confluence_uptick_count=confluence_data.get("confluence_uptick_count"),
            confluence_downtick_count=confluence_data.get("confluence_downtick_count"),
            # === VOLUME DISTRIBUTION ANALYSIS (Manipulation Detection) ===
            single_trade_dominance_pct=confluence_data.get("single_trade_dominance_pct"),
            remaining_flow_imbalance=confluence_data.get("remaining_flow_imbalance"),
            remaining_trade_count=confluence_data.get("remaining_trade_count"),
            remaining_sell_pct=confluence_data.get("remaining_sell_pct"),
            volume_distribution_class=confluence_data.get("volume_distribution_class"),
            # Float-normalized volume (Phase 1: Data Collection)
            float_shares=float_shares,
            confluence_volume_float_pct=confluence_volume_float_pct,
            surge_volume_float_pct=surge_volume_float_pct,
            # Gap/trap detection fields - critical for false negative analysis
            pub_time_ask=pub_time_ask,
            recv_time_ask=recv_time_ask,
            pub_to_recv_pct=pub_to_recv_pct,
            pub_to_recv_latency_ms=pub_to_recv_latency_ms,
        )

        # Append record and apply any pending classifications
        await self.repository.append_recall_record(record, session, received_at)

        # Apply any pending classifications that arrived before record was created
        # This fixes the race condition where AI classifies before recall record exists
        await self.record_manager.apply_pending_updates(article.id, session, received_at)

        # If initial surge, update record with surge detection
        if has_surge and surge_ticker:
            surge_stats = volume_stats.get(surge_ticker, {})
            try:
                surge_nbbo = await self.quote_fetcher.get_nbbo_snapshot(surge_ticker)
                if surge_nbbo:
                    surge_stats = surge_stats.copy() if isinstance(surge_stats, dict) else {}
                    surge_stats["surge_bid"] = surge_nbbo.get("bid")
                    surge_stats["surge_ask"] = surge_nbbo.get("ask")
                    if surge_nbbo.get("bid") and surge_nbbo.get("ask"):
                        surge_stats["surge_spread"] = surge_nbbo["ask"] - surge_nbbo["bid"]
            except Exception:
                pass

            asyncio.create_task(self.repository.update_recall_record(
                article_id=article.id,
                updates={
                    "monitoring_status": "surge_detected",
                    "surge_detected_at": datetime.now(),
                    "surge_detection_cycle": 0,
                    "surge_detection_window_stats": surge_stats,
                    "monitoring_completed_at": datetime.now()
                },
                session=session,
                date=received_at
            ))

    async def _start_monitoring_tasks(
        self,
        article: Any,
        tickers: list[str],
        initial_nbbos: Dict,
        session: str,
        received_at: datetime,
        has_surge: bool,
        surge_ticker: Optional[str],
        primary_ticker: Optional[str]
    ) -> None:
        """Start surge monitoring, price monitoring, and metadata tasks."""
        if has_surge:
            # NOTE: SURGE-based trade triggering is DISABLED
            # Trading is now controlled by Healthcare LLM classification
            logger.info(
                "Initial SURGE detected (trade trigger disabled - LLM controls trading)",
                article_id=article.id,
                ticker=surge_ticker
            )
        else:
            # Start surge monitoring
            monitoring_task = asyncio.create_task(
                self.surge_monitor.monitor_for_surge(
                    article, tickers, initial_nbbos, session, received_at, article.published_at
                )
            )
            async with self._monitoring_lock:
                self._monitoring_tasks[article.id] = monitoring_task

            asyncio.create_task(self.repository.update_recall_record(
                article_id=article.id,
                updates={"monitoring_status": "initiated", "monitoring_initiated_at": datetime.now()},
                session=session,
                date=received_at
            ))

        # Start price monitoring
        asyncio.create_task(
            self.price_monitor.monitor_price(
                article.id, [primary_ticker] if primary_ticker else [],
                initial_nbbos, session, received_at, article.published_at
            )
        )

        # Start metadata fetch
        asyncio.create_task(
            self.record_manager.fetch_and_update_metadata(article.id, tickers, session, received_at)
        )
