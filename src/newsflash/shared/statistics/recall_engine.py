"""
Recall statistics engine - tracks all articles with tradable tickers.
Event-driven, stateless, runs alongside main trading system.
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Set

try:
    from alpaca.data import StockHistoricalDataClient
except ImportError:
    StockHistoricalDataClient = None

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType, InfrastructureEventType
from ...shared.statistics.models import RecallRecord
from ...shared.statistics.volume_analyzer import analyze_volume_around_event
from ...infra.statistics.repository import StatisticsRepository
from ...infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
from ...utils.brokerage.session_detector import get_market_session, get_market_session_from_timestamp
from .yfinance_coordinator import YFinanceCoordinator
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
        yfinance_coordinator: YFinanceCoordinator,
        market_data_client: Optional["StockHistoricalDataClient"] = None
    ):
        """
        Initialize recall statistics engine.

        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
            quote_fetcher: Quote fetcher for NBBO snapshots
            yfinance_coordinator: Shared yfinance API coordinator
            market_data_client: Optional Alpaca market data client for volume stats
        """
        self.event_bus = event_bus
        self.repository = repository
        self.quote_fetcher = quote_fetcher
        self.yfinance_coordinator = yfinance_coordinator
        self.market_data_client = market_data_client
        
        # Track active monitoring tasks (article_id -> task)
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_lock = asyncio.Lock()
        
        # Track which articles were traded (to exclude from recall)
        self._traded_articles: Set[str] = set()
        self._traded_lock = asyncio.Lock()
        
        # Track pending metadata fetches (article_id -> (tickers, session, received_at, task))
        self._pending_metadata: Dict[str, tuple[list[str], str, datetime, asyncio.Task]] = {}
        self._metadata_lock = asyncio.Lock()
        
        # Track pending filter_reasons updates (article_id -> filter_reason)
        self._pending_filter_reasons: Dict[str, list[str]] = {}
        self._filter_reasons_lock = asyncio.Lock()
        
        # Store wrappers for unsubscribe
        self._article_received_wrapper: Optional[Any] = None
        self._article_classified_wrapper: Optional[Any] = None
        self._trade_executed_wrapper: Optional[Any] = None
        self._trade_failed_wrapper: Optional[Any] = None
        self._classification_skipped_wrapper: Optional[Any] = None
        
        # Background task for finalization (ensures metadata is populated)
        self._finalization_task: Optional[asyncio.Task] = None
        
        logger.info("RecallStatsEngine initialized")
    
    async def start(self) -> None:
        """Start engine - subscribe to events."""
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
        
        # Ensure yfinance coordinator is started
        await self.yfinance_coordinator.start()
        
        logger.info("RecallStatsEngine started - subscribed to events")
    
    async def stop(self) -> None:
        """Stop engine - cancel monitoring tasks and finalize metadata."""
        # Cancel finalization task
        if self._finalization_task:
            self._finalization_task.cancel()
            try:
                await self._finalization_task
            except asyncio.CancelledError:
                pass
        
        # Finalize all pending metadata before stopping
        await self._finalize_all_metadata()
        
        # Stop yfinance coordinator
        await self.yfinance_coordinator.stop()
        
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
            
            # Fetch volume stats around publication time (NO FILTERING - just data collection)
            volume_stats_dict = None
            if self.market_data_client and article.published_at and tradable_tickers:
                try:
                    volume_analysis = await analyze_volume_around_event(
                        client=self.market_data_client,
                        symbol=tradable_tickers[0],  # Use first ticker
                        event_time=article.published_at
                    )
                    if volume_analysis:
                        volume_stats_dict = volume_analysis.to_dict()
                        logger.debug(
                            "Recall: Fetched volume stats",
                            article_id=article.id,
                            ticker=tradable_tickers[0],
                            surge_type=volume_analysis.surge_type
                        )
                except Exception as vol_error:
                    logger.warning(
                        "Recall: Failed to fetch volume stats (continuing without)",
                        article_id=article.id,
                        error=str(vol_error)
                    )
            
            # Create recall record
            # Use first ticker's NBBO as the initial_nbbo (we'll track all tickers)
            record = RecallRecord(
                article_id=article.id,
                title=article.title,
                tickers=tradable_tickers,
                session=session_enum,
                published_at=article.published_at,
                received_at=received_at,
                initial_nbbo=initial_nbbos.get(tradable_tickers[0]) if tradable_tickers else None,
                filter_reasons=[],  # Will be populated later if needed
                volume_stats=volume_stats_dict
            )
            
            # Append record immediately (with initial NBBO)
            await self.repository.append_recall_record(record, session, received_at)
            
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
            
            # Check if article was traded (skip if yes)
            async with self._traded_lock:
                if article_id in self._traded_articles:
                    return  # We traded this, don't count as missed
            
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
                await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={
                        "price_check_5min": price_check,
                        "price_checked_at": datetime.now()
                    },
                    session=session,
                    date=received_at
                )
                
                logger.info(
                    "Recall: 5-minute price check completed",
                    article_id=article_id,
                    best_ticker=best_ticker,
                    actual_pnl=price_check.get("actual_pnl"),
                    mid_price_change=price_check.get("mid_price_change"),
                    percent_change=best_move,
                    moved_1_percent=price_check.get("moved_1_percent")
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
        Handle Domain.ArticleClassified event - update filter reasons.
        
        If article wasn't classified as IMMINENT, add filter reason.
        """
        try:
            # Check if classification is IMMINENT
            if event.result.classification.value != "IMMINENT":
                # Article was filtered - add filter reason
                filter_reason = f"not_classified_{event.result.classification.value.lower()}"
                
                # Determine session and date from classified_at timestamp
                # Classification usually happens within seconds of article received, so same session/day
                session, _ = get_market_session_from_timestamp(event.classified_at)
                if session == "closed":
                    return
                
                # Update record with filter reason
                # Use classified_at as the date (for file path calculation)
                # The repository's update_recall_record will find the record if it exists
                try:
                    await self.repository.update_recall_record(
                        article_id=event.article_id,
                        updates={
                            "filter_reasons": [filter_reason]  # Will be appended to existing list in update logic
                        },
                        session=session,
                        date=event.classified_at
                    )
                    
                    logger.debug(
                        "Recall: Updated record with filter reason",
                        article_id=event.article_id,
                        classification=event.result.classification.value,
                        filter_reason=filter_reason
                    )
                except Exception as update_error:
                    # If update fails (record might not exist yet), store for retry
                    logger.warning(
                        "Recall: Failed to update filter reason, storing for retry",
                        article_id=event.article_id,
                        filter_reason=filter_reason,
                        error=str(update_error)
                    )
                    async with self._filter_reasons_lock:
                        if event.article_id not in self._pending_filter_reasons:
                            self._pending_filter_reasons[event.article_id] = []
                        if filter_reason not in self._pending_filter_reasons[event.article_id]:
                            self._pending_filter_reasons[event.article_id].append(filter_reason)
                
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
                
                # Cancel monitoring task if exists
                async with self._monitoring_lock:
                    task = self._monitoring_tasks.pop(article_id, None)
                    if task:
                        task.cancel()
                
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
                
                # Cancel monitoring task if exists
                async with self._monitoring_lock:
                    task = self._monitoring_tasks.pop(article_id, None)
                    if task:
                        task.cancel()
                
                logger.debug(
                    "Recall: Marked article as traded (failed)",
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
        """
        try:
            article_id = event.request_data.article_id
            filter_reason = f"prefilter_{event.reason}"
            
            # Determine session and date from skipped_at timestamp
            session, _ = get_market_session_from_timestamp(event.skipped_at)
            if session == "closed":
                return
            
            # Update record with filter reason
            try:
                await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={
                        "filter_reasons": [filter_reason]  # Will be appended to existing list
                    },
                    session=session,
                    date=event.skipped_at
                )
                
                logger.debug(
                    "Recall: Updated record with prefilter reason",
                    article_id=article_id,
                    reason=event.reason,
                    filter_reason=filter_reason
                )
            except Exception as update_error:
                # If update fails (record might not exist yet), store for retry
                logger.warning(
                    "Recall: Failed to update prefilter reason, storing for retry",
                    article_id=article_id,
                    filter_reason=filter_reason,
                    error=str(update_error)
                )
                async with self._filter_reasons_lock:
                    if article_id not in self._pending_filter_reasons:
                        self._pending_filter_reasons[article_id] = []
                    if filter_reason not in self._pending_filter_reasons[article_id]:
                        self._pending_filter_reasons[article_id].append(filter_reason)
                        
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
                
                for ticker in tickers:
                    # Use coordinator (handles caching, rate limiting, queueing)
                    ticker_meta = await self.yfinance_coordinator.fetch_metadata(ticker, timeout=30.0)
                    if ticker_meta:
                        metadata_dict[ticker] = ticker_meta
                    else:
                        failed_tickers.append(ticker)
                
                # Update record even if only partial metadata (better than nothing)
                if metadata_dict or attempt == max_retries - 1:
                    # Determine session from received_at timestamp (stateless)
                    session_from_timestamp, _ = get_market_session_from_timestamp(received_at)
                    if session_from_timestamp == "closed":
                        # Fallback: use session parameter
                        pass  # Use session parameter
                    else:
                        session = session_from_timestamp
                    
                    # Update record in repository
                    await self.repository.update_recall_record(
                        article_id=article_id,
                        updates={"ticker_metadata": metadata_dict},
                        session=session,
                        date=received_at
                    )
                    
                    if metadata_dict:
                        logger.info(
                            "Recall: Updated record with metadata",
                            article_id=article_id,
                            tickers=list(metadata_dict.keys()),
                            failed_tickers=failed_tickers if failed_tickers else None,
                            attempt=attempt + 1
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
        
        # Retry pending filter_reasons
        await self._retry_pending_filter_reasons()
    
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
                ticker_meta = await self.yfinance_coordinator.fetch_metadata(ticker, timeout=30.0)
                if ticker_meta:
                    metadata_dict[ticker] = ticker_meta
            
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
        """Retry pending filter_reasons updates."""
        async with self._filter_reasons_lock:
            pending = dict(self._pending_filter_reasons)
            self._pending_filter_reasons.clear()
        
        for article_id, filter_reasons in pending.items():
            try:
                # Try to find the record's session and date
                # We'll need to search recent sessions
                from datetime import timedelta
                current_time = datetime.now()
                
                # Try current session first
                session, _ = get_market_session()
                if session != "closed":
                    try:
                        await self.repository.update_recall_record(
                            article_id=article_id,
                            updates={"filter_reasons": filter_reasons},
                            session=session,
                            date=current_time
                        )
                        logger.info(
                            "Recall: Retried filter_reasons update",
                            article_id=article_id,
                            filter_reasons=filter_reasons
                        )
                        continue
                    except Exception:
                        pass
                
                # Try previous sessions (within last 24 hours)
                for hours_ago in [1, 2, 4, 8, 12, 24]:
                    past_time = current_time - timedelta(hours=hours_ago)
                    session, _ = get_market_session_from_timestamp(past_time)
                    if session != "closed":
                        try:
                            await self.repository.update_recall_record(
                                article_id=article_id,
                                updates={"filter_reasons": filter_reasons},
                                session=session,
                                date=past_time
                            )
                            logger.info(
                                "Recall: Retried filter_reasons update (found in past session)",
                                article_id=article_id,
                                filter_reasons=filter_reasons,
                                hours_ago=hours_ago
                            )
                            break
                        except Exception:
                            continue
            except Exception as e:
                    logger.error(
                        "Error retrying filter_reasons update",
                        article_id=article_id,
                        error=str(e)
                    )
    