"""
Position Manager - Tracks open positions with tiered exit system.

EXIT STRATEGY (Tiered Profit-Taking + Stop Loss + Early Exit):
Our edge is capturing 20-30% quick pops, not holding for massive winners.

EARLY EXIT (5-min 10% rule):
- After 5 minutes, if profit >= 10%, exit entire position immediately
- Rationale: ELBM hit +11% at 6:55 but was only +6% at 10 min - capture the move early
- Takes precedence over tiered exits when triggered

TIERED EXITS (automatic profit-taking):
- +15%: Exit 25% of position (lock in gains early)
- +20%: Exit 25% of remaining
- +25%: Exit 50% of remaining
- +30%: Exit 50% of remaining
- +35%: Exit 50% of remaining
- +40%: Exit remaining position

FLOOR RULE: After any tiered exit, remaining position cannot go below half
of the last exit percentage. After +15% exit, floor is +7.5%. If price drops
to floor, sell remaining position immediately.

STOP LOSS (5% below entry price):
- First 5 seconds: 0.5s confirmation (brief spikes are noise, not signal)
- After 5 seconds: Immediate execution (if crashing, it's real)
- Rationale: SMTK went -7% at 1.8s then +37% at 2.0s - grace period prevents false stops

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
from ...domain.brokerage.models import TradeRequest
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

# Grace period for first 5 seconds after entry
# Rationale: First 5 seconds are chaotic with brief spikes that recover instantly
# (e.g., SMTK went -7% at 1.8s then +37% at 2.0s - recovered in 0.2s)
# After 5 seconds, if stop is breached, it's a real crash - exit immediately
ENTRY_GRACE_PERIOD_SECONDS = 5.0  # First 5 seconds: use confirmation
STOP_LOSS_CONFIRMATION_SECONDS = 0.5  # During grace period: wait 0.5s to confirm (brief spikes recover faster)

# Tiered exit configuration - capture 15-30% pops instead of holding for massive winners
TIERED_EXIT_THRESHOLDS = [
    (0.15, 0.25),  # +15%: Exit 25% of position (floor becomes +7.5%)
    (0.20, 0.25),  # +20%: Exit 25% of remaining (floor becomes +10%)
    (0.25, 0.50),  # +25%: Exit 50% of remaining
    (0.30, 0.50),  # +30%: Exit 50% of remaining
    (0.35, 0.50),  # +35%: Exit 50% of remaining
    (0.40, 1.00),  # +40%: Exit remaining position
]

# Floor rule: If price drops below half of last exit threshold, exit remaining
FLOOR_RULE_MULTIPLIER = 0.50  # Floor at 50% of last exit level

# Early exit configuration - exit entire position if profit >= 10% after 5 minutes
# Rationale: ELBM 2026-02-03 hit +11% at 6:55 but we missed it. By 10 min it was +6%.
# If we're up 10%+ after 5 minutes, take the win instead of waiting for tiered exits.
EARLY_EXIT_MINUTES = 5.0  # Check after 5 minutes of holding
EARLY_EXIT_PROFIT_PCT = 0.10  # Exit if profit >= 10%


@dataclass
class Position:
    """Represents an open position with tiered exit system."""
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
    stop_breach_time: Optional[datetime] = None  # For grace period confirmation

    # Tiered exit tracking
    next_tier_index: int = 0  # Index into TIERED_EXIT_THRESHOLDS
    highest_profit_pct: float = 0.0  # Track peak for floor rule
    last_exit_threshold: Optional[float] = None  # For floor rule calculation
    total_exits_taken: int = 0  # Count of tiered exits completed

    # Position tracking
    shares_remaining: float = field(init=False)

    # P&L tracking
    total_cost_basis: float = field(init=False)
    realized_pnl: float = 0.0  # Sum of P&L from all exits

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

    @property
    def floor_price(self) -> Optional[float]:
        """Calculate floor price based on last exit threshold (50% of last tier)."""
        if self.last_exit_threshold is not None:
            floor_pct = self.last_exit_threshold * FLOOR_RULE_MULTIPLIER
            return self.entry_price * (1 + floor_pct)
        return None

    @property
    def next_tier_threshold(self) -> Optional[tuple[float, float]]:
        """Get next tiered exit threshold (profit_pct, exit_fraction) or None if all tiers complete."""
        if self.next_tier_index < len(TIERED_EXIT_THRESHOLDS):
            return TIERED_EXIT_THRESHOLDS[self.next_tier_index]
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
        fast_notifier=None,  # FastTradeNotifier for immediate Telegram on stop loss
        poll_interval: float = 0.5,  # 500ms fallback polling
        enabled: bool = True,
    ):
        self.event_bus = event_bus
        self.quote_fetcher = quote_fetcher
        self.stream_manager = stream_manager
        self.fast_notifier = fast_notifier
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
            "PositionManager initialized (early exit + tiered exits + stop loss)",
            enabled=enabled,
            poll_interval=poll_interval,
            has_stream_manager=stream_manager is not None,
            stop_loss_pct=f"{STOP_LOSS_PCT*100:.0f}%",
            early_exit=f"+{EARLY_EXIT_PROFIT_PCT*100:.0f}% after {EARLY_EXIT_MINUTES:.0f} min",
            tiered_exits=[f"+{t[0]*100:.0f}%: {t[1]*100:.0f}%" for t in TIERED_EXIT_THRESHOLDS],
            floor_rule=f"{FLOOR_RULE_MULTIPLIER*100:.0f}% of last tier",
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
                "Position added (tiered exits + immediate stop loss)",
                ticker=ticker,
                entry_price=entry_price,
                shares=shares,
                cost_basis=position.total_cost_basis,
                conviction=conviction.value,
                stop_loss_price=position.stop_loss_price,
                first_tier=f"+{TIERED_EXIT_THRESHOLDS[0][0]*100:.0f}%",
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
        """Check and execute exit for a single position (tiered exits + stop loss)."""
        async with self._lock:
            if ticker not in self._positions:
                return

            position = self._positions[ticker]
            profit_pct = (current_price - position.entry_price) / position.entry_price

            # Update highest profit seen (for floor rule tracking)
            if profit_pct > position.highest_profit_pct:
                position.highest_profit_pct = profit_pct

            # Check for manual exit request first
            if self._manual_exits.get(ticker):
                exit_key = f"{ticker}_manual_exit"
                if exit_key not in self._exits_in_progress:
                    self._exits_in_progress.add(exit_key)
                    asyncio.create_task(self._execute_exit_async(
                        position,
                        position.shares_remaining,
                        "manual_exit",
                        profit_pct
                    ))
                    self._manual_exits.pop(ticker, None)
                return

            # 🛑 STOP LOSS CHECK with grace period logic
            # First 5 seconds: Use 0.5s confirmation (volatility is extreme, brief spikes recover)
            # After 5 seconds: Exit immediately (if still crashing, it's real)
            if position.stop_loss_price and not position.stop_loss_triggered:
                if current_price <= position.stop_loss_price:
                    now = datetime.now()
                    seconds_since_entry = (now - position.entry_time).total_seconds()
                    in_grace_period = seconds_since_entry <= ENTRY_GRACE_PERIOD_SECONDS

                    if in_grace_period:
                        # Within first 5 seconds - use confirmation to avoid false stops
                        if position.stop_breach_time is None:
                            position.stop_breach_time = now
                            logger.info(
                                f"⚠️ STOP BREACH (grace period): Starting {STOP_LOSS_CONFIRMATION_SECONDS}s confirmation",
                                ticker=ticker,
                                current_price=current_price,
                                stop_loss_price=position.stop_loss_price,
                                seconds_since_entry=round(seconds_since_entry, 1),
                            )
                            return

                        breach_duration = (now - position.stop_breach_time).total_seconds()
                        if breach_duration >= STOP_LOSS_CONFIRMATION_SECONDS:
                            # Confirmed - price stayed below stop for 2+ seconds during grace period
                            exit_key = f"{ticker}_stop_loss"
                            if exit_key not in self._exits_in_progress:
                                self._exits_in_progress.add(exit_key)
                                position.stop_loss_triggered = True
                                logger.warning(
                                    f"🛑 STOP LOSS TRIGGERED (confirmed after {breach_duration:.1f}s)",
                                    ticker=ticker,
                                    current_price=current_price,
                                    stop_loss_price=position.stop_loss_price,
                                    entry_price=position.entry_price,
                                    loss_pct=f"{profit_pct*100:.1f}%",
                                    shares=position.shares_remaining,
                                )
                                asyncio.create_task(self._execute_exit_async(
                                    position,
                                    position.shares_remaining,
                                    "stop_loss",
                                    profit_pct
                                ))
                            return
                    else:
                        # After grace period - exit immediately
                        exit_key = f"{ticker}_stop_loss"
                        if exit_key not in self._exits_in_progress:
                            self._exits_in_progress.add(exit_key)
                            position.stop_loss_triggered = True
                            logger.warning(
                                f"🛑 STOP LOSS TRIGGERED (immediate - past grace period)",
                                ticker=ticker,
                                current_price=current_price,
                                stop_loss_price=position.stop_loss_price,
                                entry_price=position.entry_price,
                                loss_pct=f"{profit_pct*100:.1f}%",
                                shares=position.shares_remaining,
                                seconds_since_entry=round(seconds_since_entry, 1),
                            )
                            asyncio.create_task(self._execute_exit_async(
                                position,
                                position.shares_remaining,
                                "stop_loss",
                                profit_pct
                            ))
                        return
                else:
                    # Price recovered above stop - reset breach timer
                    if position.stop_breach_time is not None:
                        logger.info(
                            f"✅ STOP BREACH RECOVERED: Price back above stop",
                            ticker=ticker,
                            current_price=current_price,
                            stop_loss_price=position.stop_loss_price,
                        )
                        position.stop_breach_time = None

            # 📉 FLOOR RULE CHECK: Exit if price drops below 50% of last exit threshold
            # After taking profit at +20%, floor is +10%. If price drops to +10%, exit remaining.
            if position.floor_price and position.shares_remaining > 0:
                if current_price <= position.floor_price:
                    exit_key = f"{ticker}_floor_exit"
                    if exit_key not in self._exits_in_progress:
                        self._exits_in_progress.add(exit_key)
                        floor_pct = position.last_exit_threshold * FLOOR_RULE_MULTIPLIER
                        logger.warning(
                            f"📉 FLOOR RULE TRIGGERED: Price dropped to {floor_pct*100:.0f}% (half of +{position.last_exit_threshold*100:.0f}%)",
                            ticker=ticker,
                            current_price=current_price,
                            floor_price=position.floor_price,
                            entry_price=position.entry_price,
                            profit_pct=f"{profit_pct*100:.1f}%",
                            shares_remaining=position.shares_remaining,
                            last_exit_threshold=f"+{position.last_exit_threshold*100:.0f}%",
                        )
                        asyncio.create_task(self._execute_exit_async(
                            position,
                            position.shares_remaining,
                            "floor_exit",
                            profit_pct
                        ))
                    return

            # 🚀 EARLY EXIT CHECK: Exit entire position if profit >= 10% after 5 minutes
            # Takes the win early instead of waiting for tiered exits or 10-min auto-exit.
            # Rationale: ELBM hit +11% at 6:55 but was only +6% at 10 min. Capture the move.
            now = datetime.now()
            minutes_held = (now - position.entry_time).total_seconds() / 60.0
            if (minutes_held >= EARLY_EXIT_MINUTES and
                profit_pct >= EARLY_EXIT_PROFIT_PCT and
                position.shares_remaining > 0):
                exit_key = f"{ticker}_early_exit_10pct"
                if exit_key not in self._exits_in_progress:
                    self._exits_in_progress.add(exit_key)
                    logger.info(
                        f"🚀 EARLY EXIT: +{profit_pct*100:.1f}% profit after {minutes_held:.1f} min - exiting entire position",
                        ticker=ticker,
                        current_price=current_price,
                        entry_price=position.entry_price,
                        profit_pct=f"+{profit_pct*100:.1f}%",
                        minutes_held=round(minutes_held, 1),
                        shares_remaining=position.shares_remaining,
                        threshold=f"+{EARLY_EXIT_PROFIT_PCT*100:.0f}% after {EARLY_EXIT_MINUTES:.0f} min",
                    )
                    asyncio.create_task(self._execute_exit_async(
                        position,
                        position.shares_remaining,
                        "early_exit_10pct",
                        profit_pct
                    ))
                return

            # 📈 TIERED EXIT CHECK: Take profit at predefined thresholds
            # +20%: 50%, +25%: 50% of remaining, +30%: 50%, +35%: 50%, +40%: 100%
            next_tier = position.next_tier_threshold
            if next_tier and position.shares_remaining > 0:
                threshold_pct, exit_fraction = next_tier

                if profit_pct >= threshold_pct:
                    # Calculate shares to exit
                    shares_to_exit = int(position.shares_remaining * exit_fraction)
                    if shares_to_exit < 1 and position.shares_remaining >= 1:
                        shares_to_exit = int(position.shares_remaining)  # Exit all if fraction < 1 share

                    if shares_to_exit > 0:
                        exit_key = f"{ticker}_tier_{position.next_tier_index}"
                        if exit_key not in self._exits_in_progress:
                            self._exits_in_progress.add(exit_key)
                            position.last_exit_threshold = threshold_pct  # Update for floor rule
                            position.next_tier_index += 1
                            position.total_exits_taken += 1

                            logger.info(
                                f"📈 TIERED EXIT: +{threshold_pct*100:.0f}% threshold reached - exiting {exit_fraction*100:.0f}%",
                                ticker=ticker,
                                current_price=current_price,
                                entry_price=position.entry_price,
                                profit_pct=f"+{profit_pct*100:.1f}%",
                                shares_to_exit=shares_to_exit,
                                shares_remaining_after=position.shares_remaining - shares_to_exit,
                                tier_index=position.next_tier_index,
                                total_tiers=len(TIERED_EXIT_THRESHOLDS),
                                new_floor=f"+{threshold_pct * FLOOR_RULE_MULTIPLIER * 100:.0f}%",
                            )
                            asyncio.create_task(self._execute_exit_async(
                                position,
                                shares_to_exit,
                                f"tier_{threshold_pct*100:.0f}pct",
                                profit_pct
                            ))
                    return

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

        # Send fast Telegram notification for stop loss/manual exits (fire-and-forget)
        if self.fast_notifier:
            try:
                # Estimate exit value (actual fill may differ slightly)
                estimated_exit_price = position.last_price or position.entry_price * (1 + profit_pct)
                estimated_value = estimated_exit_price * shares
                pnl_usd = (estimated_exit_price - position.entry_price) * shares

                self.fast_notifier.notify_exit_triggered(
                    ticker=position.ticker,
                    exit_reason=exit_reason,
                    shares=int(shares),
                    entry_price=position.entry_price,
                    exit_price=estimated_exit_price,
                    profit_pct=profit_pct,
                    pnl_usd=pnl_usd,
                    stop_loss_price=position.stop_loss_price,
                )
            except Exception as e:
                logger.error(f"FastTradeNotifier exit notification failed: {e}")

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
            # Track realized P&L from this exit
            estimated_exit_price = position.last_price or position.entry_price * (1 + profit_pct)
            pnl_from_exit = (estimated_exit_price - position.entry_price) * shares
            position.realized_pnl += pnl_from_exit

        logger.info(
            f"Exit trade request published: {exit_reason}",
            ticker=position.ticker,
            shares_sold=int(shares),
            shares_remaining=position.shares_remaining,
            pnl_this_exit=round(pnl_from_exit, 2),
            total_realized_pnl=round(position.realized_pnl, 2),
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
                price_source = None

                # Try WebSocket cache first (fastest)
                if self.stream_manager:
                    quote = await self.stream_manager.get_latest_quote(ticker)
                    if quote:
                        current_price = quote.get("bid")
                        price_source = "websocket"

                # Fallback to REST API if WebSocket failed
                if not current_price and self.quote_fetcher:
                    current_price = await self.quote_fetcher.get_realtime_price(ticker)
                    if current_price:
                        price_source = "rest_api"

                # CRITICAL: Log warning if we can't get price for stop loss monitoring
                if not current_price:
                    logger.warning(
                        "⚠️ STOP LOSS: Cannot get price for position - stop loss check SKIPPED",
                        ticker=ticker,
                        entry_price=position.entry_price,
                        stop_loss_price=position.stop_loss_price,
                        has_stream_manager=self.stream_manager is not None,
                        has_quote_fetcher=self.quote_fetcher is not None,
                    )
                    continue

                # Check exit conditions with valid price
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
            "PositionManager started (early exit + tiered exits + 5% stop loss)",
            poll_interval=self.poll_interval,
            has_websocket=self.stream_manager is not None,
            early_exit=f"+{EARLY_EXIT_PROFIT_PCT*100:.0f}% after {EARLY_EXIT_MINUTES:.0f} min",
            tiered_exits=[f"+{t[0]*100:.0f}%" for t in TIERED_EXIT_THRESHOLDS],
            floor_rule=f"{FLOOR_RULE_MULTIPLIER*100:.0f}% of last tier",
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
            next_tier = pos.next_tier_threshold
            positions_summary.append({
                "ticker": ticker,
                "entry_price": pos.entry_price,
                "shares": pos.shares,
                "shares_remaining": pos.shares_remaining,
                "conviction": pos.conviction.value,
                "entry_time": pos.entry_time.isoformat(),
                "last_price": pos.last_price,
                "profit_pct": f"{pos.current_profit_pct*100:.1f}%" if pos.current_profit_pct else None,
                "highest_profit_pct": f"{pos.highest_profit_pct*100:.1f}%",
                "unrealized_pnl": round(pos.unrealized_pnl, 2) if pos.unrealized_pnl else None,
                "realized_pnl": round(pos.realized_pnl, 2),
                "stop_loss_price": pos.stop_loss_price,
                "stop_loss_triggered": pos.stop_loss_triggered,
                "next_tier": f"+{next_tier[0]*100:.0f}%" if next_tier else "all_tiers_complete",
                "tiers_taken": pos.total_exits_taken,
                "floor_price": pos.floor_price,
                "last_exit_threshold": f"+{pos.last_exit_threshold*100:.0f}%" if pos.last_exit_threshold else None,
            })

        return {
            "enabled": self.enabled,
            "running": self._running,
            "open_positions": len(self._positions),
            "poll_interval": self.poll_interval,
            "has_websocket": self.stream_manager is not None,
            "stop_loss_pct": f"{STOP_LOSS_PCT*100:.0f}%",
            "early_exit": f"+{EARLY_EXIT_PROFIT_PCT*100:.0f}% after {EARLY_EXIT_MINUTES:.0f} min → exit 100%",
            "tiered_exits": [f"+{t[0]*100:.0f}%: {t[1]*100:.0f}% of remaining" for t in TIERED_EXIT_THRESHOLDS],
            "floor_rule": f"{FLOOR_RULE_MULTIPLIER*100:.0f}% of last exit threshold",
            "positions": positions_summary,
        }
