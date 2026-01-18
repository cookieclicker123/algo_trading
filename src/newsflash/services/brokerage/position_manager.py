"""
Position Manager - Tracks open positions and manages tiered exit strategy with stop loss.

CONFLUENCE-BASED POSITION SIZING:
Based on 2-second observation window after article publication:
- Spread widening >15% → +2 points (market makers retreating = real catalyst)
- Spread widening 5-15% → +1 point
- Spread stable (±5%) → 0 points
- Spread tightening >5% → -1 point (market ignoring news)
- Volume surge >3x → +1 point
- Price excursion >1% → +1 point

Position sizes by score:
- Score ≤0: $2,000 (MINIMUM) - low confluence
- Score 1: $5,000 (STANDARD)
- Score 2: $7,500 (HIGH)
- Score 3+: $10,000 (VERY_HIGH)

STOP LOSS:
- 5% below initial NBBO mid price (NOT entry price)
- Anchored to pre-move price to protect against "fake rallies"
- Good catalysts shouldn't dump 5% below where they started

STANDARD Exit Strategy (base $5k trades):
- Target 1: +10% profit → Sell 50% of position
- Target 2: +15% profit → Sell 50% of remaining (25% of original)
- Target 3: +20% profit → Sell remaining position

HIGH-CONVICTION Exit Strategy ($7.5k-$10k trades with early momentum):
- Target 1: +15% profit → Sell 25% of position
- Target 2: +25% profit → Sell 25% of position
- Target 3: +40% profit → Sell remaining 50%

Uses WebSocket for real-time price monitoring (sub-100ms latency).
Falls back to 500ms polling if WebSocket unavailable.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List, Any, Callable
from decimal import Decimal
from enum import Enum

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...models.base_models import TradeRequest
from ...domain.brokerage.models import TradeAction, TradeInstrument

logger = get_logger(__name__)


class ExitTier(Enum):
    """Exit tier levels."""
    TIER_1 = "tier_1"  # Standard: 50% at +10%, Aggressive: 25% at +15%
    TIER_2 = "tier_2"  # Standard: 25% at +15%, Aggressive: 25% at +25%
    TIER_3 = "tier_3"  # Standard: 25% at +20%, Aggressive: 50% at +40%
    COMPLETE = "complete"  # Fully exited


class ConvictionLevel(Enum):
    """Trade conviction level based on confluence scoring."""
    MINIMUM = "minimum"             # Low confluence score (≤0) → $2k position
    STANDARD = "standard"           # Score 1 → $5k, standard exits
    HIGH = "high"                   # Score 2 → $7.5k, aggressive exits
    VERY_HIGH = "very_high"         # Score 3+ → $10k, aggressive exits


# Stop loss configuration
STOP_LOSS_PCT = 0.05  # 5% below initial NBBO mid


@dataclass
class Position:
    """Represents an open position with exit tracking."""
    ticker: str
    entry_price: float
    shares: float
    entry_time: datetime
    article_id: str

    # Conviction level determines exit strategy and position size
    conviction: ConvictionLevel = ConvictionLevel.STANDARD

    # Stop loss tracking - anchored to initial NBBO mid (not entry price)
    # This protects against "fake rallies" that return to origin
    initial_nbbo_mid: Optional[float] = None
    stop_loss_triggered: bool = False

    # Exit tracking
    current_tier: ExitTier = ExitTier.TIER_1
    shares_remaining: float = field(init=False)
    shares_sold: float = 0.0

    # Tier execution tracking
    tier_1_executed: bool = False
    tier_2_executed: bool = False
    tier_3_executed: bool = False

    # P&L tracking
    total_cost_basis: float = field(init=False)
    realized_pnl: float = 0.0

    # Current price tracking (updated by monitor)
    last_price: Optional[float] = None
    last_price_time: Optional[datetime] = None

    def __post_init__(self):
        self.shares_remaining = self.shares
        self.total_cost_basis = self.entry_price * self.shares

    @property
    def stop_loss_price(self) -> Optional[float]:
        """Calculate stop loss price (5% below initial NBBO mid)."""
        if self.initial_nbbo_mid:
            return self.initial_nbbo_mid * (1 - STOP_LOSS_PCT)
        return None

    @property
    def is_high_conviction(self) -> bool:
        """Check if this is a high-conviction trade (aggressive exits)."""
        return self.conviction in (ConvictionLevel.HIGH, ConvictionLevel.VERY_HIGH)

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

    def get_shares_for_tier(self, tier: ExitTier) -> float:
        """Calculate shares to sell for a given tier based on conviction level."""
        if tier == ExitTier.TIER_3:
            # Always sell all remaining shares at final tier
            return self.shares_remaining

        # Use conviction-based share percentages
        if self.is_high_conviction:
            # Aggressive: 25% / 25% / 50% (let winners run longer)
            tier_pct = TIER_SHARES_AGGRESSIVE.get(tier, 0.25)
        else:
            # Standard: 50% / 25% / 25%
            tier_pct = TIER_SHARES_STANDARD.get(tier, 0.50)

        return self.shares * tier_pct

    def get_exit_targets(self) -> Dict[ExitTier, float]:
        """Get exit targets based on conviction level."""
        if self.is_high_conviction:
            return EXIT_TARGETS_AGGRESSIVE
        return EXIT_TARGETS_STANDARD


# Exit targets (profit % after costs)
# We add ~0.5% buffer for exit costs (spread + slippage)
EXIT_COST_BUFFER = 0.005  # 0.5%

# Standard exit targets (base $5k trades)
EXIT_TARGETS_STANDARD = {
    ExitTier.TIER_1: 0.10 + EXIT_COST_BUFFER,  # 10.5% to net 10%
    ExitTier.TIER_2: 0.15 + EXIT_COST_BUFFER,  # 15.5% to net 15%
    ExitTier.TIER_3: 0.20 + EXIT_COST_BUFFER,  # 20.5% to net 20%
}

# Aggressive exit targets for high-conviction trades ($7.5k-$10k)
# These trades showed early momentum signals - let winners run longer
EXIT_TARGETS_AGGRESSIVE = {
    ExitTier.TIER_1: 0.15 + EXIT_COST_BUFFER,  # 15.5% to net 15%
    ExitTier.TIER_2: 0.25 + EXIT_COST_BUFFER,  # 25.5% to net 25%
    ExitTier.TIER_3: 0.40 + EXIT_COST_BUFFER,  # 40.5% to net 40%
}

# Share percentages for standard exits (50% / 25% / 25%)
TIER_SHARES_STANDARD = {
    ExitTier.TIER_1: 0.50,  # 50% of position
    ExitTier.TIER_2: 0.25,  # 25% of position
    ExitTier.TIER_3: 0.25,  # Remaining 25%
}

# Share percentages for aggressive exits (25% / 25% / 50%)
TIER_SHARES_AGGRESSIVE = {
    ExitTier.TIER_1: 0.25,  # 25% of position (let more ride)
    ExitTier.TIER_2: 0.25,  # 25% of position
    ExitTier.TIER_3: 0.50,  # Remaining 50% (bigger exit at top)
}

# Default for backward compatibility
EXIT_TARGETS = EXIT_TARGETS_STANDARD


class PositionManager:
    """
    Manages open positions and tiered exit strategy.

    Uses WebSocket streaming for real-time price monitoring (sub-100ms).
    Falls back to 500ms REST polling if WebSocket unavailable.

    Responsibilities:
    - Track open positions with entry prices
    - Subscribe to WebSocket quotes for monitored symbols
    - Check exit conditions on each price update
    - Trigger partial exits at each tier
    - Publish exit trade requests

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
        """
        Initialize position manager.

        Args:
            event_bus: Event bus for publishing exit requests
            quote_fetcher: Quote fetcher for REST price lookup (fallback)
            stream_manager: WebSocket stream manager for real-time quotes
            poll_interval: Seconds between price checks (fallback only)
            enabled: Whether position management is enabled
        """
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
            exit_targets={k.value: f"{v*100:.1f}%" for k, v in EXIT_TARGETS.items()}
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
        """
        Add a new position to track.

        Args:
            ticker: Stock ticker
            entry_price: Entry fill price
            shares: Number of shares
            article_id: Associated article ID
            conviction: Conviction level (MINIMUM, STANDARD, HIGH, or VERY_HIGH)
            initial_nbbo_mid: NBBO mid price at article publication (for stop loss)

        Returns:
            Created Position object
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
            )

            self._positions[ticker] = position

            # Subscribe to WebSocket quotes for this symbol
            if self.stream_manager:
                await self.stream_manager.subscribe_symbol(ticker)

            # Log with conviction-appropriate exit targets
            targets = position.get_exit_targets()
            strategy = "AGGRESSIVE" if position.is_high_conviction else "STANDARD"

            logger.info(
                f"Position added for {strategy} tiered exit management",
                ticker=ticker,
                entry_price=entry_price,
                shares=shares,
                cost_basis=position.total_cost_basis,
                conviction=conviction.value,
                initial_nbbo_mid=initial_nbbo_mid,
                stop_loss_price=position.stop_loss_price,
                targets={
                    f"tier_1_{int(targets[ExitTier.TIER_1]*100)}pct": round(entry_price * (1 + targets[ExitTier.TIER_1]), 2),
                    f"tier_2_{int(targets[ExitTier.TIER_2]*100)}pct": round(entry_price * (1 + targets[ExitTier.TIER_2]), 2),
                    f"tier_3_{int(targets[ExitTier.TIER_3]*100)}pct": round(entry_price * (1 + targets[ExitTier.TIER_3]), 2),
                }
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
        """Remove a fully exited position."""
        async with self._lock:
            if ticker in self._positions:
                position = self._positions.pop(ticker)
                self._exits_in_progress.discard(ticker)
                logger.info(
                    "Position removed (fully exited)",
                    ticker=ticker,
                    realized_pnl=position.realized_pnl,
                    shares_sold=position.shares_sold
                )

    async def request_manual_exit(self, ticker: str) -> bool:
        """
        Request manual exit of entire position.

        Args:
            ticker: Ticker to exit

        Returns:
            True if position exists and exit requested
        """
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
        """
        Handle QuoteReceived event from WebSocket stream.

        This is the primary exit check mechanism - runs on every quote update.
        """
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
                    # Release lock before executing exit
                    asyncio.create_task(self._execute_exit_async(
                        position,
                        position.shares_remaining,
                        "manual_exit",
                        (current_price - position.entry_price) / position.entry_price
                    ))
                    self._manual_exits.pop(ticker, None)
                return

            # 🛑 STOP LOSS CHECK: Exit entire position if price drops 5% below initial NBBO
            # This protects against "fake rallies" - good catalysts shouldn't dump below origin
            if position.stop_loss_price and not position.stop_loss_triggered:
                if current_price <= position.stop_loss_price:
                    exit_key = f"{ticker}_stop_loss"
                    if exit_key not in self._exits_in_progress:
                        self._exits_in_progress.add(exit_key)
                        position.stop_loss_triggered = True
                        loss_pct = (current_price - position.entry_price) / position.entry_price
                        loss_from_initial = (current_price - position.initial_nbbo_mid) / position.initial_nbbo_mid
                        logger.warning(
                            f"🛑 STOP LOSS TRIGGERED: Price dropped {STOP_LOSS_PCT*100:.0f}% below initial NBBO",
                            ticker=ticker,
                            current_price=current_price,
                            stop_loss_price=position.stop_loss_price,
                            initial_nbbo_mid=position.initial_nbbo_mid,
                            entry_price=position.entry_price,
                            loss_from_entry_pct=f"{loss_pct*100:.1f}%",
                            loss_from_initial_pct=f"{loss_from_initial*100:.1f}%",
                            shares_remaining=position.shares_remaining
                        )
                        asyncio.create_task(self._execute_exit_async(
                            position,
                            position.shares_remaining,
                            "stop_loss",
                            loss_pct
                        ))
                    return

            # Calculate profit %
            profit_pct = (current_price - position.entry_price) / position.entry_price

            # Get conviction-based exit targets
            exit_targets = position.get_exit_targets()

            # Check each tier (with exit-in-progress guard)
            exit_key = f"{ticker}_{position.current_tier.value}"
            if exit_key in self._exits_in_progress:
                return  # Already executing this tier

            if not position.tier_1_executed and profit_pct >= exit_targets[ExitTier.TIER_1]:
                self._exits_in_progress.add(exit_key)
                shares_to_sell = position.get_shares_for_tier(ExitTier.TIER_1)
                asyncio.create_task(self._execute_exit_async(
                    position, shares_to_sell, "tier_1", profit_pct
                ))

            elif not position.tier_2_executed and position.tier_1_executed and profit_pct >= exit_targets[ExitTier.TIER_2]:
                self._exits_in_progress.add(exit_key)
                shares_to_sell = position.get_shares_for_tier(ExitTier.TIER_2)
                asyncio.create_task(self._execute_exit_async(
                    position, shares_to_sell, "tier_2", profit_pct
                ))

            elif not position.tier_3_executed and position.tier_2_executed and profit_pct >= exit_targets[ExitTier.TIER_3]:
                self._exits_in_progress.add(exit_key)
                shares_to_sell = position.get_shares_for_tier(ExitTier.TIER_3)
                asyncio.create_task(self._execute_exit_async(
                    position, shares_to_sell, "tier_3", profit_pct
                ))

    async def _execute_exit_async(
        self,
        position: Position,
        shares: float,
        exit_reason: str,
        profit_pct: float,
    ) -> None:
        """Execute exit and update position state."""
        try:
            await self._execute_exit(position, shares, exit_reason, profit_pct)
        finally:
            # Clean up exit-in-progress tracking
            exit_key = f"{position.ticker}_{exit_reason}"
            self._exits_in_progress.discard(exit_key)

    async def _execute_exit(
        self,
        position: Position,
        shares: float,
        exit_reason: str,
        profit_pct: float,
    ) -> None:
        """
        Execute a partial or full exit.

        Args:
            position: Position to exit
            shares: Number of shares to sell
            exit_reason: Reason for exit (tier_1, tier_2, tier_3, manual_exit)
            profit_pct: Current profit percentage
        """
        if shares <= 0:
            return

        logger.info(
            f"Executing {exit_reason} exit",
            ticker=position.ticker,
            shares=int(shares),
            profit_pct=f"{profit_pct*100:.1f}%",
            shares_remaining_before=position.shares_remaining,
            article_id=position.article_id
        )

        # Build sell trade request
        trade_request = TradeRequest(
            ticker=position.ticker,
            action=TradeAction.SELL,
            shares=int(shares),  # Round to whole shares
            amount_usd=None,
            leverage=None,
            article_id=position.article_id,
            instrument=TradeInstrument.STOCK,
        )

        # Publish trade request event
        from ...domain.brokerage.events import TradeRequestDomainEvent

        # Include conviction info in exit metadata
        exit_strategy = "aggressive" if position.is_high_conviction else "standard"
        exit_targets = position.get_exit_targets()

        exit_event = TradeRequestDomainEvent(
            trade_request=trade_request,
            article_id=position.article_id,
            requested_at=datetime.now(),
            metadata={
                "exit_reason": exit_reason,
                "profit_pct": profit_pct,
                "entry_price": position.entry_price,
                "tier": position.current_tier.value,
                "conviction": position.conviction.value,
                "exit_strategy": exit_strategy,
                "target_pct": exit_targets.get(position.current_tier, 0) * 100,
                "initial_nbbo_mid": position.initial_nbbo_mid,
                "stop_loss_price": position.stop_loss_price,
                "stop_loss_triggered": position.stop_loss_triggered,
            }
        )

        await self.event_bus.publish("Domain.TradeRequested", exit_event.model_dump())

        # Update position tracking
        async with self._lock:
            position.shares_remaining -= shares
            position.shares_sold += shares

            # Update tier status
            if exit_reason == "tier_1":
                position.tier_1_executed = True
                position.current_tier = ExitTier.TIER_2
            elif exit_reason == "tier_2":
                position.tier_2_executed = True
                position.current_tier = ExitTier.TIER_3
            elif exit_reason == "tier_3":
                position.tier_3_executed = True
                position.current_tier = ExitTier.COMPLETE
            elif exit_reason == "stop_loss":
                # Stop loss exits everything
                position.current_tier = ExitTier.COMPLETE

        logger.info(
            f"Exit trade request published: {exit_reason}",
            ticker=position.ticker,
            shares_sold=int(shares),
            shares_remaining=position.shares_remaining,
            total_shares_sold=position.shares_sold
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
                # Get current price via REST (fallback)
                current_price = None

                if self.stream_manager:
                    # Try WebSocket cache first
                    quote = await self.stream_manager.get_latest_quote(ticker)
                    if quote:
                        current_price = quote.get("bid")

                if not current_price and self.quote_fetcher:
                    # Fall back to REST
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
            "PositionManager started",
            poll_interval=self.poll_interval,
            has_websocket=self.stream_manager is not None,
            exit_targets={k.value: f"{v*100:.1f}%" for k, v in EXIT_TARGETS.items()}
        )

    async def stop(self) -> None:
        """Stop position monitoring."""
        self._running = False

        # Unsubscribe from quote events
        if self._quote_subscription_id:
            self.event_bus.unsubscribe("QuoteReceived", self._quote_subscription_id)
            self._quote_subscription_id = None

        # Cancel polling loop
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        logger.info(
            "PositionManager stopped",
            open_positions=len(self._positions)
        )

    def get_stats(self) -> Dict:
        """Get position manager statistics."""
        positions_summary = []
        for ticker, pos in self._positions.items():
            targets = pos.get_exit_targets()
            positions_summary.append({
                "ticker": ticker,
                "entry_price": pos.entry_price,
                "shares": pos.shares,
                "shares_remaining": pos.shares_remaining,
                "current_tier": pos.current_tier.value,
                "conviction": pos.conviction.value,
                "exit_strategy": "aggressive" if pos.is_high_conviction else "standard",
                "entry_time": pos.entry_time.isoformat(),
                "last_price": pos.last_price,
                "profit_pct": f"{pos.current_profit_pct*100:.1f}%" if pos.current_profit_pct else None,
                "unrealized_pnl": round(pos.unrealized_pnl, 2) if pos.unrealized_pnl else None,
                "targets": {k.value: f"{v*100:.1f}%" for k, v in targets.items()},
                "initial_nbbo_mid": pos.initial_nbbo_mid,
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
            "exit_targets_standard": {k.value: f"{v*100:.1f}%" for k, v in EXIT_TARGETS_STANDARD.items()},
            "exit_targets_aggressive": {k.value: f"{v*100:.1f}%" for k, v in EXIT_TARGETS_AGGRESSIVE.items()},
            "positions": positions_summary,
        }
