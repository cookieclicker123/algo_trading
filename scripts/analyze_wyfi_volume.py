#!/usr/bin/env python3
"""
WYFI Volume & Quote Analysis Script

Analyzes the volume bars and NBBO quotes around the WYFI article publication.
Uses Alpaca SIP feed for accurate data.

Key timestamps from logs:
- Article received via websocket: 2025-12-18T21:30:19.988378Z (UTC)
- This is 16:30:19 ET (postmarket)
- Trade filled ~26 seconds later at 16:30:50 ET at $16.44

Run with:
    python scripts/analyze_wyfi_volume.py
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
import pytz

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
    """Fetch minute bars and return as dict keyed by minute."""
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
            ts = pytz.UTC.localize(ts)
        result[ts] = {
            "volume": bar.volume,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "vwap": float(bar.vwap) if bar.vwap else None,
            "trade_count": bar.trade_count if hasattr(bar, 'trade_count') else None
        }
    
    return result


def fetch_quotes_around_time(
    client: StockHistoricalDataClient,
    symbol: str,
    target_time: datetime,
    seconds_before: int = 5
) -> Optional[Dict[str, Any]]:
    """Fetch quotes around a specific time and return the closest one."""
    start = target_time - timedelta(seconds=seconds_before)
    end = target_time + timedelta(seconds=5)
    
    request = StockQuotesRequest(
        symbol_or_symbols=[symbol],
        start=start,
        end=end,
        feed=DataFeed.SIP
    )
    
    try:
        quotes = client.get_stock_quotes(request)
        
        if symbol not in quotes.data:
            return None
        
        symbol_quotes = list(quotes[symbol])
        if not symbol_quotes:
            return None
        
        # Find the quote closest to (but not after) target_time
        closest_quote = None
        for quote in symbol_quotes:
            qt = quote.timestamp
            if qt.tzinfo is None:
                qt = pytz.UTC.localize(qt)
            if qt <= target_time:
                closest_quote = quote
        
        if not closest_quote:
            closest_quote = symbol_quotes[0]  # Fallback to first
        
        bid = float(closest_quote.bid_price) if closest_quote.bid_price else None
        ask = float(closest_quote.ask_price) if closest_quote.ask_price else None
        
        return {
            "timestamp": closest_quote.timestamp,
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2, 4) if bid and ask else None,
            "spread": round(ask - bid, 4) if bid and ask else None,
            "spread_pct": round((ask - bid) / bid * 100, 3) if bid and ask and bid > 0 else None,
            "bid_size": closest_quote.bid_size if hasattr(closest_quote, 'bid_size') else None,
            "ask_size": closest_quote.ask_size if hasattr(closest_quote, 'ask_size') else None
        }
    except Exception as e:
        print(f"      Quote fetch error: {e}")
        return None


def main():
    """Run the WYFI volume analysis."""
    print("\n" + "=" * 80)
    print("WYFI VOLUME & QUOTE ANALYSIS (SIP FEED)")
    print("=" * 80)
    
    et_tz = pytz.timezone("US/Eastern")
    utc_tz = pytz.UTC
    
    # EXACT timestamp from logs when article was received via websocket
    # "2025-12-18T21:30:19.988378Z" - this is UTC!
    article_received_utc = datetime(2025, 12, 18, 21, 30, 19, 988378, tzinfo=utc_tz)
    article_received_et = article_received_utc.astimezone(et_tz)
    
    # Trade execution timestamp
    trade_filled_utc = datetime(2025, 12, 18, 21, 30, 50, tzinfo=utc_tz)
    trade_filled_et = trade_filled_utc.astimezone(et_tz)
    
    symbol = "WYFI"
    
    print(f"\n� Article: 'WhiteFiber and Nscale Announce 10-Year, 40 MW Colocation Agreement...'")
    print(f"   Ticker: {symbol}")
    print(f"\n🕐 KEY TIMESTAMPS:")
    print(f"   Article received (websocket): {article_received_et.strftime('%Y-%m-%d %H:%M:%S.%f')} ET")
    print(f"   Trade filled:                 {trade_filled_et.strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"   Delay (news to fill):         {(trade_filled_utc - article_received_utc).total_seconds():.1f} seconds")
    print(f"   Entry price: $16.44")
    
    # Create client
    print("\n📡 Connecting to Alpaca SIP feed...")
    client = create_alpaca_client()
    print("   ✅ Connected")
    
    # Define the intervals we want to analyze
    # Using the article_received time as "NOW"
    intervals = {
        "3_min_before": article_received_utc - timedelta(minutes=3),
        "2_min_before": article_received_utc - timedelta(minutes=2),
        "1_min_before": article_received_utc - timedelta(minutes=1),
        "30_sec_before": article_received_utc - timedelta(seconds=30),
        "NOW (article)": article_received_utc
    }
    
    # Fetch minute bars for the window
    print("\n📊 Fetching minute bars...")
    bar_start = article_received_utc - timedelta(minutes=5)
    bar_end = article_received_utc + timedelta(minutes=2)
    bars_by_minute = fetch_minute_bars(client, symbol, bar_start, bar_end)
    print(f"   Retrieved {len(bars_by_minute)} minute bars")
    
    # Analyze each interval
    print("\n" + "=" * 80)
    print("VOLUME & QUOTE ANALYSIS AT KEY INTERVALS")
    print("=" * 80)
    
    previous_volume = None
    results = []
    
    for label, timestamp in intervals.items():
        timestamp_et = timestamp.astimezone(et_tz)
        
        print(f"\n📍 {label}")
        print(f"   Time: {timestamp_et.strftime('%H:%M:%S.%f')[:-3]} ET")
        
        # Get minute bar for this timestamp
        minute_key = timestamp.replace(second=0, microsecond=0)
        bar = bars_by_minute.get(minute_key)
        
        if bar:
            volume = bar["volume"]
            print(f"   Volume: {volume:,}")
            print(f"   Trade Count: {bar.get('trade_count', 'N/A')}")
            print(f"   OHLC: O=${bar['open']:.2f} H=${bar['high']:.2f} L=${bar['low']:.2f} C=${bar['close']:.2f}")
            
            if previous_volume and previous_volume > 0:
                vol_change = ((volume - previous_volume) / previous_volume) * 100
                print(f"   Volume Change: {vol_change:+.1f}%")
            
            previous_volume = volume
        else:
            print(f"   Volume: ⚠️ No bar for minute {minute_key}")
            volume = None
        
        # Get quote (bid/ask/spread) for this timestamp
        quote = fetch_quotes_around_time(client, symbol, timestamp)
        
        if quote:
            print(f"   Bid: ${quote['bid']:.4f}" if quote['bid'] else "   Bid: N/A")
            print(f"   Ask: ${quote['ask']:.4f}" if quote['ask'] else "   Ask: N/A")
            print(f"   Mid: ${quote['mid']:.4f}" if quote['mid'] else "   Mid: N/A")
            print(f"   Spread: ${quote['spread']:.4f} ({quote['spread_pct']:.3f}%)" if quote['spread'] else "   Spread: N/A")
        else:
            print("   Quote: ⚠️ No quote data")
            quote = {}
        
        results.append({
            "label": label,
            "timestamp_et": timestamp_et,
            "volume": volume,
            "bar": bar,
            "quote": quote
        })
    
    # Summary table
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    print(f"\n{'Time':^12} | {'Volume':^10} | {'Trades':^7} | {'Bid':^8} | {'Ask':^8} | {'Spread':^10} | {'Close':^8}")
    print("-" * 80)
    
    for r in results:
        time_str = r["timestamp_et"].strftime("%H:%M:%S")
        vol_str = f"{r['volume']:,}" if r['volume'] else "N/A"
        trades_str = str(r['bar']['trade_count']) if r['bar'] and r['bar'].get('trade_count') else "N/A"
        close_str = f"${r['bar']['close']:.2f}" if r['bar'] else "N/A"
        
        bid_str = f"${r['quote']['bid']:.2f}" if r['quote'] and r['quote'].get('bid') else "N/A"
        ask_str = f"${r['quote']['ask']:.2f}" if r['quote'] and r['quote'].get('ask') else "N/A"
        spread_str = f"${r['quote']['spread']:.3f}" if r['quote'] and r['quote'].get('spread') else "N/A"
        
        print(f"{time_str:^12} | {vol_str:^10} | {trades_str:^7} | {bid_str:^8} | {ask_str:^8} | {spread_str:^10} | {close_str:^8}")
    
    # Volume surge analysis
    print("\n" + "=" * 80)
    print("VOLUME SURGE ANALYSIS")
    print("=" * 80)
    
    # Get volume at 3 min before and at article time
    vol_3min = results[0]["volume"] if results[0]["volume"] else 0
    vol_now = results[-1]["volume"] if results[-1]["volume"] else 0
    
    if vol_3min and vol_now:
        surge_pct = ((vol_now - vol_3min) / vol_3min) * 100 if vol_3min > 0 else 0
        print(f"\n   Volume 3 min before: {vol_3min:,}")
        print(f"   Volume at article:   {vol_now:,}")
        print(f"   Volume surge:        {surge_pct:+.1f}%")
        
        if surge_pct > 100:
            print("\n   ✅ STRONG VOLUME SURGE DETECTED - News is moving the stock!")
        elif surge_pct > 50:
            print("\n   📈 MODERATE VOLUME SURGE - Increased activity")
        elif surge_pct > 0:
            print("\n   📊 SLIGHT VOLUME INCREASE")
        else:
            print("\n   ⚠️ NO VOLUME SURGE - May not be news-driven")
    
    # Price action
    print("\n" + "=" * 80)
    print("PRICE ACTION")
    print("=" * 80)
    
    first_bar = results[0]["bar"] if results[0]["bar"] else None
    last_bar = results[-1]["bar"] if results[-1]["bar"] else None
    
    if first_bar and last_bar:
        price_3min = first_bar["close"]
        price_now = last_bar["close"]
        price_change = ((price_now - price_3min) / price_3min) * 100
        
        print(f"\n   Price 3 min before: ${price_3min:.2f}")
        print(f"   Price at article:   ${price_now:.2f}")
        print(f"   Price change:       {price_change:+.2f}%")
        
        if price_change > 5:
            print("\n   🚀 MAJOR PRICE MOVE - High conviction signal!")
    
    # Final analysis
    print("\n" + "=" * 80)
    print("CONCLUSION")
    print("=" * 80)
    print(f"""
📊 WYFI TRADE ANALYSIS:

Timeline:
- Article published at 16:30:19 ET
- Trade filled at 16:30:50 ET (31 seconds later)
- Entry price: $16.44

Key Observations:
1. The volume data shows what was happening in the 3 minutes before the news
2. Quote data shows bid/ask spread evolution (important for liquidity)
3. Volume surge + narrowing spread = high conviction signal
4. Volume flat + wide spread = low liquidity, higher risk

This data can be integrated into:
- Signal/Recall notifications (show volume context)
- Failed trade analysis (was there volume?)
- Trade entry notifications (show the surge pattern)
""")


if __name__ == "__main__":
    main()
