"""
Signal statistics engine - tracks actual trade executions.
Event-driven, stateless, runs alongside main trading system.
"""
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from decimal import Decimal

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...shared.statistics.models import SignalRecord
from ...infra.statistics.repository import StatisticsRepository
from ...utils.brokerage.session_detector import get_market_session, get_market_session_from_timestamp
from ...domain.brokerage.events import TradeExecutedDomainEvent
from ...domain.brokerage.models import MarketSession
from ...infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
from .finnhub_coordinator import FinnhubCoordinator

try:
    from alpaca.trading.client import TradingClient
except ImportError:
    TradingClient = None

logger = get_logger(__name__)


class SignalStatsEngine:
    """
    Signal statistics engine - tracks actual trade executions.
    
    Responsibilities:
    - Subscribe to Domain.TradeExecuted events
    - Extract trade details (price, spread, ticker metadata)
    - Append records to JSON files in real-time
    - Track profit/loss when trades exit (future enhancement)
    
    Stateless: All state in repository (files), no in-memory storage.
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        repository: StatisticsRepository,
        finnhub_coordinator: FinnhubCoordinator,
        quote_fetcher: Optional[AlpacaQuoteFetcher] = None,
        trading_client: Optional["TradingClient"] = None
    ):
        """
        Initialize signal statistics engine.

        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
            finnhub_coordinator: Shared Finnhub API coordinator (for industry/sector/market_cap)
            quote_fetcher: Optional quote fetcher for price from NBBO
            trading_client: Optional trading client for exchange info
        """
        self.event_bus = event_bus
        self.repository = repository
        self.finnhub_coordinator = finnhub_coordinator
        self.quote_fetcher = quote_fetcher
        self.trading_client = trading_client
        
        # Track pending metadata fetches (trade_id -> (ticker, session, executed_at, task))
        self._pending_metadata: Dict[str, tuple[str, str, datetime, asyncio.Task]] = {}
        self._metadata_lock = asyncio.Lock()
        
        # Background task for finalization (ensures metadata is populated)
        self._finalization_task: Optional[asyncio.Task] = None
        
        # Store wrappers for unsubscribe
        self._trade_executed_wrapper: Optional[Any] = None
        
        logger.info("SignalStatsEngine initialized")
    
    async def start(self) -> None:
        """Start engine - subscribe to events."""
        # Subscribe to typed events using subscribe_typed helper
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent,
            self._handle_trade_executed,
        )
        
        # Start finalization task (runs every 5 minutes to ensure metadata is populated)
        self._finalization_task = asyncio.create_task(self._finalization_loop())
        
        # Ensure Finnhub coordinator is started (may already be started by MarketDataValidator)
        # Ensure Finnhub coordinator is started (may already be started by MarketDataValidator)
        if not self.finnhub_coordinator._worker_task or self.finnhub_coordinator._worker_task.done():
            await self.finnhub_coordinator.start()
        
        logger.info("SignalStatsEngine started - subscribed to events")
    
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
        
        # Stop Finnhub coordinator
        await self.finnhub_coordinator.stop()
        
        logger.info("SignalStatsEngine stopped")
    
    async def _handle_trade_executed(
        self,
        event: TradeExecutedDomainEvent,
    ) -> None:
        """Handle Domain.TradeExecuted event."""
        try:
            trade_result = event.trade_result
            
            # Only track successful trades
            if not trade_result.success or trade_result.status.value != "executed":
                logger.debug(
                    "Signal: Skipping non-executed trade",
                    success=trade_result.success,
                    status=trade_result.status.value
                )
                return
            
            # Get session from trade_result (it has the correct session)
            session_enum = trade_result.session
            # Map MarketSession enum to string format used by repository
            if session_enum == MarketSession.MARKET:
                session = "market_hours"
            elif session_enum == MarketSession.PREMARKET:
                session = "premarket"
            elif session_enum == MarketSession.POSTMARKET:
                session = "postmarket"
            else:
                # Fallback: try to get from current market session
                current_session, _ = get_market_session()
                if current_session == "closed":
                    session = "market_hours"  # Default fallback
                else:
                    session = current_session
            
            # Extract entry details from TradeResult
            entry_price = float(trade_result.fill_price) if trade_result.fill_price else 0.0
            entry_shares = int(trade_result.shares) if trade_result.shares else 0
            entry_amount_usd = float(trade_result.total_cost) if trade_result.total_cost else 0.0
            
            # Extract NBBO from trade_request dict (stored by mapper as _spread_info)
            trade_request_dict = trade_result.trade_request
            entry_nbbo = trade_request_dict.get("_spread_info", {})
            if not entry_nbbo:
                # Try alternative location
                entry_nbbo = trade_request_dict.get("spread_info", {})
            
            # Get ticker and article_id
            ticker = trade_result.get_ticker()
            article_id = trade_request_dict.get("article_id")
            
            # Generate trade_id (use order_id if available, otherwise generate)
            trade_id = trade_request_dict.get("order_id") or trade_request_dict.get("_order_id")
            if not trade_id:
                trade_id = f"trade_{int(event.executed_at.timestamp() * 1000)}"
            
            # Map session string back to MarketSession enum for record
            if session == "market_hours":
                session_enum = MarketSession.MARKET
            elif session == "premarket":
                session_enum = MarketSession.PREMARKET
            elif session == "postmarket":
                session_enum = MarketSession.POSTMARKET
            else:
                session_enum = MarketSession.MARKET  # Default fallback
            
            # Create signal record (metadata will be added later)
            record = SignalRecord(
                trade_id=trade_id,
                article_id=article_id,
                ticker=ticker,
                session=session_enum,
                executed_at=event.executed_at,
                entry_price=entry_price,
                entry_shares=entry_shares,
                entry_amount_usd=entry_amount_usd,
                entry_nbbo=entry_nbbo if entry_nbbo else None
            )
            
            # Append record immediately (before metadata fetch)
            await self.repository.append_signal_record(
                record=record,
                session=session,
                date=event.executed_at
            )
            
            # Fetch ticker metadata asynchronously (tracked for finalization)
            # Pass executed_at so we can determine session from timestamp (stateless)
            metadata_task = asyncio.create_task(
                self._fetch_and_update_metadata(record, event.executed_at)
            )
            
            # Track pending metadata fetch
            async with self._metadata_lock:
                self._pending_metadata[record.trade_id] = (record.ticker, session, event.executed_at, metadata_task)
            
            logger.debug(
                "Signal: Recorded trade execution",
                trade_id=trade_id,
                ticker=ticker,
                article_id=article_id
            )
            
        except Exception as e:
            logger.error(
                "Error handling trade executed for signal",
                error=str(e),
                exc_info=True
            )
    
    async def _fetch_and_update_metadata(
        self,
        record: SignalRecord,
        executed_at: datetime
    ) -> None:
        """
        Fetch ticker metadata and update record.
        
        Fire-and-forget background task with retry logic.
        Uses executed_at timestamp to determine session (stateless).
        
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
                
                # Get industry, sector, market_cap from Finnhub (rate-limited, 60/min)
                metadata = await self.finnhub_coordinator.fetch_metadata(record.ticker, timeout=30.0)
                if metadata:
                    # Add price and exchange from Alpaca
                    if price is not None:
                        metadata["price"] = price
                    if exchange:
                        metadata["exchange"] = exchange
                    # Determine session from executed_at timestamp (stateless)
                    session, _ = get_market_session_from_timestamp(executed_at)
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
                    await self.repository.update_signal_record(
                        trade_id=record.trade_id,
                        updates={"ticker_metadata": metadata},
                        session=session,
                        date=executed_at
                    )
                    
                    logger.info(
                        "Signal: Updated record with metadata",
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
                            "Signal: Metadata fetch failed, retrying",
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
                            "Signal: CRITICAL - Failed to fetch metadata after all retries",
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
                        "Signal: CRITICAL - Failed to update metadata after all retries",
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
            "Signal: Finalizing metadata for pending records",
            count=len(pending_items)
        )
        
        for trade_id, (ticker, session, executed_at, task) in pending_items:
            # Check if task is still running
            if not task.done():
                # Task still running - wait a bit and check result
                try:
                    await asyncio.wait_for(task, timeout=30.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    # Task timed out or was cancelled - retry
                    logger.warning(
                        "Signal: Metadata task timed out, retrying",
                        trade_id=trade_id,
                        ticker=ticker
                    )
                    await self._retry_metadata_fetch(trade_id, ticker, session, executed_at)
            else:
                # Task completed - verify metadata was populated
                try:
                    # Check if record has metadata by loading file
                    file_path = self.repository._get_session_file_path("signal", session, executed_at)
                    session_file = await self.repository._load_signal_file(file_path, session, executed_at)
                    
                    record = None
                    for r in session_file.records:
                        if r.trade_id == trade_id:
                            record = r
                            break
                    
                    if record:
                        # Check if metadata is None or empty
                        if record.ticker_metadata is None:
                            logger.warning(
                                "Signal: Record has null metadata, retrying",
                                trade_id=trade_id,
                                ticker=ticker
                            )
                            await self._retry_metadata_fetch(trade_id, ticker, session, executed_at)
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
        executed_at: datetime
    ) -> None:
        """Retry metadata fetch for a specific trade."""
        try:
            # Use coordinator (handles caching, rate limiting, queueing)
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
            
            # Get industry, sector, market_cap from Finnhub (rate-limited, 60/min)
            metadata = await self.finnhub_coordinator.fetch_metadata(ticker, timeout=30.0)
            if metadata:
                # Add price and exchange from Alpaca
                if price is not None:
                    metadata["price"] = price
                if exchange:
                    metadata["exchange"] = exchange
                
                # Update record in repository
                await self.repository.update_signal_record(
                    trade_id=trade_id,
                    updates={"ticker_metadata": metadata},
                    session=session,
                    date=executed_at
                )
        except Exception as e:
            logger.error(
                "Error retrying metadata fetch in finalization",
                trade_id=trade_id,
                error=str(e)
            )
