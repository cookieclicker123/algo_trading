"""Get RITR's full price action after the headline."""
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest
from alpaca.data.enums import DataFeed

project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

api_key = os.getenv("ALPACA_PAPER_API_KEY") or os.getenv("ALPACA_KEY")
secret_key = os.getenv("ALPACA_PAPER_SECRET_KEY") or os.getenv("ALPACA_SECRET")
client = StockHistoricalDataClient(api_key, secret_key)

# RITR article published at 2026-02-09T11:05:37+00:00
pub_time = datetime(2026, 2, 9, 11, 5, 37, tzinfo=timezone.utc)
entry_price = 0.9382  # Initial ask

# Fetch trades for 10 minutes after publication
start = pub_time
end = pub_time + timedelta(minutes=10)

trades_request = StockTradesRequest(
    symbol_or_symbols=["RITR"],
    start=start,
    end=end,
    feed=DataFeed.SIP
)

response = client.get_stock_trades(trades_request)
trades = response.data.get("RITR", [])

print(f"RITR Full Analysis")
print(f"Article published: {pub_time.strftime('%H:%M:%S')} UTC")
print(f"Entry price (ask): ${entry_price}")
print(f"="*70)

# Find highest price and when it occurred
highest_price = 0
highest_time = None
first_10pct_time = None
first_20pct_time = None

for t in trades:
    ts = t.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    price = float(t.price)

    if price > highest_price:
        highest_price = price
        highest_time = ts

    # Track when we first hit +10% and +20%
    gain_pct = (price - entry_price) / entry_price * 100

    if first_10pct_time is None and gain_pct >= 10:
        first_10pct_time = ts
    if first_20pct_time is None and gain_pct >= 20:
        first_20pct_time = ts

# Calculate stats
peak_gain = (highest_price - entry_price) / entry_price * 100
time_to_peak = (highest_time - pub_time).total_seconds() if highest_time else None

print(f"\nPEAK ANALYSIS:")
print(f"  Highest price: ${highest_price:.4f}")
print(f"  Peak gain: +{peak_gain:.1f}%")
print(f"  Time of peak: {highest_time.strftime('%H:%M:%S') if highest_time else 'N/A'} UTC")
print(f"  Time to peak: {time_to_peak:.0f} seconds ({time_to_peak/60:.1f} minutes)" if time_to_peak else "")

print(f"\nTIER TIMING:")
if first_10pct_time:
    secs = (first_10pct_time - pub_time).total_seconds()
    print(f"  +10% hit at: {first_10pct_time.strftime('%H:%M:%S')} ({secs:.0f}s after pub)")
if first_20pct_time:
    secs = (first_20pct_time - pub_time).total_seconds()
    print(f"  +20% hit at: {first_20pct_time.strftime('%H:%M:%S')} ({secs:.0f}s after pub)")

# Show minute-by-minute summary
print(f"\nMINUTE-BY-MINUTE SUMMARY:")
print(f"{'Time':>10} | {'High':>8} | {'Low':>8} | {'Gain %':>8} | {'Volume':>8}")
print("-" * 55)

trades_by_minute = {}
for t in trades:
    ts = t.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    minute_key = ts.strftime('%H:%M')
    if minute_key not in trades_by_minute:
        trades_by_minute[minute_key] = {"high": 0, "low": float('inf'), "volume": 0}

    price = float(t.price)
    trades_by_minute[minute_key]["high"] = max(trades_by_minute[minute_key]["high"], price)
    trades_by_minute[minute_key]["low"] = min(trades_by_minute[minute_key]["low"], price)
    trades_by_minute[minute_key]["volume"] += t.size

for minute in sorted(trades_by_minute.keys()):
    data = trades_by_minute[minute]
    gain = (data["high"] - entry_price) / entry_price * 100
    print(f"{minute:>10} | ${data['high']:.4f} | ${data['low']:.4f} | {gain:>+7.1f}% | {data['volume']:>8.0f}")
