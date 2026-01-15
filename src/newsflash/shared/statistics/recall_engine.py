"""
Recall statistics engine - tracks all articles with tradable tickers.
Event-driven, stateless, runs alongside main trading system.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Set

try:
    from alpaca.data import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockTradesRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    from alpaca.trading.client import TradingClient
except ImportError:
    StockHistoricalDataClient = None
    TradingClient = None
    StockBarsRequest = None
    StockTradesRequest = None
    TimeFrame = None
    DataFeed = None

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType, InfrastructureEventType
from ...shared.statistics.models import RecallRecord
from ...shared.statistics.volume_analyzer import analyze_volume_around_event
from ...infra.statistics.repository import StatisticsRepository
from ...infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
from ...utils.brokerage.session_detector import get_market_session, get_market_session_from_timestamp
from .yahoo_finance_coordinator import YahooFinanceCoordinator
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.brokerage.events import TradeExecutedDomainEvent, TradeFailedDomainEvent, TradeRequestDomainEvent
from ...domain.brokerage.models import MarketSession
from ...infra.classification.infrastructure_models import ClassificationSkippedInfrastructureEvent
from ...services.brokerage.auto_trade import build_trade_request_for_article
from ...shared.event_types import DomainEventType

logger = get_logger(__name__)


class RecallStatsEngine:
    """
    Recall statistics engine - tracks missed trading opportunities.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events
    - Check if article has tradable tickers (NBBO available + in trading session)
    - Monitor ticker price for 10 minutes after article received
    - Record missed opportunities (Four-Pillar Surge moves we didn't trade)
    - Append records to JSON files in real-time
    
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
        """
        Initialize recall statistics engine.

        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
            quote_fetcher: Quote fetcher for NBBO snapshots
            yahoo_finance_coordinator: Shared Yahoo Finance coordinator (for industry/sector/market_cap)
            market_data_client: Optional Alpaca market data client for volume stats
            trading_client: Optional Alpaca trading client for exchange info
        """
        self.event_bus = event_bus
        self.repository = repository
        self.quote_fetcher = quote_fetcher
        self.yahoo_finance_coordinator = yahoo_finance_coordinator
        self.market_data_client = market_data_client
        self.trading_client = trading_client
        
        # Track active monitoring tasks (article_id -> task)
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_lock = asyncio.Lock()
        
        # Track which articles were traded (to exclude from recall)
        self._traded_articles: Set[str] = set()
        self._traded_lock = asyncio.Lock()
        
        # Track pending metadata fetches (article_id -> (tickers, session, received_at, task))
        self._pending_metadata: Dict[str, tuple[list[str], str, datetime, asyncio.Task]] = {}
        self._metadata_lock = asyncio.Lock()
        
        # Track pending filter_reason updates (article_id -> filter_reason string)
        # SINGULAR: one reason per article
        self._pending_filter_reasons: Dict[str, str] = {}
        self._filter_reasons_lock = asyncio.Lock()
        
        # Track pending ai_classification updates (article_id -> classification value)
        self._pending_classifications: Dict[str, str] = {}
        self._classification_lock = asyncio.Lock()
        
        # Track wrapper for unsubscribe
        self._article_received_wrapper: Optional[Any] = None
        self._article_classified_wrapper: Optional[Any] = None
        self._trade_executed_wrapper: Optional[Any] = None
        self._trade_failed_wrapper: Optional[Any] = None
        self._classification_skipped_wrapper: Optional[Any] = None
        
        # Track record locations (article_id -> (session, date))
        # This prevents "record not found" errors when updates happen across session boundaries
        # (e.g., created in premarket, updated in market_hours because event timestamp drifted)
        self._record_locations: Dict[str, tuple[str, datetime]] = {}
        
        # Background task for finalization (ensures metadata is populated)
        self._finalization_task: Optional[asyncio.Task] = None
        
        logger.info("RecallStatsEngine initialized")
    
    async def start(self) -> None:
        """Start engine - subscribe to events."""
        if self._article_received_wrapper:
            logger.debug("RecallStatsEngine already started")
            return

        # Subscribe to typed events using subscribe_typed helper
        self._article_received_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_RECEIVED,
            ArticleReceivedDomainEvent,
            self._handle_article_received,
        )
        
        self._article_classified_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_CLASSIFIED,
            ArticleClassifiedDomainEvent,
            self._handle_article_classified,
        )
        
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent,
            self._handle_trade_executed,
        )
        
        # Subscribe to TradeFailed events (to exclude failed trades from recall)
        self._trade_failed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_FAILED,
            TradeFailedDomainEvent,
            self._handle_trade_failed,
        )
        
        # Subscribe to ClassificationSkipped infrastructure events (prefilter reasons)
        self._classification_skipped_wrapper = subscribe_typed(
            self.event_bus,
            InfrastructureEventType.CLASSIFICATION_SKIPPED,
            ClassificationSkippedInfrastructureEvent,
            self._handle_classification_skipped,
        )
        
        # Start finalization task (runs every 5 minutes to ensure metadata is populated)
        self._finalization_task = asyncio.create_task(self._finalization_loop())
        
        # Ensure coordinator is started (may already be started by MarketDataValidator)
        # YahooFinanceCoordinator has _worker_task for compatibility, but it's optional
        # YahooFinanceCoordinator - just call start (no worker_task check needed)
        await self.yahoo_finance_coordinator.start()
        
        logger.info("RecallStatsEngine started - subscribed to events")
    
    async def stop(self) -> None:
        """Stop engine - cancel monitoring tasks and finalize metadata."""
        # Unsubscribe from events
        if self._article_received_wrapper:
            self.event_bus.unsubscribe(DomainEventType.ARTICLE_RECEIVED, self._article_received_wrapper)
            self._article_received_wrapper = None
            
        if self._article_classified_wrapper:
            self.event_bus.unsubscribe(DomainEventType.ARTICLE_CLASSIFIED, self._article_classified_wrapper)
            self._article_classified_wrapper = None
            
        if self._trade_executed_wrapper:
            self.event_bus.unsubscribe(DomainEventType.TRADE_EXECUTED, self._trade_executed_wrapper)
            self._trade_executed_wrapper = None
            
        if self._trade_failed_wrapper:
            self.event_bus.unsubscribe(DomainEventType.TRADE_FAILED, self._trade_failed_wrapper)
            self._trade_failed_wrapper = None
            
        if self._classification_skipped_wrapper:
            self.event_bus.unsubscribe(InfrastructureEventType.CLASSIFICATION_SKIPPED, self._classification_skipped_wrapper)
            self._classification_skipped_wrapper = None

        # Cancel finalization task
        if self._finalization_task:
            self._finalization_task.cancel()
            try:
                await self._finalization_task
            except asyncio.CancelledError:
                pass
            self._finalization_task = None
        
        # Finalize all pending metadata before stopping
        await self._finalize_all_metadata()
        
        # Stop Finnhub coordinator
        await self.yahoo_finance_coordinator.stop()
        
        # Cancel monitoring tasks
        async with self._monitoring_lock:
            for task in self._monitoring_tasks.values():
                task.cancel()
            self._monitoring_tasks.clear()
        
        logger.info("RecallStatsEngine stopped")
    
    async def _handle_article_received(
        self,
        event: ArticleReceivedDomainEvent,
    ) -> None:
        """Handle Domain.ArticleReceived event."""
        try:
            article = event.article
            
            # Skip if no tickers
            if not article.tickers:
                return
            
            # Check current session
            session, is_extended = get_market_session()
            if session == "closed":
                return  # Don't track closed market
            
            # Fire and forget: Check if ticker is tradable and start monitoring
            asyncio.create_task(
                self._check_and_monitor_ticker(article, session, event.received_at)
            )
            
        except Exception as e:
            logger.error(
                "Error handling article received for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _check_and_monitor_ticker(
        self,
        article: Any,  # Domain Article model
        session: str,
        received_at: datetime
    ) -> None:
        """
        Check if ticker is tradable and start 5-minute monitoring.
        
        Steps:
        1. Check if article was already traded (skip if yes)
        2. For each ticker, check NBBO availability
        3. If tradable, create RecallRecord and start monitoring task
        4. Append record immediately (with initial NBBO)
        """
        try:
            # Check if already traded
            async with self._traded_lock:
                if article.id in self._traded_articles:
                    return  # Skip - we traded this
            
            # TRACK LOCATION EARLY: Prevent race condition with ClassificationSkipped
            # Store where we INTEND to put this record (if created)
            self._record_locations[article.id] = (session, received_at)
            
            # Check each ticker for tradability
            tradable_tickers = []
            initial_nbbos = {}
            
            raw_tickers = set(article.tickers)
            candidates = []
            for t in article.tickers:
                # User Rule: If T and T+'W' exist, ignore T+'W'.
                if t.endswith('W') and len(t) > 1 and t[:-1] in raw_tickers:
                    continue
                candidates.append(t)

            # Filter out non-US exchanges first (before parallelization)
            us_candidates = [
                t for t in candidates
                if not any(t.startswith(prefix) for prefix in ["TSX:", "TSXV:", "CSE:", "NEO:", "CBOE:"])
            ]
            
            # PARALLELIZE NBBO checks for all tickers simultaneously
            # This reduces latency from ~0.1s per ticker to ~0.1s total (for all tickers)
            async def check_ticker_nbbo(ticker: str) -> tuple[str, Optional[Dict[str, Any]]]:
                """Check NBBO for a single ticker, return (ticker, nbbo_dict or None)."""
                try:
                    nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                    return (ticker, nbbo)
                except Exception as e:
                    logger.debug(
                        "Error checking NBBO for ticker",
                        article_id=article.id,
                        ticker=ticker,
                        error=str(e)
                    )
                    return (ticker, None)
            
            # Fetch NBBO for all candidates in parallel
            nbbo_tasks = [check_ticker_nbbo(t) for t in us_candidates]
            nbbo_results = await asyncio.gather(*nbbo_tasks, return_exceptions=True)
            
            # Process results
            for result in nbbo_results:
                if isinstance(result, Exception):
                    logger.debug(
                        "NBBO check task failed",
                        article_id=article.id,
                        error=str(result)
                    )
                    continue
                
                ticker, nbbo = result
                
                if nbbo:
                    # CRITICAL: 10 cent minimum stock price filter
                    # Reject stocks below $0.10 to avoid bad trades from low-priced, illiquid stocks
                    mid_price = nbbo.get("mid")
                    if mid_price and mid_price < 0.10:
                        logger.info(
                            "⏭️ RECALL: Skipping ticker - stock price below $0.10 minimum",
                            article_id=article.id,
                            ticker=ticker,
                            price=mid_price
                        )
                        continue  # Skip this ticker
                    
                    # Ticker is tradable (has NBBO in current session) and meets minimum price
                    tradable_tickers.append(ticker)
                    initial_nbbos[ticker] = nbbo
            
            # If no tradable tickers, skip
            if not tradable_tickers:
                logger.debug(
                    "Recall: No tradable tickers for article",
                    article_id=article.id,
                    tickers=list(article.tickers)
                )
                return
            
            # Map session string to MarketSession enum
            session_enum_map = {
                "premarket": MarketSession.PREMARKET,
                "market_hours": MarketSession.MARKET,
                "postmarket": MarketSession.POSTMARKET
            }
            session_enum = session_enum_map.get(session, MarketSession.MARKET)
            
            # Fetch sector metadata for surge detection (all sectors treated equally now)
            # Try quick fetch (2 second timeout) - if fails, default to strict requirements (safe)
            # Fetch in parallel for all tickers to avoid blocking
            sector_by_ticker = {}
            async def fetch_sector_quick(t: str) -> tuple[str, Optional[str]]:
                """Quick sector fetch with short timeout - non-blocking with queue on failure."""
                try:
                    # Try quick fetch (2 second timeout - don't block)
                    # Use queue_on_failure=True to ensure metadata is eventually populated
                    ticker_meta = await self.yahoo_finance_coordinator.fetch_metadata(
                        t,
                        timeout=2.0,
                        queue_on_failure=True  # Queue for background retry if fails
                    )
                    if ticker_meta and ticker_meta.get("sector"):
                        return (t, ticker_meta.get("sector"))
                except (asyncio.TimeoutError, Exception):
                    # If fetch fails or times out, default to None (strict requirements)
                    # This is safe - we'll use strict requirements if sector unavailable
                    # Metadata will be queued for background retry
                    pass
                return (t, None)
            
            # Fetch sectors in parallel (non-blocking, quick timeout)
            # Don't wait for these - proceed with volume analysis even if sector fetch fails
            sector_tasks = [fetch_sector_quick(t) for t in tradable_tickers]
            sector_results = await asyncio.gather(*sector_tasks, return_exceptions=True)
            for result in sector_results:
                if isinstance(result, Exception):
                    continue
                ticker, sector = result
                sector_by_ticker[ticker] = sector
            
            # Fetch volume stats for ALL tradable tickers (NO FILTERING - just data collection)
            # CRITICAL: Run volume analysis in parallel to avoid delays (ASBP issue)
            # Pass sector information for preferred sector exceptions
            volume_stats_by_ticker = {}
            if self.market_data_client and article.published_at and tradable_tickers:
                async def fetch_volume_for_ticker(t: str) -> tuple[str, dict]:
                    """Fetch volume stats for a single ticker, return (ticker, stats_dict)."""
                    try:
                        # Get sector for this ticker (if available)
                        ticker_sector = sector_by_ticker.get(t)
                        
                        volume_analysis = await analyze_volume_around_event(
                            client=self.market_data_client,
                            symbol=t,
                            event_time=article.published_at,
                            received_at=received_at,
                            reference_nbbo=initial_nbbos.get(t),
                            stream_manager=self.quote_fetcher.stream_manager if self.quote_fetcher else None,
                            sector=ticker_sector  # Pass sector for surge detection
                        )
                        if volume_analysis:
                            return (t, volume_analysis.to_dict())
                        else:
                            return (t, {"error": "No volume analysis returned"})
                    except Exception as vol_error:
                        error_type = type(vol_error).__name__
                        error_msg = str(vol_error)
                        is_illiquidity = ("no data" in error_msg.lower() or "not found" in error_msg.lower() or "no bars" in error_msg.lower() or error_type == "ValueError")
                        
                        logger.warning("Recall: Failed to fetch volume stats", article_id=article.id, ticker=t, error=error_msg, likely_illiquidity=is_illiquidity)
                        
                        return (t, {
                            "error": error_msg,
                            "error_type": error_type,
                            "likely_illiquidity": is_illiquidity
                        })
                
                # Run all volume analyses in parallel for speed
                volume_tasks = [fetch_volume_for_ticker(t) for t in tradable_tickers]
                volume_results = await asyncio.gather(*volume_tasks, return_exceptions=True)
                
                for result in volume_results:
                    if isinstance(result, Exception):
                        logger.error("Volume analysis task failed", article_id=article.id, error=str(result))
                        continue
                    ticker, stats = result
                    volume_stats_by_ticker[ticker] = stats
                    if isinstance(stats, dict) and "error" not in stats:
                        logger.debug("Recall: Fetched volume stats", article_id=article.id, ticker=ticker, move_type=stats.get("move_type"))
            
            # DOUBLE-CHECK: Make sure article wasn't traded between initial check and now (race condition protection)
            async with self._traded_lock:
                if article.id in self._traded_articles:
                    logger.debug(
                        "Recall: Article was traded between check and record creation (skipping)",
                        article_id=article.id
                    )
                    return  # Skip - we traded this
            
            # Create recall record
            # Use first ticker's NBBO as the initial_nbbo (we'll track all tickers)
            # Check if we already have pending filter reason or classification (from events that fire before record creation)
            # Combined lock acquisition to reduce lock overhead
            initial_filter_reason = None
            initial_classification = None
            async with self._filter_reasons_lock:
                initial_filter_reason = self._pending_filter_reasons.pop(article.id, None)
            async with self._classification_lock:
                initial_classification = self._pending_classifications.pop(article.id, None)
            
            # CRITICAL: Check for initial SURGE BEFORE any blocking DB operations
            # This ensures we trigger trades immediately when surge is detected (ASBP fix)
            # The volume analysis already checked the 4-second window from event_time (published_at)
            # If we received the article after the shock window ended, no wait was needed
            # So surge detection happens at the EVENT TIME of the surge, not after
            has_initial_surge = False
            surge_ticker = None
            for ticker, stats in volume_stats_by_ticker.items():
                # Only check if stats is a valid dict (not an error dict)
                if isinstance(stats, dict) and "error" not in stats and stats.get("move_type") == "SURGE":
                    has_initial_surge = True
                    surge_ticker = ticker
                    logger.info(
                        "🚀 INITIAL SURGE: Detected in initial 4-second window",
                        article_id=article.id,
                        ticker=ticker,
                        move_type=stats.get("move_type"),
                        max_excursion_pct=stats.get("max_excursion_pct"),
                        surge_multiplier=stats.get("surge_multiplier")
                    )
                    break
            
            # IDENTITY PRIMARY TICKER (Crucial for Data Consistency)
            # 1. If SURGE detected, that is the primary ticker.
            # 2. Else, the first tradable ticker (after sanitization) is primary.
            primary_ticker = surge_ticker if has_initial_surge else (tradable_tickers[0] if tradable_tickers else None)
            
            # Create recall record
            record = RecallRecord(
                article_id=article.id,
                title=article.title,
                tickers=tradable_tickers,
                session=session_enum,
                published_at=article.published_at,
                received_at=received_at,
                initial_nbbo=initial_nbbos.get(primary_ticker) if primary_ticker else None,
                filter_reason=initial_filter_reason,  # Set immediately if known (from prefilter events)
                ai_classification=initial_classification,  # Set immediately if known (from classification events)
                volume_stats=volume_stats_by_ticker
            )
            
            # If SURGE detected, trigger trade IMMEDIATELY (don't wait for DB write)
            # GUARANTEE: Trade enters at the EVENT TIME of the surge, not after
            # - Volume analysis checks the 4-second window from published_at (event_time)
            # - If shock window already passed when we received article, no wait needed
            # - Surge check happens immediately after volume_stats populated
            # - Trade triggered via asyncio.create_task (non-blocking, fire-and-forget)
            # - No DB writes block trade execution
            if has_initial_surge:
                # SURGE detected immediately - trigger trade NOW (at event time, not after)
                logger.info(
                    "🚀 SURGE DETECTED: Initial 4-second window shows SURGE, triggering trade immediately at event time",
                    article_id=article.id,
                    ticker=surge_ticker,
                    published_at=article.published_at.isoformat(),
                    received_at=received_at.isoformat()
                )
                
                # Capture NBBO at surge detection time (for surge_detection_window_stats)
                surge_nbbo = None
                try:
                    surge_nbbo = await self.quote_fetcher.get_nbbo_snapshot(surge_ticker)
                except Exception as nbbo_error:
                    logger.debug(
                        "Could not fetch NBBO at surge detection time",
                        ticker=surge_ticker,
                        error=str(nbbo_error)
                    )
                
                # Get initial surge stats and add surge NBBO fields
                initial_surge_stats = volume_stats_by_ticker.get(surge_ticker, {})
                if isinstance(initial_surge_stats, dict):
                    # Create a copy to avoid modifying the original
                    surge_detection_stats = initial_surge_stats.copy()
                    
                    # Add surge NBBO fields to surge_detection_stats
                    # These show bid/ask/spread at the moment of surge classification
                    # Compare: pub_ask (publication), recv_ask (reception), surge_ask (surge detection)
                    # The surge_ask is the price we use for trade execution (plus premium)
                    if surge_nbbo:
                        surge_bid = surge_nbbo.get("bid")
                        surge_ask = surge_nbbo.get("ask")
                        surge_spread = surge_ask - surge_bid if (surge_bid and surge_ask) else None
                        
                        surge_detection_stats["surge_bid"] = surge_bid
                        surge_detection_stats["surge_ask"] = surge_ask
                        surge_detection_stats["surge_spread"] = surge_spread
                else:
                    surge_detection_stats = initial_surge_stats
                
                # Update record with surge detection (in background, non-blocking)
                async def update_with_initial_surge():
                    try:
                        await self.repository.update_recall_record(
                            article_id=article.id,
                            updates={
                                "monitoring_status": "surge_detected",
                                "surge_detected_at": datetime.now(),
                                "surge_detection_cycle": 0,  # Initial surge = cycle 0
                                "surge_detection_window_stats": surge_detection_stats,
                                "monitoring_completed_at": datetime.now()
                            },
                            session=session,
                            date=received_at
                        )
                    except Exception as e:
                        logger.error("Failed to update initial surge detection", article_id=article.id, error=str(e))
                
                # CRITICAL: Trade execution is PRIORITY #1 - everything else is secondary
                # Fire and forget: Trigger trade immediately, don't wait for anything
                # This executes at the EVENT TIME of the surge (published_at + 4s window)
                # NO blocking operations - trade placement is prioritized above ALL else
                asyncio.create_task(self._trigger_trade_for_surge(article, surge_ticker))
                
                # Update record with surge detection (non-blocking)
                asyncio.create_task(update_with_initial_surge())
                
                # Append record in background (non-blocking, doesn't delay trade)
                # DB writes are LOWEST priority - they never block trade execution
                asyncio.create_task(self.repository.append_recall_record(record, session, received_at))
            else:
                # No initial SURGE - append record in background (non-blocking, doesn't delay monitoring)
                # DB writes are LOWEST priority - start monitoring immediately, don't wait for DB write
                asyncio.create_task(self.repository.append_recall_record(record, session, received_at))
                
                # No initial SURGE - start 2-minute monitoring for SURGE detection IMMEDIATELY
                logger.info(
                    "📊 NO INITIAL SURGE: Starting 2-minute monitoring for SURGE detection",
                    article_id=article.id,
                    tickers=tradable_tickers,
                    initial_move_types={t: (v.get("move_type") if isinstance(v, dict) else None) for t, v in volume_stats_by_ticker.items()}
                )
                
                # CRITICAL: Start monitoring task IMMEDIATELY (don't wait for DB update)
                # The DB update can happen in background - speed is critical for surge detection
                monitoring_task = asyncio.create_task(
                    self._monitor_for_surge(
                        article=article,
                        tradable_tickers=tradable_tickers,
                        initial_nbbos=initial_nbbos,
                        session=session,
                        received_at=received_at,
                        published_at=article.published_at
                    )
                )
                
                async with self._monitoring_lock:
                    self._monitoring_tasks[article.id] = monitoring_task
                
                # Update record asynchronously (don't block on this)
                # This was causing ~20-30 second delays before monitoring started!
                async def update_monitoring_status():
                    try:
                        await self.repository.update_recall_record(
                            article_id=article.id,
                            updates={
                                "monitoring_status": "initiated",
                                "monitoring_initiated_at": datetime.now()
                            },
                            session=session,
                            date=received_at
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to update monitoring status in background",
                            article_id=article.id,
                            error=str(e),
                            exc_info=True
                        )
                
                asyncio.create_task(update_monitoring_status())
            
            # Start 10-minute price monitoring task
            # STICK TO PRIMARY TICKER ONLY
            price_monitoring_task = asyncio.create_task(
                self._monitor_ticker_price(
                    article_id=article.id,
                    tickers=[primary_ticker] if primary_ticker else [],
                    initial_nbbos=initial_nbbos,
                    session=session,
                    received_at=received_at,
                    published_at=article.published_at
                )
            )
            
            # Track price monitoring task separately (don't overwrite surge monitoring)
            # We can track multiple tasks per article if needed, but for now just track one
            
            # Fetch ticker metadata asynchronously (tracked for finalization)
            # Pass received_at so we can determine session from timestamp (stateless)
            metadata_task = asyncio.create_task(
                self._fetch_and_update_metadata(article.id, tradable_tickers, session, received_at)
            )
            
            # Track pending metadata fetch
            async with self._metadata_lock:
                self._pending_metadata[article.id] = (tradable_tickers, session, received_at, metadata_task)
            
            logger.debug(
                "Recall: Started monitoring ticker",
                article_id=article.id,
                tickers=tradable_tickers,
                has_initial_surge=has_initial_surge
            )
            
        except Exception as e:
            logger.error(
                "Error checking and monitoring ticker for recall",
                article_id=article.id,
                error=str(e),
                exc_info=True
            )
    
    async def _trigger_trade_for_surge(
        self,
        article: Any,  # Domain Article model
        ticker: str
    ) -> None:
        """
        Trigger trade execution when SURGE is detected.
        
        PRIORITY #1: Place trade immediately - no delays, no blocking operations.
        This function is called via asyncio.create_task (non-blocking) and executes
        the trade request as fast as possible. Only essential operations (spread check,
        price fetch) happen before trade placement. All DB writes are deferred.
        
        Args:
            article: Domain Article model
            ticker: Ticker symbol that showed SURGE
        """
        try:
            # Check if already traded
            async with self._traded_lock:
                if article.id in self._traded_articles:
                    logger.debug(
                        "Recall: Article already traded, skipping trade trigger",
                        article_id=article.id,
                        ticker=ticker
                    )
                    return
            
            # Get current ask price for calculating $2k trade size and validate spread
            current_price = None
            MAX_SPREAD_THRESHOLD_PCT = 2.0  # Reject trades if spread >= 2%
            
            try:
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                if nbbo:
                    current_price = nbbo.get("ask")  # Use ask price for buying
                    
                    # CRITICAL: Validate spread before triggering trade
                    # Spread > 2% indicates illiquidity - reject trade even if surge detected
                    bid = nbbo.get("bid")
                    ask = nbbo.get("ask")
                    
                    if bid and ask and ask > 0:
                        spread = ask - bid
                        mid_price = (bid + ask) / 2.0
                        spread_pct = (spread / mid_price) * 100 if mid_price > 0 else 0
                        
                        if spread_pct >= MAX_SPREAD_THRESHOLD_PCT:
                            logger.warning(
                                "🚫 TRADE REJECTED: Spread too wide (>= 2%), rejecting surge trade",
                                article_id=article.id,
                                ticker=ticker,
                                spread_pct=round(spread_pct, 2),
                                bid=bid,
                                ask=ask,
                                spread=spread,
                                threshold=MAX_SPREAD_THRESHOLD_PCT
                            )
                            # Update recall record to note spread rejection (non-blocking, fire-and-forget)
                            # DB writes are LOWEST priority - never block trade execution
                            try:
                                now = datetime.now(timezone.utc)
                                session, _ = get_market_session_from_timestamp(now)
                                if session == "closed":
                                    session = "premarket"  # Fallback
                                # Fire-and-forget: Don't await, just schedule it
                                asyncio.create_task(
                                    self.repository.update_recall_record(
                                        article_id=article.id,
                                        updates={
                                            "filter_reason": f"spread_too_wide_{spread_pct:.2f}%"
                                        },
                                        session=session,
                                        date=now
                                    )
                                )
                            except Exception:
                                pass  # Don't fail on metadata update
                            return  # Reject trade - spread trumps surge signal
                        
                        logger.debug(
                            "✅ Spread validation passed",
                            ticker=ticker,
                            spread_pct=round(spread_pct, 2),
                            threshold=MAX_SPREAD_THRESHOLD_PCT
                        )
                    else:
                        logger.warning(
                            "⚠️ Could not validate spread (missing bid/ask), proceeding with caution",
                            ticker=ticker,
                            nbbo=nbbo
                        )
            except Exception as price_error:
                logger.debug(
                    "Recall: Could not get current price for trade sizing, will use amount_usd",
                    ticker=ticker,
                    error=str(price_error)
                )
            
            # Build trade request from article with $2k position size
            # CRITICAL: Pass the specific ticker that showed SURGE to avoid trading wrong ticker
            trade_request = build_trade_request_for_article(article, current_price=current_price, ticker=ticker)
            
            if not trade_request:
                logger.warning(
                    "Recall: Could not build trade request for surge",
                    article_id=article.id,
                    ticker=ticker
                )
                return
            
            # Mark as traded immediately (before publishing event to prevent race conditions)
            async with self._traded_lock:
                self._traded_articles.add(article.id)
            
            # CRITICAL PATH: Publish trade request domain event
            # This is the ONLY blocking operation in the trade execution path
            # Everything else (DB writes, logging) is fire-and-forget
            # Trade placement is PRIORITY #1 - this await is necessary for trade execution
            domain_trade_event = TradeRequestDomainEvent(
                trade_request=trade_request,
                article_id=article.id,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish(
                DomainEventType.TRADE_REQUESTED,
                domain_trade_event.model_dump()
            )
            
            logger.info(
                "🚀 TRADE TRIGGERED: SURGE detected, trade request published",
                article_id=article.id,
                ticker=ticker,
                trade_ticker=trade_request.ticker
            )
            
        except Exception as e:
            logger.error(
                "Error triggering trade for surge",
                article_id=article.id,
                ticker=ticker,
                error=str(e),
                exc_info=True
            )
    
    async def _monitor_for_surge(
        self,
        article: Any,  # Domain Article model
        tradable_tickers: list[str],
        initial_nbbos: Dict[str, Dict[str, Any]],
        session: str,
        received_at: datetime,
        published_at: datetime
    ) -> None:
        """
        Monitor for SURGE detection over 2 minutes (30 cycles of 4-second windows).
        
        Checks 4-second windows every 4 seconds:
        - Cycle 0: published_at + 4s to published_at + 8s (first cycle after initial check)
        - Cycle 1: published_at + 8s to published_at + 12s
        - ...
        - Cycle 29: published_at + 120s to published_at + 124s
        
        If SURGE detected in any cycle, immediately trigger trade and stop monitoring.
        
        Args:
            article: Domain Article model
            tradable_tickers: List of tradable ticker symbols
            initial_nbbos: Initial NBBO snapshots
            session: Market session
            received_at: When article was received
            published_at: When article was published
        """
        try:
            monitoring_start = datetime.now()
            max_cycles = 30  # 30 cycles * 4 seconds = 120 seconds = 2 minutes
            cycle_duration = 4.0  # 4-second windows
            
            logger.info(
                "📊 SURGE MONITORING: Starting 2-minute monitoring",
                article_id=article.id,
                tickers=tradable_tickers,
                max_cycles=max_cycles,
                cycle_duration_seconds=cycle_duration
            )
            
            for cycle in range(max_cycles):
                # Check if article was already traded
                async with self._traded_lock:
                    if article.id in self._traded_articles:
                        logger.debug(
                            "Recall: Article traded during monitoring, stopping",
                            article_id=article.id,
                            cycle=cycle
                        )
                        break
                
                # Calculate window start time for this cycle
                # Cycle 0 starts at published_at + 4s (right after initial 4s check)
                # Cycle 1 starts at published_at + 8s, etc.
                window_start = published_at + timedelta(seconds=(cycle + 1) * cycle_duration)
                
                # Wait until window start time (if in the future)
                # Ensure window_start has timezone
                if window_start.tzinfo is None:
                    window_start = window_start.replace(tzinfo=timezone.utc)
                
                now = datetime.now(timezone.utc)
                if window_start > now:
                    wait_time = (window_start - now).total_seconds()
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                
                # Analyze 4-second window starting at window_start
                # We use analyze_volume_around_event which analyzes 4 seconds from event_time
                surge_detected = False
                surge_ticker = None
                surge_stats = None
                
                if not self.market_data_client:
                    logger.warning("No market data client available for surge monitoring")
                    break
                
                for ticker in tradable_tickers:
                    try:
                        # Get sector for this ticker (try to fetch if not already cached)
                        # For monitoring cycles, we can try quick fetch or use cached value
                        ticker_sector = None
                        try:
                            # Try quick fetch (1 second timeout - don't block monitoring)
                            # Use queue_on_failure=True to ensure metadata is eventually populated
                            ticker_meta = await self.yahoo_finance_coordinator.fetch_metadata(
                                ticker,
                                timeout=1.0,
                                queue_on_failure=True  # Queue for background retry if fails
                            )
                            if ticker_meta:
                                ticker_sector = ticker_meta.get("sector")
                        except (asyncio.TimeoutError, Exception):
                            # If fetch fails, proceed without sector (strict requirements)
                            # Metadata will be queued for background retry
                            pass
                        
                        # Analyze this 4-second window
                        volume_analysis = await analyze_volume_around_event(
                            client=self.market_data_client,
                            symbol=ticker,
                            event_time=window_start,  # Analyze 4 seconds starting from here
                            received_at=window_start,  # Use window_start as received_at for this analysis
                            reference_nbbo=initial_nbbos.get(ticker),
                            sector=ticker_sector,  # Pass sector for surge detection
                            stream_manager=self.quote_fetcher.stream_manager if self.quote_fetcher else None
                        )
                        
                        if volume_analysis and volume_analysis.move_type == "SURGE":
                            surge_detected = True
                            surge_ticker = ticker
                            surge_stats = volume_analysis.to_dict()
                            
                            # Capture NBBO at surge detection time (for surge_detection_window_stats)
                            # These show bid/ask/spread at the moment of surge classification
                            # Compare: pub_ask (publication), recv_ask (reception), surge_ask (surge detection)
                            # The surge_ask is the price we use for trade execution (plus premium)
                            try:
                                surge_nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                                if surge_nbbo:
                                    surge_bid = surge_nbbo.get("bid")
                                    surge_ask = surge_nbbo.get("ask")
                                    surge_spread = surge_ask - surge_bid if (surge_bid and surge_ask) else None
                                    
                                    # Add surge NBBO fields to the stats dict
                                    surge_stats["surge_bid"] = surge_bid
                                    surge_stats["surge_ask"] = surge_ask
                                    surge_stats["surge_spread"] = surge_spread
                            except Exception as nbbo_error:
                                logger.debug(
                                    "Could not fetch NBBO at surge detection time",
                                    ticker=ticker,
                                    error=str(nbbo_error)
                                )
                            
                            logger.info(
                                "🚀 SURGE DETECTED: Found SURGE during monitoring cycle",
                                article_id=article.id,
                                ticker=ticker,
                                cycle=cycle,
                                cycle_start=window_start.isoformat(),
                                move_type=volume_analysis.move_type,
                                surge_multiplier=volume_analysis.surge_multiplier,
                                surge_ask=surge_stats.get("surge_ask")
                            )
                            break  # Found surge, stop checking other tickers
                    
                    except Exception as vol_error:
                        logger.debug(
                            "Recall: Error analyzing volume in monitoring cycle",
                            article_id=article.id,
                            ticker=ticker,
                            cycle=cycle,
                            error=str(vol_error)
                        )
                        continue
                
                # Update record with cycle progress (fire-and-forget, don't block monitoring)
                asyncio.create_task(self.repository.update_recall_record(
                    article_id=article.id,
                    updates={
                        "monitoring_cycles_completed": cycle + 1
                    },
                    session=session,
                    date=received_at
                ))
                
                # If surge detected, trigger trade and stop monitoring
                if surge_detected:
                    # CRITICAL: Trigger trade IMMEDIATELY (fire-and-forget)
                    # This is the #1 priority - no blocking operations before trade placement
                    asyncio.create_task(self._trigger_trade_for_surge(article, surge_ticker))
                    
                    # Update record with surge detection details (fire-and-forget, don't block)
                    asyncio.create_task(self.repository.update_recall_record(
                        article_id=article.id,
                        updates={
                            "monitoring_status": "surge_detected",
                            "surge_detected_at": datetime.now(),
                            "surge_detection_cycle": cycle,
                            "surge_detection_window_stats": surge_stats,
                            "monitoring_completed_at": datetime.now()
                        },
                        session=session,
                        date=received_at
                    ))
                    
                    # Stop monitoring (surge found)
                    break
                
                # Wait 4 seconds before next cycle (but account for analysis time)
                # We want cycles to be every 4 seconds, so if analysis took time, reduce wait
                cycle_end = datetime.now(timezone.utc)
                next_cycle_start = window_start + timedelta(seconds=cycle_duration)
                if next_cycle_start.tzinfo is None:
                    next_cycle_start = next_cycle_start.replace(tzinfo=timezone.utc)
                if next_cycle_start > cycle_end:
                    wait_until_next = (next_cycle_start - cycle_end).total_seconds()
                    if wait_until_next > 0:
                        await asyncio.sleep(wait_until_next)
            
            # Monitoring completed (either all cycles done or surge detected)
            async with self._traded_lock:
                was_traded = article.id in self._traded_articles
            
            if not was_traded:
                # No surge detected in 2 minutes - update record
                await self.repository.update_recall_record(
                    article_id=article.id,
                    updates={
                        "monitoring_status": "completed_no_surge",
                        "monitoring_completed_at": datetime.now()
                    },
                    session=session,
                    date=received_at
                )
                
                logger.info(
                    "📊 SURGE MONITORING: Completed 2-minute monitoring, no SURGE detected",
                    article_id=article.id,
                    cycles_completed=max_cycles
                )
            
            # Clean up monitoring task
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article.id, None)
        
        except asyncio.CancelledError:
            logger.debug(
                "Recall: Surge monitoring task cancelled",
                article_id=article.id
            )
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article.id, None)
            raise
        except Exception as e:
            logger.error(
                "Error in surge monitoring",
                article_id=article.id,
                error=str(e),
                exc_info=True
            )
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article.id, None)
    
    async def _monitor_ticker_price(
        self,
        article_id: str,
        tickers: list[str],
        initial_nbbos: Dict[str, Dict[str, Any]],
        session: str,
        received_at: datetime,
        published_at: datetime
    ) -> None:
        """
        Monitor ticker price for 10 minutes, track highest price and max adverse excursion.
        
        Background task: Waits 10 minutes, then analyzes price action:
        - Final price at 10 minutes
        - Highest price reached (with timestamp and minute/second)
        - Lowest price reached (max adverse excursion, with timestamp and minute/second)
        
        Args:
            article_id: Article ID
            tickers: List of ticker symbols
            initial_nbbos: Initial NBBO snapshots
            session: Market session
            received_at: When article was received
            published_at: When article was published (used as hold period start)
        """
        try:
            # Wait 10 minutes (600 seconds)
            hold_duration_seconds = 600  # 10 minutes
            await asyncio.sleep(hold_duration_seconds)
            
            # Check if article was traded (OPTIONAL: skip if yes? User wants to track everything)
            # async with self._traded_lock:
            #     if article_id in self._traded_articles:
            #         return  # We traded this, don't count as missed
            pass
            
            # Get final NBBO and analyze price action during 15-minute hold
            final_nbbos = {}
            best_move = None
            best_ticker = None
            highest_price_data = None
            max_adverse_excursion_data = None
            
            # Use published_at as the start of the hold period (when article was published)
            # This is consistent with how we track price action relative to news
            monitoring_start = published_at
            if monitoring_start.tzinfo is None:
                monitoring_start = monitoring_start.replace(tzinfo=timezone.utc)
            
            # CRITICAL FIX: Only monitor the PRIMARY ticker (tickers[0])
            # The RecallRecord stores initial_nbbo for tickers[0] only.
            # If we iterate and pick the "best" ticker here (e.g. Common vs Warrant), 
            # we create a mismatch where Initial is Warrant (0.08) and Final is Common (4.00),
            # resulting in massive fake P&L.
            # We must verify apples-to-apples performance.
            target_ticker = tickers[0] if tickers else None
            
            # Create a single-item list to preserve existing loop logic structure
            tickers_to_monitor = [target_ticker] if target_ticker else []

            for ticker in tickers_to_monitor:
                if not initial_nbbos.get(ticker):
                    continue
                
                initial_nbbo = initial_nbbos[ticker]
                initial_bid = initial_nbbo.get("bid")
                initial_ask = initial_nbbo.get("ask")
                initial_mid = initial_nbbo.get("mid")
                
                # Entry price for calculations (we buy at ask)
                entry_price = initial_ask if initial_ask else initial_mid
                if not entry_price or entry_price <= 0:
                    continue
                
                # Fetch minute bars for the 10-minute period to track highest/lowest
                if self.market_data_client and StockBarsRequest:
                    try:
                        bars_end = monitoring_start + timedelta(minutes=15)
                        bars_request = StockBarsRequest(
                            symbol_or_symbols=[ticker],
                            timeframe=TimeFrame.Minute,
                            start=monitoring_start,
                            end=bars_end,
                            feed=DataFeed.SIP
                        )
                        bars_response = self.market_data_client.get_stock_bars(bars_request)
                        
                        highest_price = None
                        highest_price_timestamp = None
                        lowest_price = None
                        lowest_price_timestamp = None
                        
                        if bars_response and bars_response.data and ticker in bars_response.data:
                            # First pass: Find which minute had highest/lowest prices
                            minute_with_highest = None
                            minute_with_lowest = None
                            
                            for bar in bars_response.data[ticker]:
                                bar_high = float(bar.high) if bar.high else None
                                bar_low = float(bar.low) if bar.low else None
                                bar_timestamp = bar.timestamp
                                if bar_timestamp.tzinfo is None:
                                    bar_timestamp = bar_timestamp.replace(tzinfo=timezone.utc)
                                
                                # Track highest price and which minute it occurred in
                                if bar_high and (highest_price is None or bar_high > highest_price):
                                    highest_price = bar_high
                                    minute_with_highest = bar_timestamp
                                
                                # Track lowest price and which minute it occurred in
                                if bar_low and (lowest_price is None or bar_low < lowest_price):
                                    lowest_price = bar_low
                                    minute_with_lowest = bar_timestamp
                            
                            # Second pass: Get exact second for highest price (fetch trades for that minute)
                            highest_price_timestamp = minute_with_highest
                            if highest_price and minute_with_highest and StockTradesRequest:
                                try:
                                    # Fetch trades for the minute with highest price to get exact second
                                    minute_start = minute_with_highest.replace(second=0, microsecond=0)
                                    minute_end = minute_start + timedelta(minutes=1)
                                    trades_request = StockTradesRequest(
                                        symbol_or_symbols=[ticker],
                                        start=minute_start,
                                        end=minute_end,
                                        feed=DataFeed.SIP
                                    )
                                    trades_response = self.market_data_client.get_stock_trades(trades_request)
                                    
                                    if trades_response and trades_response.data and ticker in trades_response.data:
                                        # Find the trade with the highest price in this minute (closest to bar high)
                                        max_trade_price = None
                                        for trade in trades_response.data[ticker]:
                                            trade_price = float(trade.price) if trade.price else None
                                            if trade_price:
                                                # Update if this is the highest trade price we've seen
                                                if max_trade_price is None or trade_price > max_trade_price:
                                                    max_trade_price = trade_price
                                                    trade_ts = trade.timestamp
                                                    if trade_ts.tzinfo is None:
                                                        trade_ts = trade_ts.replace(tzinfo=timezone.utc)
                                                    highest_price_timestamp = trade_ts
                                except Exception:
                                    # If trades fetch fails, use minute-level timestamp (already set)
                                    pass
                            
                            # Get exact second for lowest price (max adverse excursion)
                            lowest_price_timestamp = minute_with_lowest
                            if lowest_price and minute_with_lowest and StockTradesRequest:
                                try:
                                    # Fetch trades for the minute with lowest price to get exact second
                                    minute_start = minute_with_lowest.replace(second=0, microsecond=0)
                                    minute_end = minute_start + timedelta(minutes=1)
                                    trades_request = StockTradesRequest(
                                        symbol_or_symbols=[ticker],
                                        start=minute_start,
                                        end=minute_end,
                                        feed=DataFeed.SIP
                                    )
                                    trades_response = self.market_data_client.get_stock_trades(trades_request)
                                    
                                    if trades_response and trades_response.data and ticker in trades_response.data:
                                        # Find the trade with the lowest price in this minute (closest to bar low)
                                        min_trade_price = None
                                        for trade in trades_response.data[ticker]:
                                            trade_price = float(trade.price) if trade.price else None
                                            if trade_price:
                                                # Update if this is the lowest trade price we've seen
                                                if min_trade_price is None or trade_price < min_trade_price:
                                                    min_trade_price = trade_price
                                                    trade_ts = trade.timestamp
                                                    if trade_ts.tzinfo is None:
                                                        trade_ts = trade_ts.replace(tzinfo=timezone.utc)
                                                    lowest_price_timestamp = trade_ts
                                except Exception:
                                    # If trades fetch fails, use minute-level timestamp (already set)
                                    pass
                            
                            # Calculate percentages from entry price and build data structures
                            if highest_price and highest_price_timestamp:
                                highest_gain_pct = ((highest_price - entry_price) / entry_price) * 100
                                highest_price_data = {
                                    "price": highest_price,
                                    "timestamp": highest_price_timestamp.isoformat(),
                                    "percent_gain_from_entry": round(highest_gain_pct, 2),
                                    "minute": highest_price_timestamp.minute,
                                    "second": highest_price_timestamp.second,
                                    "ticker": ticker
                                }
                            
                            if lowest_price and lowest_price_timestamp:
                                max_adverse_pct = ((lowest_price - entry_price) / entry_price) * 100
                                # Calculate stop loss metrics
                                stop_loss_percentage = abs(round(max_adverse_pct, 3))  # Minimum stop loss % needed (e.g., 2.125)
                                stop_loss_dollar_per_share = round(entry_price * (stop_loss_percentage / 100), 4)  # Dollar loss per share
                                
                                max_adverse_excursion_data = {
                                    "price": lowest_price,
                                    "timestamp": lowest_price_timestamp.isoformat(),
                                    "percent_loss_from_entry": round(max_adverse_pct, 2),
                                    "minute": lowest_price_timestamp.minute,
                                    "second": lowest_price_timestamp.second,
                                    "stop_loss_percentage": stop_loss_percentage,  # Minimum stop loss % needed to avoid this loss
                                    "stop_loss_dollar_per_share": stop_loss_dollar_per_share,  # Dollar amount lost per share at this stop
                                    "ticker": ticker
                                }
                    except Exception as bars_error:
                        logger.debug(
                            "Recall: Error fetching minute bars for price tracking",
                            article_id=article_id,
                            ticker=ticker,
                            error=str(bars_error)
                        )
                
                # Get final NBBO at 10 minutes
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                if nbbo:
                    final_bid = nbbo.get("bid")
                    final_ask = nbbo.get("ask")
                    final_mid = nbbo.get("mid")
                    
                    # Calculate actual tradeable price change (10 minutes)
                    # We buy at ask (pay more), sell at bid (get less)
                    # Actual P&L = (final_bid - initial_ask) / initial_ask
                    
                    actual_pnl = None
                    if initial_ask and final_bid and initial_ask > 0:
                        actual_pnl = ((final_bid - initial_ask) / initial_ask) * 100
                    
                    # Also track mid price change for reference
                    mid_price_change = None
                    if initial_mid and final_mid and initial_mid > 0:
                        mid_price_change = ((final_mid - initial_mid) / initial_mid) * 100
                    
                    # Use actual P&L for decision (if available), otherwise fall back to mid
                    percent_change = actual_pnl if actual_pnl is not None else mid_price_change
                    
                    if percent_change is not None:
                        final_nbbos[ticker] = {
                            **nbbo,
                            "percent_change": percent_change,
                            "mid_price_change": mid_price_change,
                            "actual_pnl": actual_pnl,
                            "moved_1_percent": percent_change >= 1.0
                        }
                        
                        # Track best move (using actual P&L)
                        if best_move is None or percent_change > best_move:
                            best_move = percent_change
                            best_ticker = ticker
            
            # Update record with price check result and tracking data
            if best_ticker and final_nbbos.get(best_ticker):
                price_check = final_nbbos[best_ticker]
                
                updates = {
                    "price_check_10min": price_check,  # Changed from price_check_5min to 10min
                    "price_checked_at": datetime.now()
                }
                
                # CRITICAL FIX: Ensure highest_price_during_hold is at least as high as 10-minute ask
                # The highest price during the hold period must be >= the 10-minute price
                # This fixes cases where bar data is missing/incomplete (especially premarket)
                final_ask = price_check.get("ask")
                if final_ask and entry_price:
                    # If we have a 10-minute ask price, ensure highest_price is at least that high
                    if highest_price_data:
                        current_highest = highest_price_data.get("price")
                        if current_highest is None or final_ask > current_highest:
                            # Update to use 10-minute ask (or keep current if it's higher)
                            highest_gain_pct = ((final_ask - entry_price) / entry_price) * 100
                            price_check_time = datetime.now(timezone.utc)
                            highest_price_data = {
                                "price": final_ask,
                                "timestamp": price_check_time.isoformat(),
                                "percent_gain_from_entry": round(highest_gain_pct, 2),
                                "minute": price_check_time.minute,
                                "second": price_check_time.second,
                                "ticker": best_ticker
                            }
                            logger.debug(
                                "Recall: Updated highest_price_during_hold to match/exceed 10-minute ask",
                                article_id=article_id,
                                ticker=best_ticker,
                                previous_highest=current_highest,
                                new_highest=final_ask
                            )
                    else:
                        # No highest_price_data from bars, use 10-minute ask as fallback
                        highest_gain_pct = ((final_ask - entry_price) / entry_price) * 100
                        price_check_time = datetime.now(timezone.utc)
                        highest_price_data = {
                            "price": final_ask,
                            "timestamp": price_check_time.isoformat(),
                            "percent_gain_from_entry": round(highest_gain_pct, 2),
                            "minute": price_check_time.minute,
                            "second": price_check_time.second,
                            "ticker": best_ticker
                        }
                        logger.debug(
                            "Recall: Created highest_price_during_hold from 10-minute ask (no bar data)",
                            article_id=article_id,
                            ticker=best_ticker,
                            highest_price=final_ask
                        )
                
                # Add highest price tracking
                if highest_price_data:
                    updates["highest_price_during_hold"] = highest_price_data
                
                # Add max adverse excursion tracking
                if max_adverse_excursion_data:
                    updates["max_adverse_excursion"] = max_adverse_excursion_data
                
                # Update record in repository
                updated = await self.repository.update_recall_record(
                    article_id=article_id,
                    updates=updates,
                    session=session,
                    date=received_at
                )
                
                if updated:
                    logger.info(
                        "Recall: 10-minute price check completed",
                        article_id=article_id,
                        best_ticker=best_ticker,
                        actual_pnl=price_check.get("actual_pnl"),
                        mid_price_change=price_check.get("mid_price_change"),
                        percent_change=best_move,
                        moved_1_percent=price_check.get("moved_1_percent"),
                        highest_price=highest_price_data.get("price") if highest_price_data else None,
                        highest_gain_pct=highest_price_data.get("percent_gain_from_entry") if highest_price_data else None,
                        max_adverse_price=max_adverse_excursion_data.get("price") if max_adverse_excursion_data else None,
                        max_adverse_pct=max_adverse_excursion_data.get("percent_loss_from_entry") if max_adverse_excursion_data else None,
                        stop_loss_percentage=max_adverse_excursion_data.get("stop_loss_percentage") if max_adverse_excursion_data else None,
                        stop_loss_dollar_per_share=max_adverse_excursion_data.get("stop_loss_dollar_per_share") if max_adverse_excursion_data else None
                    )
                else:
                    logger.warning(
                        "Recall: Failed to update record with price check (record not found)",
                        article_id=article_id
                    )
            
        except asyncio.CancelledError:
            logger.debug("Recall: Monitoring task cancelled", article_id=article_id)
        except Exception as e:
            logger.error(
                "Error monitoring ticker price for recall",
                article_id=article_id,
                error=str(e),
                exc_info=True
            )
        finally:
            # Remove from monitoring tasks
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article_id, None)
    
    async def _handle_article_classified(
        self,
        event: ArticleClassifiedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleClassified event - update classification and filter reasons.
        
        ALWAYS stores ai_classification (IMMINENT, SPECULATIVE, ROUTINE, IGNORE).
        If not IMMINENT, also sets filter_reason.
        
        RACE CONDITION FIX: Always store to pending first, then attempt update.
        """
        try:
            classification_value = event.result.classification.value
            article_id = event.article_id
            
            # Determine session and date from classified_at timestamp
            session, _ = get_market_session_from_timestamp(event.classified_at)
            if session == "closed":
                return
            
            # Build updates dict - ALWAYS include ai_classification
            updates = {"ai_classification": classification_value}
            
            # If not IMMINENT, also set filter reason
            if classification_value != "IMMINENT":
                filter_reason = f"ai_classified_{classification_value.lower()}"
                updates["filter_reason"] = filter_reason
                
                # Store to pending for race condition handling
                async with self._filter_reasons_lock:
                    self._pending_filter_reasons[article_id] = filter_reason
            
            # Store ai_classification to pending too (for race condition)
            async with self._classification_lock:
                self._pending_classifications[article_id] = classification_value
            
            logger.debug(
                "Recall: Stored classification to pending",
                article_id=article_id,
                classification=classification_value
            )
            
            # Attempt immediate update (if record exists)
            try:
                # Use stored location if available (prevents session mismatch errors)
                record_loc = self._record_locations.get(article_id)
                target_session = record_loc[0] if record_loc else session
                target_date = record_loc[1] if record_loc else event.classified_at
                
                updated = await self.repository.update_recall_record(
                    article_id=article_id,
                    updates=updates,
                    session=target_session,
                    date=target_date
                )
                
                if updated:
                    # Success - remove from pending (combined operations to reduce lock overhead)
                    async with self._filter_reasons_lock:
                        self._pending_filter_reasons.pop(article_id, None)
                    async with self._classification_lock:
                        self._pending_classifications.pop(article_id, None)
                        
                    logger.info(
                        "Recall: Updated record with AI classification",
                        article_id=article_id,
                        classification=classification_value,
                        filter_reason=updates.get("filter_reason")
                    )
                else:
                    # Record doesn't exist yet - keep in pending for creation or finalization
                    logger.debug(
                        "Recall: Record not found for immediate update (AI class), keeping in pending",
                        article_id=article_id,
                        classification=classification_value
                    )
            except Exception as update_error:
                # Record doesn't exist yet - pending will be picked up on creation
                logger.debug(
                    "Recall: Record not found for immediate update, will be applied on creation",
                    article_id=article_id,
                    classification=classification_value
                )
                
        except Exception as e:
            logger.error(
                "Error handling article classified for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_trade_executed(
        self,
        event: TradeExecutedDomainEvent,
    ) -> None:
        """Handle Domain.TradeExecuted event - mark article as traded."""
        try:
            trade_result = event.trade_result
            
            # Get article_id from trade_request dict (TradeResult stores trade_request as dict)
            trade_request_dict = trade_result.trade_request
            article_id = trade_request_dict.get("article_id")
            
            if article_id:
                async with self._traded_lock:
                    self._traded_articles.add(article_id)
                
                # DO NOT cancel monitoring task - we want to track 5-min result for training data
                # async with self._monitoring_lock:
                #     task = self._monitoring_tasks.pop(article_id, None)
                #     if task:
                #         task.cancel()
                
                # Update recall record to link with trade
                try:
                    trade_request_dict = trade_result.trade_request
                    # Generate trade_id (use order_id if available, otherwise generate)
                    trade_id = trade_request_dict.get("order_id") or trade_request_dict.get("_order_id")
                    if not trade_id:
                        trade_id = f"trade_{int(event.executed_at.timestamp() * 1000)}"

                    session, _ = get_market_session_from_timestamp(event.executed_at)
                    await self.repository.update_recall_record(
                        article_id=article_id,
                        updates={
                            "is_traded": True,
                            "trade_id": trade_id
                        },
                        session=session,
                        date=event.executed_at
                    )
                except Exception as update_err:
                    logger.debug(f"Recall: Could not link trade to recall record: {update_err}")
                
                logger.debug(
                    "Recall: Marked article as traded (executed)",
                    article_id=article_id
                )
        except Exception as e:
            logger.error(
                "Error handling trade executed for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_trade_failed(
        self,
        event: TradeFailedDomainEvent,
    ) -> None:
        """Handle Domain.TradeFailed event - mark article as traded (failed, but still attempted)."""
        try:
            trade_request = event.trade_request
            
            # Get article_id from trade_request
            article_id = trade_request.article_id if hasattr(trade_request, 'article_id') else None
            if not article_id:
                # Try to get from dict if it's stored as dict
                trade_request_dict = trade_request if isinstance(trade_request, dict) else trade_request.model_dump() if hasattr(trade_request, 'model_dump') else {}
                article_id = trade_request_dict.get("article_id")
            
            if article_id:
                async with self._traded_lock:
                    self._traded_articles.add(article_id)
                
                # DO NOT cancel monitoring task - we want to track 5-min result for training data
                # async with self._monitoring_lock:
                #     task = self._monitoring_tasks.pop(article_id, None)
                #     if task:
                #         task.cancel()
                
                # DO NOT remove record. We want everything in the dataset.
                # try:
                #     # Determine session from failed_at timestamp
                #     session, _ = get_market_session_from_timestamp(event.failed_at)
                #     if session != "closed":
                #         # Try to remove the record from the file (if it exists)
                #         # This is best-effort - if file doesn't exist or record doesn't exist, that's fine
                #         await self.repository.remove_recall_record(article_id, session, event.failed_at)
                # except Exception as remove_error:
                #     logger.debug(
                #         "Recall: Could not remove record for failed trade (may not exist)",
                #         article_id=article_id,
                #         error=str(remove_error)
                #     )
                
                logger.debug(
                    "Recall: Marked article as traded (failed) and removed from recall if present",
                    article_id=article_id,
                    error=event.error
                )
        except Exception as e:
            logger.error(
                "Error handling trade failed for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_classification_skipped(
        self,
        event: ClassificationSkippedInfrastructureEvent,
    ) -> None:
        """
        Handle ClassificationSkipped infrastructure event - capture prefilter reasons.
        
        This captures why articles were filtered BEFORE AI classification:
        - no_tickers: Article has no tickers (no record created, skip update)
        - invalid_exchange: Exchange is not NASDAQ/NYSE/AMEX
        - broker_not_tradeable: Tickers not tradeable on broker (Alpaca) despite valid exchange
        - low_market_cap: Market cap below threshold
        - low_price: Price below threshold
        - nbbo_unavailable: No active bid/ask in current session
        
        RACE CONDITION FIX: Always store to pending first, then attempt update.
        """
        try:
            article_id = event.request_data.article_id
            filter_reason = f"prefilter_{event.reason}"
            
            # Determine session and date from skipped_at timestamp
            session, _ = get_market_session_from_timestamp(event.skipped_at)
            if session == "closed":
                return
            
            # SPECIAL CASE: If article has no tickers, no record was created
            # (because _handle_article_received returns early)
            # So we can't update a non-existent record - just store to pending
            # in case a record gets created later (shouldn't happen, but safe)
            if event.reason == "no_tickers":
                async with self._filter_reasons_lock:
                    self._pending_filter_reasons[article_id] = filter_reason
                logger.debug(
                    "Recall: Article has no tickers - no record created, stored filter reason to pending",
                    article_id=article_id,
                    filter_reason=filter_reason
                )
                return  # No record exists, skip update attempt
            
            # STEP 1: Always store to pending first (eliminates race condition)
            async with self._filter_reasons_lock:
                self._pending_filter_reasons[article_id] = filter_reason
            
            logger.debug(
                "Recall: Stored prefilter reason to pending",
                article_id=article_id,
                filter_reason=filter_reason
            )
            
            # STEP 2: Attempt immediate update (if record exists)
            try:
                # Use stored location if available (prevents session mismatch errors)
                record_loc = self._record_locations.get(article_id)
                target_session = record_loc[0] if record_loc else session
                target_date = record_loc[1] if record_loc else event.skipped_at
                
                updated = await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={"filter_reason": filter_reason},
                    session=target_session,
                    date=target_date
                )
                
                if updated:
                    # Success - remove from pending
                    async with self._filter_reasons_lock:
                        self._pending_filter_reasons.pop(article_id, None)
                        
                    logger.info(
                        "Recall: Updated record with prefilter reason (immediate)",
                        article_id=article_id,
                        reason=event.reason,
                        filter_reason=filter_reason
                    )
                else:
                    # Record doesn't exist yet - keep in pending
                    logger.debug(
                        "Recall: Record not found for immediate update (prefilter), keeping in pending",
                        article_id=article_id,
                        filter_reason=filter_reason
                    )
            except Exception as update_error:
                # Record doesn't exist yet - that's fine, pending will be picked up on creation
                logger.debug(
                    "Recall: Record not found for immediate update, will be applied on creation",
                    article_id=article_id,
                    filter_reason=filter_reason
                )
                        
        except Exception as e:
            logger.error(
                "Error handling classification skipped for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _fetch_and_update_metadata(
        self,
        article_id: str,
        tickers: list[str],
        session: str,
        received_at: datetime
    ) -> None:
        """
        Fetch ticker metadata for all tickers and update record.
        
        Fire-and-forget background task with retry logic.
        Uses received_at timestamp to determine session (stateless).
        
        CRITICAL: Ensures metadata is populated even if some tickers fail.
        """
        max_retries = 3
        retry_delay = 1.0  # Start with 1 second
        
        for attempt in range(max_retries):
            try:
                # Fetch metadata for all tickers (with retry per ticker)
                metadata_dict = {}
                failed_tickers = []
                
                metadata_errors = {}
                for ticker in tickers:
                    # Get price from NBBO (Alpaca - instant, no rate limits)
                    price = None
                    try:
                        nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                        if nbbo:
                            price = nbbo.get("mid") or nbbo.get("ask") or nbbo.get("bid")
                    except Exception as price_error:
                        logger.debug(
                            "Recall: Failed to get price from NBBO",
                            ticker=ticker,
                            error=str(price_error)
                        )
                    
                    # Get exchange from Alpaca asset info (instant, no rate limits)
                    exchange = None
                    if self.trading_client:
                        try:
                            asset = self.trading_client.get_asset(ticker)
                            if asset:
                                exchange = asset.exchange
                        except Exception as exchange_error:
                            logger.debug(
                                "Recall: Failed to get exchange from Alpaca",
                                ticker=ticker,
                                error=str(exchange_error)
                            )
                    
                    # Get industry, sector, market_cap from Yahoo Finance
                    # CRITICAL: If fetch fails, automatically queue for background retry to ensure eventual completion
                    async def update_metadata_callback(t: str, meta: Optional[Dict[str, Any]]) -> None:
                        """Callback to update record when metadata is eventually fetched (background retry)."""
                        if meta:
                            # Add price and exchange from Alpaca if available
                            if price is not None:
                                meta["price"] = price
                            if exchange:
                                meta["exchange"] = exchange
                            
                            # Fire-and-forget: Update record in background (non-blocking)
                            asyncio.create_task(
                                self.repository.update_recall_record(
                                    article_id,
                                    {"ticker_metadata": {t: meta}},
                                    session,
                                    received_at
                                )
                            )
                            logger.info(
                                "✅ RECALL: Metadata eventually fetched and updated (background retry)",
                                article_id=article_id,
                                ticker=t,
                                industry=meta.get("industry"),
                                sector=meta.get("sector")
                            )
                    
                    try:
                        # Attempt fetch with automatic queuing on failure
                        ticker_meta = await self.yahoo_finance_coordinator.fetch_metadata(
                            ticker,
                            timeout=30.0,
                            queue_on_failure=True,  # Automatically queue for background retry if fails
                            callback=update_metadata_callback
                        )
                        if ticker_meta:
                            # Add price and exchange from Alpaca
                            if price is not None:
                                ticker_meta["price"] = price
                            if exchange:
                                ticker_meta["exchange"] = exchange
                            metadata_dict[ticker] = ticker_meta
                        else:
                            # Fetch returned None - queued for background retry
                            # Store partial metadata (price/exchange) now, full metadata will be updated later
                            if price is not None or exchange:
                                metadata_dict[ticker] = {
                                    "industry": None,
                                    "sector": None,
                                    "market_cap_millions": None,
                                    "price": price,
                                    "exchange": exchange
                                }
                                metadata_errors[ticker] = "metadata_queued_for_retry"
                            else:
                                failed_tickers.append(ticker)
                                metadata_errors[ticker] = "no_data_available_queued_for_retry"
                    except asyncio.TimeoutError:
                        # Timeout - queue for background retry
                        if price is not None or exchange:
                            metadata_dict[ticker] = {
                                "industry": None,
                                "sector": None,
                                "market_cap_millions": None,
                                "price": price,
                                "exchange": exchange
                            }
                            metadata_errors[ticker] = "timeout_queued_for_retry"
                        else:
                            failed_tickers.append(ticker)
                            metadata_errors[ticker] = "api_timeout_queued_for_retry"
                        
                        # Queue for background retry
                        await self.yahoo_finance_coordinator.queue_metadata_fetch(ticker, update_metadata_callback)
                    except Exception as meta_error:
                        # Error - queue for background retry
                        if price is not None or exchange:
                            metadata_dict[ticker] = {
                                "industry": None,
                                "sector": None,
                                "market_cap_millions": None,
                                "price": price,
                                "exchange": exchange
                            }
                        error_type = type(meta_error).__name__
                        metadata_errors[ticker] = f"error_{error_type.lower()}_queued_for_retry"
                        logger.debug(
                            "Recall: Metadata fetch error - queued for background retry",
                            article_id=article_id,
                            ticker=ticker,
                            error=str(meta_error),
                            error_type=error_type
                        )
                        
                        # Queue for background retry
                        await self.yahoo_finance_coordinator.queue_metadata_fetch(ticker, update_metadata_callback)
                
                # Update record even if only partial metadata (better than nothing)
                if metadata_dict or attempt == max_retries - 1:
                    # Use passed session - do NOT recalculate from timestamp
                    # (Prevent incorrectly switching sessions due to boundary/timezone bugs)
                    pass
                    
                    # Update record in repository (include errors for failed tickers)
                    updates = {"ticker_metadata": metadata_dict}
                    if metadata_errors:
                        updates["metadata_errors"] = metadata_errors
                    
                    updated = await self.repository.update_recall_record(
                        article_id=article_id,
                        updates=updates,
                        session=session,
                        date=received_at
                    )
                    
                    if metadata_dict:
                        if updated:
                            logger.info(
                                "Recall: Updated record with metadata",
                                article_id=article_id,
                                tickers=list(metadata_dict.keys()),
                                failed_tickers=failed_tickers if failed_tickers else None,
                                attempt=attempt + 1
                            )
                        else:
                            logger.warning(
                                "Recall: Failed to update record with metadata (record not found)",
                                article_id=article_id,
                                session=session,
                                received_at=received_at.isoformat()
                            )
                        # Remove from pending if we got at least some metadata
                        async with self._metadata_lock:
                            self._pending_metadata.pop(article_id, None)
                    else:
                        logger.error(
                            "Recall: Failed to fetch metadata for all tickers after retries",
                            article_id=article_id,
                            tickers=tickers,
                            attempts=max_retries
                        )
                    
                    # If we got at least some metadata, or this is the last attempt, we're done
                    if metadata_dict or attempt == max_retries - 1:
                        break
                
                # If we got here and metadata_dict is empty, retry after delay
                if not metadata_dict and attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    
            except Exception as e:
                logger.error(
                    "Error updating record with metadata",
                    article_id=article_id,
                    tickers=tickers,
                    attempt=attempt + 1,
                    error=str(e),
                    exc_info=True
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    # Last attempt failed - log as error
                    logger.error(
                        "Recall: CRITICAL - Failed to update metadata after all retries",
                        article_id=article_id,
                        tickers=tickers,
                        error=str(e)
                    )
    
    
    async def _finalization_loop(self) -> None:
        """
        Background task that periodically ensures all metadata is populated.
        
        Runs every 5 minutes to retry failed metadata fetches.
        """
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                await self._finalize_all_metadata()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Error in finalization loop",
                    error=str(e),
                    exc_info=True
                )
    
    async def _finalize_all_metadata(self) -> None:
        """
        Finalize all pending metadata fetches.
        
        Retries failed metadata fetches and ensures all records have metadata populated.
        """
        async with self._metadata_lock:
            pending_items = list(self._pending_metadata.items())
        
        if not pending_items:
            return
        
        logger.info(
            "Recall: Finalizing metadata for pending records",
            count=len(pending_items)
        )
        
        for article_id, (tickers, session, received_at, task) in pending_items:
            # Check if task is still running
            if not task.done():
                # Task still running - wait a bit and check result
                try:
                    await asyncio.wait_for(task, timeout=30.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    # Task timed out or was cancelled - retry
                    logger.warning(
                        "Recall: Metadata task timed out, retrying",
                        article_id=article_id,
                        tickers=tickers
                    )
                    await self._retry_metadata_fetch(article_id, tickers, session, received_at)
            else:
                # Task completed - verify metadata was populated
                try:
                    # Check if record has metadata by loading file
                    file_path = self.repository._get_session_file_path("recall", session, received_at)
                    session_file = await self.repository._load_recall_file(file_path, session, received_at)
                    
                    record = None
                    for r in session_file.records:
                        if r.article_id == article_id:
                            record = r
                            break
                    
                    if record:
                        # Check if metadata is empty or missing
                        if not record.ticker_metadata or len(record.ticker_metadata) == 0:
                            logger.warning(
                                "Recall: Record has empty metadata, retrying",
                                article_id=article_id,
                                tickers=tickers
                            )
                            await self._retry_metadata_fetch(article_id, tickers, session, received_at)
                        else:
                            # Metadata populated - remove from pending
                            async with self._metadata_lock:
                                self._pending_metadata.pop(article_id, None)
                except Exception as e:
                    logger.error(
                        "Error checking metadata in finalization",
                        article_id=article_id,
                        error=str(e)
                    )
        
        # Retry pending filter_reasons and classifications
        await self._retry_pending_filter_reasons()
        await self._retry_pending_classifications()
    
    async def _retry_metadata_fetch(
        self,
        article_id: str,
        tickers: list[str],
        session: str,
        received_at: datetime
    ) -> None:
        """Retry metadata fetch for a specific article."""
        try:
            # Use coordinator (handles caching, rate limiting, queueing)
            metadata_dict = {}
            for ticker in tickers:
                # Get price from NBBO (Alpaca - instant)
                price = None
                try:
                    nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                    if nbbo:
                        price = nbbo.get("mid") or nbbo.get("ask") or nbbo.get("bid")
                except Exception:
                    pass
                
                # Get exchange from Alpaca (instant)
                exchange = None
                if self.trading_client:
                    try:
                        asset = self.trading_client.get_asset(ticker)
                        if asset:
                            exchange = asset.exchange
                    except Exception:
                        pass
                
                # Callback to update record when metadata is eventually fetched
                async def update_metadata_callback(t: str, meta: Optional[Dict[str, Any]]) -> None:
                    """Callback to update record when metadata is eventually fetched (background retry)."""
                    if meta:
                        # Add price and exchange from Alpaca if available
                        if price is not None:
                            meta["price"] = price
                        if exchange:
                            meta["exchange"] = exchange
                        
                        # Fire-and-forget: Update record in background (non-blocking)
                        asyncio.create_task(
                            self.repository.update_recall_record(
                                article_id,
                                {"ticker_metadata": {t: meta}},
                                session,
                                received_at
                            )
                        )
                        logger.info(
                            "✅ RECALL: Metadata eventually fetched and updated (retry)",
                            article_id=article_id,
                            ticker=t,
                            industry=meta.get("industry"),
                            sector=meta.get("sector")
                        )
                
                # Get industry, sector, market_cap from Yahoo Finance with automatic queuing on failure
                ticker_meta = await self.yahoo_finance_coordinator.fetch_metadata(
                    ticker,
                    timeout=30.0,
                    queue_on_failure=True,  # Automatically queue for background retry if fails
                    callback=update_metadata_callback
                )
                if ticker_meta:
                    # Add price and exchange from Alpaca
                    if price is not None:
                        ticker_meta["price"] = price
                    if exchange:
                        ticker_meta["exchange"] = exchange
                    metadata_dict[ticker] = ticker_meta
                elif price is not None or exchange:
                    # Fetch failed but we have price/exchange from Alpaca (queued for retry)
                    metadata_dict[ticker] = {
                        "industry": None,
                        "sector": None,
                        "market_cap_millions": None,
                        "price": price,
                        "exchange": exchange
                    }
            
            if metadata_dict:
                # Determine session from received_at timestamp (stateless)
                session_from_timestamp, _ = get_market_session_from_timestamp(received_at)
                if session_from_timestamp != "closed":
                    session = session_from_timestamp
                
                # Update record in repository
                await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={"ticker_metadata": metadata_dict},
                    session=session,
                    date=received_at
                )
        except Exception as e:
            logger.error(
                "Error retrying metadata fetch in finalization",
                article_id=article_id,
                error=str(e)
            )
    
    async def _retry_pending_filter_reasons(self) -> None:
        """Retry pending filter_reason updates."""
        async with self._filter_reasons_lock:
            pending = dict(self._pending_filter_reasons)
        
        for article_id, filter_reason in pending.items():
            try:
                # Use stored location if available
                record_loc = self._record_locations.get(article_id)
                if record_loc:
                    updated = await self.repository.update_recall_record(
                        article_id=article_id,
                        updates={"filter_reason": filter_reason},
                        session=record_loc[0],
                        date=record_loc[1]
                    )
                    if updated:
                        async with self._filter_reasons_lock:
                            self._pending_filter_reasons.pop(article_id, None)
                        logger.info("Recall: Finalization retried and updated filter_reason", article_id=article_id)
                else:
                    # Search recent sessions (fallback)
                    current_time = datetime.now()
                    for hours_ago in [0, 1, 2]:
                        past_time = current_time - timedelta(hours=hours_ago)
                        session_name, _ = get_market_session_from_timestamp(past_time)
                        if session_name != "closed":
                            updated = await self.repository.update_recall_record(
                                article_id=article_id,
                                updates={"filter_reason": filter_reason},
                                session=session_name,
                                date=past_time
                            )
                            if updated:
                                async with self._filter_reasons_lock:
                                    self._pending_filter_reasons.pop(article_id, None)
                                break
            except Exception as e:
                logger.error("Error retrying filter_reason update", article_id=article_id, error=str(e))

    async def _retry_pending_classifications(self) -> None:
        """Retry pending ai_classification updates."""
        async with self._classification_lock:
            pending = dict(self._pending_classifications)
        
        for article_id, classification in pending.items():
            try:
                # Use stored location if available
                record_loc = self._record_locations.get(article_id)
                if record_loc:
                    updated = await self.repository.update_recall_record(
                        article_id=article_id,
                        updates={"ai_classification": classification},
                        session=record_loc[0],
                        date=record_loc[1]
                    )
                    if updated:
                        async with self._classification_lock:
                            self._pending_classifications.pop(article_id, None)
                        logger.info("Recall: Finalization retried and updated classification", article_id=article_id)
                else:
                    # Fallback search
                    current_time = datetime.now()
                    for hours_ago in [0, 1, 2]:
                        past_time = current_time - timedelta(hours=hours_ago)
                        session_name, _ = get_market_session_from_timestamp(past_time)
                        if session_name != "closed":
                            updated = await self.repository.update_recall_record(
                                article_id=article_id,
                                updates={"ai_classification": classification},
                                session=session_name,
                                date=past_time
                            )
                            if updated:
                                async with self._classification_lock:
                                    self._pending_classifications.pop(article_id, None)
                                break
            except Exception as e:
                logger.error("Error retrying classification update", article_id=article_id, error=str(e))
    