"""
Recall statistics engine - tracks all articles with tradable tickers.
Event-driven, stateless, runs alongside main trading system.
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
from ...utils.brokerage.session_detector import get_market_session, get_market_session_from_timestamp
from .finnhub_coordinator import FinnhubCoordinator
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.brokerage.events import TradeExecutedDomainEvent, TradeFailedDomainEvent
from ...domain.brokerage.models import MarketSession
from ...infra.classification.infrastructure_models import ClassificationSkippedInfrastructureEvent

logger = get_logger(__name__)


class RecallStatsEngine:
    """
    Recall statistics engine - tracks missed trading opportunities.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events
    - Check if article has tradable tickers (NBBO available + in trading session)
    - Monitor ticker price for 5 minutes after article received
    - Record missed opportunities (1%+ moves we didn't trade)
    - Append records to JSON files in real-time
    
    Stateless: All state in repository (files), no in-memory storage.
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        repository: StatisticsRepository,
        quote_fetcher: AlpacaQuoteFetcher,
        finnhub_coordinator: FinnhubCoordinator,
        market_data_client: Optional["StockHistoricalDataClient"] = None,
        trading_client: Optional["TradingClient"] = None
    ):
        """
        Initialize recall statistics engine.

        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
            quote_fetcher: Quote fetcher for NBBO snapshots
            finnhub_coordinator: Shared Finnhub API coordinator (for industry/sector/market_cap)
            market_data_client: Optional Alpaca market data client for volume stats
            trading_client: Optional Alpaca trading client for exchange info
        """
        self.event_bus = event_bus
        self.repository = repository
        self.quote_fetcher = quote_fetcher
        self.finnhub_coordinator = finnhub_coordinator
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
        
        # Ensure Finnhub coordinator is started (may already be started by MarketDataValidator)
        if not self.finnhub_coordinator._worker_task or self.finnhub_coordinator._worker_task.done():
            await self.finnhub_coordinator.start()
        
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
        await self.finnhub_coordinator.stop()
        
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
            
            for ticker in article.tickers:
                # Skip non-US exchanges (TSX, TSXV, CSE, etc.) - Alpaca doesn't support them
                # This prevents unnecessary API calls and error logs
                if any(ticker.startswith(prefix) for prefix in ["TSX:", "TSXV:", "CSE:", "NEO:", "CBOE:"]):
                    continue
                
                # Get NBBO snapshot (this checks if ticker is tradable in current session)
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                
                if nbbo:
                    # Ticker is tradable (has NBBO in current session)
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
            
            # Fetch volume stats for ALL tradable tickers (NO FILTERING - just data collection)
            volume_stats_by_ticker = {}
            if self.market_data_client and article.published_at and tradable_tickers:
                for ticker in tradable_tickers:
                    try:
                        volume_analysis = await analyze_volume_around_event(
                            client=self.market_data_client,
                            symbol=ticker,
                            event_time=article.published_at,
                            received_at=received_at,
                            reference_nbbo=initial_nbbos.get(ticker)
                        )
                        if volume_analysis:
                            volume_stats_by_ticker[ticker] = volume_analysis.to_dict()
                            logger.debug("Recall: Fetched volume stats", article_id=article.id, ticker=ticker, move_type=volume_analysis.move_type)
                    except Exception as vol_error:
                        error_type = type(vol_error).__name__
                        error_msg = str(vol_error)
                        is_illiquidity = ("no data" in error_msg.lower() or "not found" in error_msg.lower() or "no bars" in error_msg.lower() or error_type == "ValueError")
                        
                        logger.warning("Recall: Failed to fetch volume stats", article_id=article.id, ticker=ticker, error=error_msg, likely_illiquidity=is_illiquidity)
                        
                        volume_stats_by_ticker[ticker] = {
                            "error": error_msg,
                            "error_type": error_type,
                            "likely_illiquidity": is_illiquidity
                        }
            
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
            initial_filter_reason = None
            async with self._filter_reasons_lock:
                initial_filter_reason = self._pending_filter_reasons.pop(article.id, None)
            
            initial_classification = None
            async with self._classification_lock:
                initial_classification = self._pending_classifications.pop(article.id, None)
            
            record = RecallRecord(
                article_id=article.id,
                title=article.title,
                tickers=tradable_tickers,
                session=session_enum,
                published_at=article.published_at,
                received_at=received_at,
                initial_nbbo=initial_nbbos.get(tradable_tickers[0]) if tradable_tickers else None,
                filter_reason=initial_filter_reason,  # Set immediately if known (from prefilter events)
                ai_classification=initial_classification,  # Set immediately if known (from classification events)
                volume_stats=volume_stats_by_ticker
            )
            
            # Append record immediately (with initial NBBO)
            await self.repository.append_recall_record(record, session, received_at)
            
            # TRACK LOCATION: Already done early to prevent race condition
            # self._record_locations[article.id] = (session, received_at)
            
            # Start 5-minute monitoring task (fire and forget)
            monitoring_task = asyncio.create_task(
                self._monitor_ticker_price(article.id, tradable_tickers, initial_nbbos, session, received_at)
            )
            
            async with self._monitoring_lock:
                self._monitoring_tasks[article.id] = monitoring_task
            
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
                tickers=tradable_tickers
            )
            
        except Exception as e:
            logger.error(
                "Error checking and monitoring ticker for recall",
                article_id=article.id,
                error=str(e),
                exc_info=True
            )
    
    async def _monitor_ticker_price(
        self,
        article_id: str,
        tickers: list[str],
        initial_nbbos: Dict[str, Dict[str, Any]],
        session: str,
        received_at: datetime
    ) -> None:
        """
        Monitor ticker price for 5 minutes, then check if it moved 1%+.
        
        Background task: Waits 5 minutes, then checks final price.
        """
        try:
            # Wait 5 minutes
            await asyncio.sleep(300)  # 300 seconds = 5 minutes
            
            # Check if article was traded (OPTIONAL: skip if yes? User wants to track everything)
            # async with self._traded_lock:
            #     if article_id in self._traded_articles:
            #         return  # We traded this, don't count as missed
            pass
            
            # Get final NBBO for each ticker
            final_nbbos = {}
            best_move = None
            best_ticker = None
            
            for ticker in tickers:
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                if nbbo and initial_nbbos.get(ticker):
                    initial_nbbo = initial_nbbos[ticker]
                    initial_bid = initial_nbbo.get("bid")
                    initial_ask = initial_nbbo.get("ask")
                    initial_mid = initial_nbbo.get("mid")
                    
                    final_bid = nbbo.get("bid")
                    final_ask = nbbo.get("ask")
                    final_mid = nbbo.get("mid")
                    
                    # Calculate actual tradeable price change
                    # We buy at ask (pay more), sell at bid (get less)
                    # Actual P&L = (final_bid - initial_ask) / initial_ask
                    # This is what we'd actually make if we bought and sold
                    
                    actual_pnl = None
                    if initial_ask and final_bid and initial_ask > 0:
                        actual_pnl = ((final_bid - initial_ask) / initial_ask) * 100
                    
                    # Also track mid price change for reference (but don't use for decision)
                    mid_price_change = None
                    if initial_mid and final_mid and initial_mid > 0:
                        mid_price_change = ((final_mid - initial_mid) / initial_mid) * 100
                    
                    # Use actual P&L for decision (if available), otherwise fall back to mid
                    percent_change = actual_pnl if actual_pnl is not None else mid_price_change
                    
                    if percent_change is not None:
                        final_nbbos[ticker] = {
                            **nbbo,
                            "percent_change": percent_change,
                            "mid_price_change": mid_price_change,  # Keep for reference
                            "actual_pnl": actual_pnl,  # Actual tradeable P&L
                            "moved_1_percent": percent_change >= 1.0
                        }
                        
                        # Track best move (using actual P&L)
                        if best_move is None or percent_change > best_move:
                            best_move = percent_change
                            best_ticker = ticker
            
            # Update record with price check result
            if best_ticker and final_nbbos.get(best_ticker):
                price_check = final_nbbos[best_ticker]
                
                # Update record in repository
                updated = await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={
                        "price_check_5min": price_check,
                        "price_checked_at": datetime.now()
                    },
                    session=session,
                    date=received_at
                )
                
                if updated:
                    logger.info(
                        "Recall: 5-minute price check completed",
                        article_id=article_id,
                        best_ticker=best_ticker,
                        actual_pnl=price_check.get("actual_pnl"),
                        mid_price_change=price_check.get("mid_price_change"),
                        percent_change=best_move,
                        moved_1_percent=price_check.get("moved_1_percent")
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
                    # Success - remove from pending
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
        - no_tickers: Article has no tickers
        - not_tradeable_exchange: Tickers not tradeable on NASDAQ/NYSE
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
                    
                    # Get industry, sector, market_cap from Finnhub (rate-limited, 60/min)
                    try:
                        ticker_meta = await self.finnhub_coordinator.fetch_metadata(ticker, timeout=30.0)
                        if ticker_meta:
                            # Add price and exchange from Alpaca
                            if price is not None:
                                ticker_meta["price"] = price
                            if exchange:
                                ticker_meta["exchange"] = exchange
                            metadata_dict[ticker] = ticker_meta
                        else:
                            # Finnhub returned None - but we might still have price/exchange from Alpaca
                            if price is not None or exchange:
                                metadata_dict[ticker] = {
                                    "industry": None,
                                    "sector": None,
                                    "market_cap_millions": None,
                                    "price": price,
                                    "exchange": exchange
                                }
                            else:
                                failed_tickers.append(ticker)
                                metadata_errors[ticker] = "no_data_available"
                    except asyncio.TimeoutError:
                        # Finnhub timeout - but we might still have price/exchange from Alpaca
                        if price is not None or exchange:
                            metadata_dict[ticker] = {
                                "industry": None,
                                "sector": None,
                                "market_cap_millions": None,
                                "price": price,
                                "exchange": exchange
                            }
                            metadata_errors[ticker] = "finnhub_timeout"
                        else:
                            failed_tickers.append(ticker)
                            metadata_errors[ticker] = "api_timeout"
                    except Exception as meta_error:
                        # Finnhub error - but we might still have price/exchange from Alpaca
                        if price is not None or exchange:
                            metadata_dict[ticker] = {
                                "industry": None,
                                "sector": None,
                                "market_cap_millions": None,
                                "price": price,
                                "exchange": exchange
                            }
                        error_type = type(meta_error).__name__
                        metadata_errors[ticker] = f"finnhub_error_{error_type.lower()}"
                        logger.debug(
                            "Recall: Finnhub metadata fetch error (but have price/exchange from Alpaca)",
                            article_id=article_id,
                            ticker=ticker,
                            error=str(meta_error),
                            error_type=error_type
                        )
                
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
                
                # Get industry, sector, market_cap from Finnhub
                ticker_meta = await self.finnhub_coordinator.fetch_metadata(ticker, timeout=30.0)
                if ticker_meta:
                    # Add price and exchange from Alpaca
                    if price is not None:
                        ticker_meta["price"] = price
                    if exchange:
                        ticker_meta["exchange"] = exchange
                    metadata_dict[ticker] = ticker_meta
                elif price is not None or exchange:
                    # Finnhub failed but we have price/exchange from Alpaca
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
    