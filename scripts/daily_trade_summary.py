"""
Daily trade summary helper.

Usage:
    uv run python scripts/daily_trade_summary.py --date 2025-11-11

Outputs PnL breakdowns, market-cap buckets, and keyword diagnostics
for the given classification audit trail day.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional


def load_audit_entries(date: str, base_dir: Path) -> List[Dict[str, Any]]:
    """Load the audit JSON for the supplied date string (YYYY-MM-DD)."""
    dt = date.replace("/", "-")
    try:
        year, month, _ = dt.split("-")
    except ValueError as exc:
        raise SystemExit(f"Invalid date format '{date}'. Expected YYYY-MM-DD.") from exc

    # Find file via directory pattern year/month/week/date.json
    year_dir = base_dir / year
    if not year_dir.exists():
        raise SystemExit(f"No data directory for year {year}: {year_dir}")

    month_dir = year_dir / month
    if not month_dir.exists():
        raise SystemExit(f"No data directory for {year}-{month}: {month_dir}")
    candidates: List[Path] = list(month_dir.glob(f"week_*/{dt}.json"))
    if not candidates:
        raise SystemExit(f"No audit file located for {date}.")
    if len(candidates) > 1:
        raise SystemExit(f"Multiple audit files found for {date}: {candidates}")

    with candidates[0].open("r", encoding="utf-8") as handle:
        return json.load(handle)


def summarise(entries: List[Dict[str, Any]]):
    trade_details = [e.get("trade_details", {}) for e in entries]
    entry_count = sum(1 for d in trade_details if d.get("entry_time"))
    exit_count = sum(1 for d in trade_details if d.get("exit_time"))
    trades = [e for e in entries if e.get("trade_details", {}).get("pnl") is not None]
    print(
        f"Articles: {len(entries)}  Entries: {entry_count}  Completed exits: {exit_count}  "
        f"Trades with PnL: {len(trades)}"
    )
    if not trades:
        return

    pnl = [t["trade_details"]["pnl"] for t in trades]
    print(
        f"PnL  mean={mean(pnl):.2f}  median={median(pnl):.2f}  "
        f"min={min(pnl):.2f}  max={max(pnl):.2f}"
    )

    sector_totals = defaultdict(float)
    for trade in trades:
        sector = (trade.get("metadata", {}).get("sector") or "Unknown").strip() or "Unknown"
        sector_totals[sector] += trade["trade_details"]["pnl"]
    print("\nPnL by sector:")
    for sector, total in sorted(sector_totals.items(), key=lambda item: item[1]):
        print(f"  {sector:<25} {total:>8.2f}")

    def _num(value: Optional[Any]) -> Optional[float]:
        if value in (None, "N/A"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    buckets = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for trade in trades:
        cap = _num(trade.get("metadata", {}).get("market_cap"))
        if cap is None:
            bucket = "Unknown"
        elif cap < 500_000_000:
            bucket = "<500M"
        elif cap < 1_000_000_000:
            bucket = "500M-1B"
        elif cap < 5_000_000_000:
            bucket = "1-5B"
        else:
            bucket = "5B+"
        buckets[bucket]["count"] += 1
        buckets[bucket]["pnl"] += trade["trade_details"]["pnl"]

    print("\nPnL by market cap bucket:")
    for bucket, stats in sorted(buckets.items(), key=lambda item: item[0]):
        print(f"  {bucket:<8} count={stats['count']:<3} pnl={stats['pnl']:.2f}")

    avg_volumes = [
        _num(trade.get("metadata", {}).get("average_volume_30d"))
        or _num(trade.get("metadata", {}).get("average_volume_10d"))
        for trade in trades
    ]
    avg_volumes = [v for v in avg_volumes if v is not None]
    if avg_volumes:
        print(
            f"\nAverage volume (non-null): median={median(avg_volumes):.0f} "
            f"mean={mean(avg_volumes):.0f}"
        )


def main():
    parser = argparse.ArgumentParser(description="Summarise daily trade outcomes.")
    parser.add_argument(
        "--date",
        required=True,
        help="Date to summarise (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--base-dir",
        default="tmp/classification_audit_trail",
        help="Base directory for audit files.",
    )
    args = parser.parse_args()

    entries = load_audit_entries(args.date, Path(args.base_dir))
    summarise(entries)


if __name__ == "__main__":
    main()

