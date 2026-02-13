#!/usr/bin/env python3
"""
Classify trades into confusion matrix categories for any date range.

Usage:
    python scripts/run_trade_classification.py                  # Yesterday
    python scripts/run_trade_classification.py 2026-02-05       # Specific date
    python scripts/run_trade_classification.py --days 7         # Last 7 days
"""
import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.jobs.trade_classification import TradeClassificationJob


async def main():
    parser = argparse.ArgumentParser(description="Classify trades into confusion matrix categories")
    parser.add_argument("date", nargs="?", help="Date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Generate for last N days")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")

    args = parser.parse_args()

    job = TradeClassificationJob()

    if args.days:
        totals = {"true_positive": 0, "false_positive": 0, "false_negative": 0, "true_negative": 0}
        for i in range(args.days):
            target = date.today() - timedelta(days=i + 1)
            result = await job.run(target)
            if result:
                counts = result.get("counts", {})
                print(
                    f"{target}: TP={counts.get('true_positive', 0):2} "
                    f"FP={counts.get('false_positive', 0):2} "
                    f"FN={counts.get('false_negative', 0):2} "
                    f"TN={counts.get('true_negative', 0):3} | "
                    f"P={result.get('precision', 'N/A')!s:5} "
                    f"R={result.get('recall', 'N/A')!s:5} "
                    f"F1={result.get('f1_score', 'N/A')!s:5}"
                )
                for k, v in counts.items():
                    totals[k] += v
            else:
                print(f"{target}: No data")

        print("-" * 70)
        tp, fp, fn, tn = totals["true_positive"], totals["false_positive"], totals["false_negative"], totals["true_negative"]
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
        p_str = f"{precision:.3f}" if precision is not None else "N/A"
        r_str = f"{recall:.3f}" if recall is not None else "N/A"
        f1_str = f"{f1:.3f}" if f1 is not None else "N/A"
        print(
            f"TOTAL:     TP={tp:3} FP={fp:3} FN={fn:3} TN={tn:3} | "
            f"P={p_str:5} R={r_str:5} F1={f1_str:5}"
        )
    else:
        target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
        result = await job.run(target)

        if result:
            print(f"\n{'='*70}")
            print(f"TRADE CLASSIFICATION: {result['date']}")
            print(f"{'='*70}")

            counts = result.get("counts", {})
            print(f"\n✅ True Positives:  {counts.get('true_positive', 0):3}  (profitable trades)")
            print(f"❌ False Positives: {counts.get('false_positive', 0):3}  (losing trades)")
            print(f"⚠️  False Negatives: {counts.get('false_negative', 0):3}  (missed winners)")
            print(f"✓  True Negatives:  {counts.get('true_negative', 0):3}  (correctly ignored)")

            print(f"\n📊 Metrics:")
            print(f"   Precision: {result.get('precision', 'N/A')}")
            print(f"   Recall:    {result.get('recall', 'N/A')}")
            print(f"   F1 Score:  {result.get('f1_score', 'N/A')}")

            print(f"\n📁 Files:")
            for category, path in result.get("files", {}).items():
                print(f"   {category}: {path}")
            print(f"   summary: {result.get('summary_file')}")

            if args.verbose:
                # Print the category files
                for category in ["true_positive", "false_positive", "false_negative", "true_negative"]:
                    path = result.get("files", {}).get(category)
                    if path:
                        print(f"\n{'='*70}")
                        with open(path) as f:
                            print(f.read())
        else:
            print("No data found")


if __name__ == "__main__":
    asyncio.run(main())
