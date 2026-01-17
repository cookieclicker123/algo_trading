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
from datetime import datetime
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
        trading_client: Optional["TradingClient"] = None
    ):
        """Initialize recall statistics engine with all dependencies."""
        self.event_bus = event_bus
        self.repository = repository
        self.quote_fetcher = quote_fetcher
        self.yahoo_finance_coordinator = yahoo_finance_coordinator
        self.market_data_client = market_data_client
        self.trading_client = trading_client

        # Shared state
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_lock = asyncio.Lock()
        self._traded_articles: Set[str] = set()
        self._traded_lock = asyncio.Lock()

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
            on_surge_detected=self._on_surge_detected
        )

        self.price_monitor = PriceMonitor(
            market_data_client=market_data_client,
            quote_fetcher=quote_fetcher,
            repository=repository,
            monitoring_tasks=self._monitoring_tasks,
            monitoring_lock=self._monitoring_lock
        )

        self.record_manager = RecordManager(
            repository=repository,
            quote_fetcher=quote_fetcher,
            metadata_fetcher=yahoo_finance_coordinator,
            trading_client=trading_client
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
            if classification != "IMMINENT":
                filter_reason = f"ai_classification:{classification}"

            await self.record_manager.update_classification(
                article_id, classification, filter_reason
            )
        except Exception as e:
            logger.error("Error handling classification", error=str(e), exc_info=True)

    async def _handle_trade_executed(self, event: TradeExecutedDomainEvent) -> None:
        """Handle Domain.TradeExecuted event."""
        try:
            async with self._traded_lock:
                self._traded_articles.add(event.article_id)

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
            await self.record_manager.update_trade_failed(
                article_id=event.article_id,
                ticker=event.ticker,
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
        except Exception as e:
            logger.error("Error handling classification skipped", error=str(e), exc_info=True)

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
                analysis = await analyze_volume_around_event(
                    client=self.market_data_client,
                    symbol=ticker,
                    event_time=article.published_at,
                    received_at=received_at,
                    reference_nbbo=initial_nbbos.get(ticker),
                    stream_manager=self.quote_fetcher.stream_manager if self.quote_fetcher else None,
                    sector=sector_by_ticker.get(ticker)
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

        record = RecallRecord(
            article_id=article.id,
            title=article.title,
            tickers=tickers,
            session=session_enum,
            published_at=article.published_at,
            received_at=received_at,
            initial_nbbo=initial_nbbos.get(primary_ticker) if primary_ticker else None,
            volume_stats=volume_stats
        )

        # Append record (fire-and-forget)
        asyncio.create_task(self.repository.append_recall_record(record, session, received_at))

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
            # DISABLED: asyncio.create_task(self.trade_trigger.trigger_trade(article, surge_ticker))
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
