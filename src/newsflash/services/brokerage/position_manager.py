"""
Position Manager - Tracks open positions with stop loss protection.

EXIT STRATEGY (Let Winners Run):
- NO automatic take-profits (user exits manually via Telegram when they see weakness)
- Stop Loss: 5% below actual entry price (limits max loss per trade)
- Manual Exit: User can exit anytime via Telegram /exit command
- Time-based Exit: Handled by ExitTradeUseCase (10 min default, can /hold to extend)

STOP LOSS:
- 5% below actual entry price (NOT NBBO mid)
- Anchored to fill price so max loss per trade is capped at ~5%
- Winners massively outweigh the occasional 5% loss on failed trades

Uses WebSocket for real-time price monitoring (sub-100ms latency).
Falls back to 500ms polling if WebSocket unavailable.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List, Any, Callable
from enum import Enum

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...models.base_models import TradeRequest
from ...domain.brokerage.models import TradeAction, TradeInstrument

logger = get_logger(__name__)


class ConvictionLevel(Enum):
    """Trade conviction level based on confluence scoring (determines position size)."""
    MINIMUM = "minimum"             # Score 0 → surge window → $5k position
    STANDARD = "standard"           # Score 1 → $7.5k position
    HIGH = "high"                   # Score 2 → $10k position
    VERY_HIGH = "very_high"         # Score 3 → $15k position


# Stop loss configuration
STOP_LOSS_PCT = 0.05  # 5% below actual entry price


@dataclass
class Position:
    """Represents an open position with stop loss protection."""
    ticker: str
    entry_price: float
    shares: float
    entry_time: datetime
    article_id: str

    # Conviction level (determines position size)
    conviction: ConvictionLevel = ConvictionLevel.STANDARD

    # Stop loss tracking - anchored to actual entry price
    initial_nbbo_mid: Optional[float] = None  # Kept for logging/analytics
    stop_loss_triggered: bool = False

    # Position tracking
    shares_remaining: float = field(init=False)

    # P&L tracking
    total_cost_basis: float = field(init=False)

    # Current price tracking (updated by monitor)
    last_price: Optional[float] = None
    last_price_time: Optional[datetime] = None

    def __post_init__(self):
        self.shares_remaining = self.shares
        self.total_cost_basis = self.entry_price * self.shares

    @property
    def stop_loss_price(self) -> Optional[float]:
        """Calculate stop loss price (5% below actual entry price)."""
        if self.entry_price:
            return self.entry_price * (1 - STOP_LOSS_PCT)
        return None

    @property
    def current_profit_pct(self) -> Optional[float]:
        """Calculate current profit % based on last known price."""
        if self.last_price and self.entry_price:
            return (self.last_price - self.entry_price) / self.entry_price
        return None

    @property
    def unrealized_pnl(self) -> Optional[float]:
        """Calculate unrealized P&L based on last known price."""
        if self.last_price:
            return (self.last_price - self.entry_price) * self.shares_remaining
        return None


class PositionManager:
    """
    Manages open positions with stop loss protection.

    Uses WebSocket streaming for real-time price monitoring (sub-100ms).
    Falls back to 500ms REST polling if WebSocket unavailable.

    Exit conditions:
    - Stop loss: 5% below entry price (automatic)
    - Manual exit: User triggers via Telegram /exit
    - Time-based exit: 10 min default (ExitTradeUseCase)

    Does NOT:
    - Execute trades (brokerage service does that)
    - Manage the WebSocket connection (stream manager does that)
    """

    def __init__(
        self,
        event_bus: AsyncEventBus,
        quote_fetcher=None,  # AlpacaQuoteFetcher (for REST fallback)
        stream_manager=None,  # AlpacaMarketDataStreamManager (for WebSocket)
        poll_interval: float = 0.5,  # 500ms fallback polling
        enabled: bool = True,
    ):
        self.event_bus = event_bus
        self.quote_fetcher = quote_fetcher
        self.stream_manager = stream_manager
        self.poll_interval = poll_interval
        self.enabled = enabled

        # Open positions by ticker
        self._positions: Dict[str, Position] = {}

        # Lock for thread-safe position updates
        self._lock = asyncio.Lock()

        # Monitor task (fallback polling)
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

        # Manual override flags (ticker -> should_exit_all)
        self._manual_exits: Dict[str, bool] = {}

        # Event subscription tracking
        self._quote_subscription_id: Optional[Callable] = None

        # Exit in progress tracking (prevent duplicate exits)
        self._exits_in_progress: set = set()

        logger.info(
            "PositionManager initialized",
            enabled=enabled,
            poll_interval=poll_interval,
            has_stream_manager=stream_manager is not None,
            stop_loss_pct=f"{STOP_LOSS_PCT*100:.0f}%"
        )

    async def add_position(
        self,
        ticker: str,
        entry_price: float,
        shares: float,
        article_id: str,
        conviction: ConvictionLevel = ConvictionLevel.STANDARD,
        initial_nbbo_mid: Optional[float] = None,
    ) -> Position:
        """Add a new position to track."""
        async with self._lock:
            position = Position(
                ticker=ticker,
                entry_price=entry_price,
                shares=shares,
                entry_time=datetime.now(),
                article_id=article_id,
                conviction=conviction,
                initial_nbbo_mid=initial_nbbo_mid,
            )

            self._positions[ticker] = position

            # Subscribe to WebSocket quotes for this symbol
            if self.stream_manager:
                await self.stream_manager.subscribe_symbol(ticker)

            logger.info(
                "Position added (stop loss + let winners run)",
                ticker=ticker,
                entry_price=entry_price,
                shares=shares,
                cost_basis=position.total_cost_basis,
                conviction=conviction.value,
                stop_loss_price=position.stop_loss_price,
            )

            return position

    async def get_position(self, ticker: str) -> Optional[Position]:
        """Get position by ticker."""
        async with self._lock:
            return self._positions.get(ticker)

    async def get_all_positions(self) -> List[Position]:
        """Get all open positions."""
        async with self._lock:
            return list(self._positions.values())

    async def remove_position(self, ticker: str) -> None:
        """Remove a fully exited position and clean up quote stream subscription."""
        async with self._lock:
            if ticker in self._positions:
                position = self._positions.pop(ticker)
                self._exits_in_progress.discard(ticker)

                # Unsubscribe from quote stream to prevent memory leak
                if self.stream_manager:
                    try:
                        await self.stream_manager.unsubscribe_symbol(ticker)
                    except Exception as e:
                        logger.warning(f"Failed to unsubscribe from {ticker} quotes: {e}")

                logger.info(
                    "Position removed (fully exited)",
                    ticker=ticker,
                )

    async def request_manual_exit(self, ticker: str) -> bool:
        """Request manual exit of entire position."""
        async with self._lock:
            if ticker in self._positions:
                self._manual_exits[ticker] = True
                logger.info(
                    "Manual exit requested",
                    ticker=ticker,
                    shares_remaining=self._positions[ticker].shares_remaining
                )
                return True
            return False

    async def _handle_quote_event(self, event_type: str, event_data: dict) -> None:
        """Handle QuoteReceived event from WebSocket stream."""
        try:
            symbol = event_data.get("symbol")
            if not symbol or symbol not in self._positions:
                return

            # Get bid price from quote (we sell at bid)
            nbbo = event_data.get("nbbo", {})
            bid_price = nbbo.get("bid") if isinstance(nbbo, dict) else getattr(nbbo, "bid", None)

            if not bid_price:
                return

            # Update position's last price
            async with self._lock:
                if symbol in self._positions:
                    position = self._positions[symbol]
                    position.last_price = bid_price
                    position.last_price_time = datetime.now()

            # Check exit conditions
            await self._check_position_exit(symbol, bid_price)

        except Exception as e:
            logger.error(f"Error handling quote event: {e}", exc_info=True)

    async def _check_position_exit(self, ticker: str, current_price: float) -> None:
        """Check and execute exit for a single position."""
        async with self._lock:
            if ticker not in self._positions:
                return

            position = self._positions[ticker]

            # Check for manual exit request first
            if self._manual_exits.get(ticker):
                if ticker not in self._exits_in_progress:
                    self._exits_in_progress.add(ticker)
                    asyncio.create_task(self._execute_exit_async(
                        position,
                        position.shares_remaining,
                        "manual_exit",
                        (current_price - position.entry_price) / position.entry_price
                    ))
                    self._manual_exits.pop(ticker, None)
                return

            # 🛑 STOP LOSS CHECK: Exit entire position if price drops 5% below entry
            if position.stop_loss_price and not position.stop_loss_triggered:
                if current_price <= position.stop_loss_price:
                    exit_key = f"{ticker}_stop_loss"
                    if exit_key not in self._exits_in_progress:
                        self._exits_in_progress.add(exit_key)
                        position.stop_loss_triggered = True
                        loss_pct = (current_price - position.entry_price) / position.entry_price
                        logger.warning(
                            f"🛑 STOP LOSS TRIGGERED: Price dropped {STOP_LOSS_PCT*100:.0f}% below entry",
                            ticker=ticker,
                            current_price=current_price,
                            stop_loss_price=position.stop_loss_price,
                            entry_price=position.entry_price,
                            loss_pct=f"{loss_pct*100:.1f}%",
                            shares=position.shares_remaining
                        )
                        asyncio.create_task(self._execute_exit_async(
                            position,
                            position.shares_remaining,
                            "stop_loss",
                            loss_pct
                        ))
                    return

            # No automatic take-profits: let winners run.
            # User exits via Telegram /exit or time-based exit (10 min).

    async def _execute_exit_async(
        self,
        position: Position,
        shares: float,
        exit_reason: str,
        profit_pct: float,
    ) -> None:
        """Execute exit and clean up tracking."""
        try:
            await self._execute_exit(position, shares, exit_reason, profit_pct)
        finally:
            exit_key = f"{position.ticker}_{exit_reason}"
            self._exits_in_progress.discard(exit_key)

    async def _execute_exit(
        self,
        position: Position,
        shares: float,
        exit_reason: str,
        profit_pct: float,
    ) -> None:
        """Execute a full exit (sell all shares)."""
        if shares <= 0:
            return

        logger.info(
            f"Executing {exit_reason} exit",
            ticker=position.ticker,
            shares=int(shares),
            profit_pct=f"{profit_pct*100:.1f}%",
            article_id=position.article_id
        )

        # Build sell trade request
        trade_request = TradeRequest(
            ticker=position.ticker,
            action=TradeAction.SELL,
            shares=int(shares),
            amount_usd=None,
            leverage=None,
            article_id=position.article_id,
            instrument=TradeInstrument.STOCK,
        )

        # Publish trade request event
        from ...domain.brokerage.events import TradeRequestDomainEvent

        exit_event = TradeRequestDomainEvent(
            trade_request=trade_request,
            article_id=position.article_id,
            requested_at=datetime.now(),
            metadata={
                "exit_reason": exit_reason,
                "profit_pct": profit_pct,
                "entry_price": position.entry_price,
                "conviction": position.conviction.value,
                "stop_loss_price": position.stop_loss_price,
                "stop_loss_triggered": position.stop_loss_triggered,
            }
        )

        await self.event_bus.publish("Domain.TradeRequested", exit_event.model_dump())

        # Update position tracking
        async with self._lock:
            position.shares_remaining -= shares

        logger.info(
            f"Exit trade request published: {exit_reason}",
            ticker=position.ticker,
            shares_sold=int(shares),
            shares_remaining=position.shares_remaining,
        )

        # Remove position if fully exited
        if position.shares_remaining <= 0:
            await self.remove_position(position.ticker)

    async def _check_all_positions(self) -> None:
        """Check all positions for exit conditions (fallback polling)."""
        async with self._lock:
            positions_to_check = list(self._positions.items())

        for ticker, position in positions_to_check:
            try:
                current_price = None

                if self.stream_manager:
                    quote = await self.stream_manager.get_latest_quote(ticker)
                    if quote:
                        current_price = quote.get("bid")

                if not current_price and self.quote_fetcher:
                    current_price = await self.quote_fetcher.get_realtime_price(ticker)

                if current_price:
                    await self._check_position_exit(ticker, current_price)

            except Exception as e:
                logger.error(f"Error checking position {ticker}: {e}", exc_info=True)

    async def _monitor_loop(self) -> None:
        """Fallback monitoring loop - polls positions periodically."""
        logger.info("Position monitor loop started (fallback polling)")

        while self._running:
            try:
                if self._positions:
                    await self._check_all_positions()

                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in position monitor loop", error=str(e), exc_info=True)
                await asyncio.sleep(self.poll_interval)

        logger.info("Position monitor loop stopped")

    async def start(self) -> None:
        """Start position monitoring."""
        if not self.enabled:
            logger.info("PositionManager disabled, not starting monitor")
            return

        if self._running:
            logger.debug("PositionManager already running")
            return

        self._running = True

        # Subscribe to WebSocket quote events for real-time exit checks
        if self.stream_manager:
            self._quote_subscription_id = self._handle_quote_event
            self.event_bus.subscribe("QuoteReceived", self._quote_subscription_id)
            logger.info("PositionManager subscribed to WebSocket quotes (real-time exits)")

        # Start fallback polling loop
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        logger.info(
            "PositionManager started (5% stop loss, let winners run)",
            poll_interval=self.poll_interval,
            has_websocket=self.stream_manager is not None,
        )

    async def stop(self) -> None:
        """Stop position monitoring."""
        self._running = False

        if self._quote_subscription_id:
            self.event_bus.unsubscribe("QuoteReceived", self._quote_subscription_id)
            self._quote_subscription_id = None

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        logger.info("PositionManager stopped", open_positions=len(self._positions))

    def get_stats(self) -> Dict:
        """Get position manager statistics."""
        positions_summary = []
        for ticker, pos in self._positions.items():
            positions_summary.append({
                "ticker": ticker,
                "entry_price": pos.entry_price,
                "shares": pos.shares,
                "shares_remaining": pos.shares_remaining,
                "conviction": pos.conviction.value,
                "entry_time": pos.entry_time.isoformat(),
                "last_price": pos.last_price,
                "profit_pct": f"{pos.current_profit_pct*100:.1f}%" if pos.current_profit_pct else None,
                "unrealized_pnl": round(pos.unrealized_pnl, 2) if pos.unrealized_pnl else None,
                "stop_loss_price": pos.stop_loss_price,
                "stop_loss_triggered": pos.stop_loss_triggered,
            })

        return {
            "enabled": self.enabled,
            "running": self._running,
            "open_positions": len(self._positions),
            "poll_interval": self.poll_interval,
            "has_websocket": self.stream_manager is not None,
            "stop_loss_pct": f"{STOP_LOSS_PCT*100:.0f}%",
            "positions": positions_summary,
        }
