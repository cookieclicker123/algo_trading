#!/usr/bin/env python3
"""
Analyze missed trades (NEOG, CNCK) to see if they would have been traded
with relaxed sector requirements during the 2-minute monitoring period.

For each 4-second window, checks:
- Volume surge multiplier (3x)
- Trade count multiplier (2x)
- Max excursion (1%)
- Buying pressure (70%)
- Window volume (5000) - WAIVED for Healthcare/Technology/Financial Services
- Buy volume (1000) - WAIVED for Healthcare/Technology/Financial Services
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest, StockQuotesRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.data.models import Trade, Quote

import os
from dotenv import load_dotenv

load_dotenv()

from newsflash.shared.statistics.volume_analyzer import (
    _fetch_trades_in_window,
    _fetch_prior_history_stats,
    _get_stats_at_time
)


def analyze_window(
    stats: Optional[Any],  # VolumeStats object
    prior_avg_vol: float,
    prior_avg_trade_count: float,
    reference_price: float,
    window_seconds: float = 4.0
) -> Dict[str, Any]:
    """Analyze a 4-second window for surge criteria."""
    if not stats:
        return {
            "window_volume": 0,
            "trade_count": 0,
            "surge_multiplier": 0.0,
            "trade_count_multiplier": 0.0,
            "max_excursion_pct": 0.0,
            "buying_pressure_pct": 0.0,
            "buy_volume": 0,
            "meets_volume_surge": False,
            "meets_trade_count": False,
            "meets_excursion": False,
            "meets_buying_pressure": False,
            "meets_min_volume": False,
            "meets_min_buy_volume": False,
            "would_trade_strict": False,
            "would_trade_relaxed": False
        }
    
    # Get normalized volume (scaled to 1 minute)
    norm_vol = stats.normalized_minute_volume if hasattr(stats, 'normalized_minute_volume') and stats.normalized_minute_volume is not None else (stats.volume * (60.0 / window_seconds) if stats.volume else 0)
    window_vol = stats.volume if stats.volume else 0
    trade_count = stats.trade_count if stats.trade_count else 0
    buy_vol = stats.buy_volume if stats.buy_volume else 0
    sell_vol = stats.sell_volume if stats.sell_volume else 0
    
    # Calculate multipliers (using normalized volume)
    if prior_avg_vol > 0 and norm_vol is not None and norm_vol > 0:
        surge_multiplier = norm_vol / prior_avg_vol
    else:
        surge_multiplier = float('inf') if (norm_vol and norm_vol > 0) else 0.0
    
    # Normalize trade count to 1 minute
    norm_factor = 60.0 / window_seconds
    norm_trades = trade_count * norm_factor
    if prior_avg_trade_count > 0:
        trade_count_multiplier = norm_trades / prior_avg_trade_count
    else:
        trade_count_multiplier = float('inf') if norm_trades > 0 else 0.0
    
    # Max excursion (using max_price from stats vs reference_price)
    max_price = stats.max_price if hasattr(stats, 'max_price') and stats.max_price else reference_price
    if reference_price > 0:
        max_excursion_pct = ((max_price - reference_price) / reference_price) * 100
    else:
        max_excursion_pct = 0.0
    
    # Buying pressure (from imbalance_ratio)
    imbalance = stats.imbalance_ratio if hasattr(stats, 'imbalance_ratio') and stats.imbalance_ratio is not None else 0.0
    buying_pressure_pct = ((imbalance + 1) / 2) * 100  # Convert -1 to +1 range to 0-100%
    
    # Check criteria
    VOLUME_SURGE_THRESHOLD = 3.0
    TRADE_COUNT_THRESHOLD = 2.0
    MAX_EXCURSION_THRESHOLD = 1.0
    BUYING_PRESSURE_THRESHOLD = 70.0
    MIN_WINDOW_VOLUME_THRESHOLD = 5000
    MIN_BUY_VOLUME_THRESHOLD = 1000
    
    meets_volume_surge = surge_multiplier >= VOLUME_SURGE_THRESHOLD
    meets_trade_count = trade_count_multiplier >= TRADE_COUNT_THRESHOLD
    meets_excursion = max_excursion_pct >= MAX_EXCURSION_THRESHOLD
    meets_buying_pressure = buying_pressure_pct >= BUYING_PRESSURE_THRESHOLD
    meets_min_volume = window_vol >= MIN_WINDOW_VOLUME_THRESHOLD
    meets_min_buy_volume = buy_vol >= MIN_BUY_VOLUME_THRESHOLD
    
    # Would trade with STRICT requirements (all sectors)
    would_trade_strict = (
        meets_volume_surge and
        meets_trade_count and
        meets_excursion and
        meets_buying_pressure and
        meets_min_volume and
        meets_min_buy_volume
    )
    
    # Would trade with RELAXED requirements (preferred sectors: Healthcare, Technology, Financial Services)
    # No minimum window volume (5000) or minimum buy_volume (1000) required
    would_trade_relaxed = (
        meets_volume_surge and
        meets_trade_count and
        meets_excursion and
        meets_buying_pressure
        # No min_volume or min_buy_volume check for preferred sectors
    )
    
    return {
        "window_volume": window_vol,
        "trade_count": trade_count,
        "surge_multiplier": surge_multiplier,
        "trade_count_multiplier": trade_count_multiplier,
        "max_excursion_pct": max_excursion_pct,
        "buying_pressure_pct": buying_pressure_pct,
        "buy_volume": buy_vol,
        "meets_volume_surge": meets_volume_surge,
        "meets_trade_count": meets_trade_count,
        "meets_excursion": meets_excursion,
        "meets_buying_pressure": meets_buying_pressure,
        "meets_min_volume": meets_min_volume,
        "meets_min_buy_volume": meets_min_buy_volume,
        "would_trade_strict": would_trade_strict,
        "would_trade_relaxed": would_trade_relaxed
    }


async def get_prior_averages(
    client: StockHistoricalDataClient,
    symbol: str,
    event_time: datetime
) -> tuple[float, float]:
    """Get prior average volume and trade count using the same method as volume_analyzer."""
    prior_history = _fetch_prior_history_stats(client, symbol, event_time, lookback_minutes=10)
    if not prior_history:
        return (0.0, 0.0)
    
    # prior_history returns avg_volume (normalized to 1 minute) and avg_trade_count
    prior_avg_vol = prior_history.get("avg_volume", 0) if prior_history else 0
    prior_avg_trades = prior_history.get("avg_trade_count", 0) if prior_history else 0
    
    return (prior_avg_vol, prior_avg_trades)


async def analyze_ticker_monitoring(
    client: StockHistoricalDataClient,
    symbol: str,
    published_at: datetime,
    sector: str,
    reference_price: float
) -> None:
    """Analyze 2-minute monitoring period (30 cycles of 4-second windows)."""
    print(f"\n{'='*80}")
    print(f"Analyzing {symbol} ({sector})")
    print(f"Published at: {published_at}")
    print(f"Reference price: ${reference_price:.4f}")
    print(f"{'='*80}\n")
    
    # Get prior averages
    print("Fetching prior averages...")
    prior_avg_vol, prior_avg_trade_count = await get_prior_averages(client, symbol, published_at)
    print(f"Prior avg volume (4s): {prior_avg_vol:.1f}")
    print(f"Prior avg trade count (4s): {prior_avg_trade_count:.1f}\n")
    
    # Analyze 30 cycles (2 minutes = 120 seconds, 30 cycles of 4 seconds each)
    best_window = None
    best_score = 0
    windows_that_would_trade = []
    
    print("Analyzing 30 monitoring cycles (4-second windows)...\n")
    print(f"{'Cycle':<6} {'Window Start':<20} {'Vol':<8} {'VolX':<8} {'TCX':<8} {'Exc%':<8} {'BP%':<8} {'BuyVol':<8} {'Strict':<8} {'Relaxed':<8}")
    print("-" * 100)
    
    for cycle in range(30):
        # Cycle 0: published_at + 4s to published_at + 8s
        # Cycle 1: published_at + 8s to published_at + 12s
        # ...
        window_start = published_at + timedelta(seconds=4 + (cycle * 4))
        window_end = window_start + timedelta(seconds=4)
        
        # Fetch stats for this window (using same method as volume_analyzer)
        # Note: _get_stats_at_time is synchronous, not async
        stats = _get_stats_at_time(
            client=client,
            symbol=symbol,
            target_time=window_start,
            use_realtime_window=True,
            window_end=window_end,
            reference_nbbo=None
        )
        
        # Analyze window
        analysis = analyze_window(stats, prior_avg_vol, prior_avg_trade_count, reference_price, window_seconds=4.0)
        
        # Score: count how many criteria are met
        criteria_met = sum([
            analysis["meets_volume_surge"],
            analysis["meets_trade_count"],
            analysis["meets_excursion"],
            analysis["meets_buying_pressure"],
        ])
        
        if criteria_met > best_score:
            best_score = criteria_met
            best_window = {
                "cycle": cycle,
                "window_start": window_start,
                "analysis": analysis
            }
        
        if analysis["would_trade_relaxed"]:
            windows_that_would_trade.append({
                "cycle": cycle,
                "window_start": window_start,
                "analysis": analysis
            })
        
        # Print summary
        vol_str = f"{analysis['window_volume']:.0f}"
        volx_str = f"{analysis['surge_multiplier']:.1f}x" if analysis['surge_multiplier'] < 1000 else "∞"
        tcx_str = f"{analysis['trade_count_multiplier']:.1f}x" if analysis['trade_count_multiplier'] < 1000 else "∞"
        exc_str = f"{analysis['max_excursion_pct']:.2f}%"
        bp_str = f"{analysis['buying_pressure_pct']:.1f}%"
        buyvol_str = f"{analysis['buy_volume']:.0f}"
        strict_str = "✓" if analysis['would_trade_strict'] else "✗"
        relaxed_str = "✓" if analysis['would_trade_relaxed'] else "✗"
        
        print(f"{cycle:<6} {window_start.strftime('%H:%M:%S'):<20} {vol_str:<8} {volx_str:<8} {tcx_str:<8} {exc_str:<8} {bp_str:<8} {buyvol_str:<8} {strict_str:<8} {relaxed_str:<8}")
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    if best_window:
        print(f"\nBest window (most criteria met): Cycle {best_window['cycle']}")
        print(f"  Window: {best_window['window_start'].strftime('%H:%M:%S')} - {(best_window['window_start'] + timedelta(seconds=4)).strftime('%H:%M:%S')}")
        a = best_window['analysis']
        print(f"  Volume: {a['window_volume']:.0f} ({a['surge_multiplier']:.1f}x surge)")
        print(f"  Trade Count: {a['trade_count']:.0f} ({a['trade_count_multiplier']:.1f}x)")
        print(f"  Max Excursion: {a['max_excursion_pct']:.2f}%")
        print(f"  Buying Pressure: {a['buying_pressure_pct']:.1f}%")
        print(f"  Buy Volume: {a['buy_volume']:.0f}")
        print(f"\n  Criteria met:")
        print(f"    ✓ Volume surge (3x): {a['meets_volume_surge']}")
        print(f"    ✓ Trade count (2x): {a['meets_trade_count']}")
        print(f"    ✓ Excursion (1%): {a['meets_excursion']}")
        print(f"    ✓ Buying pressure (70%): {a['meets_buying_pressure']}")
        print(f"    ✓ Min volume (5000): {a['meets_min_volume']}")
        print(f"    ✓ Min buy volume (1000): {a['meets_min_buy_volume']}")
        print(f"\n  Would trade (STRICT): {a['would_trade_strict']}")
        print(f"  Would trade (RELAXED - {sector}): {a['would_trade_relaxed']}")
    
    if windows_that_would_trade:
        print(f"\n✓ WOULD HAVE TRADED with relaxed requirements ({sector} sector):")
        for w in windows_that_would_trade:
            print(f"  Cycle {w['cycle']}: {w['window_start'].strftime('%H:%M:%S')} - {(w['window_start'] + timedelta(seconds=4)).strftime('%H:%M:%S')}")
    else:
        print(f"\n✗ Would NOT have traded even with relaxed requirements")
        print(f"  (Did not meet all four pillars: 3x volume, 2x trade count, 1% excursion, 70% buying pressure)")


async def main():
    """Main analysis."""
    # Initialize Alpaca client
    api_key = os.getenv("ALPACA_KEY")
    api_secret = os.getenv("ALPACA_SECRET")
    
    if not api_key or not api_secret:
        raise ValueError("ALPACA_KEY and ALPACA_SECRET must be set in environment")
    
    client = StockHistoricalDataClient(
        api_key=api_key,
        secret_key=api_secret
    )
    
    # NEOG
    neog_published = datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)
    neog_reference_price = 7.845
    
    await analyze_ticker_monitoring(
        client, "NEOG", neog_published, "Healthcare", neog_reference_price
    )
    
    # CNCK
    cnck_published = datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)
    cnck_reference_price = 2.815
    
    await analyze_ticker_monitoring(
        client, "CNCK", cnck_published, "Financial Services", cnck_reference_price
    )


if __name__ == "__main__":
    asyncio.run(main())
