#!/usr/bin/env python3
"""
Probe Reaction Timing - Second-by-Second Analysis

Drills down into a specific minute to find the exact second when the market
reaction started (when it went from inactive to active).

Usage:
    python scripts/probe_reaction_seconds.py

Modify TICKER, PUBLISHED_AT, and TARGET_MINUTE constants at the bottom.
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from collections import defaultdict
from dotenv import load_dotenv

# Add src to path
PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

load_dotenv()

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest, StockQuotesRequest
from alpaca.data.enums import DataFeed


def create_alpaca_client() -> StockHistoricalDataClient:
    """Create Alpaca market data client."""
    api_key = os.getenv("ALPACA_KEY")
    api_secret = os.getenv("ALPACA_SECRET")
    
    if not api_key or not api_secret:
        raise ValueError("ALPACA_KEY and ALPACA_SECRET must be set in environment")
    
    return StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)


def fetch_trades_in_window(
    client: StockHistoricalDataClient,
    symbol: str,
    start_time: datetime,
    end_time: datetime
) -> List[Dict[str, Any]]:
    """Fetch all trades in a time window."""
    request = StockTradesRequest(
        symbol_or_symbols=[symbol],
        start=start_time,
        end=end_time,
        feed=DataFeed.SIP
    )
    
    trades_response = client.get_stock_trades(request)
    
    if symbol not in trades_response.data:
        return []
    
    trades = []
    for trade in trades_response[symbol]:
        ts = trade.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        
        trades.append({
            "timestamp": ts,
            "price": float(trade.price),
            "size": int(trade.size),
        })
    
    # Sort by timestamp
    trades.sort(key=lambda x: x["timestamp"])
    return trades


def fetch_quotes_in_window(
    client: StockHistoricalDataClient,
    symbol: str,
    start_time: datetime,
    end_time: datetime
) -> List[Dict[str, Any]]:
    """Fetch quotes in a time window."""
    request = StockQuotesRequest(
        symbol_or_symbols=[symbol],
        start=start_time,
        end=end_time,
        feed=DataFeed.SIP
    )
    
    quotes_response = client.get_stock_quotes(request)
    
    if symbol not in quotes_response.data:
        return []
    
    quotes = []
    for quote in quotes_response[symbol]:
        ts = quote.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        
        quotes.append({
            "timestamp": ts,
            "bid": float(quote.bid_price),
            "ask": float(quote.ask_price),
            "mid": (float(quote.bid_price) + float(quote.ask_price)) / 2,
        })
    
    # Sort by timestamp
    quotes.sort(key=lambda x: x["timestamp"])
    return quotes


def analyze_second_by_second(
    client: StockHistoricalDataClient,
    symbol: str,
    minute_start: datetime,
    publication_price: float
) -> List[Dict[str, Any]]:
    """
    Analyze second-by-second activity in a specific minute.
    
    Groups trades and quotes by second to show when activity started.
    """
    minute_end = minute_start + timedelta(minutes=1)
    
    print(f"📊 Fetching trades and quotes for minute starting at {minute_start}")
    trades = fetch_trades_in_window(client, symbol, minute_start, minute_end)
    quotes = fetch_quotes_in_window(client, symbol, minute_start, minute_end)
    
    print(f"   Found {len(trades)} trades and {len(quotes)} quotes")
    
    # Group by second
    trades_by_second = defaultdict(list)
    quotes_by_second = defaultdict(list)
    
    for trade in trades:
        second = trade["timestamp"].replace(microsecond=0)
        trades_by_second[second].append(trade)
    
    for quote in quotes:
        second = quote["timestamp"].replace(microsecond=0)
        quotes_by_second[second].append(quote)
    
    # Analyze each second
    results = []
    all_seconds = sorted(set(list(trades_by_second.keys()) + list(quotes_by_second.keys())))
    
    if not all_seconds:
        print("⚠️  No trades or quotes found in this minute")
        return []
    
    # Fill in missing seconds
    current_second = minute_start.replace(second=0, microsecond=0)
    while current_second < minute_end:
        if current_second not in all_seconds:
            all_seconds.append(current_second)
        current_second += timedelta(seconds=1)
    all_seconds.sort()
    
    # Calculate cumulative stats
    cumulative_volume = 0
    cumulative_trades = 0
    max_price_so_far = publication_price
    first_trade_time = None
    
    for second in all_seconds:
        second_trades = trades_by_second.get(second, [])
        second_quotes = quotes_by_second.get(second, [])
        
        # Calculate stats for this second
        second_volume = sum(t["size"] for t in second_trades)
        second_trade_count = len(second_trades)
        
        # Price stats
        if second_trades:
            second_prices = [t["price"] for t in second_trades]
            second_high = max(second_prices)
            second_low = min(second_prices)
            second_avg = sum(second_prices) / len(second_prices)
            
            # Track first trade
            if first_trade_time is None:
                first_trade_time = second_trades[0]["timestamp"]
        else:
            second_high = None
            second_low = None
            second_avg = None
        
        # Quote stats
        if second_quotes:
            quote_mids = [q["mid"] for q in second_quotes]
            quote_mid = quote_mids[-1]  # Use last quote of the second
        else:
            quote_mid = None
        
        # Update cumulative
        cumulative_volume += second_volume
        cumulative_trades += second_trade_count
        
        # Track max price
        if second_high:
            max_price_so_far = max(max_price_so_far, second_high)
        if quote_mid:
            max_price_so_far = max(max_price_so_far, quote_mid)
        
        # Calculate excursion from publication price
        if second_high:
            excursion_pct = ((second_high - publication_price) / publication_price) * 100
        elif quote_mid:
            excursion_pct = ((quote_mid - publication_price) / publication_price) * 100
        else:
            excursion_pct = None
        
        # Determine status
        if second_trade_count == 0:
            status = "⏳ No trades"
        elif cumulative_trades == second_trade_count:
            status = "🚀 FIRST TRADES"
        elif second_trade_count > 10:
            status = f"🔥 HEAVY ({second_trade_count} trades)"
        elif second_trade_count > 5:
            status = f"📈 Active ({second_trade_count} trades)"
        else:
            status = f"📊 Trading ({second_trade_count} trades)"
        
        results.append({
            "second": second,
            "second_of_minute": second.second,
            "trades": second_trade_count,
            "volume": second_volume,
            "high": second_high,
            "low": second_low,
            "avg_price": second_avg,
            "quote_mid": quote_mid,
            "excursion_pct": excursion_pct,
            "cumulative_volume": cumulative_volume,
            "cumulative_trades": cumulative_trades,
            "max_price_so_far": max_price_so_far,
            "status": status,
        })
    
    return results


def print_results(results: List[Dict[str, Any]], symbol: str, minute_start: datetime, publication_price: float):
    """Print second-by-second results."""
    print("\n" + "=" * 120)
    print(f"SECOND-BY-SECOND ANALYSIS: {symbol} - Minute starting at {minute_start}")
    print("=" * 120)
    print(f"Publication Price: ${publication_price:.4f}")
    print("\n")
    
    # Header
    print(f"{'Second':<8} {'Trades':<8} {'Volume':<12} {'High':<10} {'Exc %':<10} {'Cum Vol':<12} {'Cum Trades':<12} {'Status':<30}")
    print("-" * 120)
    
    reaction_started_second = None
    
    for result in results:
        second = result["second"]
        second_num = result["second_of_minute"]
        trades = result["trades"]
        volume = result["volume"]
        high = result["high"]
        exc_pct = result["excursion_pct"]
        cum_vol = result["cumulative_volume"]
        cum_trades = result["cumulative_trades"]
        status = result["status"]
        
        # Track when reaction started (first significant activity)
        if reaction_started_second is None:
            if trades > 0 or (high and exc_pct and exc_pct > 1.0):
                reaction_started_second = second_num
        
        # Format output
        high_str = f"${high:.4f}" if high else "N/A"
        exc_str = f"{exc_pct:+.2f}%" if exc_pct else "N/A"
        
        print(f"{second_num:02d}s      {trades:<8} {volume:<12,} {high_str:<10} {exc_str:<10} {cum_vol:<12,} {cum_trades:<12,} {status}")
    
    print("-" * 120)
    
    if reaction_started_second is not None:
        print(f"\n✅ REACTION STARTED: Second {reaction_started_second:02d} ({reaction_started_second} seconds into the minute)")
        reaction_timestamp = minute_start.replace(second=reaction_started_second)
        print(f"   Timestamp: {reaction_timestamp}")
    else:
        print(f"\n⚠️  No significant reaction detected in this minute")
    
    # Summary
    if results:
        total_volume = results[-1]["cumulative_volume"]
        total_trades = results[-1]["cumulative_trades"]
        max_price = max(r["max_price_so_far"] for r in results if r["max_price_so_far"])
        max_exc = ((max_price - publication_price) / publication_price) * 100
        
        print(f"\n📊 MINUTE SUMMARY:")
        print(f"   Total Volume: {total_volume:,} shares")
        print(f"   Total Trades: {total_trades:,}")
        print(f"   Max Price: ${max_price:.4f} ({max_exc:+.2f}%)")


def main():
    """Main function."""
    print("\n" + "=" * 120)
    print("PROBE REACTION TIMING - SECOND-BY-SECOND ANALYSIS")
    print("=" * 120)
    
    # ========================================================================
    # CONFIGURATION
    # ========================================================================
    TICKER = "WTO"
    # Published at: "2025-12-31T20:45:00Z"
    PUBLISHED_AT = datetime(2025, 12, 31, 20, 45, 0, tzinfo=timezone.utc)
    PUBLICATION_PRICE = 0.5942  # From previous analysis
    TARGET_MINUTE = 1  # Analyze minute 1 (first minute after publication)
    # ========================================================================
    
    try:
        client = create_alpaca_client()
        
        # Calculate minute start (the target minute after publication)
        minute_start = PUBLISHED_AT.replace(second=0, microsecond=0) + timedelta(minutes=TARGET_MINUTE - 1)
        
        print(f"\n🔍 Analyzing: {TICKER}")
        print(f"   Publication Time: {PUBLISHED_AT}")
        print(f"   Publication Price: ${PUBLICATION_PRICE:.4f}")
        print(f"   Target Minute: Minute {TARGET_MINUTE} (starting at {minute_start})")
        print()
        
        results = analyze_second_by_second(
            client=client,
            symbol=TICKER,
            minute_start=minute_start,
            publication_price=PUBLICATION_PRICE
        )
        
        if not results:
            print("❌ No results - check configuration")
            return
        
        print_results(results, TICKER, minute_start, PUBLICATION_PRICE)
        
        print("\n" + "=" * 120)
        print("ANALYSIS COMPLETE")
        print("=" * 120)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
