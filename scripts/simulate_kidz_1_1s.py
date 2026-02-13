#!/usr/bin/env python3
"""Simulate KIDZ trade with 1.1 second confirmation."""

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

CONFIRMATION_TIME = 1.1  # seconds

end_time = pub_time + timedelta(minutes=10)

trades = client.get_stock_trades(StockTradesRequest(
    symbol_or_symbols=ticker,
    start=pub_time,
    end=end_time,
    feed=DataFeed.SIP
))

trade_list = trades.data[ticker]

print(f"KIDZ SIMULATION - {CONFIRMATION_TIME}s CONFIRMATION RULE")
print(f"Entry: ${entry_price:.4f}, Stop -5%: ${stop_price:.4f}")
print(f"="*70)

tp_20pct = entry_price * 1.20
tp_30pct = entry_price * 1.30
tp_40pct = entry_price * 1.40

print(f"TP levels: +20%=${tp_20pct:.4f}, +30%=${tp_30pct:.4f}, +40%=${tp_40pct:.4f}")
print()

position = 100
current_stop = stop_price
total_realized = 0
breach_start = None

for t in trade_list:
    if position <= 0:
        break

    pct = ((t.price - entry_price) / entry_price) * 100
    elapsed = (t.timestamp - pub_time).total_seconds()

    # Check stop with confirmation
    if t.price <= current_stop:
        if breach_start is None:
            breach_start = t.timestamp
        elif (t.timestamp - breach_start).total_seconds() >= CONFIRMATION_TIME:
            stop_pct = ((current_stop - entry_price) / entry_price) * 100
            realized = position * (pct / 100)
            total_realized += realized
            print(f"⚠️ STOPPED at {elapsed:.1f}s - ${t.price:.4f} ({pct:+.1f}%)")
            print(f"   Stop was at ${current_stop:.4f} ({stop_pct:+.0f}%), {position}% position exited")
            position = 0
            break
    else:
        breach_start = None

    # Take profit 1: +20% - sell 50%, stop moves to +5%
    if position >= 100 and t.price >= tp_20pct:
        current_stop = entry_price * 1.05
        sold = 50
        realized = sold * (pct / 100)
        total_realized += realized
        position -= sold
        print(f"💰 TP1 (+20%) at {elapsed:.1f}s - ${t.price:.4f} ({pct:+.1f}%)")
        print(f"   Sold 50%, realized +{realized:.1f}%, stop moved to +5%")
        breach_start = None

    # Take profit 2: +30% - sell 25%, stop moves to +10%
    if position >= 50 and t.price >= tp_30pct:
        current_stop = entry_price * 1.10
        sold = 25
        realized = sold * (pct / 100)
        total_realized += realized
        position -= sold
        print(f"💰 TP2 (+30%) at {elapsed:.1f}s - ${t.price:.4f} ({pct:+.1f}%)")
        print(f"   Sold 25%, realized +{realized:.1f}%, stop moved to +10%")
        breach_start = None

    # Take profit 3: +40% - sell final 25%
    if position >= 25 and t.price >= tp_40pct:
        sold = 25
        realized = sold * (pct / 100)
        total_realized += realized
        position -= sold
        print(f"💰 TP3 (+40%) at {elapsed:.1f}s - ${t.price:.4f} ({pct:+.1f}%)")
        print(f"   Sold final 25%, realized +{realized:.1f}%")

# Final summary
print(f"\n{'='*70}")
print(f"FINAL RESULT:")
print(f"{'='*70}")
if position > 0:
    final = trade_list[-1]
    final_pct = ((final.price - entry_price) / entry_price) * 100
    unrealized = position * (final_pct / 100)
    print(f"  Position remaining: {position}%")
    print(f"  Final price: ${final.price:.4f} ({final_pct:+.1f}%)")
    print(f"  Unrealized P&L: {unrealized:+.1f}%")
    print(f"  Realized P&L: {total_realized:+.1f}%")
    print(f"  ════════════════════")
    print(f"  TOTAL P&L: {total_realized + unrealized:+.1f}%")
else:
    print(f"  Position: FULLY EXITED")
    print(f"  ════════════════════")
    print(f"  TOTAL REALIZED: {total_realized:+.1f}%")
