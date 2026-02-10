#!/usr/bin/env python3
"""
Aggregate trade classifications into weekly training data for ML.

Usage:
    python scripts/run_weekly_aggregation.py                    # Last week (most recent Friday)
    python scripts/run_weekly_aggregation.py 2026-02-07         # Specific Friday
    python scripts/run_weekly_aggregation.py --weeks 4          # Last 4 weeks
    python scripts/run_weekly_aggregation.py --all              # All available weeks
"""
import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.jobs.trade_classification import WeeklyAggregationJob


def find_all_weeks(daily_path: Path) -> list[date]:
    """Find all Fridays that have daily classification data."""
    fridays = set()
    if not daily_path.exists():
        return []

    for date_dir in daily_path.iterdir():
        if date_dir.is_dir():
            try:
                d = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
                # Find the Friday of that week
                friday = d + timedelta(days=(4 - d.weekday()) % 7)
                fridays.add(friday)
            except ValueError:
                continue

    return sorted(fridays)


async def main():
    parser = argparse.ArgumentParser(description="Aggregate weekly trade classification data")
    parser.add_argument("date", nargs="?", help="Friday date (YYYY-MM-DD)")
    parser.add_argument("--weeks", type=int, help="Generate for last N weeks")
    parser.add_argument("--all", action="store_true", help="Generate for all available weeks")

    args = parser.parse_args()

    job = WeeklyAggregationJob()

    if args.all:
        fridays = find_all_weeks(job.daily_path)
        if not fridays:
            print("No daily classification data found")
            return

        print(f"Found {len(fridays)} weeks with data\n")
        for friday in fridays:
            result = await job.run(friday)
            if result:
                metrics = result.get("metrics", {})
                totals = result.get("totals", {})
                print(
                    f"{result['week']}: "
                    f"TP={totals.get('true_positive', 0):3} "
                    f"FP={totals.get('false_positive', 0):3} "
                    f"FN={totals.get('false_negative', 0):3} "
                    f"TN={totals.get('true_negative', 0):3} | "
                    f"P={metrics.get('precision', 'N/A')!s:5} "
                    f"R={metrics.get('recall', 'N/A')!s:5} "
                    f"F1={metrics.get('f1_score', 'N/A')!s:5}"
                )
            else:
                print(f"{friday}: No data")

    elif args.weeks:
        # Find last N Fridays
        now = datetime.now()
        days_since_friday = (now.weekday() - 4) % 7
        if days_since_friday == 0 and now.hour < 1:
            days_since_friday = 7

        for i in range(args.weeks):
            friday = (now - timedelta(days=days_since_friday + 7 * i)).date()
            result = await job.run(friday)
            if result:
                metrics = result.get("metrics", {})
                totals = result.get("totals", {})
                print(
                    f"{result['week']}: "
                    f"TP={totals.get('true_positive', 0):3} "
                    f"FP={totals.get('false_positive', 0):3} "
                    f"FN={totals.get('false_negative', 0):3} "
                    f"TN={totals.get('true_negative', 0):3} | "
                    f"P={metrics.get('precision', 'N/A')!s:5} "
                    f"R={metrics.get('recall', 'N/A')!s:5} "
                    f"F1={metrics.get('f1_score', 'N/A')!s:5}"
                )
            else:
                print(f"Week ending {friday}: No data")

    else:
        # Single week
        target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
        result = await job.run(target)

        if result:
            print(f"\n{'='*70}")
            print(f"WEEKLY AGGREGATION: {result['week']}")
            print(f"{'='*70}")

            totals = result.get("totals", {})
            metrics = result.get("metrics", {})

            print(f"\n📊 Confusion Matrix:")
            print(f"   ✅ True Positives:  {totals.get('true_positive', 0):3}")
            print(f"   ❌ False Positives: {totals.get('false_positive', 0):3}")
            print(f"   ⚠️  False Negatives: {totals.get('false_negative', 0):3}")
            print(f"   ✓  True Negatives:  {totals.get('true_negative', 0):3}")

            print(f"\n📈 Metrics:")
            print(f"   Precision: {metrics.get('precision', 'N/A')}")
            print(f"   Recall:    {metrics.get('recall', 'N/A')}")
            print(f"   F1 Score:  {metrics.get('f1_score', 'N/A')}")

            print(f"\n📁 Output Files:")
            print(f"   Stats:    {result.get('stats_file')}")
            print(f"   Training: {result.get('training_file')}")

            # Show training sample count
            samples_total = totals.get('true_positive', 0) + totals.get('false_positive', 0) + \
                           totals.get('false_negative', 0) + totals.get('true_negative', 0)
            positive_samples = totals.get('true_positive', 0) + totals.get('false_negative', 0)
            negative_samples = totals.get('false_positive', 0) + totals.get('true_negative', 0)

            print(f"\n🤖 ML Training Data:")
            print(f"   Total samples:    {samples_total}")
            print(f"   Positive (label=1): {positive_samples} (TP + FN)")
            print(f"   Negative (label=0): {negative_samples} (FP + TN)")
        else:
            print("No data found for this week")


if __name__ == "__main__":
    asyncio.run(main())
