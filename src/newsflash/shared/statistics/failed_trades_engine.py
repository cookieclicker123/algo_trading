"""
Failed trades statistics engine - tracks failed trade attempts.
Event-driven, stateless, runs alongside main trading system.
"""
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...shared.statistics.models import FailedTradeRecord
from ...infra.statistics.repository import StatisticsRepository
from ...utils.brokerage.session_detector import get_market_session, get_market_session_from_timestamp
from ...domain.brokerage.events import TradeFailedDomainEvent
from ...domain.brokerage.models import MarketSession
from ...infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
from .yahoo_finance_coordinator import YahooFinanceCoordinator

try:
    from alpaca.trading.client import TradingClient
except ImportError:
    TradingClient = None

logger = get_logger(__name__)


class FailedTradeStatsEngine:
    """
    Failed trades statistics engine - tracks failed trade attempts.
    
    Responsibilities:
    - Subscribe to Domain.TradeFailed events
    - Extract failure details (error, NBBO at failure, ladder attempts)
    - Append records to JSON files in real-time
    - Track failure patterns for analysis
    
    Stateless: All state in repository (files), no in-memory storage.
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        repository: StatisticsRepository,
        yahoo_finance_coordinator: YahooFinanceCoordinator,
        quote_fetcher: Optional[AlpacaQuoteFetcher] = None,
        trading_client: Optional["TradingClient"] = None
    ):
        """
        Initialize failed trades statistics engine.

        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
            yahoo_finance_coordinator: Shared Yahoo Finance coordinator (for industry/sector/market_cap)
            quote_fetcher: Optional quote fetcher for NBBO at failure time
            trading_client: Optional trading client for exchange info
        """
        self.event_bus = event_bus
        self.repository = repository
        self.yahoo_finance_coordinator = yahoo_finance_coordinator
        self.quote_fetcher = quote_fetcher
        self.trading_client = trading_client
        
        # Track pending metadata fetches (trade_id -> (ticker, session, failed_at, task))
        self._pending_metadata: Dict[str, tuple[str, str, datetime, asyncio.Task]] = {}
        self._metadata_lock = asyncio.Lock()
        
        # Background task for finalization (ensures metadata is populated)
        self._finalization_task: Optional[asyncio.Task] = None
        
        # Store wrappers for unsubscribe
        self._trade_failed_wrapper: Optional[Any] = None
        
        logger.info("FailedTradeStatsEngine initialized")
    
    async def start(self) -> None:
        """Start engine - subscribe to events."""
        # Subscribe to typed events using subscribe_typed helper
        self._trade_failed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_FAILED,
            TradeFailedDomainEvent,
            self._handle_trade_failed,
        )
        
        # Start finalization task (runs every 5 minutes to ensure metadata is populated)
        self._finalization_task = asyncio.create_task(self._finalization_loop())
        
        # Ensure YahooFinanceCoordinator is started (may already be started by MarketDataValidator)
        await self.yahoo_finance_coordinator.start()
        
        logger.info("FailedTradeStatsEngine started - subscribed to events")
    
    async def stop(self) -> None:
        """Stop engine and finalize metadata."""
        # Cancel finalization task
        if self._finalization_task:
            self._finalization_task.cancel()
            try:
                await self._finalization_task
            except asyncio.CancelledError:
                pass
        
        # Finalize all pending metadata before stopping
        await self._finalize_all_metadata()
        
        # Stop YahooFinanceCoordinator
        await self.yahoo_finance_coordinator.stop()
        
        logger.info("FailedTradeStatsEngine stopped")
    
    async def _handle_trade_failed(
        self,
        event: TradeFailedDomainEvent,
    ) -> None:
        """Handle Domain.TradeFailed event."""
        try:
            trade_request = event.trade_request
            
            # Get ticker and article_id
            ticker = trade_request.ticker
            article_id = trade_request.article_id
            
            # Generate trade_id (order_id is infrastructure concern, not in domain model)
            trade_id = f"failed_{int(event.failed_at.timestamp() * 1000)}"
            
            # Get session from failed_at timestamp (stateless)
            session, _ = get_market_session_from_timestamp(event.failed_at)
            if session == "closed":
                # Fallback: try to get from current market session
                current_session, _ = get_market_session()
                if current_session == "closed":
                    session = "market_hours"  # Default fallback
                else:
                    session = current_session
            
            # Map session string to MarketSession enum
            if session == "market_hours":
                session_enum = MarketSession.MARKET
            elif session == "premarket":
                session_enum = MarketSession.PREMARKET
            elif session == "postmarket":
                session_enum = MarketSession.POSTMARKET
            else:
                session_enum = MarketSession.MARKET  # Default fallback
            
            # Extract time of day
            failed_at_et = event.failed_at  # Assuming UTC, adjust if needed
            hour = failed_at_et.hour
            minute = failed_at_et.minute
            time_of_day = f"{hour:02d}:{minute:02d}"
            
            # Get NBBO at failure time (if quote_fetcher available)
            failure_nbbo = None
            if self.quote_fetcher:
                try:
                    nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                    if nbbo:
                        failure_nbbo = {
                            "bid": nbbo.get("bid"),
                            "ask": nbbo.get("ask"),
                            "spread": nbbo.get("spread"),
                            "mid": nbbo.get("mid"),
                            "bid_size": nbbo.get("bid_size"),
                            "ask_size": nbbo.get("ask_size")
                        }
                except Exception as e:
                    logger.debug(
                        "Failed to fetch NBBO at failure time",
                        ticker=ticker,
                        error=str(e)
                    )
            
            # Extract trade request details
            requested_shares = trade_request.shares
            requested_price = None  # limit_price is infrastructure concern, not in domain model
            order_type = None  # order_type is infrastructure concern, not in domain model
            
            # Create failed trade record (metadata will be added later)
            record = FailedTradeRecord(
                trade_id=trade_id,
                article_id=article_id,
                ticker=ticker,
                session=session_enum,
                failed_at=event.failed_at,
                failure_reason=event.error,
                ladder_attempts=event.ladder_attempts,
                ladder_attempts_detail=event.ladder_attempts_detail,
                failure_nbbo=failure_nbbo,
                hour=hour,
                minute=minute,
                time_of_day=time_of_day,
                requested_shares=requested_shares,
                requested_price=requested_price,
                order_type=order_type
            )
            
            # Append record immediately (before metadata fetch)
            await self.repository.append_failed_trade_record(
                record=record,
                session=session,
                date=event.failed_at
            )
            
            # Fetch ticker metadata asynchronously (tracked for finalization)
            metadata_task = asyncio.create_task(
                self._fetch_and_update_metadata(record, event.failed_at)
            )
            
            # Track pending metadata fetch
            async with self._metadata_lock:
                self._pending_metadata[record.trade_id] = (record.ticker, session, event.failed_at, metadata_task)
            
            logger.debug(
                "FailedTrade: Recorded failed trade",
                trade_id=trade_id,
                ticker=ticker,
                article_id=article_id,
                error=event.error
            )
            
        except Exception as e:
            logger.error(
                "Error handling trade failed for failed trades engine",
                error=str(e),
                exc_info=True
            )
    
    async def _fetch_and_update_metadata(
        self,
        record: FailedTradeRecord,
        failed_at: datetime
    ) -> None:
        """
        Fetch ticker metadata and update record.
        
        Fire-and-forget background task with retry logic.
        Uses failed_at timestamp to determine session (stateless).
        
        CRITICAL: Ensures metadata is populated even if initial fetch fails.
        """
        max_retries = 3
        retry_delay = 1.0  # Start with 1 second
        
        for attempt in range(max_retries):
            try:
                # Get price from NBBO (Alpaca - instant)
                price = None
                if self.quote_fetcher:
                    try:
                        nbbo = await self.quote_fetcher.get_nbbo_snapshot(record.ticker)
                        if nbbo:
                            price = nbbo.get("mid") or nbbo.get("ask") or nbbo.get("bid")
                    except Exception:
                        pass
                
                # Get exchange from Alpaca (instant)
                exchange = None
                if self.trading_client:
                    try:
                        asset = self.trading_client.get_asset(record.ticker)
                        if asset:
                            exchange = asset.exchange
                    except Exception:
                        pass
                
                # Get industry, sector, market_cap from YahooFinance (rate-limited)
                metadata = await self.yahoo_finance_coordinator.fetch_metadata(record.ticker, timeout=30.0)
                if metadata:
                    # Add price and exchange from Alpaca
                    if price is not None:
                        metadata["price"] = price
                    if exchange:
                        metadata["exchange"] = exchange
                    
                    # Determine session from failed_at timestamp (stateless)
                    session, _ = get_market_session_from_timestamp(failed_at)
                    if session == "closed":
                        # Fallback: use session from record
                        session_enum = record.session
                        if session_enum == MarketSession.MARKET:
                            session = "market_hours"
                        elif session_enum == MarketSession.PREMARKET:
                            session = "premarket"
                        elif session_enum == MarketSession.POSTMARKET:
                            session = "postmarket"
                        else:
                            session = "market_hours"  # Default fallback
                    
                    # Update record in repository
                    await self.repository.update_failed_trade_record(
                        trade_id=record.trade_id,
                        updates={"ticker_metadata": metadata},
                        session=session,
                        date=failed_at
                    )
                    
                    logger.info(
                        "FailedTrade: Updated record with metadata",
                        trade_id=record.trade_id,
                        ticker=record.ticker,
                        attempt=attempt + 1 if attempt > 0 else None
                    )
                    # Remove from pending tracking
                    async with self._metadata_lock:
                        self._pending_metadata.pop(record.trade_id, None)
                    break  # Success - exit retry loop
                else:
                    # Metadata fetch failed
                    if attempt < max_retries - 1:
                        logger.warning(
                            "FailedTrade: Metadata fetch failed, retrying",
                            trade_id=record.trade_id,
                            ticker=record.ticker,
                            attempt=attempt + 1,
                            max_retries=max_retries
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        # Last attempt failed
                        logger.error(
                            "FailedTrade: CRITICAL - Failed to fetch metadata after all retries",
                            trade_id=record.trade_id,
                            ticker=record.ticker,
                            attempts=max_retries
                        )
                        
            except Exception as e:
                logger.error(
                    "Error updating record with metadata",
                    trade_id=record.trade_id,
                    ticker=record.ticker,
                    attempt=attempt + 1,
                    error=str(e),
                    exc_info=True
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(
                        "FailedTrade: CRITICAL - Failed to update metadata after all retries",
                        trade_id=record.trade_id,
                        ticker=record.ticker,
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
            "FailedTrade: Finalizing metadata for pending records",
            count=len(pending_items)
        )
        
        for trade_id, (ticker, session, failed_at, task) in pending_items:
            # Check if task is still running
            if not task.done():
                # Task still running - wait a bit and check result
                try:
                    await asyncio.wait_for(task, timeout=30.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    # Task timed out or was cancelled - retry
                    logger.warning(
                        "FailedTrade: Metadata task timed out, retrying",
                        trade_id=trade_id,
                        ticker=ticker
                    )
                    await self._retry_metadata_fetch(trade_id, ticker, session, failed_at)
            else:
                # Task completed - verify metadata was populated
                try:
                    # Check if record has metadata by loading file
                    file_path = self.repository._get_session_file_path("failed_trades", session, failed_at)
                    session_file = await self.repository._load_failed_trade_file(file_path, session, failed_at)
                    
                    record = None
                    for r in session_file.records:
                        if r.trade_id == trade_id:
                            record = r
                            break
                    
                    if record:
                        # Check if metadata is None or empty
                        if record.ticker_metadata is None:
                            logger.warning(
                                "FailedTrade: Record has null metadata, retrying",
                                trade_id=trade_id,
                                ticker=ticker
                            )
                            await self._retry_metadata_fetch(trade_id, ticker, session, failed_at)
                        else:
                            # Metadata populated - remove from pending
                            async with self._metadata_lock:
                                self._pending_metadata.pop(trade_id, None)
                except Exception as e:
                    logger.error(
                        "Error checking metadata in finalization",
                        trade_id=trade_id,
                        error=str(e)
                    )
    
    async def _retry_metadata_fetch(
        self,
        trade_id: str,
        ticker: str,
        session: str,
        failed_at: datetime
    ) -> None:
        """Retry metadata fetch for a specific failed trade."""
        try:
            # Get price from NBBO (Alpaca - instant)
            price = None
            if self.quote_fetcher:
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
            
            # Get industry, sector, market_cap from YahooFinance (rate-limited)
            metadata = await self.yahoo_finance_coordinator.fetch_metadata(ticker, timeout=30.0)
            if metadata:
                # Add price and exchange from Alpaca
                if price is not None:
                    metadata["price"] = price
                if exchange:
                    metadata["exchange"] = exchange
                
                # Update record in repository
                await self.repository.update_failed_trade_record(
                    trade_id=trade_id,
                    updates={"ticker_metadata": metadata},
                    session=session,
                    date=failed_at
                )
        except Exception as e:
            logger.error(
                "Error retrying metadata fetch in finalization",
                trade_id=trade_id,
                error=str(e)
            )
