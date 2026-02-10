"""Check when RITR started getting volume after article publication."""
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest
from alpaca.data.enums import DataFeed

# Load env
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

api_key = os.getenv("ALPACA_PAPER_API_KEY") or os.getenv("ALPACA_KEY")
secret_key = os.getenv("ALPACA_PAPER_SECRET_KEY") or os.getenv("ALPACA_SECRET")

client = StockHistoricalDataClient(api_key, secret_key)

# RITR article published at 2026-02-09T11:05:37+00:00
pub_time = datetime(2026, 2, 9, 11, 5, 37, tzinfo=timezone.utc)

# Fetch trades from 30 seconds before to 2 minutes after
start = pub_time - timedelta(seconds=30)
end = pub_time + timedelta(minutes=2)

trades_request = StockTradesRequest(
    symbol_or_symbols=["RITR"],
    start=start,
    end=end,
    feed=DataFeed.SIP
)

response = client.get_stock_trades(trades_request)
trades = response.data.get("RITR", [])

print(f"RITR Volume Analysis")
print(f"Article published: {pub_time.isoformat()}")
print(f"="*60)
print(f"\nTrades from {start.isoformat()} to {end.isoformat()}:")
print(f"Total trades in window: {len(trades)}")

if trades:
    # Group by second
    trades_by_second = {}
    for t in trades:
        ts = t.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        # Calculate offset from publication
        offset = (ts - pub_time).total_seconds()
        second_key = int(offset)

        if second_key not in trades_by_second:
            trades_by_second[second_key] = {"count": 0, "volume": 0, "prices": []}

        trades_by_second[second_key]["count"] += 1
        trades_by_second[second_key]["volume"] += t.size
        trades_by_second[second_key]["prices"].append(float(t.price))

    print(f"\n{'Offset':>8} | {'Trades':>6} | {'Volume':>8} | {'Price Range':<20}")
    print("-" * 50)

    for offset in sorted(trades_by_second.keys()):
        data = trades_by_second[offset]
        min_p = min(data["prices"])
        max_p = max(data["prices"])
        price_range = f"${min_p:.4f} - ${max_p:.4f}" if min_p != max_p else f"${min_p:.4f}"

        offset_str = f"{offset:+}s" if offset >= 0 else f"{offset}s"
        print(f"{offset_str:>8} | {data['count']:>6} | {data['volume']:>8} | {price_range}")

    # First trade after publication
    first_after = None
    for t in trades:
        ts = t.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= pub_time:
            first_after = t
            break

    if first_after:
        ts = first_after.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        latency = (ts - pub_time).total_seconds()
        print(f"\n{'='*60}")
        print(f"FIRST TRADE AFTER PUBLICATION:")
        print(f"  Time: {ts.isoformat()}")
        print(f"  Latency: {latency:.3f} seconds")
        print(f"  Price: ${first_after.price}")
        print(f"  Size: {first_after.size} shares")
