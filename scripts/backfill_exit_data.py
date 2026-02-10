#!/usr/bin/env python3
"""
Backfill missing exit data in signal records from Alpaca trade history.

This script finds signal records with null exit_price and tries to match them
with SELL trades from Alpaca's order history.

Usage:
    python scripts/backfill_exit_data.py                    # Last 7 days
    python scripts/backfill_exit_data.py --days 30          # Last 30 days
    python scripts/backfill_exit_data.py --dry-run          # Preview changes
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import OrderSide, QueryOrderStatus
except ImportError:
    print("Alpaca SDK not installed. Run: pip install alpaca-py")
    sys.exit(1)


SIGNAL_PATH = Path("tmp/statistics/signal")


def get_trading_client() -> TradingClient:
    """Get Alpaca trading client."""
    api_key = os.getenv("ALPACA_PAPER_API_KEY") or os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_PAPER_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
    paper = os.getenv("PAPER_TRADING", "true").lower() == "true"

    if not api_key or not secret_key:
        raise ValueError("Alpaca API keys not found in environment")

    return TradingClient(api_key, secret_key, paper=paper)


def load_signal_files(days: int) -> List[Dict]:
    """Load all signal record files from the last N days."""
    records = []
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    current = start_date
    while current <= end_date:
        year = current.year
        month = current.month
        day = current.day
        week = current.isocalendar()[1]

        for session in ["premarket", "market_hours", "postmarket"]:
            file_path = (
                SIGNAL_PATH / str(year) / f"{month:02d}" /
                f"week_{week}" / f"{day:02d}" / session / f"{session}.json"
            )
            if file_path.exists():
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    for record in data.get("records", []):
                        record["_file_path"] = str(file_path)
                        record["_session"] = session
                        record["_date"] = current.isoformat()
                        records.append(record)
                except Exception as e:
                    print(f"Error loading {file_path}: {e}")

        current += timedelta(days=1)

    return records


def get_alpaca_orders(client: TradingClient, days: int) -> List[Dict]:
    """Get filled SELL orders from Alpaca."""
    after = datetime.now() - timedelta(days=days)

    request = GetOrdersRequest(
        status=QueryOrderStatus.CLOSED,
        side=OrderSide.SELL,
        after=after.isoformat(),
        limit=500,
    )

    orders = client.get_orders(request)

    sell_orders = []
    for order in orders:
        if order.status.value == "filled" and order.side == OrderSide.SELL:
            sell_orders.append({
                "id": str(order.id),
                "ticker": order.symbol,
                "filled_at": order.filled_at.isoformat() if order.filled_at else None,
                "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
                "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else 0,
                "created_at": order.created_at.isoformat() if order.created_at else None,
            })

    return sell_orders


def match_exits(records: List[Dict], sell_orders: List[Dict]) -> List[Dict]:
    """Match BUY records with corresponding SELL orders."""
    matches = []

    # Group records by ticker
    records_by_ticker: Dict[str, List[Dict]] = {}
    for record in records:
        if record.get("exit_price") is None:  # Missing exit
            ticker = record.get("ticker")
            if ticker:
                if ticker not in records_by_ticker:
                    records_by_ticker[ticker] = []
                records_by_ticker[ticker].append(record)

    # Sort each ticker's records by executed_at (oldest first for FIFO matching)
    for ticker in records_by_ticker:
        records_by_ticker[ticker].sort(key=lambda r: r.get("executed_at", ""))

    # Group sell orders by ticker
    sells_by_ticker: Dict[str, List[Dict]] = {}
    for order in sell_orders:
        ticker = order.get("ticker")
        if ticker:
            if ticker not in sells_by_ticker:
                sells_by_ticker[ticker] = []
            sells_by_ticker[ticker].append(order)

    # Sort each ticker's sell orders by filled_at (oldest first)
    for ticker in sells_by_ticker:
        sells_by_ticker[ticker].sort(key=lambda o: o.get("filled_at", ""))

    # Match FIFO
    for ticker, buy_records in records_by_ticker.items():
        if ticker not in sells_by_ticker:
            continue

        sell_list = sells_by_ticker[ticker]
        sell_idx = 0

        for record in buy_records:
            if sell_idx >= len(sell_list):
                break

            sell = sell_list[sell_idx]
            entry_time = record.get("executed_at")
            exit_time = sell.get("filled_at")

            # Check if sell is after entry
            if entry_time and exit_time and exit_time > entry_time:
                entry_price = record.get("entry_price", 0)
                exit_price = sell.get("filled_avg_price", 0)
                shares = sell.get("filled_qty", 0)

                if entry_price and exit_price:
                    pnl_usd = (exit_price - entry_price) * shares
                    pnl_pct = ((exit_price - entry_price) / entry_price * 100)

                    matches.append({
                        "record": record,
                        "sell_order": sell,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "shares": shares,
                        "pnl_usd": round(pnl_usd, 2),
                        "pnl_pct": round(pnl_pct, 2),
                    })

                    sell_idx += 1

    return matches


def update_signal_file(file_path: str, trade_id: str, updates: Dict) -> bool:
    """Update a signal record with exit data."""
    try:
        with open(file_path) as f:
            data = json.load(f)

        updated = False
        for record in data.get("records", []):
            if record.get("trade_id") == trade_id:
                record.update(updates)
                updated = True
                break

        if updated:
            # Update summary
            profitable = sum(1 for r in data["records"] if r.get("profit_loss_percent", 0) and r["profit_loss_percent"] > 0)
            losing = sum(1 for r in data["records"] if r.get("profit_loss_percent", 0) and r["profit_loss_percent"] < 0)
            total_pnl = sum(r.get("profit_loss_usd", 0) or 0 for r in data["records"])

            data["summary"]["profitable_trades"] = profitable
            data["summary"]["losing_trades"] = losing
            data["summary"]["total_profit_loss_usd"] = round(total_pnl, 2)
            data["last_updated_at"] = datetime.now().isoformat()

            with open(file_path, "w") as f:
                json.dump(data, f, indent=4)

            return True
    except Exception as e:
        print(f"Error updating {file_path}: {e}")

    return False


def main():
    parser = argparse.ArgumentParser(description="Backfill missing exit data")
    parser.add_argument("--days", type=int, default=7, help="Days to look back")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"BACKFILL EXIT DATA")
    print(f"{'='*60}")
    print(f"Looking back {args.days} days...")

    # Load data
    records = load_signal_files(args.days)
    missing_exit = [r for r in records if r.get("exit_price") is None]

    print(f"\nSignal records found: {len(records)}")
    print(f"Missing exit data: {len(missing_exit)}")

    if not missing_exit:
        print("\nNo records need backfilling!")
        return

    # Get Alpaca orders
    print("\nFetching Alpaca SELL orders...")
    try:
        client = get_trading_client()
        sell_orders = get_alpaca_orders(client, args.days)
        print(f"SELL orders found: {len(sell_orders)}")
    except Exception as e:
        print(f"Error fetching Alpaca orders: {e}")
        return

    # Match
    matches = match_exits(missing_exit, sell_orders)
    print(f"\nMatches found: {len(matches)}")

    if not matches:
        print("\nNo matches found - cannot backfill")

        # Show what we found
        print("\nMissing exit records:")
        for r in missing_exit[:10]:
            print(f"  {r.get('ticker'):6} | Entry: ${r.get('entry_price', 0):.2f} | {r.get('executed_at', '')[:19]}")

        print("\nSELL orders:")
        for o in sell_orders[:10]:
            print(f"  {o.get('ticker'):6} | Exit: ${o.get('filled_avg_price', 0):.2f} | {o.get('filled_at', '')[:19]}")

        return

    # Show matches
    print(f"\n{'Ticker':<8} {'Entry':>10} {'Exit':>10} {'P&L $':>10} {'P&L %':>10}")
    print("-" * 60)

    total_pnl = 0
    for m in matches:
        print(
            f"{m['record'].get('ticker'):<8} "
            f"${m['entry_price']:>8.2f} "
            f"${m['exit_price']:>8.2f} "
            f"${m['pnl_usd']:>+9.2f} "
            f"{m['pnl_pct']:>+9.1f}%"
        )
        total_pnl += m['pnl_usd']

    print("-" * 60)
    print(f"{'TOTAL':<8} {'':<10} {'':<10} ${total_pnl:>+9.2f}")

    if args.dry_run:
        print("\n[DRY RUN] No changes made")
        return

    # Apply updates
    print("\nApplying updates...")
    success_count = 0

    for m in matches:
        record = m['record']
        sell = m['sell_order']

        # Calculate hold duration
        entry_time = datetime.fromisoformat(record.get("executed_at").replace("Z", "+00:00"))
        exit_time = datetime.fromisoformat(sell.get("filled_at").replace("Z", "+00:00"))
        hold_duration = (exit_time - entry_time).total_seconds()

        updates = {
            "exit_price": m['exit_price'],
            "exit_shares": int(m['shares']),
            "exit_amount_usd": round(m['exit_price'] * m['shares'], 2),
            "exit_reason": "backfilled",
            "exited_at": sell.get("filled_at"),
            "hold_duration_seconds": round(hold_duration, 1),
            "profit_loss_usd": m['pnl_usd'],
            "profit_loss_percent": m['pnl_pct'],
        }

        if update_signal_file(record["_file_path"], record.get("trade_id"), updates):
            success_count += 1
            print(f"  ✅ {record.get('ticker')} updated")
        else:
            print(f"  ❌ {record.get('ticker')} failed")

    print(f"\nUpdated {success_count}/{len(matches)} records")


if __name__ == "__main__":
    main()
