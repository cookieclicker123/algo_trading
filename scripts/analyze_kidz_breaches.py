#!/usr/bin/env python3
"""Analyze KIDZ breach periods to determine if 10s soft stop would work."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest
from alpaca.data.enums import DataFeed

client = StockHistoricalDataClient(
    api_key=os.environ.get("ALPACA_KEY_PAPER"),
    secret_key=os.environ.get("ALPACA_SECRET_PAPER")
)

# KIDZ data
ticker = "KIDZ"
pub_time = datetime.fromisoformat("2026-02-11T13:00:00+00:00")
entry_price = 0.189
stop_price = entry_price * 0.95  # -5%

# Get first 15 seconds of trades (to see full picture)
end_time = pub_time + timedelta(seconds=15)

trades = client.get_stock_trades(StockTradesRequest(
    symbol_or_symbols=ticker,
    start=pub_time,
    end=end_time,
    feed=DataFeed.SIP
))

trade_list = trades.data[ticker]
print(f"KIDZ - First 15 seconds analysis")
print(f"Entry: ${entry_price:.4f}, Stop -5%: ${stop_price:.4f}")
print(f"Total trades in 15s: {len(trade_list)}")
print()

# Track breach periods
breach_start = None
breach_start_price = None
max_breach_duration = 0
breach_periods = []

for t in trade_list:
    pct = ((t.price - entry_price) / entry_price) * 100
    elapsed = (t.timestamp - pub_time).total_seconds()

    if t.price <= stop_price:
        if breach_start is None:
            breach_start = t.timestamp
            breach_start_price = t.price
    else:
        if breach_start is not None:
            duration = (t.timestamp - breach_start).total_seconds()
            breach_periods.append({
                'start': breach_start,
                'end': t.timestamp,
                'duration': duration,
                'start_price': breach_start_price,
                'recovery_price': t.price
            })
            if duration > max_breach_duration:
                max_breach_duration = duration
            breach_start = None

# Handle if still in breach at end
if breach_start is not None:
    duration = (trade_list[-1].timestamp - breach_start).total_seconds()
    breach_periods.append({
        'start': breach_start,
        'end': trade_list[-1].timestamp,
        'duration': duration,
        'start_price': breach_start_price,
        'recovery_price': trade_list[-1].price,
        'ongoing': True
    })
    if duration > max_breach_duration:
        max_breach_duration = duration

print(f"BREACH PERIODS (price <= ${stop_price:.4f} = -5%):")
print(f"{'='*70}")

for i, bp in enumerate(breach_periods):
    start_elapsed = (bp['start'] - pub_time).total_seconds()
    end_elapsed = (bp['end'] - pub_time).total_seconds()
    start_pct = ((bp['start_price'] - entry_price) / entry_price) * 100
    print(f"  Breach {i+1}: {start_elapsed:.2f}s to {end_elapsed:.2f}s = {bp['duration']:.3f}s duration")
    print(f"           Started at ${bp['start_price']:.4f} ({start_pct:.1f}%)")
    if bp['duration'] >= 0.5:
        print(f"           ⚠️ WOULD TRIGGER SOFT STOP (>= 0.5s)")
    else:
        print(f"           ✅ Recovered in time (< 0.5s)")
    print()

print(f"{'='*70}")
print(f"Max breach duration: {max_breach_duration:.3f}s")
print()

if max_breach_duration >= 0.5:
    print(f"❌ WITH 10s SOFT STOP: Would still be STOPPED OUT")
    print(f"   (breach lasted {max_breach_duration:.3f}s >= 0.5s confirmation)")
else:
    print(f"✅ WITH 10s SOFT STOP: Would have SURVIVED!")
    print(f"   (no breach lasted >= 0.5s)")

# Show price trajectory
print(f"\n{'='*70}")
print("PRICE TRAJECTORY (every 0.5s):")
print(f"{'='*70}")

for sec in [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 6, 7, 8, 9, 10, 12, 15]:
    target_time = pub_time + timedelta(seconds=sec)
    # Find closest trade
    closest = min(trade_list, key=lambda t: abs((t.timestamp - target_time).total_seconds()))
    pct = ((closest.price - entry_price) / entry_price) * 100
    marker = "⚠️" if closest.price <= stop_price else "  "
    print(f"  {sec:5.1f}s: ${closest.price:.4f} ({pct:+6.1f}%) {marker}")
