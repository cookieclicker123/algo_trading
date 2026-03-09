"""
Position Manager - Tracks open positions with tiered exit system.

EXIT STRATEGY (Tiered Profit-Taking + Stop Loss + Early Exit):
Aggressive profit-taking to capture gains before reversals.

EARLY EXIT (5-min 10% rule):
- After 5 minutes, if profit >= 10%, exit entire position immediately
- Rationale: ELBM hit +11% at 6:55 but was only +6% at 10 min - capture the move early
- Takes precedence over tiered exits when triggered

TIERED EXITS (automatic profit-taking):
- +10%: Exit 50% of position (capture gains early)
- +15%: Exit 50% of remaining (25% of original)
- +20%: Exit remaining position (25% of original)

FLOOR RULE (fixed levels to protect gains without premature exits):
- After +10% exit: Floor is +2.5% (if price drops to +2.5%, exit remaining)
- After +15% exit: Floor is +5.0% (if price drops to +5%, exit remaining)
- After +20% exit: Fully exited, no floor needed

The fixed floors are wider than the old 50% rule to avoid volatility-induced
stopouts. Example: CETX went +9.5% then reversed - with 10% tier, would have
captured 50% at the top instead of full loss.

STOP LOSS (5% below entry price, 12% for high-conviction):
- First 5 seconds: 0.5s confirmation (brief spikes are noise, not signal)
- After 5 seconds: Immediate execution (if crashing, it's real)
- Rationale: SMTK went -7% at 1.8s then +37% at 2.0s - grace period prevents false stops

HIGH-CONVICTION TRADES (gov/military + major commercial contracts):
- Wider tiers: +25% (34%), +40% (50%), +60% (100%)
- 12% stop loss (MTEK had -9.99% MAE then ran +49%)
- Trailing stop after first tier: 15pp below peak (replaces fixed floors)
- No early exit (trailing stop handles fading moves)

Uses WebSocket for real-time price monitoring (sub-100ms latency).
Falls back to 500ms polling if WebSocket unavailable.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any, Callable
from enum import Enum

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...domain.brokerage.models import TradeRequest
from ...domain.brokerage.models import TradeAction, TradeInstrument
from ...utils.brokerage.session_detector import seconds_until_extended_hours_end

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
# (e.g., KIDZ had 1.046s breach at -7.4% but recovered to +40% - 0.5s was too tight)
# After 5 seconds, if stop is breached, it's a real crash - exit immediately
ENTRY_GRACE_PERIOD_SECONDS = 5.0  # First 5 seconds: use confirmation
STOP_LOSS_CONFIRMATION_SECONDS = 1.25  # During grace period: wait 1.25s to confirm (KIDZ max breach was 1.046s)

# Breakeven stop configuration - protects gains after reaching +5%
# Once price stays at +5% for 0.5 seconds, stop moves from -5% to breakeven (0%)
# Rationale: If trade hits +5%, buying pressure is real. Moving to breakeven protects
# against turning winner into loser, while still giving 5% buffer room.
BREAKEVEN_TRIGGER_PCT = 0.05  # Move to breakeven after hitting +5%
BREAKEVEN_CONFIRMATION_SECONDS = 0.5  # Must stay at +5% for 0.5s to confirm

# Tiered exit configuration - let winners run, capture large moves
# Strategy: Most winners go past 15%, so hold for bigger gains
# Previous tiers at 10%/15%/20% left too much on the table (e.g. 18% move → only 4.5% return)
TIERED_EXIT_THRESHOLDS = [
    (0.15, 0.50),  # +15%: Exit 50% of position
    (0.20, 0.50),  # +20%: Exit 50% of remaining (25% of original)
    (0.30, 1.00),  # +30%: Exit remaining position (25% of original)
]

# Fixed floor levels per tier - NOT a multiplier, but absolute profit % floors
# After taking profit at a tier, if price drops to floor, exit remaining
# This protects gains without being too tight (which causes premature exits on volatility)
TIER_FLOOR_PCT = {
    0.15: 0.05,   # After +15% exit, floor is +5%
    0.20: 0.10,   # After +20% exit, floor is +10%
    0.25: 0.15,   # After +25% exit (mega tier 2), floor is +15%
    0.30: None,   # After +30%, fully exited - no floor needed
}

# Legacy multiplier kept for compatibility but not used with new tier system
FLOOR_RULE_MULTIPLIER = 0.25  # Fallback: 25% of last exit level

# Early exit configuration - exit entire position if profit >= 10% after 5 minutes
# Rationale: ELBM 2026-02-03 hit +11% at 6:55 but we missed it. By 10 min it was +6%.
# If we're up 10%+ after 5 minutes, take the win instead of waiting for tiered exits.
EARLY_EXIT_MINUTES = 5.0  # Check after 5 minutes of holding
EARLY_EXIT_PROFIT_PCT = 0.10  # Exit if profit >= 10%

# High-conviction exit configuration (gov/military contracts)
# Wider tiers because these headlines sustain momentum (PRSO +72%, SYNX +62%)
# Normal tiers at 15/20/30% leave too much on the table for sustained movers.
HIGH_CONVICTION_TIERS = [
    (0.25, 0.34),  # +25%: Exit 34% of position
    (0.40, 0.50),  # +40%: Exit 50% of remaining
    (0.60, 1.00),  # +60%: Exit remaining
]

# Trailing stop replaces fixed floors for high-conviction trades.
# After first tier exit, tracks peak profit and exits remaining if price
# drops 15 percentage points below peak.
# Example: peak at +72%, trailing stop exits at +57%.
HIGH_CONVICTION_TRAILING_PCT = 0.15  # 15 percentage points below peak

# Overnight risk configuration - force exit before extended hours close
# Rationale: If still holding at 8 PM ET (post-market close), stuck until 4 AM ET next day.
# Overnight gap risk is unacceptable - force exit 10 minutes before session end.
FORCE_EXIT_MINUTES_BEFORE_SESSION_END = 10.0  # Force exit 10 min before close
FORCE_EXIT_ENABLED = True  # Can disable for testing


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

    # Breakeven stop tracking - moves stop from -5% to 0% after confirmed +5%
    breakeven_trigger_time: Optional[datetime] = None  # When we first hit +5%
    breakeven_stop_active: bool = False  # True once +5% confirmed for 0.5s
    breakeven_breach_time: Optional[datetime] = None  # For breakeven stop confirmation

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

    # === SCALE-IN MONITORING (for no_volume entries) ===
    # When entering at 0.5x due to no_volume, monitor for confirmation to add more
    awaiting_confirmation: bool = False  # True if we entered small and await volume
    target_full_shares: float = 0.0  # Full position size if confirmed
    confirmation_deadline: Optional[datetime] = None  # When to stop waiting (30s)
    confirmation_received: bool = False  # True once volume/activity confirmed
    scale_in_triggered: bool = False  # True once scale-in order sent

    # === MEGA TRADE: Manual exit control ===
    # Mega trades skip all automated exits (stop loss, breakeven, early exit, tiered).
    # Only manual /exit, 10-min scheduled exit (ExitTradeUseCase), and session-end force exit remain.
    is_mega_trade: bool = False

    # === HIGH-CONVICTION: Wider stop loss + wider tiers + trailing stop ===
    # Gov/military contract headlines: 12% stop, wider tiers (25/40/60%), trailing stop after T1.
    is_high_conviction: bool = False
    trailing_stop_active: bool = False  # Activated after first high-conviction tier exit
    trailing_stop_peak_pct: float = 0.0  # Highest profit % since trailing stop activation

    # === MOMENTUM TRACKING (Phase 1: Data Collection) ===
    # After Tier 2 (+20%), track price trajectory for comparison analysis.
    # End-of-day will compare fixed tier exits vs momentum-based trailing.
    momentum_tracking_active: bool = False  # True once Tier 2 triggered
    momentum_tier_2_time: Optional[datetime] = None  # When +15% tier triggered
    momentum_tier_2_price: Optional[float] = None  # Price at +15% trigger
    momentum_peak_after_tier_2: float = 0.0  # Highest profit % after Tier 2
    momentum_peak_price: Optional[float] = None  # Price at peak
    momentum_peak_time: Optional[datetime] = None  # Time of peak
    momentum_trajectory: List[Dict[str, Any]] = field(default_factory=list)  # Price samples after Tier 2

    def __post_init__(self):
        self.shares_remaining = self.shares
        self.total_cost_basis = self.entry_price * self.shares

    @property
    def _stop_loss_pct(self) -> float:
        """Get stop loss percentage — 12% for high-conviction, 5% normal.

        High-conviction (gov/military contracts) have extreme initial volatility:
        MTEK had -9.99% MAE at T+51s then ran +49%. Normal 5% stop kills these.
        12% gives enough room to survive the market-maker repricing whipsaw.
        """
        return 0.12 if self.is_high_conviction else STOP_LOSS_PCT

    @property
    def stop_loss_price(self) -> Optional[float]:
        """Calculate stop loss price (5% or 12% below actual entry price)."""
        if self.entry_price:
            return self.entry_price * (1 - self._stop_loss_pct)
        return None

    @property
    def effective_stop_price(self) -> Optional[float]:
        """Get effective stop price - breakeven if activated, otherwise -5%/-12%."""
        if self.entry_price:
            if self.breakeven_stop_active:
                return self.entry_price  # Breakeven stop
            return self.entry_price * (1 - self._stop_loss_pct)
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
        """Calculate floor price based on fixed floor levels per tier.
        Returns None for high-conviction trades (trailing stop replaces floor)."""
        if self.is_high_conviction and self.trailing_stop_active:
            return None  # Trailing stop handles exit protection
        if self.last_exit_threshold is not None:
            # Use fixed floor from TIER_FLOOR_PCT if available, else fallback to multiplier
            floor_pct = TIER_FLOOR_PCT.get(self.last_exit_threshold)
            if floor_pct is None:
                # Fallback for thresholds not in mapping
                floor_pct = self.last_exit_threshold * FLOOR_RULE_MULTIPLIER
            if floor_pct is not None:
                return self.entry_price * (1 + floor_pct)
        return None

    @property
    def _exit_thresholds(self) -> list:
        """Get exit tier thresholds based on trade type."""
        if self.is_high_conviction:
            return HIGH_CONVICTION_TIERS
        return TIERED_EXIT_THRESHOLDS

    @property
    def next_tier_threshold(self) -> Optional[tuple[float, float]]:
        """Get next tiered exit threshold (profit_pct, exit_fraction) or None if all tiers complete."""
        thresholds = self._exit_thresholds
        if self.next_tier_index < len(thresholds):
            return thresholds[self.next_tier_index]
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
        self.exit_trade_use_case = None  # Set after composition_root creates it
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
            "PositionManager initialized (early exit + tiered exits + stop loss + breakeven)",
            enabled=enabled,
            poll_interval=poll_interval,
            has_stream_manager=stream_manager is not None,
            stop_loss_pct=f"{STOP_LOSS_PCT*100:.0f}%",
            breakeven_trigger=f"+{BREAKEVEN_TRIGGER_PCT*100:.0f}% → stop moves to 0%",
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
        awaiting_confirmation: bool = False,
        target_full_shares: float = 0.0,
        is_mega_trade: bool = False,
        is_high_conviction: bool = False,
    ) -> Position:
        """
        Add a new position to track.

        Args:
            awaiting_confirmation: True if this is a partial entry awaiting volume confirmation
            target_full_shares: Full position size to scale into if confirmed
            is_mega_trade: True for mega trades — skips automated exits, manual control via /exit
            is_high_conviction: True for high-conviction headlines — uses mega stop loss but normal exits
        """
        async with self._lock:
            position = Position(
                ticker=ticker,
                entry_price=entry_price,
                shares=shares,
                entry_time=datetime.now(),
                article_id=article_id,
                conviction=conviction,
                initial_nbbo_mid=initial_nbbo_mid,
                is_mega_trade=is_mega_trade,
                is_high_conviction=is_high_conviction,
            )

            # Scale-in tracking for no_volume entries
            if awaiting_confirmation and target_full_shares > shares:
                position.awaiting_confirmation = True
                position.target_full_shares = target_full_shares
                # 30 second deadline to receive confirmation
                position.confirmation_deadline = datetime.now() + timedelta(seconds=30)

            self._positions[ticker] = position

            # Subscribe to WebSocket quotes for this symbol
            if self.stream_manager:
                await self.stream_manager.subscribe_symbol(ticker)

            if awaiting_confirmation:
                logger.info(
                    "📊 Position added (AWAITING CONFIRMATION for scale-in)",
                    ticker=ticker,
                    entry_price=entry_price,
                    initial_shares=shares,
                    target_shares=target_full_shares,
                    shares_to_add=target_full_shares - shares,
                    deadline_seconds=30,
                    conviction=conviction.value,
                )
            else:
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

    async def update_scale_in(self, ticker: str, fill_price: float, shares_added: int) -> bool:
        """
        Update an existing position with scale-in shares.

        Called when a scale-in buy order fills. Updates:
        - shares: Original + added
        - shares_remaining: Original + added
        - total_cost_basis: Weighted average (not recalculated, just added value)

        Returns True if position was updated, False if position not found.
        """
        async with self._lock:
            if ticker not in self._positions:
                logger.warning(
                    "Scale-in update failed: Position not found",
                    ticker=ticker,
                    shares_added=shares_added,
                )
                return False

            position = self._positions[ticker]
            old_shares = position.shares
            old_cost_basis = position.total_cost_basis

            # Update position with new shares
            position.shares += shares_added
            position.shares_remaining += shares_added
            position.total_cost_basis += fill_price * shares_added

            # Calculate new average entry for tracking
            new_avg_entry = position.total_cost_basis / position.shares if position.shares > 0 else position.entry_price

            logger.info(
                f"📈 SCALE-IN FILLED: Added {shares_added} shares at ${fill_price:.2f}",
                ticker=ticker,
                old_shares=old_shares,
                new_shares=int(position.shares),
                old_cost_basis=round(old_cost_basis, 2),
                new_cost_basis=round(position.total_cost_basis, 2),
                original_entry=position.entry_price,
                scale_in_price=fill_price,
                weighted_avg_entry=round(new_avg_entry, 2),
            )

            return True

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

            # Check scale-in confirmation (non-blocking, fire-and-forget)
            await self._check_scale_in_confirmation(symbol, bid_price)

        except Exception as e:
            logger.error(f"Error handling quote event: {e}", exc_info=True)

    async def _check_scale_in_confirmation(self, ticker: str, current_price: float) -> None:
        """
        Check if a no_volume position should scale in based on post-entry confirmation.

        Confirmation criteria (non-blocking, fire-and-forget):
        - Position is awaiting confirmation (entered at 0.5x due to no_volume)
        - At least 5 seconds have passed since entry (give market time to react)
        - Price is still above entry (demand confirmed)
        - Within 30 second deadline

        If confirmed, publishes a scale-in buy order (fire-and-forget) to add remaining shares.
        """
        async with self._lock:
            if ticker not in self._positions:
                return

            position = self._positions[ticker]

            # Skip if not awaiting confirmation or already triggered
            if not position.awaiting_confirmation or position.scale_in_triggered:
                return

            now = datetime.now()

            # Check if deadline passed - no scale-in, stay at partial position
            if position.confirmation_deadline and now > position.confirmation_deadline:
                position.awaiting_confirmation = False  # Stop checking
                logger.info(
                    "📊 SCALE-IN TIMEOUT: No confirmation within 30s, staying at partial position",
                    ticker=ticker,
                    entry_price=position.entry_price,
                    current_shares=position.shares,
                    target_shares=position.target_full_shares,
                )
                return

            # Need at least 5 seconds of price action to confirm
            seconds_since_entry = (now - position.entry_time).total_seconds()
            if seconds_since_entry < 5.0:
                return

            # Confirmation criteria: price still above entry (demand is real)
            if current_price <= position.entry_price:
                # Price dropped below entry - not confirming yet, keep watching
                return

            # CONFIRMED! Price held above entry for 5+ seconds
            position.confirmation_received = True
            position.scale_in_triggered = True  # Prevent duplicate orders
            position.awaiting_confirmation = False

            # Calculate shares to add
            shares_to_add = int(position.target_full_shares - position.shares)
            if shares_to_add <= 0:
                return

            profit_pct = (current_price - position.entry_price) / position.entry_price

            logger.info(
                f"✅ SCALE-IN CONFIRMED: Price held +{profit_pct*100:.1f}% for {seconds_since_entry:.1f}s - adding {shares_to_add} shares",
                ticker=ticker,
                entry_price=position.entry_price,
                current_price=current_price,
                current_shares=int(position.shares),
                adding_shares=shares_to_add,
                new_total_shares=int(position.target_full_shares),
                seconds_since_entry=round(seconds_since_entry, 1),
            )

            # Fire-and-forget scale-in order (non-blocking)
            asyncio.create_task(self._execute_scale_in(position, shares_to_add, current_price))

    async def _execute_scale_in(self, position: Position, shares_to_add: int, current_price: float) -> None:
        """Execute scale-in buy order (fire-and-forget, non-blocking)."""
        try:
            # Build scale-in trade request
            trade_request = TradeRequest(
                ticker=position.ticker,
                action=TradeAction.BUY,
                shares=shares_to_add,
                amount_usd=None,
                leverage=None,
                article_id=position.article_id,
                instrument=TradeInstrument.STOCK,
            )

            # Publish trade request event
            from ...domain.brokerage.events import TradeRequestDomainEvent

            scale_in_event = TradeRequestDomainEvent(
                trade_request=trade_request,
                article_id=position.article_id,
                requested_at=datetime.now(),
                metadata={
                    "scale_in": True,
                    "original_entry_price": position.entry_price,
                    "original_shares": position.shares,
                    "scale_in_shares": shares_to_add,
                    "scale_in_price": current_price,
                    "conviction": position.conviction.value,
                }
            )

            await self.event_bus.publish("Domain.TradeRequested", scale_in_event.model_dump())

            logger.info(
                f"📈 SCALE-IN ORDER PUBLISHED: Adding {shares_to_add} shares",
                ticker=position.ticker,
                article_id=position.article_id,
                shares_to_add=shares_to_add,
                estimated_price=current_price,
                estimated_cost=round(shares_to_add * current_price, 2),
            )

            # Update position tracking (will be fully updated when fill comes back)
            async with self._lock:
                position.target_full_shares = position.shares + shares_to_add  # Reflect intent

        except Exception as e:
            logger.error(f"Error executing scale-in for {position.ticker}: {e}", exc_info=True)

    async def _check_force_exit_session_end(self) -> None:
        """
        OVERNIGHT RISK: Force exit all positions before extended hours session ends.

        If still holding at 8 PM ET (post-market close), you're stuck until 4 AM ET next day.
        This method forces exits 10 minutes before session end to avoid overnight gap risk.
        """
        seconds_remaining, session = seconds_until_extended_hours_end()

        # Not in extended hours or session end isn't imminent
        if session == "closed" or seconds_remaining <= 0:
            return

        minutes_remaining = seconds_remaining / 60.0

        # Check if we're within the force exit window
        if minutes_remaining > FORCE_EXIT_MINUTES_BEFORE_SESSION_END:
            return

        # We're within X minutes of session end - force exit all positions
        async with self._lock:
            positions_to_exit = list(self._positions.items())

        if not positions_to_exit:
            return

        logger.warning(
            "🚨 OVERNIGHT RISK: Force exiting all positions before session end",
            session=session,
            minutes_until_close=round(minutes_remaining, 1),
            positions_count=len(positions_to_exit),
            tickers=[t for t, _ in positions_to_exit],
            reason=f"Extended hours ({session}) ends in {minutes_remaining:.1f} min - avoiding overnight gap risk",
        )

        for ticker, position in positions_to_exit:
            if ticker in self._exits_in_progress:
                continue  # Already exiting

            self._exits_in_progress.add(ticker)

            # Get current price for exit
            current_price = position.last_price or position.entry_price
            profit_pct = (current_price - position.entry_price) / position.entry_price

            logger.warning(
                "🌙 FORCE EXIT: Exiting position before session close",
                ticker=ticker,
                shares=position.shares_remaining,
                entry_price=position.entry_price,
                current_price=current_price,
                profit_pct=round(profit_pct * 100, 2),
                session_ends_in_min=round(minutes_remaining, 1),
            )

            asyncio.create_task(self._execute_exit_async(
                position=position,
                shares=position.shares_remaining,
                profit_pct=profit_pct,
                exit_reason=f"session_end_{session}",
            ))

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

            # Update trailing stop peak (high-conviction trades)
            if position.trailing_stop_active and profit_pct > position.trailing_stop_peak_pct:
                position.trailing_stop_peak_pct = profit_pct

            # Check for manual exit request first (always allowed)
            if self._manual_exits.get(ticker):
                if ticker not in self._exits_in_progress:
                    self._exits_in_progress.add(ticker)
                    asyncio.create_task(self._execute_exit_async(
                        position,
                        position.shares_remaining,
                        "manual_exit",
                        profit_pct
                    ))
                    self._manual_exits.pop(ticker, None)
                return

            # MEGA TRADE: Slightly wider stop + breakeven at +20% + first tier at +20%
            # Minimal differences from normal trades — just enough breathing room.
            # Normal: 5% stop / 5s grace / 1.25s confirm / breakeven +5% / tiers +15/+20/+30
            # Mega:   7.5% stop / 5s grace / 1s confirm / breakeven +20% / tiers +20/+25/+30
            if position.is_mega_trade:
                now = datetime.now()
                mega_stop_pct = 0.075  # 7.5% stop loss (vs 5% normal)
                mega_breakeven_trigger = 0.20  # Move to breakeven at +20% (vs +5% normal)
                mega_grace_period = 5.0  # 5 seconds grace (same as normal)
                mega_confirmation_seconds = 1.0  # 1 second confirmation during grace (vs 1.25s normal)

                # Breakeven activation at +20%
                if not position.breakeven_stop_active and profit_pct >= mega_breakeven_trigger:
                    if position.breakeven_trigger_time is None:
                        position.breakeven_trigger_time = now
                    else:
                        trigger_duration = (now - position.breakeven_trigger_time).total_seconds()
                        if trigger_duration >= 0.5:
                            position.breakeven_stop_active = True
                            logger.info(
                                "MEGA TRADE BREAKEVEN ACTIVATED: Stop moved from -7.5% to 0%",
                                ticker=ticker,
                                current_price=current_price,
                                profit_pct=f"+{profit_pct*100:.1f}%",
                            )
                elif not position.breakeven_stop_active and profit_pct < mega_breakeven_trigger:
                    position.breakeven_trigger_time = None

                # Stop loss check (7.5% or breakeven)
                effective_stop = position.entry_price if position.breakeven_stop_active else position.entry_price * (1 - mega_stop_pct)
                if current_price <= effective_stop and not position.stop_loss_triggered:
                    seconds_since_entry = (now - position.entry_time).total_seconds()
                    in_grace = seconds_since_entry <= mega_grace_period
                    breach_time = position.breakeven_breach_time if position.breakeven_stop_active else position.stop_breach_time

                    if in_grace:
                        # Soft stop: require 1s confirmation
                        if breach_time is None:
                            if position.breakeven_stop_active:
                                position.breakeven_breach_time = now
                            else:
                                position.stop_breach_time = now
                            return
                        breach_duration = (now - breach_time).total_seconds()
                        if breach_duration >= mega_confirmation_seconds:
                            if ticker not in self._exits_in_progress:
                                self._exits_in_progress.add(ticker)
                                position.stop_loss_triggered = True
                                stop_type = "mega_breakeven_stop" if position.breakeven_stop_active else "mega_stop_loss"
                                logger.warning(
                                    f"MEGA TRADE {stop_type.upper()} TRIGGERED (confirmed after {breach_duration:.1f}s)",
                                    ticker=ticker,
                                    current_price=current_price,
                                    effective_stop=effective_stop,
                                    entry_price=position.entry_price,
                                    pnl_pct=f"{profit_pct*100:+.1f}%",
                                )
                                asyncio.create_task(self._execute_exit_async(
                                    position, position.shares_remaining, stop_type, profit_pct
                                ))
                        return
                    else:
                        # Hard stop: immediate after grace period
                        if ticker not in self._exits_in_progress:
                            self._exits_in_progress.add(ticker)
                            position.stop_loss_triggered = True
                            stop_type = "mega_breakeven_stop" if position.breakeven_stop_active else "mega_stop_loss"
                            logger.warning(
                                f"MEGA TRADE {stop_type.upper()} TRIGGERED (immediate — past grace period)",
                                ticker=ticker,
                                current_price=current_price,
                                effective_stop=effective_stop,
                                entry_price=position.entry_price,
                                pnl_pct=f"{profit_pct*100:+.1f}%",
                            )
                            asyncio.create_task(self._execute_exit_async(
                                position, position.shares_remaining, stop_type, profit_pct
                            ))
                        return
                else:
                    # Price above stop — reset breach timers
                    position.stop_breach_time = None
                    position.breakeven_breach_time = None

                # Mega trade tiered exits: first tier at +20% instead of +15%
                # Tiers: +20%: 50%, +25%: 50%, +30%: 100% (normal is +15/+20/+30)
                MEGA_TIERS = [(0.20, 0.50), (0.25, 0.50), (0.30, 1.00)]
                if position.next_tier_index < len(MEGA_TIERS) and position.shares_remaining > 0:
                    threshold_pct, exit_fraction = MEGA_TIERS[position.next_tier_index]
                    if profit_pct >= threshold_pct:
                        shares_to_exit = int(position.shares_remaining * exit_fraction)
                        if shares_to_exit < 1 and position.shares_remaining >= 1:
                            shares_to_exit = int(position.shares_remaining)
                        if shares_to_exit > 0 and ticker not in self._exits_in_progress:
                            self._exits_in_progress.add(ticker)
                            position.last_exit_threshold = threshold_pct
                            position.next_tier_index += 1
                            position.total_exits_taken += 1
                            new_floor_pct = TIER_FLOOR_PCT.get(threshold_pct)
                            new_floor_str = f"+{new_floor_pct*100:.1f}%" if new_floor_pct else "N/A"
                            logger.info(
                                f"📈 MEGA TIERED EXIT: +{threshold_pct*100:.0f}% threshold - exiting {exit_fraction*100:.0f}%",
                                ticker=ticker,
                                current_price=current_price,
                                entry_price=position.entry_price,
                                profit_pct=f"+{profit_pct*100:.1f}%",
                                shares_to_exit=shares_to_exit,
                                shares_remaining_after=position.shares_remaining - shares_to_exit,
                                new_floor=new_floor_str,
                            )
                            asyncio.create_task(self._execute_exit_async(
                                position, shares_to_exit, f"mega_tier_{threshold_pct*100:.0f}pct", profit_pct
                            ))
                        return

                # Floor rule for mega trades (same as normal)
                if position.floor_price and position.shares_remaining > 0:
                    if current_price <= position.floor_price:
                        if ticker not in self._exits_in_progress:
                            self._exits_in_progress.add(ticker)
                            floor_pct = TIER_FLOOR_PCT.get(position.last_exit_threshold)
                            if floor_pct is None:
                                floor_pct = position.last_exit_threshold * FLOOR_RULE_MULTIPLIER
                            logger.warning(
                                f"📉 MEGA FLOOR RULE: Price dropped to +{floor_pct*100:.1f}% floor",
                                ticker=ticker,
                                current_price=current_price,
                                floor_price=position.floor_price,
                                profit_pct=f"{profit_pct*100:.1f}%",
                            )
                            asyncio.create_task(self._execute_exit_async(
                                position, position.shares_remaining, "mega_floor_exit", profit_pct
                            ))
                        return

                # Early exit for mega trades (same as normal: +10% after 5 min)
                minutes_held = (now - position.entry_time).total_seconds() / 60.0
                if (minutes_held >= EARLY_EXIT_MINUTES and
                    profit_pct >= EARLY_EXIT_PROFIT_PCT and
                    position.shares_remaining > 0):
                    if ticker not in self._exits_in_progress:
                        self._exits_in_progress.add(ticker)
                        logger.info(
                            f"🚀 MEGA EARLY EXIT: +{profit_pct*100:.1f}% after {minutes_held:.1f} min",
                            ticker=ticker,
                            current_price=current_price,
                            profit_pct=f"+{profit_pct*100:.1f}%",
                        )
                        asyncio.create_task(self._execute_exit_async(
                            position, position.shares_remaining, "mega_early_exit", profit_pct
                        ))
                    return

                return

            # 🎯 BREAKEVEN STOP ACTIVATION CHECK
            # If price hits +5% and stays there for 0.5s, move stop from -5% to breakeven
            now = datetime.now()
            if not position.breakeven_stop_active and profit_pct >= BREAKEVEN_TRIGGER_PCT:
                if position.breakeven_trigger_time is None:
                    # First time hitting +5% - start confirmation timer
                    position.breakeven_trigger_time = now
                    logger.info(
                        f"📈 BREAKEVEN TRIGGER: Hit +{BREAKEVEN_TRIGGER_PCT*100:.0f}%, starting {BREAKEVEN_CONFIRMATION_SECONDS}s confirmation",
                        ticker=ticker,
                        current_price=current_price,
                        profit_pct=f"+{profit_pct*100:.1f}%",
                    )
                else:
                    # Check if we've stayed at +5% long enough
                    trigger_duration = (now - position.breakeven_trigger_time).total_seconds()
                    if trigger_duration >= BREAKEVEN_CONFIRMATION_SECONDS:
                        # Confirmed! Activate breakeven stop
                        position.breakeven_stop_active = True
                        logger.info(
                            f"✅ BREAKEVEN STOP ACTIVATED: Stop moved from -5% to 0% (confirmed +{BREAKEVEN_TRIGGER_PCT*100:.0f}% for {trigger_duration:.1f}s)",
                            ticker=ticker,
                            current_price=current_price,
                            profit_pct=f"+{profit_pct*100:.1f}%",
                            old_stop=position.stop_loss_price,
                            new_stop=position.entry_price,
                        )
            elif not position.breakeven_stop_active and profit_pct < BREAKEVEN_TRIGGER_PCT:
                # Dropped below +5% before confirmation - reset trigger
                if position.breakeven_trigger_time is not None:
                    logger.debug(
                        f"BREAKEVEN TRIGGER RESET: Dropped below +{BREAKEVEN_TRIGGER_PCT*100:.0f}%",
                        ticker=ticker,
                        profit_pct=f"{profit_pct*100:+.1f}%",
                    )
                    position.breakeven_trigger_time = None

            # 🛑 STOP LOSS CHECK with grace period logic
            # Uses effective_stop_price (breakeven if activated, otherwise -5%)
            # First 5 seconds: Use 1.25s confirmation (volatility is extreme, brief spikes recover)
            # After 5 seconds: Exit immediately (if still crashing, it's real)
            effective_stop = position.effective_stop_price
            if effective_stop and not position.stop_loss_triggered:
                if current_price <= effective_stop:
                    seconds_since_entry = (now - position.entry_time).total_seconds()
                    in_grace_period = seconds_since_entry <= ENTRY_GRACE_PERIOD_SECONDS
                    stop_type = "breakeven" if position.breakeven_stop_active else "stop_loss"
                    breach_time_attr = "breakeven_breach_time" if position.breakeven_stop_active else "stop_breach_time"
                    current_breach_time = position.breakeven_breach_time if position.breakeven_stop_active else position.stop_breach_time

                    # Always use confirmation for breakeven stops (they're protecting gains)
                    # For regular stops: use confirmation only in grace period
                    use_confirmation = position.breakeven_stop_active or in_grace_period

                    if use_confirmation:
                        if current_breach_time is None:
                            # Start confirmation timer
                            if position.breakeven_stop_active:
                                position.breakeven_breach_time = now
                            else:
                                position.stop_breach_time = now
                            logger.info(
                                f"⚠️ {stop_type.upper()} BREACH: Starting {STOP_LOSS_CONFIRMATION_SECONDS}s confirmation",
                                ticker=ticker,
                                current_price=current_price,
                                effective_stop=effective_stop,
                                profit_pct=f"{profit_pct*100:+.1f}%",
                                breakeven_active=position.breakeven_stop_active,
                            )
                            return

                        breach_duration = (now - current_breach_time).total_seconds()
                        if breach_duration >= STOP_LOSS_CONFIRMATION_SECONDS:
                            # Confirmed - price stayed below stop
                            if ticker not in self._exits_in_progress:
                                self._exits_in_progress.add(ticker)
                                position.stop_loss_triggered = True
                                exit_reason = "breakeven_stop" if position.breakeven_stop_active else "stop_loss"
                                logger.warning(
                                    f"🛑 {stop_type.upper()} TRIGGERED (confirmed after {breach_duration:.1f}s)",
                                    ticker=ticker,
                                    current_price=current_price,
                                    effective_stop=effective_stop,
                                    entry_price=position.entry_price,
                                    pnl_pct=f"{profit_pct*100:+.1f}%",
                                    shares=position.shares_remaining,
                                    breakeven_active=position.breakeven_stop_active,
                                )
                                asyncio.create_task(self._execute_exit_async(
                                    position,
                                    position.shares_remaining,
                                    exit_reason,
                                    profit_pct
                                ))
                            return
                    else:
                        # After grace period with regular stop - exit immediately
                        if ticker not in self._exits_in_progress:
                            self._exits_in_progress.add(ticker)
                            position.stop_loss_triggered = True
                            logger.warning(
                                f"🛑 STOP LOSS TRIGGERED (immediate - past grace period)",
                                ticker=ticker,
                                current_price=current_price,
                                stop_loss_price=effective_stop,
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
                    # Price recovered above stop - reset breach timers
                    if position.stop_breach_time is not None:
                        logger.info(
                            f"✅ STOP BREACH RECOVERED: Price back above stop",
                            ticker=ticker,
                            current_price=current_price,
                            effective_stop=effective_stop,
                        )
                        position.stop_breach_time = None
                    if position.breakeven_breach_time is not None:
                        logger.info(
                            f"✅ BREAKEVEN BREACH RECOVERED: Price back above entry",
                            ticker=ticker,
                            current_price=current_price,
                            entry_price=position.entry_price,
                        )
                        position.breakeven_breach_time = None
                        position.stop_breach_time = None

            # 📉 TRAILING STOP CHECK (high-conviction only — replaces floor rule)
            # After first tier exit, tracks peak and exits if price drops 15pp below peak.
            # Example: peak +72%, trailing stop fires at +57%.
            if position.is_high_conviction and position.trailing_stop_active and position.shares_remaining > 0:
                trailing_exit_level = position.trailing_stop_peak_pct - HIGH_CONVICTION_TRAILING_PCT
                if profit_pct <= trailing_exit_level:
                    if ticker not in self._exits_in_progress:
                        self._exits_in_progress.add(ticker)
                        logger.warning(
                            f"📉 TRAILING STOP TRIGGERED: Dropped 15pp from peak +{position.trailing_stop_peak_pct*100:.1f}%",
                            ticker=ticker,
                            current_price=current_price,
                            entry_price=position.entry_price,
                            profit_pct=f"+{profit_pct*100:.1f}%",
                            peak_pct=f"+{position.trailing_stop_peak_pct*100:.1f}%",
                            trailing_exit_level=f"+{trailing_exit_level*100:.1f}%",
                            shares_remaining=position.shares_remaining,
                        )
                        asyncio.create_task(self._execute_exit_async(
                            position,
                            position.shares_remaining,
                            "hc_trailing_stop",
                            profit_pct
                        ))
                    return

            # 📉 FLOOR RULE CHECK: Exit if price drops to fixed floor level
            # After +15% exit, floor is +5%. After +20% exit, floor is +10%.
            # (Skipped for high-conviction — trailing stop handles this)
            if position.floor_price and position.shares_remaining > 0:
                if current_price <= position.floor_price:
                    if ticker not in self._exits_in_progress:
                        self._exits_in_progress.add(ticker)
                        # Get fixed floor % from mapping
                        floor_pct = TIER_FLOOR_PCT.get(position.last_exit_threshold)
                        if floor_pct is None:
                            floor_pct = position.last_exit_threshold * FLOOR_RULE_MULTIPLIER
                        logger.warning(
                            f"📉 FLOOR RULE TRIGGERED: Price dropped to +{floor_pct*100:.1f}% floor (after +{position.last_exit_threshold*100:.0f}% exit)",
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
            # Skipped for high-conviction — trailing stop handles fading moves better.
            if not position.is_high_conviction:
                now = datetime.now()
                minutes_held = (now - position.entry_time).total_seconds() / 60.0
                if (minutes_held >= EARLY_EXIT_MINUTES and
                    profit_pct >= EARLY_EXIT_PROFIT_PCT and
                    position.shares_remaining > 0):
                    if ticker not in self._exits_in_progress:
                        self._exits_in_progress.add(ticker)
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
                        if ticker not in self._exits_in_progress:
                            self._exits_in_progress.add(ticker)
                            position.last_exit_threshold = threshold_pct  # Update for floor rule
                            position.next_tier_index += 1
                            position.total_exits_taken += 1

                            # === HIGH-CONVICTION: Activate trailing stop after first tier ===
                            if position.is_high_conviction and not position.trailing_stop_active:
                                position.trailing_stop_active = True
                                position.trailing_stop_peak_pct = profit_pct
                                logger.info(
                                    f"📈 TRAILING STOP ACTIVATED: Tracking peak from +{profit_pct*100:.1f}%, "
                                    f"will exit if drops 15pp below peak",
                                    ticker=ticker,
                                    current_price=current_price,
                                    trailing_exit_at=f"+{(profit_pct - HIGH_CONVICTION_TRAILING_PCT)*100:.1f}%",
                                )

                            # === MOMENTUM TRACKING: Start after Tier 2 (+20%) ===
                            # Tier 2 is index 1, threshold 0.20 (+20%)
                            # After triggering tier 2, next_tier_index becomes 2
                            if position.next_tier_index == 2 and not position.momentum_tracking_active:
                                position.momentum_tracking_active = True
                                position.momentum_tier_2_time = now
                                position.momentum_tier_2_price = current_price
                                position.momentum_peak_after_tier_2 = profit_pct
                                position.momentum_peak_price = current_price
                                position.momentum_peak_time = now
                                logger.info(
                                    "📊 MOMENTUM TRACKING: Started after Tier 2 (+20%)",
                                    ticker=ticker,
                                    tier_2_price=current_price,
                                    profit_pct=f"+{profit_pct*100:.1f}%",
                                )

                            # Get new floor from fixed mapping (not used for high-conviction — trailing stop instead)
                            if position.is_high_conviction:
                                new_floor_str = "trailing_stop"
                            else:
                                new_floor_pct = TIER_FLOOR_PCT.get(threshold_pct)
                                new_floor_str = f"+{new_floor_pct*100:.1f}%" if new_floor_pct else "N/A (fully exited)"
                            exit_label = "HC TIERED EXIT" if position.is_high_conviction else "TIERED EXIT"
                            exit_reason = f"hc_tier_{threshold_pct*100:.0f}pct" if position.is_high_conviction else f"tier_{threshold_pct*100:.0f}pct"
                            logger.info(
                                f"📈 {exit_label}: +{threshold_pct*100:.0f}% threshold reached - exiting {exit_fraction*100:.0f}%",
                                ticker=ticker,
                                current_price=current_price,
                                entry_price=position.entry_price,
                                profit_pct=f"+{profit_pct*100:.1f}%",
                                shares_to_exit=shares_to_exit,
                                shares_remaining_after=position.shares_remaining - shares_to_exit,
                                tier_index=position.next_tier_index,
                                total_tiers=len(position._exit_thresholds),
                                new_floor=new_floor_str,
                            )
                            asyncio.create_task(self._execute_exit_async(
                                position,
                                shares_to_exit,
                                exit_reason,
                                profit_pct
                            ))
                    return

            # === MOMENTUM TRACKING: Record samples after Tier 2 ===
            # Sample every ~500ms to build trajectory for comparison analysis
            if position.momentum_tracking_active and position.shares_remaining > 0:
                # Update peak tracking
                if profit_pct > position.momentum_peak_after_tier_2:
                    position.momentum_peak_after_tier_2 = profit_pct
                    position.momentum_peak_price = current_price
                    position.momentum_peak_time = now

                # Record trajectory sample (limit to every 500ms to avoid too much data)
                should_sample = True
                if position.momentum_trajectory:
                    last_sample_time = position.momentum_trajectory[-1].get("time")
                    if last_sample_time:
                        last_dt = datetime.fromisoformat(last_sample_time) if isinstance(last_sample_time, str) else last_sample_time
                        if (now - last_dt).total_seconds() < 0.5:
                            should_sample = False

                if should_sample:
                    # Calculate velocity (rate of change since Tier 2)
                    seconds_since_tier_2 = (now - position.momentum_tier_2_time).total_seconds() if position.momentum_tier_2_time else 0
                    tier_2_profit = (position.momentum_tier_2_price - position.entry_price) / position.entry_price if position.momentum_tier_2_price else 0.15
                    velocity = (profit_pct - tier_2_profit) / seconds_since_tier_2 if seconds_since_tier_2 > 0 else 0

                    position.momentum_trajectory.append({
                        "time": now.isoformat(),
                        "price": current_price,
                        "profit_pct": round(profit_pct * 100, 2),
                        "seconds_since_tier_2": round(seconds_since_tier_2, 2),
                        "velocity_pct_per_sec": round(velocity * 100, 4),  # % change per second
                    })

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
            # Use unified ticker key (matches all add() calls)
            self._exits_in_progress.discard(position.ticker)

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

        # Calculate highest price from highest_profit_pct for signal analytics
        highest_price = position.entry_price * (1 + position.highest_profit_pct) if position.highest_profit_pct > 0 else None

        # Build momentum tracking data for exit analysis
        momentum_data = None
        if position.momentum_tracking_active:
            momentum_data = {
                "tier_2_triggered": True,
                "tier_2_time": position.momentum_tier_2_time.isoformat() if position.momentum_tier_2_time else None,
                "tier_2_price": position.momentum_tier_2_price,
                "peak_after_tier_2_pct": round(position.momentum_peak_after_tier_2 * 100, 2),
                "peak_price": position.momentum_peak_price,
                "peak_time": position.momentum_peak_time.isoformat() if position.momentum_peak_time else None,
                "trajectory_samples": len(position.momentum_trajectory),
                "trajectory": position.momentum_trajectory[-50:] if position.momentum_trajectory else [],  # Last 50 samples
            }

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
                # Peak tracking for exit pattern analysis
                "highest_profit_pct": position.highest_profit_pct,
                "highest_price": highest_price,
                # Momentum tracking for fixed vs trailing comparison
                "momentum_tracking": momentum_data,
            }
        )

        # Update position tracking FIRST (before publishing to prevent race condition
        # where another exit fires because shares_remaining hasn't been decremented yet)
        async with self._lock:
            position.shares_remaining -= shares
            # Track realized P&L from this exit
            estimated_exit_price = position.last_price or position.entry_price * (1 + profit_pct)
            pnl_from_exit = (estimated_exit_price - position.entry_price) * shares
            position.realized_pnl += pnl_from_exit

            # Record P&L for daily circuit breaker tracking (lazy import to avoid circular)
            from .auto_trade import record_trade_pnl
            record_trade_pnl(position.ticker, pnl_from_exit)

        # THEN publish the exit event (after shares_remaining is already decremented)
        await self.event_bus.publish("Domain.TradeRequested", exit_event.model_dump())

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
            # Cancel the scheduled 10-min auto-exit (ExitTradeUseCase) to prevent
            # a phantom SELL after position is already closed
            if self.exit_trade_use_case:
                self.exit_trade_use_case.cancel_scheduled_exit(position.ticker)
            await self.remove_position(position.ticker)

    async def _check_all_positions(self) -> None:
        """Check all positions for exit conditions (fallback polling)."""
        # OVERNIGHT RISK CHECK: Force exit all positions before session end
        if FORCE_EXIT_ENABLED:
            await self._check_force_exit_session_end()

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

                # Check scale-in confirmation (for no_volume entries awaiting confirmation)
                await self._check_scale_in_confirmation(ticker, current_price)

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
                "effective_stop": pos.effective_stop_price,
                "breakeven_stop_active": pos.breakeven_stop_active,
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
            "breakeven_trigger": f"+{BREAKEVEN_TRIGGER_PCT*100:.0f}% → stop moves to 0%",
            "early_exit": f"+{EARLY_EXIT_PROFIT_PCT*100:.0f}% after {EARLY_EXIT_MINUTES:.0f} min → exit 100%",
            "tiered_exits": [f"+{t[0]*100:.0f}%: {t[1]*100:.0f}% of remaining" for t in TIERED_EXIT_THRESHOLDS],
            "floor_rule": f"{FLOOR_RULE_MULTIPLIER*100:.0f}% of last exit threshold",
            "positions": positions_summary,
        }
