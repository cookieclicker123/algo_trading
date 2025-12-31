#!/usr/bin/env python3
"""
Probe Reaction Timing Script

Analyzes when the market started reacting to news by checking minute-by-minute
price excursions from publication time.

For each minute window (1 min, 2 min, 3 min, etc.), calculates:
- Max price reached in that window
- Price excursion % from publication price
- When the reaction began

This helps confirm if we were "too early" to detect the surge.

Usage:
    python scripts/probe_reaction_timing.py

You can modify the TICKER and PUBLISHED_AT constants at the bottom.
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

# Add src to path
PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

load_dotenv()

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockQuotesRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed


def create_alpaca_client() -> StockHistoricalDataClient:
    """Create Alpaca market data client."""
    api_key = os.getenv("ALPACA_KEY")
    api_secret = os.getenv("ALPACA_SECRET")
    
    if not api_key or not api_secret:
        raise ValueError("ALPACA_KEY and ALPACA_SECRET must be set in environment")
    
    return StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)


def fetch_minute_bars(
    client: StockHistoricalDataClient,
    symbol: str,
    start_time: datetime,
    end_time: datetime
) -> Dict[datetime, Dict[str, Any]]:
    """Fetch minute bars and return as dict keyed by minute timestamp."""
    request = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Minute,
        start=start_time,
        end=end_time,
        feed=DataFeed.SIP
    )
    
    bars = client.get_stock_bars(request)
    
    if symbol not in bars.data:
        return {}
    
    result = {}
    for bar in bars[symbol]:
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        result[ts] = {
            "volume": int(bar.volume) if bar.volume else 0,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "vwap": float(bar.vwap) if bar.vwap else None,
            "trade_count": int(bar.trade_count) if hasattr(bar, 'trade_count') and bar.trade_count else None
        }
    
    return result


def fetch_quote_at_time(
    client: StockHistoricalDataClient,
    symbol: str,
    target_time: datetime,
    window_seconds: int = 10
) -> Optional[Dict[str, Any]]:
    """Fetch quote closest to target time."""
    start = target_time - timedelta(seconds=window_seconds)
    end = target_time + timedelta(seconds=window_seconds)
    
    request = StockQuotesRequest(
        symbol_or_symbols=[symbol],
        start=start,
        end=end,
        feed=DataFeed.SIP
    )
    
    quotes = client.get_stock_quotes(request)
    
    if symbol not in quotes.data or not quotes[symbol]:
        return None
    
    # Find closest quote to target time
    closest = None
    min_diff = None
    
    for quote in quotes[symbol]:
        ts = quote.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        
        diff = abs((ts - target_time).total_seconds())
        if min_diff is None or diff < min_diff:
            min_diff = diff
            closest = quote
    
    if not closest:
        return None
    
    return {
        "timestamp": closest.timestamp,
        "bid": float(closest.bid_price),
        "ask": float(closest.ask_price),
        "mid": (float(closest.bid_price) + float(closest.ask_price)) / 2,
        "bid_size": int(closest.bid_size),
        "ask_size": int(closest.ask_size),
    }


def analyze_minute_by_minute_excursion(
    client: StockHistoricalDataClient,
    symbol: str,
    published_at: datetime,
    max_minutes: int = 10,
    publication_price: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    Analyze price excursions minute-by-minute from publication time.
    
    Returns a list of results for each minute window (1 min, 2 min, 3 min, etc.)
    """
    # Ensure UTC
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    
    # Get publication price (mid from NBBO)
    if publication_price is None:
        quote = fetch_quote_at_time(client, symbol, published_at)
        if quote:
            publication_price = quote["mid"]
            print(f"📊 Publication price (mid): ${publication_price:.4f}")
        else:
            print("⚠️  Warning: Could not fetch publication quote, will use first bar open")
    
    # Fetch bars for the analysis window
    end_time = published_at + timedelta(minutes=max_minutes)
    bars = fetch_minute_bars(client, symbol, published_at, end_time)
    
    if not bars:
        print(f"❌ No minute bars found for {symbol} after publication time")
        return []
    
    # If we don't have publication price, use first bar's open
    if publication_price is None:
        first_bar_time = min(bars.keys())
        publication_price = bars[first_bar_time]["open"]
        print(f"📊 Using first bar open as publication price: ${publication_price:.4f}")
    
    # Sort bars by timestamp
    sorted_bars = sorted(bars.items())
    
    results = []
    
    # Analyze each minute window
    for window_minutes in range(1, max_minutes + 1):
        window_end = published_at + timedelta(minutes=window_minutes)
        
        # Get all bars within this window
        window_bars = [
            (ts, bar_data) 
            for ts, bar_data in sorted_bars 
            if ts <= window_end
        ]
        
        if not window_bars:
            continue
        
        # Find max high price in window
        max_high = max(bar_data["high"] for _, bar_data in window_bars)
        min_low = min(bar_data["low"] for _, bar_data in window_bars)
        
        # Get last bar in window
        last_bar_time, last_bar_data = window_bars[-1]
        last_close = last_bar_data["close"]
        
        # Calculate excursions
        max_excursion_pct = ((max_high - publication_price) / publication_price) * 100 if publication_price > 0 else 0
        min_excursion_pct = ((min_low - publication_price) / publication_price) * 100 if publication_price > 0 else 0
        close_excursion_pct = ((last_close - publication_price) / publication_price) * 100 if publication_price > 0 else 0
        
        # Calculate total volume in window
        total_volume = sum(bar_data["volume"] for _, bar_data in window_bars)
        total_trades = sum(bar_data["trade_count"] for _, bar_data in window_bars if bar_data["trade_count"])
        
        results.append({
            "window_minutes": window_minutes,
            "window_end": window_end,
            "publication_price": publication_price,
            "max_high": max_high,
            "min_low": min_low,
            "last_close": last_close,
            "max_excursion_pct": max_excursion_pct,
            "min_excursion_pct": min_excursion_pct,
            "close_excursion_pct": close_excursion_pct,
            "total_volume": total_volume,
            "total_trades": total_trades,
            "bar_count": len(window_bars),
        })
    
    return results


def print_results(results: List[Dict[str, Any]], symbol: str, published_at: datetime):
    """Print analysis results in a formatted table."""
    print("\n" + "=" * 100)
    print(f"MINUTE-BY-MINUTE PRICE EXCURSION ANALYSIS: {symbol}")
    print("=" * 100)
    print(f"Publication Time: {published_at} (UTC)")
    print(f"Publication Price: ${results[0]['publication_price']:.4f}" if results else "N/A")
    print("\n")
    
    # Header
    print(f"{'Window':<8} {'Max High':<12} {'Max Exc %':<12} {'Close %':<12} {'Volume':<12} {'Trades':<10} {'Status':<20}")
    print("-" * 100)
    
    # Find when reaction started (first minute with >1% excursion)
    reaction_started_minute = None
    
    for result in results:
        window = result["window_minutes"]
        max_exc = result["max_excursion_pct"]
        close_exc = result["close_excursion_pct"]
        volume = result["total_volume"]
        trades = result["total_trades"] if result["total_trades"] else 0
        
        # Determine status
        if reaction_started_minute is None:
            if max_exc > 1.0:  # First time we see >1% move
                reaction_started_minute = window
                status = f"🚀 REACTION STARTED"
            elif max_exc > 0.5:
                status = "📈 Building"
            else:
                status = "⏳ Waiting"
        else:
            if window == reaction_started_minute:
                status = "🚀 REACTION STARTED"
            else:
                status = f"📈 Continuing ({max_exc:.1f}%)"
        
        print(f"{window} min   ${result['max_high']:<11.4f} {max_exc:>+11.2f}%  {close_exc:>+11.2f}%  {volume:>11,}  {trades:>9,}  {status}")
    
    print("-" * 100)
    
    if reaction_started_minute:
        print(f"\n✅ REACTION STARTED: Minute {reaction_started_minute} ({reaction_started_minute * 60} seconds after publication)")
    else:
        print(f"\n⚠️  NO SIGNIFICANT REACTION detected in the {len(results)} minute window")
        if results:
            max_result = max(results, key=lambda x: x["max_excursion_pct"])
            print(f"   Peak excursion: {max_result['max_excursion_pct']:.2f}% at minute {max_result['window_minutes']}")
    
    # Summary statistics
    if results:
        final_result = results[-1]
        print(f"\n📊 FINAL STATS (after {len(results)} minutes):")
        print(f"   Max High: ${final_result['max_high']:.4f} ({final_result['max_excursion_pct']:+.2f}%)")
        print(f"   Final Close: ${final_result['last_close']:.4f} ({final_result['close_excursion_pct']:+.2f}%)")
        print(f"   Total Volume: {final_result['total_volume']:,} shares")
        if final_result['total_trades']:
            print(f"   Total Trades: {final_result['total_trades']:,}")


def main():
    """Main function."""
    print("\n" + "=" * 100)
    print("PROBE REACTION TIMING ANALYSIS")
    print("=" * 100)
    
    # ========================================================================
    # CONFIGURATION - Modify these values for different articles
    # ========================================================================
    TICKER = "WTO"
    # Published at: "2025-12-31T20:45:00Z" (from recall JSON)
    PUBLISHED_AT = datetime(2025, 12, 31, 20, 45, 0, tzinfo=timezone.utc)
    MAX_MINUTES = 10  # How many minutes to analyze
    # ========================================================================
    
    try:
        client = create_alpaca_client()
        
        print(f"\n🔍 Analyzing: {TICKER}")
        print(f"   Publication Time: {PUBLISHED_AT} (UTC)")
        print(f"   Analysis Window: {MAX_MINUTES} minutes")
        print()
        
        results = analyze_minute_by_minute_excursion(
            client=client,
            symbol=TICKER,
            published_at=PUBLISHED_AT,
            max_minutes=MAX_MINUTES
        )
        
        if not results:
            print("❌ No results - check ticker symbol and timestamp")
            return
        
        print_results(results, TICKER, PUBLISHED_AT)
        
        print("\n" + "=" * 100)
        print("ANALYSIS COMPLETE")
        print("=" * 100)
        print("\n💡 Next step: If reaction started after minute 1, we were 'too early'")
        print("   This supports monitoring IMMINENT articles for 2 minutes even without initial SURGE")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
