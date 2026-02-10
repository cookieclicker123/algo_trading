#!/usr/bin/env python3
"""
Generate human-readable winners summary for any date.

Usage:
    python scripts/run_winners_summary.py                  # Yesterday
    python scripts/run_winners_summary.py 2026-02-05       # Specific date
    python scripts/run_winners_summary.py --days 7         # Last 7 days
"""
import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.jobs.winners_summary import WinnersSummaryJob


async def main():
    parser = argparse.ArgumentParser(description="Generate winners summary")
    parser.add_argument("date", nargs="?", help="Date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Generate for last N days")

    args = parser.parse_args()

    job = WinnersSummaryJob()

    if args.days:
        for i in range(args.days):
            target = date.today() - timedelta(days=i + 1)
            result = await job.run(target)
            if result:
                print(f"✅ {target}: {result}")
    else:
        target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
        result = await job.run(target)
        if result:
            with open(result) as f:
                print(f.read())
        else:
            print("No data found")


if __name__ == "__main__":
    asyncio.run(main())
