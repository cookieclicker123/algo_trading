#!/usr/bin/env python3
"""
Run daily performance report manually for a specific date.

Usage:
    python scripts/run_daily_performance.py                    # Yesterday
    python scripts/run_daily_performance.py --date 2026-04-06  # Specific date
    python scripts/run_daily_performance.py --days 7           # Last 7 days
"""
import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.jobs.daily_performance import DailyPerformanceJob


async def main():
    parser = argparse.ArgumentParser(description="Generate daily performance report")
    parser.add_argument("--date", type=str, help="Specific date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Analyze last N days")

    args = parser.parse_args()

    job = DailyPerformanceJob()

    if args.days:
        print(f"Generating reports for last {args.days} days...\n")
        for i in range(args.days):
            target = date.today() - timedelta(days=i + 1)
            print(f"{'='*60}")
            print(f"  {target}")
            print(f"{'='*60}")
            report = await job.run(target)
            if report:
                s = report["summary"]
                print(f"  Articles: {s['total_articles']}")
                print(f"  Traded: {s['traded']} ({s['traded_well']}W / {s['traded_poorly']}L)")
                print(f"  Missed Winners: {s['missed_winners']} (best: +{s['best_missed_pct']}%)")
                print(f"  Correctly Skipped: {s['correctly_skipped']}")
                print(f"  Failed Execution: {s['failed_execution']}")
                print(f"  P&L: ${s['total_pnl_usd']:+.2f}")
                if s["missed_filter_breakdown"]:
                    print(f"  Missed by filter:")
                    for reason, count in sorted(s["missed_filter_breakdown"].items(), key=lambda x: -x[1]):
                        print(f"    {reason}: {count}")
            else:
                print(f"  No data")
            print()

    elif args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
        print(f"Generating report for {target}...\n")
        report = await job.run(target)
        if report:
            s = report["summary"]
            print(f"{'='*60}")
            print(f"  DAILY PERFORMANCE: {target}")
            print(f"{'='*60}")
            print(f"\n  SUMMARY:")
            print(f"    Total Articles: {s['total_articles']}")
            print(f"    Traded: {s['traded']} ({s['traded_well']}W / {s['traded_poorly']}L)")
            print(f"    Missed Winners: {s['missed_winners']}")
            print(f"    Correctly Skipped: {s['correctly_skipped']}")
            print(f"    Failed Execution: {s['failed_execution']}")
            print(f"    P&L: ${s['total_pnl_usd']:+.2f}")
            print(f"    Best Missed: +{s['best_missed_pct']}%")
            print(f"    Worst Trade: {s['worst_trade_pct']}%")

            if s["missed_filter_breakdown"]:
                print(f"\n  MISSED WINNERS BY FILTER:")
                for reason, count in sorted(s["missed_filter_breakdown"].items(), key=lambda x: -x[1]):
                    print(f"    {reason}: {count}")

            if s["missed_headline_breakdown"]:
                print(f"\n  MISSED WINNERS BY HEADLINE TYPE:")
                for ht, count in sorted(s["missed_headline_breakdown"].items(), key=lambda x: -x[1]):
                    print(f"    {ht}: {count}")

            if s["missed_sector_breakdown"]:
                print(f"\n  MISSED WINNERS BY SECTOR:")
                for sector, count in sorted(s["missed_sector_breakdown"].items(), key=lambda x: -x[1]):
                    print(f"    {sector}: {count}")

            # Show top missed opportunities
            missed = [r for r in report["records"] if r["outcome"] == "missed_winner"]
            if missed:
                missed_sorted = sorted(missed, key=lambda r: -(r.get("peak_pct") or 0))
                print(f"\n  TOP MISSED OPPORTUNITIES:")
                for r in missed_sorted[:10]:
                    ticker = r.get('ticker') or '???'
                    peak = r.get('peak_pct') or 0
                    ht = r.get('headline_type') or 'unknown'
                    reason = (r.get('filter_reason') or r.get('postfilter_reason') or 'unknown')[:50]
                    title = (r.get('title') or '')[:70]
                    print(f"    {ticker:6s} +{peak:5.1f}% | {ht:20s} | {reason}")
                    print(f"           {title}")

            print(f"\n  Saved to: tmp/daily_performance/{target}.json")
        else:
            print(f"No data for {target}")

    else:
        # Default: yesterday
        target = date.today() - timedelta(days=1)
        print(f"Generating report for yesterday ({target})...")
        report = await job.run(target)
        if report:
            s = report["summary"]
            print(f"  {s['traded']} trades ({s['traded_well']}W/{s['traded_poorly']}L), "
                  f"${s['total_pnl_usd']:+.2f} P&L, "
                  f"{s['missed_winners']} missed (+{s['best_missed_pct']}% best)")
            print(f"  Saved to: tmp/daily_performance/{target}.json")
        else:
            print(f"No data for {target}")


if __name__ == "__main__":
    asyncio.run(main())
