#!/usr/bin/env python3
"""Analyze stop-out behavior for missed trades using Alpaca tick data."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest
from alpaca.data.enums import DataFeed

# Initialize client (using paper trading keys for historical data)
client = StockHistoricalDataClient(
    api_key=os.environ.get("ALPACA_KEY_PAPER"),
    secret_key=os.environ.get("ALPACA_SECRET_PAPER")
)

# Trade data: (ticker, pub_time, entry_ask)
trades_to_check = [
    ("ASBP", "2026-02-11T12:45:00Z", 1.50),
    ("KIDZ", "2026-02-11T13:00:00Z", 0.189),
    ("PRFX", "2026-02-11T13:30:00Z", 3.05),
]

for ticker, pub_time_str, entry_price in trades_to_check:
    pub_time = datetime.fromisoformat(pub_time_str.replace("Z", "+00:00"))
    end_time = pub_time + timedelta(minutes=10)

    print(f"\n{'='*65}")
    print(f"{ticker} - Entry: ${entry_price:.4f} at {pub_time_str}")
    print(f"{'='*65}")

    try:
        trades = client.get_stock_trades(StockTradesRequest(
            symbol_or_symbols=ticker,
            start=pub_time,
            end=end_time,
            feed=DataFeed.SIP
        ))

        if not trades.data or ticker not in trades.data:
            print("  No trades found")
            continue

        trade_list = trades.data[ticker]
        print(f"  Total trades in 10min: {len(trade_list)}")

        # Stop and take profit levels
        stop_5pct = entry_price * 0.95   # -5%
        tp_20pct = entry_price * 1.20    # +20%
        tp_30pct = entry_price * 1.30    # +30%
        tp_40pct = entry_price * 1.40    # +40%

        print(f"  Stop -5%: ${stop_5pct:.4f}")
        print(f"  TP +20%: ${tp_20pct:.4f}, +30%: ${tp_30pct:.4f}, +40%: ${tp_40pct:.4f}")

        # ============================================================
        # FIRST 5 SECONDS (soft stop with 0.5s confirmation)
        # ============================================================
        soft_stop_end = pub_time + timedelta(seconds=5)
        first_5s_trades = [t for t in trade_list if t.timestamp <= soft_stop_end]

        print(f"\n  FIRST 5 SECONDS ({len(first_5s_trades)} trades):")

        breach_start = None
        breach_confirmed = False
        min_price_5s = entry_price
        max_price_5s = entry_price

        for t in first_5s_trades:
            min_price_5s = min(min_price_5s, t.price)
            max_price_5s = max(max_price_5s, t.price)

            if t.price <= stop_5pct:
                if breach_start is None:
                    breach_start = t.timestamp
                elif (t.timestamp - breach_start).total_seconds() >= 0.5:
                    breach_confirmed = True
                    pct = ((t.price - entry_price) / entry_price) * 100
                    print(f"    ⚠️ SOFT STOP TRIGGERED at {t.timestamp.strftime('%H:%M:%S.%f')[:12]}")
                    print(f"       Price: ${t.price:.4f} ({pct:.1f}%), below stop for 0.5s+")
                    break
            else:
                breach_start = None

        pct_min = ((min_price_5s - entry_price) / entry_price) * 100
        pct_max = ((max_price_5s - entry_price) / entry_price) * 100

        if not breach_confirmed:
            print(f"    ✅ NO STOP - Range: ${min_price_5s:.4f} ({pct_min:.1f}%) to ${max_price_5s:.4f} ({pct_max:.1f}%)")

        if breach_confirmed:
            print(f"\n  ❌ TRADE WOULD HAVE BEEN STOPPED IN FIRST 5 SECONDS")
            continue

        # ============================================================
        # AFTER 5 SECONDS (hard stop, take profits)
        # ============================================================
        after_5s_trades = [t for t in trade_list if t.timestamp > soft_stop_end]

        print(f"\n  AFTER 5 SECONDS ({len(after_5s_trades)} trades):")

        position = 100
        current_stop = stop_5pct
        stopped = False
        total_realized = 0

        for t in after_5s_trades:
            if stopped or position <= 0:
                break

            pct = ((t.price - entry_price) / entry_price) * 100

            # Hard stop check (immediate, no confirmation)
            if t.price <= current_stop:
                stop_pct = ((current_stop - entry_price) / entry_price) * 100
                # Calculate what we would have realized
                remaining_value = position * (1 + pct/100)
                print(f"    ⚠️ STOPPED at {t.timestamp.strftime('%H:%M:%S')} - ${t.price:.4f} ({pct:.1f}%)")
                print(f"       Stop was at {stop_pct:.0f}%, {position}% position exited")
                total_realized += position * (pct / 100)
                stopped = True
                break

            # Take profit 1: +20% - sell 50%, stop moves to +5%
            if position >= 100 and t.price >= tp_20pct:
                current_stop = entry_price * 1.05  # +5%
                sold = 50
                realized = sold * (pct / 100)
                total_realized += realized
                position -= sold
                print(f"    💰 TP1 (+20%) at {t.timestamp.strftime('%H:%M:%S')} - ${t.price:.4f} ({pct:.1f}%)")
                print(f"       Sold 50%, realized +{realized:.1f}%, stop→+5%")

            # Take profit 2: +30% - sell 25%, stop moves to +10%
            if position >= 50 and t.price >= tp_30pct:
                current_stop = entry_price * 1.10  # +10%
                sold = 25
                realized = sold * (pct / 100)
                total_realized += realized
                position -= sold
                print(f"    💰 TP2 (+30%) at {t.timestamp.strftime('%H:%M:%S')} - ${t.price:.4f} ({pct:.1f}%)")
                print(f"       Sold 25%, realized +{realized:.1f}%, stop→+10%")

            # Take profit 3: +40% - sell final 25%
            if position >= 25 and t.price >= tp_40pct:
                sold = 25
                realized = sold * (pct / 100)
                total_realized += realized
                position -= sold
                print(f"    💰 TP3 (+40%) at {t.timestamp.strftime('%H:%M:%S')} - ${t.price:.4f} ({pct:.1f}%)")
                print(f"       Sold final 25%, realized +{realized:.1f}%")

        # Summary
        print(f"\n  SUMMARY:")
        if stopped:
            print(f"    Position: STOPPED OUT")
        elif position > 0:
            final = after_5s_trades[-1] if after_5s_trades else trade_list[-1]
            final_pct = ((final.price - entry_price) / entry_price) * 100
            unrealized = position * (final_pct / 100)
            print(f"    Position: {position}% still held at ${final.price:.4f} ({final_pct:.1f}%)")
            print(f"    Unrealized: +{unrealized:.1f}%")
        else:
            print(f"    Position: FULLY EXITED via take profits")
        print(f"    Total Realized: +{total_realized:.1f}%")

    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()
