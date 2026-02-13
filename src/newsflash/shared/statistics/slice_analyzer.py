"""
Slice Analyzer - Divides 2-second confluence window into 8 x 250ms sub-slices.

Used for ML feature extraction to capture micro-trajectory of price/volume/pressure.
Key insight: Pressure consistency across slices predicts continuation.
"""
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import statistics

from .models import ConfluenceWindow, ConfluenceSlice


# Slice configuration
SLICE_DURATION_MS = 250
NUM_SLICES = 8  # 8 x 250ms = 2 seconds
WINDOW_DURATION_MS = 2000


@dataclass
class TradeData:
    """Normalized trade data for slice analysis."""
    timestamp_ms: int  # Ms offset from window start
    price: float
    size: int
    is_uptick: bool  # True if price > previous trade, False if price < previous
    is_buy: bool  # Classified as buy (uptick or same price with uptick momentum)


def classify_trades(trades: List[Dict[str, Any]], window_start: datetime) -> List[TradeData]:
    """
    Classify trades as buys/sells using tick rule and normalize timestamps.

    Args:
        trades: List of trade dicts with timestamp, price, size
        window_start: Start of the confluence window (publication time)

    Returns:
        List of TradeData with normalized timestamps and buy/sell classification
    """
    if not trades:
        return []

    result = []
    prev_price = None
    prev_direction = 0  # 1 = up, -1 = down, 0 = neutral

    for trade in trades:
        # Get timestamp
        ts = trade.get("timestamp") or trade.get("t")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except:
                continue
        elif isinstance(ts, datetime):
            pass
        else:
            continue

        # Calculate ms offset from window start
        if ts.tzinfo is None and window_start.tzinfo is not None:
            # Make ts timezone-aware if needed
            ts = ts.replace(tzinfo=window_start.tzinfo)
        elif ts.tzinfo is not None and window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=ts.tzinfo)

        offset_ms = int((ts - window_start).total_seconds() * 1000)

        # Skip if outside window
        if offset_ms < 0 or offset_ms >= WINDOW_DURATION_MS:
            continue

        # Get price and size
        price = float(trade.get("price") or trade.get("p") or 0)
        size = int(trade.get("size") or trade.get("s") or 0)

        if price <= 0 or size <= 0:
            continue

        # Tick rule: classify based on price movement
        if prev_price is None:
            is_uptick = True  # First trade assumed neutral/up
            is_buy = True
        elif price > prev_price:
            is_uptick = True
            prev_direction = 1
            is_buy = True
        elif price < prev_price:
            is_uptick = False
            prev_direction = -1
            is_buy = False
        else:
            # Same price - use previous direction
            is_uptick = prev_direction >= 0
            is_buy = prev_direction >= 0

        result.append(TradeData(
            timestamp_ms=offset_ms,
            price=price,
            size=size,
            is_uptick=is_uptick,
            is_buy=is_buy
        ))

        prev_price = price

    return result


def create_slice(
    trades: List[TradeData],
    slice_index: int,
    slice_start_ms: int,
    slice_end_ms: int
) -> ConfluenceSlice:
    """
    Create stats for a single 250ms slice.
    """
    # Filter trades in this slice
    slice_trades = [t for t in trades if slice_start_ms <= t.timestamp_ms < slice_end_ms]

    slice_data = ConfluenceSlice(
        slice_start_ms=slice_start_ms,
        slice_end_ms=slice_end_ms,
    )

    if not slice_trades:
        return slice_data

    # Volume stats
    slice_data.volume = sum(t.size for t in slice_trades)
    slice_data.trade_count = len(slice_trades)
    slice_data.buy_volume = sum(t.size for t in slice_trades if t.is_buy)
    slice_data.sell_volume = sum(t.size for t in slice_trades if not t.is_buy)

    # Price stats
    prices = [t.price for t in slice_trades]
    slice_data.first_price = prices[0]
    slice_data.last_price = prices[-1]
    slice_data.high_price = max(prices)
    slice_data.low_price = min(prices)

    # Pressure
    total_vol = slice_data.buy_volume + slice_data.sell_volume
    if total_vol > 0:
        slice_data.imbalance_ratio = (slice_data.buy_volume - slice_data.sell_volume) / total_vol
        if slice_data.imbalance_ratio > 0.1:
            slice_data.pressure_sign = 1
        elif slice_data.imbalance_ratio < -0.1:
            slice_data.pressure_sign = -1
        else:
            slice_data.pressure_sign = 0

    # Tick counts
    slice_data.uptick_count = sum(1 for t in slice_trades if t.is_uptick)
    slice_data.downtick_count = sum(1 for t in slice_trades if not t.is_uptick)

    return slice_data


def build_confluence_window(
    trades: List[Dict[str, Any]],
    window_start: datetime,
    initial_nbbo: Optional[Dict[str, Any]] = None,
    final_nbbo: Optional[Dict[str, Any]] = None,
    baseline_volume_5s: Optional[int] = None,
    baseline_trades_5s: Optional[int] = None,
    baseline_spread: Optional[float] = None,
    baseline_avg_trade_size: Optional[float] = None,
) -> ConfluenceWindow:
    """
    Build comprehensive ConfluenceWindow with 8 x 250ms sub-slices.

    Args:
        trades: Raw trade list from Alpaca
        window_start: Publication time (start of 2-second window)
        initial_nbbo: NBBO at window start
        final_nbbo: NBBO at window end
        baseline_*: Pre-news baseline stats for ratio calculation

    Returns:
        ConfluenceWindow with all stats and sub-slices
    """
    # Classify trades
    classified = classify_trades(trades, window_start)

    # Initialize window
    window = ConfluenceWindow()

    if not classified:
        window.confluence_met = False
        return window

    # === Overall 2-second stats ===
    window.total_volume = sum(t.size for t in classified)
    window.total_trades = len(classified)
    window.total_buy_volume = sum(t.size for t in classified if t.is_buy)
    window.total_sell_volume = sum(t.size for t in classified if not t.is_buy)

    # Price trajectory
    prices = [t.price for t in classified]
    sizes = [t.size for t in classified]

    window.first_price = prices[0]
    window.last_price = prices[-1]
    window.high_price = max(prices)
    window.low_price = min(prices)

    # VWAP
    total_value = sum(t.price * t.size for t in classified)
    if window.total_volume > 0:
        window.vwap = total_value / window.total_volume

    # Dollar volume
    window.dollar_volume = total_value

    # Price excursion (max move from first price)
    if window.first_price > 0:
        up_excursion = (window.high_price - window.first_price) / window.first_price
        down_excursion = (window.first_price - window.low_price) / window.first_price
        window.price_excursion_pct = max(up_excursion, down_excursion) * 100

        # Direction
        net_move = (window.last_price - window.first_price) / window.first_price
        if net_move > 0.001:
            window.price_direction = 1
        elif net_move < -0.001:
            window.price_direction = -1
        else:
            window.price_direction = 0

    # Pressure
    total_vol = window.total_buy_volume + window.total_sell_volume
    if total_vol > 0:
        window.imbalance_ratio = (window.total_buy_volume - window.total_sell_volume) / total_vol
        window.buying_pressure_pct = (window.total_buy_volume / total_vol) * 100

    # Tick counts
    window.uptick_count = sum(1 for t in classified if t.is_uptick)
    window.downtick_count = sum(1 for t in classified if not t.is_uptick)
    total_ticks = window.uptick_count + window.downtick_count
    if total_ticks > 0:
        window.uptick_ratio = window.uptick_count / total_ticks

    # Trade size analysis
    if classified:
        window.avg_trade_size = window.total_volume / len(classified)
        sorted_sizes = sorted(sizes)
        window.median_trade_size = sorted_sizes[len(sorted_sizes) // 2]
        window.max_single_trade = max(sizes)
        large_volume = sum(t.size for t in classified if t.size >= 500)
        window.large_trade_pct = (large_volume / window.total_volume * 100) if window.total_volume > 0 else 0

    # Timing
    window.first_trade_latency_ms = classified[0].timestamp_ms
    first_uptick = next((t for t in classified if t.is_uptick), None)
    if first_uptick:
        window.first_uptick_latency_ms = first_uptick.timestamp_ms

    # Volume in first 500ms
    window.trades_in_first_500ms = sum(1 for t in classified if t.timestamp_ms < 500)
    window.volume_in_first_500ms = sum(t.size for t in classified if t.timestamp_ms < 500)

    # Max trade gap
    if len(classified) > 1:
        gaps = [classified[i+1].timestamp_ms - classified[i].timestamp_ms
                for i in range(len(classified) - 1)]
        window.max_trade_gap_ms = max(gaps) if gaps else 0

    # Spread/liquidity from NBBO
    if initial_nbbo:
        bid = initial_nbbo.get("bid", 0)
        ask = initial_nbbo.get("ask", 0)
        if ask > 0:
            window.initial_spread = ((ask - bid) / ask) * 100
        window.initial_bid_depth = initial_nbbo.get("bid_size")
        window.initial_ask_depth = initial_nbbo.get("ask_size")

    if final_nbbo:
        bid = final_nbbo.get("bid", 0)
        ask = final_nbbo.get("ask", 0)
        if ask > 0:
            window.final_spread = ((ask - bid) / ask) * 100
        window.final_bid_depth = final_nbbo.get("bid_size")
        window.final_ask_depth = final_nbbo.get("ask_size")

        # Spread compression
        if window.initial_spread and window.initial_spread > 0 and window.final_spread is not None:
            window.spread_compression_pct = ((window.initial_spread - window.final_spread) / window.initial_spread) * 100

    # Quote update count (not available from trade data, would need quote stream)

    # === Baseline ratios ===
    window.baseline_volume_5s = baseline_volume_5s
    window.baseline_trades_5s = baseline_trades_5s
    window.baseline_spread = baseline_spread
    window.baseline_avg_trade_size = baseline_avg_trade_size

    if baseline_volume_5s and baseline_volume_5s > 0:
        window.volume_ratio = window.total_volume / baseline_volume_5s
    if baseline_trades_5s and baseline_trades_5s > 0:
        window.trade_count_ratio = window.total_trades / baseline_trades_5s
    if baseline_spread and baseline_spread > 0 and window.final_spread:
        window.spread_ratio = window.final_spread / baseline_spread
    if baseline_avg_trade_size and baseline_avg_trade_size > 0 and window.avg_trade_size:
        window.trade_size_ratio = window.avg_trade_size / baseline_avg_trade_size

    # === Build 8 x 250ms slices ===
    slices = []
    for i in range(NUM_SLICES):
        slice_start = i * SLICE_DURATION_MS
        slice_end = (i + 1) * SLICE_DURATION_MS
        slice_data = create_slice(classified, i, slice_start, slice_end)
        slices.append(slice_data)

    window.slices = slices

    # === Pressure consistency (KEY ML FEATURE) ===
    # Calculate pressure for first half (0-1000ms) vs second half (1000-2000ms)
    first_half = [t for t in classified if t.timestamp_ms < 1000]
    second_half = [t for t in classified if t.timestamp_ms >= 1000]

    if first_half:
        buy_vol_first = sum(t.size for t in first_half if t.is_buy)
        total_first = sum(t.size for t in first_half)
        if total_first > 0:
            window.pressure_first_half = (buy_vol_first - (total_first - buy_vol_first)) / total_first

    if second_half:
        buy_vol_second = sum(t.size for t in second_half if t.is_buy)
        total_second = sum(t.size for t in second_half)
        if total_second > 0:
            window.pressure_second_half = (buy_vol_second - (total_second - buy_vol_second)) / total_second

    # Pressure consistent if same sign in both halves
    if window.pressure_first_half is not None and window.pressure_second_half is not None:
        window.pressure_consistent = (
            (window.pressure_first_half > 0 and window.pressure_second_half > 0) or
            (window.pressure_first_half < 0 and window.pressure_second_half < 0)
        )
        # Pressure strengthening if second half stronger in same direction
        if window.pressure_consistent:
            window.pressure_strengthening = abs(window.pressure_second_half) > abs(window.pressure_first_half)

    # === Confluence scoring ===
    window.has_volume_surge = window.total_volume >= 2000
    window.has_price_excursion = (window.price_excursion_pct or 0) >= 1.0
    window.has_buying_pressure = (window.buying_pressure_pct or 0) >= 80

    window.confluence_score = sum([
        window.has_volume_surge,
        window.has_price_excursion,
        window.has_buying_pressure
    ])

    window.confluence_met = window.confluence_score >= 1

    return window


def build_surge_window(
    surge_stats: Dict[str, Any],
    triggered: bool = False,
    found: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Build SurgeWindow from surge detection stats.
    Only populated if surge monitoring was triggered.

    Returns dict instead of SurgeWindow for easy serialization.
    """
    if not triggered:
        return None

    return {
        "triggered": triggered,
        "found": found,
        "detection_cycle": surge_stats.get("detection_cycle"),
        "seconds_elapsed": surge_stats.get("seconds_elapsed"),
        "volume": surge_stats.get("volume"),
        "trade_count": surge_stats.get("trade_count"),
        "buy_volume": surge_stats.get("buy_volume"),
        "sell_volume": surge_stats.get("sell_volume"),
        "buying_pressure_pct": surge_stats.get("buying_pressure_pct"),
        "imbalance_ratio": surge_stats.get("imbalance_ratio"),
        "price_excursion_pct": surge_stats.get("price_excursion_pct"),
        "volume_multiplier": surge_stats.get("volume_multiplier"),
        "trade_count_multiplier": surge_stats.get("trade_count_multiplier"),
        "bid": surge_stats.get("bid"),
        "ask": surge_stats.get("ask"),
        "mid": surge_stats.get("mid"),
    }
