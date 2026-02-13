#!/usr/bin/env python3
"""Find optimal confirmation time for KIDZ trade."""

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

ticker = "KIDZ"
pub_time = datetime.fromisoformat("2026-02-11T13:00:00+00:00")
entry_price = 0.189
stop_price = entry_price * 0.95  # -5%

end_time = pub_time + timedelta(minutes=10)

trades = client.get_stock_trades(StockTradesRequest(
    symbol_or_symbols=ticker,
    start=pub_time,
    end=end_time,
    feed=DataFeed.SIP
))

trade_list = trades.data[ticker]

# Find all breach periods and their durations
breach_start = None
breach_start_price = None
breaches = []

for t in trade_list:
    if t.price <= stop_price:
        if breach_start is None:
            breach_start = t.timestamp
            breach_start_price = t.price
    else:
        if breach_start is not None:
            duration = (t.timestamp - breach_start).total_seconds()
            elapsed_start = (breach_start - pub_time).total_seconds()
            breaches.append({
                'start': elapsed_start,
                'duration': duration,
                'price': breach_start_price
            })
            breach_start = None

# Check end
if breach_start is not None:
    duration = (trade_list[-1].timestamp - breach_start).total_seconds()
    elapsed_start = (breach_start - pub_time).total_seconds()
    breaches.append({
        'start': elapsed_start,
        'duration': duration,
        'price': breach_start_price
    })

# Sort by duration
breaches_sorted = sorted(breaches, key=lambda x: -x['duration'])

print("TOP 10 LONGEST BREACHES:")
print("="*60)
for i, b in enumerate(breaches_sorted[:10]):
    pct = ((b['price'] - entry_price) / entry_price) * 100
    print(f"  {i+1}. {b['duration']:.3f}s at {b['start']:.1f}s (${b['price']:.4f}, {pct:.1f}%)")

print("\n" + "="*60)
print("CONFIRMATION TIME ANALYSIS:")
print("="*60)

max_breach = breaches_sorted[0]['duration']
print(f"Max breach: {max_breach:.3f}s")
print(f"Required confirmation time to survive: >{max_breach:.2f}s")

# Test various confirmation times
for conf_time in [0.5, 0.75, 1.0, 1.05, 1.1, 1.25, 1.5, 2.0]:
    would_stop = max_breach >= conf_time
    status = "❌ STOPPED" if would_stop else "✅ SURVIVES"
    print(f"  {conf_time:.2f}s confirmation: {status}")
