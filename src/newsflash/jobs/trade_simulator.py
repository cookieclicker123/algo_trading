"""
Trade Simulator - Simulates trades using Alpaca tick data.

Uses actual historical tick data to determine if a trade would have been
profitable given our stop-loss and take-profit rules.

Rules:
- Entry: +3 seconds after publication (prefilter + AI + confluence + fill time)
- First 5 seconds: 1.25s soft stop confirmation at -5%
- After 5 seconds: hard stop at -5% (immediate)
- Take profits (trailing stop):
  - +15%: sell 50%, stop moves to +5%
  - +30%: sell 25%, stop moves to +15%
  - +40%: sell remaining 25%
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from dotenv import load_dotenv
load_dotenv()

from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Simulation parameters
ENTRY_DELAY_SECONDS = 3.0  # Time from publication to entry (prefilter + AI + confluence + fill)
SOFT_STOP_WINDOW_SECONDS = 5.0  # First 5 seconds use soft stop with confirmation
SOFT_STOP_CONFIRMATION_SECONDS = 1.25  # Must stay below stop for 1.25s to trigger
STOP_LOSS_PCT = -5.0  # -5% stop loss
SIMULATION_DURATION_SECONDS = 600  # 10 minutes

# Take profit tiers: (trigger_pct, sell_pct, new_stop_pct)
TAKE_PROFIT_TIERS = [
    (15.0, 50, 5.0),   # At +15%, sell 50%, stop moves to +5%
    (30.0, 25, 15.0),  # At +30%, sell 25%, stop moves to +15%
    (40.0, 25, None),  # At +40%, sell remaining 25%
]


@dataclass
class SimulationResult:
    """Result of trade simulation."""
    ticker: str
    received_time: datetime  # When article was received
    entry_time: datetime     # When we entered (received + 3s)
    entry_price: float

    # Outcome
    would_have_traded: bool  # False if stopped out before any TP
    total_pnl_pct: float  # Total realized + unrealized P&L
    realized_pnl_pct: float  # Realized from take profits
    unrealized_pnl_pct: float  # Unrealized from remaining position

    # Position tracking
    position_remaining_pct: int  # % of position still held at end
    final_price: float
    final_pnl_pct: float  # P&L at final price

    # Stop/TP events
    stopped_out: bool
    stop_triggered_at: Optional[datetime] = None
    stop_price: Optional[float] = None
    stop_type: Optional[str] = None  # "soft" or "hard"
    stop_elapsed_seconds: Optional[float] = None  # Seconds from entry to stop

    # Take profit events
    tp_events: List[Dict[str, Any]] = None

    # Peak tracking
    max_price: float = 0.0
    max_pnl_pct: float = 0.0
    max_pnl_elapsed_seconds: float = 0.0  # When peak occurred
    min_price: float = 0.0
    min_pnl_pct: float = 0.0

    # Simple hold comparison (10 min hold with -5% hard stop only, no TPs)
    simple_hold_pnl_pct: float = 0.0  # P&L from simple hold strategy
    end_of_window_price: float = 0.0  # Price at exactly 10 min mark

    # Move progression tracking (when key price levels were first crossed)
    # These help identify "late movers" and move graduation patterns
    move_progression: Dict[str, Any] = None  # Populated with timing of key events

    # Quote count
    trade_count: int = 0

    def __post_init__(self):
        if self.tp_events is None:
            self.tp_events = []

    def format_elapsed(self, seconds: float) -> str:
        """Format elapsed seconds as Xm Ys.XXXs"""
        mins = int(seconds // 60)
        secs = seconds % 60
        if mins > 0:
            return f"{mins}m {secs:.3f}s"
        return f"{secs:.3f}s"

    @property
    def stop_elapsed_formatted(self) -> Optional[str]:
        """Human-readable stop timing (e.g., '2m 15.234s')"""
        if self.stop_elapsed_seconds is not None:
            return self.format_elapsed(self.stop_elapsed_seconds)
        return None


def get_alpaca_data_client():
    """Get Alpaca historical data client."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient

        api_key = (
            os.getenv("ALPACA_KEY_PAPER") or
            os.getenv("ALPACA_API_KEY") or
            os.getenv("ALPACA_KEY")
        )
        secret_key = (
            os.getenv("ALPACA_SECRET_PAPER") or
            os.getenv("ALPACA_SECRET_KEY") or
            os.getenv("ALPACA_SECRET")
        )

        if api_key and secret_key:
            return StockHistoricalDataClient(api_key, secret_key)
    except ImportError:
        pass
    return None


async def simulate_trade(
    ticker: str,
    received_time: datetime,
    entry_price_hint: Optional[float] = None,
) -> Optional[SimulationResult]:
    """
    Simulate a trade using Alpaca historical quote data.

    Uses NBBO quotes (not trades) for accurate stop-loss and take-profit simulation.
    Entry is at ask price (buying), exits at bid price (selling).

    Args:
        ticker: Stock ticker
        received_time: When article was RECEIVED (not published) - entry is received + 3s
        entry_price_hint: Optional hint for entry price (from recall record ask price)

    Returns:
        SimulationResult with full trade simulation, or None if data unavailable
    """
    client = get_alpaca_data_client()
    if not client:
        logger.warning("Alpaca data client not available for simulation")
        return None

    try:
        from alpaca.data.requests import StockQuotesRequest
        from alpaca.data.enums import DataFeed

        # Fetch quotes from received time to +10 minutes
        # Entry is at received + 3 seconds (time for prefilter + AI + confluence + fill)
        start_time = received_time
        end_time = received_time + timedelta(seconds=SIMULATION_DURATION_SECONDS + ENTRY_DELAY_SECONDS + 10)

        quotes_response = client.get_stock_quotes(StockQuotesRequest(
            symbol_or_symbols=ticker,
            start=start_time,
            end=end_time,
            feed=DataFeed.SIP
        ))

        if not quotes_response.data or ticker not in quotes_response.data:
            logger.debug(f"No quote data for {ticker} at {received_time}")
            return None

        quote_list = quotes_response.data[ticker]
        if len(quote_list) < 5:
            logger.debug(f"Insufficient quote data for {ticker}: {len(quote_list)} quotes")
            return None

        # Find entry time and price (+3 seconds after publication)
        entry_time = received_time + timedelta(seconds=ENTRY_DELAY_SECONDS)

        # Find first quote at or after entry time
        entry_quote = None
        for q in quote_list:
            if q.timestamp >= entry_time:
                entry_quote = q
                break

        if not entry_quote:
            logger.debug(f"No quotes after entry time for {ticker}")
            return None

        # Entry at ask price (we're buying)
        entry_price = entry_quote.ask_price
        entry_time = entry_quote.timestamp

        # If entry price hint provided and significantly different, use hint
        if entry_price_hint and abs(entry_price - entry_price_hint) / entry_price_hint > 0.05:
            entry_price = entry_price_hint

        # Run simulation with quotes
        return _run_simulation(
            ticker=ticker,
            received_time=received_time,
            entry_time=entry_time,
            entry_price=entry_price,
            quotes=quote_list,
        )

    except Exception as e:
        logger.error(f"Error simulating trade for {ticker}: {e}", exc_info=True)
        return None


def _run_simulation(
    ticker: str,
    received_time: datetime,
    entry_time: datetime,
    entry_price: float,
    quotes: List,
) -> SimulationResult:
    """
    Run the actual trade simulation with stop-loss and take-profit logic.

    Uses bid price for all exit calculations (stop-loss and take-profit)
    since that's what we'd actually receive when selling.
    """
    # Initialize state
    position_pct = 100  # Start with 100% position
    current_stop_pct = STOP_LOSS_PCT  # -5%
    realized_pnl = 0.0

    # Tracking
    breach_start = None
    stopped_out = False
    stop_triggered_at = None
    stop_price = None
    stop_type = None
    stop_elapsed_seconds = None
    tp_events = []
    tp_tier_index = 0  # Which TP tier we're on

    max_price = entry_price
    min_price = entry_price
    max_pnl_pct = 0.0
    max_pnl_elapsed_seconds = 0.0
    min_pnl_pct = 0.0

    final_price = entry_price
    quote_count = 0

    # Simple hold tracking (separate from main simulation)
    simple_hold_stopped = False
    simple_hold_stop_price = None
    end_of_window_price = entry_price  # Will be updated to last quote in window

    # Move progression tracking - when key price levels were first crossed
    # This helps identify "late movers" and graduation patterns
    move_progression = {
        "first_positive_at": None,      # When P&L first > 0%
        "first_positive_elapsed": None,
        "pct_05_at": None,              # When P&L first >= +0.5% (strength level)
        "pct_05_elapsed": None,
        "pct_1_at": None,               # When P&L first >= +1%
        "pct_1_elapsed": None,
        "pct_5_at": None,               # When P&L first >= +5% (surge level)
        "pct_5_elapsed": None,
        "pct_10_at": None,              # When P&L first >= +10%
        "pct_10_elapsed": None,
        "pct_15_at": None,              # When P&L first >= +15% (TP1)
        "pct_15_elapsed": None,
    }

    # Simulation end time
    sim_end_time = entry_time + timedelta(seconds=SIMULATION_DURATION_SECONDS)
    soft_stop_end_time = entry_time + timedelta(seconds=SOFT_STOP_WINDOW_SECONDS)

    for q in quotes:
        # Skip quotes before entry
        if q.timestamp < entry_time:
            continue

        # Stop at simulation end
        if q.timestamp > sim_end_time:
            break

        quote_count += 1

        # Use bid price for exit calculations (what we'd actually get when selling)
        bid_price = q.bid_price
        mid_price = (q.bid_price + q.ask_price) / 2
        final_price = mid_price
        end_of_window_price = mid_price  # Update to latest price in window

        # P&L based on bid (exit price)
        bid_pnl_pct = ((bid_price - entry_price) / entry_price) * 100

        # Track extremes using midpoint
        elapsed_seconds = (q.timestamp - entry_time).total_seconds()
        mid_pnl_pct = ((mid_price - entry_price) / entry_price) * 100
        if mid_price > max_price:
            max_price = mid_price
            max_pnl_pct = mid_pnl_pct
            max_pnl_elapsed_seconds = elapsed_seconds
        if mid_price < min_price:
            min_price = mid_price
            min_pnl_pct = mid_pnl_pct

        # Simple hold tracking: -5% hard stop only, no TPs
        if not simple_hold_stopped and bid_pnl_pct <= STOP_LOSS_PCT:
            simple_hold_stopped = True
            simple_hold_stop_price = bid_price

        # Move progression tracking - when key price levels are first crossed
        # Use mid_pnl_pct for consistency with max_pnl tracking
        if move_progression["first_positive_at"] is None and mid_pnl_pct > 0:
            move_progression["first_positive_at"] = q.timestamp.isoformat()
            move_progression["first_positive_elapsed"] = round(elapsed_seconds, 3)
        if move_progression["pct_05_at"] is None and mid_pnl_pct >= 0.5:
            move_progression["pct_05_at"] = q.timestamp.isoformat()
            move_progression["pct_05_elapsed"] = round(elapsed_seconds, 3)
        if move_progression["pct_1_at"] is None and mid_pnl_pct >= 1.0:
            move_progression["pct_1_at"] = q.timestamp.isoformat()
            move_progression["pct_1_elapsed"] = round(elapsed_seconds, 3)
        if move_progression["pct_5_at"] is None and mid_pnl_pct >= 5.0:
            move_progression["pct_5_at"] = q.timestamp.isoformat()
            move_progression["pct_5_elapsed"] = round(elapsed_seconds, 3)
        if move_progression["pct_10_at"] is None and mid_pnl_pct >= 10.0:
            move_progression["pct_10_at"] = q.timestamp.isoformat()
            move_progression["pct_10_elapsed"] = round(elapsed_seconds, 3)
        if move_progression["pct_15_at"] is None and mid_pnl_pct >= 15.0:
            move_progression["pct_15_at"] = q.timestamp.isoformat()
            move_progression["pct_15_elapsed"] = round(elapsed_seconds, 3)

        # Check if stopped out already
        if stopped_out or position_pct <= 0:
            continue

        # Determine current stop level
        in_soft_window = q.timestamp <= soft_stop_end_time

        # Check stop-loss (using bid price - what we'd actually get)
        if bid_pnl_pct <= current_stop_pct:
            if in_soft_window:
                # Soft stop - need 1.25s confirmation
                if breach_start is None:
                    breach_start = q.timestamp
                elif (q.timestamp - breach_start).total_seconds() >= SOFT_STOP_CONFIRMATION_SECONDS:
                    # Confirmed soft stop
                    stopped_out = True
                    stop_triggered_at = q.timestamp
                    stop_price = bid_price
                    stop_type = "soft"
                    stop_elapsed_seconds = elapsed_seconds
                    realized_pnl += position_pct * (bid_pnl_pct / 100)
                    position_pct = 0
                    break
            else:
                # Hard stop - immediate
                stopped_out = True
                stop_triggered_at = q.timestamp
                stop_price = bid_price
                stop_type = "hard"
                stop_elapsed_seconds = elapsed_seconds
                realized_pnl += position_pct * (bid_pnl_pct / 100)
                position_pct = 0
                break
        else:
            # Price recovered - reset breach timer
            breach_start = None

        # Check take profits (only if we have position, using bid price)
        if position_pct > 0 and tp_tier_index < len(TAKE_PROFIT_TIERS):
            trigger_pct, sell_pct, new_stop_pct = TAKE_PROFIT_TIERS[tp_tier_index]

            if bid_pnl_pct >= trigger_pct:
                # Hit take profit tier
                actual_sell = min(sell_pct, position_pct)

                # Realize P&L at bid price
                tier_realized = actual_sell * (bid_pnl_pct / 100)
                realized_pnl += tier_realized
                position_pct -= actual_sell

                # Move stop up
                if new_stop_pct is not None:
                    current_stop_pct = new_stop_pct

                tp_events.append({
                    "tier": tp_tier_index + 1,
                    "trigger_pct": trigger_pct,
                    "price": bid_price,
                    "pnl_pct": bid_pnl_pct,
                    "sold_pct": actual_sell,
                    "realized": tier_realized,
                    "new_stop_pct": new_stop_pct,
                    "timestamp": q.timestamp.isoformat(),
                    "elapsed_seconds": (q.timestamp - entry_time).total_seconds(),
                })

                tp_tier_index += 1
                breach_start = None  # Reset breach since stop moved

    # Calculate final unrealized P&L (based on midpoint)
    final_pnl_pct = ((final_price - entry_price) / entry_price) * 100
    unrealized_pnl = (position_pct / 100) * final_pnl_pct if position_pct > 0 else 0.0
    total_pnl = realized_pnl + unrealized_pnl

    # Calculate simple hold P&L: -5% hard stop only, no TPs
    if simple_hold_stopped:
        simple_hold_pnl = STOP_LOSS_PCT  # -5%
    else:
        # Held to end of window - use end of window price
        simple_hold_pnl = ((end_of_window_price - entry_price) / entry_price) * 100

    # Determine if this would have been a successful trade
    # A trade is successful if it wasn't stopped out OR if it hit at least one TP
    would_have_traded = not stopped_out or len(tp_events) > 0

    return SimulationResult(
        ticker=ticker,
        received_time=received_time,
        entry_time=entry_time,
        entry_price=entry_price,
        would_have_traded=would_have_traded,
        total_pnl_pct=round(total_pnl, 2),
        realized_pnl_pct=round(realized_pnl, 2),
        unrealized_pnl_pct=round(unrealized_pnl, 2),
        position_remaining_pct=position_pct,
        final_price=final_price,
        final_pnl_pct=round(final_pnl_pct, 2),
        stopped_out=stopped_out,
        stop_triggered_at=stop_triggered_at,
        stop_price=stop_price,
        stop_type=stop_type,
        stop_elapsed_seconds=round(stop_elapsed_seconds, 3) if stop_elapsed_seconds is not None else None,
        tp_events=tp_events,
        max_price=max_price,
        max_pnl_pct=round(max_pnl_pct, 2),
        max_pnl_elapsed_seconds=round(max_pnl_elapsed_seconds, 3),
        min_price=min_price,
        min_pnl_pct=round(min_pnl_pct, 2),
        simple_hold_pnl_pct=round(simple_hold_pnl, 2),
        end_of_window_price=end_of_window_price,
        move_progression=move_progression,
        trade_count=quote_count,
    )


async def simulate_trade_sync(
    ticker: str,
    received_time: datetime,
    entry_price_hint: Optional[float] = None,
) -> Optional[SimulationResult]:
    """Synchronous wrapper for simulate_trade (for use in sync contexts)."""
    return await simulate_trade(ticker, received_time, entry_price_hint)
