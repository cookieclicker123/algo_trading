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
from .yahoo_finance_coordinator import YahooFinanceCoordinator
from .headline_classifier import get_headline_classifier

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
        yahoo_finance_coordinator: YahooFinanceCoordinator,
        quote_fetcher: Optional[AlpacaQuoteFetcher] = None,
        trading_client: Optional["TradingClient"] = None
    ):
        """
        Initialize signal statistics engine.

        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
            yahoo_finance_coordinator: Shared Yahoo Finance coordinator (for industry/sector/market_cap)
            quote_fetcher: Optional quote fetcher for price from NBBO
            trading_client: Optional trading client for exchange info
        """
        self.event_bus = event_bus
        self.repository = repository
        self.yahoo_finance_coordinator = yahoo_finance_coordinator
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
        # YahooFinanceCoordinator - just call start (no worker_task check needed)
        await self.yahoo_finance_coordinator.start()
        
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
        await self.yahoo_finance_coordinator.stop()
        
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

            # Check if this is a BUY or SELL
            trade_request_dict = trade_result.trade_request
            action = trade_request_dict.get("action", "BUY").upper()

            if action == "SELL":
                # Handle SELL - update corresponding BUY record with exit data
                await self._handle_sell_trade(event, session)
                return

            # Handle BUY - create new signal record
            await self._handle_buy_trade(event, session)

        except Exception as e:
            logger.error(
                "Error handling trade executed for signal",
                error=str(e),
                exc_info=True
            )

    async def _handle_buy_trade(
        self,
        event: TradeExecutedDomainEvent,
        session: str,
    ) -> None:
        """Handle BUY trade - create new signal record."""
        trade_result = event.trade_result

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
        headline = trade_request_dict.get("headline") or trade_request_dict.get("title")

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

        # Extract confluence and surge metadata from trade request
        # This metadata originates from auto_trade.py's check_confluence_signals()
        confluence_metadata = trade_request_dict.get("metadata", {}) or {}

        # Create signal record with confluence and surge data from the trade decision point
        record = SignalRecord(
            trade_id=trade_id,
            article_id=article_id,
            ticker=ticker,
            headline=headline,
            session=session_enum,
            executed_at=event.executed_at,
            entry_price=entry_price,
            entry_shares=entry_shares,
            entry_amount_usd=entry_amount_usd,
            entry_nbbo=entry_nbbo if entry_nbbo else None,
            # === CONFLUENCE WINDOW DATA (0-2 seconds after publication) ===
            confluence_score=confluence_metadata.get("confluence_score"),
            confluence_volume=confluence_metadata.get("confluence_volume"),
            confluence_trade_count=confluence_metadata.get("confluence_trade_count") or confluence_metadata.get("trade_count"),
            confluence_buy_volume=confluence_metadata.get("confluence_buy_volume") or confluence_metadata.get("buy_volume"),
            confluence_sell_volume=confluence_metadata.get("confluence_sell_volume") or confluence_metadata.get("sell_volume"),
            confluence_buying_pressure_pct=confluence_metadata.get("confluence_buying_pressure_pct") or confluence_metadata.get("buying_pressure_pct"),
            confluence_imbalance_ratio=confluence_metadata.get("confluence_imbalance_ratio"),
            confluence_price_excursion_pct=confluence_metadata.get("confluence_price_excursion_pct") or confluence_metadata.get("price_excursion_pct"),
            confluence_first_price=confluence_metadata.get("confluence_first_price"),
            confluence_max_price=confluence_metadata.get("confluence_max_price"),
            confluence_min_price=confluence_metadata.get("confluence_min_price"),
            confluence_vwap=confluence_metadata.get("confluence_vwap"),
            confluence_initial_spread=confluence_metadata.get("confluence_initial_spread") or confluence_metadata.get("initial_spread"),
            confluence_final_spread=confluence_metadata.get("confluence_final_spread") or confluence_metadata.get("final_spread"),
            confluence_spread_compression_pct=confluence_metadata.get("confluence_spread_compression_pct") or confluence_metadata.get("spread_compression_pct"),
            confluence_first_trade_latency_ms=confluence_metadata.get("confluence_first_trade_latency_ms"),
            confluence_avg_trade_size=confluence_metadata.get("confluence_avg_trade_size"),
            confluence_max_trade_gap_ms=confluence_metadata.get("confluence_max_trade_gap_ms"),
            confluence_has_volume_surge=confluence_metadata.get("confluence_has_volume_surge") or confluence_metadata.get("volume_surge"),
            confluence_has_price_excursion=confluence_metadata.get("confluence_has_price_excursion") or confluence_metadata.get("has_price_excursion"),
            confluence_has_buying_pressure=confluence_metadata.get("confluence_has_buying_pressure") or confluence_metadata.get("has_buying_pressure"),
            # Additional market physics for long-term analysis
            confluence_last_price=confluence_metadata.get("confluence_last_price"),
            confluence_price_direction=confluence_metadata.get("confluence_price_direction"),
            confluence_dollar_volume=confluence_metadata.get("confluence_dollar_volume"),
            confluence_max_single_trade=confluence_metadata.get("confluence_max_single_trade"),
            confluence_median_trade_size=confluence_metadata.get("confluence_median_trade_size"),
            confluence_large_trade_pct=confluence_metadata.get("confluence_large_trade_pct"),
            confluence_uptick_count=confluence_metadata.get("confluence_uptick_count"),
            confluence_downtick_count=confluence_metadata.get("confluence_downtick_count"),
            # === SURGE WINDOW DATA (8-second last chance, only if confluence failed) ===
            surge_triggered=confluence_metadata.get("surge_triggered"),
            surge_found=confluence_metadata.get("surge_found"),
            surge_detection_cycle=confluence_metadata.get("surge_detection_cycle"),
            surge_seconds_elapsed=confluence_metadata.get("surge_seconds_elapsed"),
            surge_volume=confluence_metadata.get("surge_volume"),
            surge_trade_count=confluence_metadata.get("surge_trade_count"),
            surge_buy_volume=confluence_metadata.get("surge_buy_volume"),
            surge_sell_volume=confluence_metadata.get("surge_sell_volume"),
            surge_buying_pressure_pct=confluence_metadata.get("surge_buying_pressure_pct"),
            surge_imbalance_ratio=confluence_metadata.get("surge_imbalance_ratio"),
            surge_price_excursion_pct=confluence_metadata.get("surge_price_excursion_pct") or confluence_metadata.get("surge_max_excursion_pct"),
            surge_volume_multiplier=confluence_metadata.get("surge_volume_multiplier") or confluence_metadata.get("surge_multiplier"),
            surge_trade_count_multiplier=confluence_metadata.get("surge_trade_count_multiplier"),
            surge_ask=confluence_metadata.get("surge_ask"),
            surge_bid=confluence_metadata.get("surge_bid"),
            surge_mid=confluence_metadata.get("surge_mid"),
            # Gap/trap detection (from auto_trade.py two-leg filter)
            pub_time_ask=confluence_metadata.get("pub_time_ask"),
            recv_time_ask=confluence_metadata.get("initial_ask"),  # initial_ask = recv_time_ask
            fill_time_ask=entry_nbbo.get("ask") if entry_nbbo else None,
            pub_to_recv_pct=confluence_metadata.get("pub_to_recv_pct"),
            recv_to_fill_pct=confluence_metadata.get("recv_to_fill_pct"),
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

        # Spawn async enrichment task (fire-and-forget, NEVER blocks execution)
        # Collects spread/price/volume data at T+5s, T+10s, T+30s, T+1min, T+5min
        # Also classifies headline type using AI (background only)
        asyncio.create_task(
            self._enrich_trade_stats(
                trade_id=trade_id,
                ticker=ticker,
                session=session,
                executed_at=event.executed_at,
                entry_price=entry_price,
                entry_nbbo=entry_nbbo,
                article_id=article_id,
                headline=headline,
            )
        )

        logger.info(
            "Signal: Recorded BUY trade with confluence data",
            trade_id=trade_id,
            ticker=ticker,
            entry_price=entry_price,
            shares=entry_shares,
            article_id=article_id,
            confluence_score=confluence_metadata.get("confluence_score"),
            confluence_imbalance_ratio=confluence_metadata.get("confluence_imbalance_ratio"),
            surge_found=confluence_metadata.get("surge_found"),
        )

    async def _handle_sell_trade(
        self,
        event: TradeExecutedDomainEvent,
        session: str,
    ) -> None:
        """Handle SELL trade - update corresponding BUY record with exit data."""
        trade_result = event.trade_result
        trade_request_dict = trade_result.trade_request

        ticker = trade_result.get_ticker()
        exit_price = float(trade_result.fill_price) if trade_result.fill_price else 0.0
        exit_shares = int(trade_result.shares) if trade_result.shares else 0
        exit_amount_usd = float(trade_result.total_cost) if trade_result.total_cost else 0.0

        # Get exit reason and peak data from metadata (set by position manager)
        metadata = trade_request_dict.get("metadata", {}) or {}
        exit_reason = metadata.get("exit_reason", "unknown")
        entry_price_from_meta = metadata.get("entry_price")
        highest_profit_pct = metadata.get("highest_profit_pct")
        highest_price = metadata.get("highest_price")

        # Find matching BUY records for this ticker across ALL sessions today
        # Trades opened in premarket may close in market_hours or postmarket
        try:
            matching_records = []
            found_session = None

            # Search all sessions for matching BUY record
            for search_session in ["premarket", "market_hours", "postmarket"]:
                try:
                    file_path = self.repository._get_session_file_path("signal", search_session, event.executed_at)
                    session_file = await self.repository._load_signal_file(file_path, search_session, event.executed_at)

                    # Find records for this ticker without exit data
                    session_matches = [
                        r for r in session_file.records
                        if r.ticker == ticker and r.exit_price is None
                    ]
                    if session_matches:
                        matching_records.extend(session_matches)
                        if not found_session:
                            found_session = search_session
                except Exception:
                    # Session file doesn't exist, skip
                    continue

            if not matching_records:
                logger.warning(
                    "Signal: SELL trade but no matching BUY record found in any session",
                    ticker=ticker,
                    exit_price=exit_price,
                    exit_shares=exit_shares
                )
                return

            # Use FIFO - update oldest unfilled record first (sort by executed_at)
            matching_records.sort(key=lambda r: r.executed_at)
            target_record = matching_records[0]

            # Determine which session the BUY was in
            buy_session = None
            for search_session in ["premarket", "market_hours", "postmarket"]:
                try:
                    file_path = self.repository._get_session_file_path("signal", search_session, event.executed_at)
                    session_file = await self.repository._load_signal_file(file_path, search_session, event.executed_at)
                    if any(r.trade_id == target_record.trade_id for r in session_file.records):
                        buy_session = search_session
                        break
                except Exception:
                    continue

            if not buy_session:
                buy_session = session  # Fallback to sell session

            # Use the buy_session for updates
            session = buy_session

            # Calculate P&L
            entry_price = entry_price_from_meta or target_record.entry_price
            pnl_usd = (exit_price - entry_price) * exit_shares
            pnl_percent = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0.0

            # Calculate hold duration
            hold_duration = (event.executed_at - target_record.executed_at).total_seconds()

            # Build highest_price_during_hold dict for analytics
            highest_price_data = None
            if highest_price and highest_profit_pct is not None:
                highest_price_data = {
                    "price": highest_price,
                    "percent_gain_from_entry": round(highest_profit_pct * 100, 2),
                }

            # Prepare update
            updates = {
                "exit_price": exit_price,
                "exit_shares": exit_shares,
                "exit_amount_usd": exit_amount_usd,
                "exit_reason": exit_reason,
                "exited_at": event.executed_at.isoformat(),
                "hold_duration_seconds": hold_duration,
                "profit_loss_usd": round(pnl_usd, 2),
                "profit_loss_percent": round(pnl_percent, 2),
                "highest_price_during_hold": highest_price_data,
            }

            # Update record in repository
            await self.repository.update_signal_record(
                trade_id=target_record.trade_id,
                updates=updates,
                session=session,
                date=event.executed_at
            )

            logger.info(
                "Signal: Recorded SELL trade (exit)",
                ticker=ticker,
                trade_id=target_record.trade_id,
                entry_price=entry_price,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl_usd=round(pnl_usd, 2),
                pnl_percent=f"{pnl_percent:+.1f}%",
                peak_profit_pct=f"+{highest_profit_pct*100:.1f}%" if highest_profit_pct else None,
                hold_seconds=round(hold_duration, 1)
            )

        except Exception as e:
            logger.error(
                "Error updating signal record with exit data",
                ticker=ticker,
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
                
                # Get exchange from Alpaca (use to_thread to avoid blocking event loop)
                exchange = None
                if self.trading_client:
                    try:
                        asset = await asyncio.to_thread(self.trading_client.get_asset, record.ticker)
                        if asset:
                            exchange = asset.exchange
                    except Exception:
                        pass

                # Get industry, sector, market_cap from Finnhub (rate-limited, 60/min)
                metadata = await self.yahoo_finance_coordinator.fetch_metadata(record.ticker, timeout=30.0)
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
            
            # Get exchange from Alpaca (use to_thread to avoid blocking event loop)
            exchange = None
            if self.trading_client:
                try:
                    asset = await asyncio.to_thread(self.trading_client.get_asset, ticker)
                    if asset:
                        exchange = asset.exchange
                except Exception:
                    pass

            # Get industry, sector, market_cap from Finnhub (rate-limited, 60/min)
            metadata = await self.yahoo_finance_coordinator.fetch_metadata(ticker, timeout=30.0)
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

    async def _enrich_trade_stats(
        self,
        trade_id: str,
        ticker: str,
        session: str,
        executed_at: datetime,
        entry_price: float,
        entry_nbbo: Optional[Dict[str, Any]],
        article_id: Optional[str],
        headline: Optional[str] = None,
    ) -> None:
        """
        Async enrichment of trade statistics - runs in background, NEVER blocks execution.

        Collects spread/price/volume data at multiple time points after trade.
        Also classifies headline type using AI (fast, lightweight model).
        Fire-and-forget: failures are logged but don't affect anything.
        """
        try:
            updates: Dict[str, Any] = {}

            # Classify headline type in background (uses fast Groq model)
            if headline:
                try:
                    # Get industry from metadata for industry-specific classification
                    metadata = await self.yahoo_finance_coordinator.fetch_metadata(ticker, timeout=10.0)
                    industry = metadata.get("industry") if metadata else None

                    if industry:
                        classifier = get_headline_classifier()
                        headline_type = await classifier.classify(
                            headline=headline,
                            industry=industry,
                            timeout=5.0
                        )
                        if headline_type:
                            updates["headline_type"] = headline_type
                            logger.debug(
                                "Signal: Classified headline",
                                trade_id=trade_id,
                                ticker=ticker,
                                headline_type=headline_type
                            )
                except Exception as e:
                    logger.debug(f"Headline classification failed for {ticker}: {e}")

            # Calculate immediate fill quality metrics
            if entry_nbbo:
                mid = entry_nbbo.get("mid")
                ask = entry_nbbo.get("ask")
                spread = entry_nbbo.get("spread", 0)

                if mid and entry_price:
                    slippage_mid = ((entry_price - mid) / mid * 100) if mid else 0
                    updates["slippage_vs_mid"] = round(slippage_mid, 3)

                if ask and entry_price:
                    slippage_ask = ((entry_price - ask) / ask * 100) if ask else 0
                    updates["slippage_vs_ask"] = round(slippage_ask, 3)

                if ask and spread:
                    spread_pct = (spread / ask * 100)
                    updates["spread_at_fill"] = round(spread_pct, 2)

            # Add timing info
            updates["time_of_day"] = executed_at.strftime("%H:%M")
            updates["day_of_week"] = executed_at.strftime("%A")

            # Determine minutes after market open
            hour = executed_at.hour
            minute = executed_at.minute
            if hour < 9 or (hour == 9 and minute < 30):
                # Premarket - minutes after 4 AM
                updates["minutes_after_open"] = (hour - 4) * 60 + minute
            else:
                # Regular hours - minutes after 9:30
                updates["minutes_after_open"] = (hour - 9) * 60 + (minute - 30)

            # Write immediate updates
            await self.repository.update_signal_record(
                trade_id=trade_id,
                updates=updates,
                session=session,
                date=executed_at
            )

            # Now collect time-series data at intervals
            # T+5 seconds
            await asyncio.sleep(5)
            await self._collect_timepoint_data(trade_id, ticker, session, executed_at, entry_price, "5s", updates)

            # T+10 seconds
            await asyncio.sleep(5)  # 5 more seconds
            await self._collect_timepoint_data(trade_id, ticker, session, executed_at, entry_price, "10s", updates)

            # T+30 seconds
            await asyncio.sleep(20)  # 20 more seconds
            await self._collect_timepoint_data(trade_id, ticker, session, executed_at, entry_price, "30s", updates)

            # T+1 minute
            await asyncio.sleep(30)  # 30 more seconds
            await self._collect_timepoint_data(trade_id, ticker, session, executed_at, entry_price, "1min", updates)

            # T+5 minutes - collect volume summary
            await asyncio.sleep(240)  # 4 more minutes
            await self._collect_volume_summary(trade_id, ticker, session, executed_at, updates)

            # Mark enrichment complete
            updates["enrichment_completed"] = True
            updates["enrichment_completed_at"] = datetime.now().isoformat()

            await self.repository.update_signal_record(
                trade_id=trade_id,
                updates=updates,
                session=session,
                date=executed_at
            )

            logger.info(
                "Signal: Trade enrichment completed",
                trade_id=trade_id,
                ticker=ticker,
            )

        except asyncio.CancelledError:
            logger.debug(f"Signal enrichment cancelled for {trade_id}")
        except Exception as e:
            logger.warning(
                "Signal: Enrichment failed (non-critical)",
                trade_id=trade_id,
                ticker=ticker,
                error=str(e)
            )

    async def _collect_timepoint_data(
        self,
        trade_id: str,
        ticker: str,
        session: str,
        executed_at: datetime,
        entry_price: float,
        timepoint: str,
        updates: Dict[str, Any],
    ) -> None:
        """Collect spread and price at a specific timepoint."""
        try:
            if not self.quote_fetcher:
                return

            nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
            if not nbbo:
                return

            bid = nbbo.get("bid", 0)
            ask = nbbo.get("ask", 0)
            mid = nbbo.get("mid") or ((bid + ask) / 2 if bid and ask else 0)
            spread = ask - bid if ask and bid else 0
            spread_pct = (spread / ask * 100) if ask else 0

            # Store spread
            updates[f"spread_at_{timepoint}"] = round(spread_pct, 2)

            # Store price
            if mid:
                updates[f"price_at_{timepoint}"] = round(mid, 4)

            # Calculate spread compression if we have initial spread
            if "spread_at_fill" in updates and updates["spread_at_fill"]:
                initial = updates["spread_at_fill"]
                if initial > 0:
                    compression = ((initial - spread_pct) / initial * 100)
                    if timepoint in ["5s", "10s"]:
                        # Use 10s as proxy for 2s compression (we don't have 2s data)
                        pass
                    elif timepoint == "30s":
                        updates["spread_compression_30s"] = round(compression, 1)

            # Write incremental update
            await self.repository.update_signal_record(
                trade_id=trade_id,
                updates={
                    f"spread_at_{timepoint}": updates.get(f"spread_at_{timepoint}"),
                    f"price_at_{timepoint}": updates.get(f"price_at_{timepoint}"),
                },
                session=session,
                date=executed_at
            )

        except Exception as e:
            logger.debug(f"Failed to collect {timepoint} data for {ticker}: {e}")

    async def _collect_volume_summary(
        self,
        trade_id: str,
        ticker: str,
        session: str,
        executed_at: datetime,
        updates: Dict[str, Any],
    ) -> None:
        """Collect volume summary after 5 minutes."""
        try:
            # Try to get volume data from Yahoo Finance (has historical minute bars)
            import yfinance as yf

            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d", interval="1m")

            if len(hist) > 0:
                # Get volume for first 1, 5, 10 minutes
                # This is approximate - based on available minute bars
                vol_1min = int(hist.head(1)["Volume"].sum()) if len(hist) >= 1 else None
                vol_5min = int(hist.head(5)["Volume"].sum()) if len(hist) >= 5 else None
                vol_10min = int(hist.head(10)["Volume"].sum()) if len(hist) >= 10 else None

                if vol_1min:
                    updates["volume_1min"] = vol_1min
                if vol_5min:
                    updates["volume_5min"] = vol_5min
                if vol_10min:
                    updates["volume_10min"] = vol_10min

                # Get float and ADV from info
                info = stock.info
                if info:
                    float_shares = info.get("floatShares")
                    avg_vol = info.get("averageVolume")

                    if float_shares:
                        updates["float_shares"] = int(float_shares)
                    if avg_vol:
                        updates["avg_daily_volume"] = int(avg_vol)

                    # Calculate volume vs ADV ratio
                    if vol_1min and avg_vol:
                        # Normalize to per-minute ADV (assuming 390 min trading day)
                        adv_per_min = avg_vol / 390
                        updates["volume_vs_adv_ratio"] = round(vol_1min / adv_per_min, 2) if adv_per_min else None

                await self.repository.update_signal_record(
                    trade_id=trade_id,
                    updates=updates,
                    session=session,
                    date=executed_at
                )

        except Exception as e:
            logger.debug(f"Failed to collect volume summary for {ticker}: {e}")
