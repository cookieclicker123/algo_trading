#!/usr/bin/env python3
"""
Combine weekly training data into a cumulative dataset for ML training.

Maintains persistent state in combined_state.json - only processes NEW weeks
that haven't been added yet. First run processes everything, subsequent runs
are incremental.

Usage:
    python scripts/combine_training_data.py                     # Process new weeks only
    python scripts/combine_training_data.py --rebuild           # Rebuild from scratch
    python scripts/combine_training_data.py --status            # Show current state
    python scripts/combine_training_data.py --weeks 4           # Only use last 4 weeks (rebuilds)
    python scripts/combine_training_data.py --from 2026-01-06   # From date (rebuilds)
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Paths
CLASSIFICATION_PATH = Path("tmp/trade_classification")
WEEKLY_PATH = CLASSIFICATION_PATH / "weekly"
STATE_FILE = CLASSIFICATION_PATH / "combined_state.json"
OUTPUT_FILE = CLASSIFICATION_PATH / "combined_training_data.json"


def load_state() -> Dict[str, Any]:
    """Load persistent state or return empty state."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load state file: {e}")

    return {
        "created_at": datetime.now().isoformat(),
        "last_updated": None,
        "weeks_processed": [],
        "cumulative_totals": {
            "true_positive": 0,
            "false_positive": 0,
            "false_negative": 0,
            "true_negative": 0,
        },
        "cumulative_samples": [],
    }


def save_state(state: Dict[str, Any]) -> None:
    """Save persistent state."""
    CLASSIFICATION_PATH.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now().isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def find_all_weeks() -> List[str]:
    """Find all available weekly directories."""
    if not WEEKLY_PATH.exists():
        return []

    weeks = []
    for d in WEEKLY_PATH.iterdir():
        if d.is_dir() and "_week_" in d.name:
            # Verify it has training data
            if (d / "training_data.json").exists():
                weeks.append(d.name)

    # Sort by year and week number
    def sort_key(w: str) -> tuple:
        parts = w.split("_week_")
        return (int(parts[0]), int(parts[1]))

    return sorted(weeks, key=sort_key)


def load_week_data(week: str) -> Optional[Dict]:
    """Load training data for a week."""
    training_file = WEEKLY_PATH / week / "training_data.json"
    stats_file = WEEKLY_PATH / week / "aggregated_stats.json"

    if not training_file.exists():
        return None

    try:
        with open(training_file) as f:
            training = json.load(f)

        stats = {}
        if stats_file.exists():
            with open(stats_file) as f:
                stats = json.load(f)

        return {
            "week": week,
            "samples": training.get("samples", []),
            "totals": stats.get("totals", {}),
            "start_date": stats.get("start_date"),
            "end_date": stats.get("end_date"),
        }
    except Exception as e:
        print(f"Error loading {week}: {e}")
        return None


def calculate_metrics(totals: Dict) -> Dict:
    """Calculate precision, recall, F1 from totals."""
    tp = totals.get("true_positive", 0)
    fp = totals.get("false_positive", 0)
    fn = totals.get("false_negative", 0)

    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall and (precision + recall) > 0 else None

    return {
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1_score": round(f1, 4) if f1 is not None else None,
    }


def write_combined_output(state: Dict) -> None:
    """Write the combined training data file."""
    totals = state["cumulative_totals"]
    samples = state["cumulative_samples"]

    positive_count = sum(1 for s in samples if s.get("label") == 1)
    negative_count = sum(1 for s in samples if s.get("label") == 0)

    output = {
        "generated_at": datetime.now().isoformat(),
        "weeks_included": state["weeks_processed"],
        "week_count": len(state["weeks_processed"]),
        "date_range": {
            "first_week": state["weeks_processed"][0] if state["weeks_processed"] else None,
            "last_week": state["weeks_processed"][-1] if state["weeks_processed"] else None,
        },
        "totals": totals,
        "metrics": calculate_metrics(totals),
        "label_definition": {
            "positive": "+10% peak with <5% MAE (should trade)",
            "negative": "Did not meet positive criteria (should not trade)",
        },
        "sample_count": {
            "total": len(samples),
            "positive": positive_count,
            "negative": negative_count,
        },
        "samples": samples,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)


def print_status(state: Dict) -> None:
    """Print current state summary."""
    totals = state["cumulative_totals"]
    metrics = calculate_metrics(totals)
    samples = state["cumulative_samples"]

    positive_count = sum(1 for s in samples if s.get("label") == 1)
    negative_count = sum(1 for s in samples if s.get("label") == 0)

    print(f"\n{'='*70}")
    print(f"COMBINED TRAINING DATA STATE")
    print(f"{'='*70}")

    print(f"\n📅 State file: {STATE_FILE}")
    print(f"   Created: {state.get('created_at', 'N/A')}")
    print(f"   Updated: {state.get('last_updated', 'Never')}")

    print(f"\n📊 Weeks processed ({len(state['weeks_processed'])}):")
    if state["weeks_processed"]:
        # Show first and last few
        weeks = state["weeks_processed"]
        if len(weeks) <= 6:
            for w in weeks:
                print(f"   - {w}")
        else:
            for w in weeks[:3]:
                print(f"   - {w}")
            print(f"   ... ({len(weeks) - 6} more)")
            for w in weeks[-3:]:
                print(f"   - {w}")
    else:
        print("   (none)")

    print(f"\n📊 Confusion Matrix (cumulative):")
    print(f"   ✅ True Positives:  {totals.get('true_positive', 0):5}")
    print(f"   ❌ False Positives: {totals.get('false_positive', 0):5}")
    print(f"   ⚠️  False Negatives: {totals.get('false_negative', 0):5}")
    print(f"   ✓  True Negatives:  {totals.get('true_negative', 0):5}")

    p_str = f"{metrics['precision']:.4f}" if metrics['precision'] is not None else "N/A"
    r_str = f"{metrics['recall']:.4f}" if metrics['recall'] is not None else "N/A"
    f1_str = f"{metrics['f1_score']:.4f}" if metrics['f1_score'] is not None else "N/A"

    print(f"\n📈 Aggregate Metrics:")
    print(f"   Precision: {p_str}")
    print(f"   Recall:    {r_str}")
    print(f"   F1 Score:  {f1_str}")

    print(f"\n🤖 ML Training Data:")
    print(f"   Total samples:      {len(samples)}")
    print(f"   Positive (label=1): {positive_count}")
    print(f"   Negative (label=0): {negative_count}")
    if positive_count > 0:
        print(f"   Class ratio:        1:{negative_count/positive_count:.1f} (pos:neg)")

    # Check for new weeks available
    all_weeks = set(find_all_weeks())
    processed = set(state["weeks_processed"])
    new_weeks = all_weeks - processed

    if new_weeks:
        print(f"\n🆕 New weeks available ({len(new_weeks)}):")
        for w in sorted(new_weeks):
            print(f"   - {w}")
        print(f"\n   Run without --status to process these.")
    else:
        print(f"\n✅ All available weeks are processed.")


def main():
    parser = argparse.ArgumentParser(description="Combine weekly training data for ML")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild from scratch (ignore existing state)")
    parser.add_argument("--status", action="store_true",
                        help="Show current state without processing")
    parser.add_argument("--weeks", type=int,
                        help="Only use last N weeks (implies --rebuild)")
    parser.add_argument("--from", dest="from_date",
                        help="Only use weeks from this date (implies --rebuild)")

    args = parser.parse_args()

    # Load or initialize state
    if args.rebuild or args.weeks or args.from_date:
        state = load_state()
        state["weeks_processed"] = []
        state["cumulative_totals"] = {
            "true_positive": 0, "false_positive": 0,
            "false_negative": 0, "true_negative": 0,
        }
        state["cumulative_samples"] = []
    else:
        state = load_state()

    # Get all available weeks
    all_weeks = find_all_weeks()

    if not all_weeks:
        print("No weekly aggregation data found.")
        print("Run daily classification first, or wait for Friday's automatic aggregation.")
        return

    # Filter weeks if needed
    if args.weeks:
        all_weeks = all_weeks[-args.weeks:]
    elif args.from_date:
        from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        filtered = []
        for w in all_weeks:
            # Parse week to get its start date
            parts = w.split("_week_")
            year, week_num = int(parts[0]), int(parts[1])
            # ISO week calculation
            jan4 = date(year, 1, 4)
            week1_monday = jan4 - timedelta(days=jan4.weekday())
            monday = week1_monday + timedelta(weeks=week_num - 1)
            if monday >= from_date:
                filtered.append(w)
        all_weeks = filtered

    if args.status:
        print_status(state)
        return

    # Find new weeks to process
    processed_set = set(state["weeks_processed"])
    new_weeks = [w for w in all_weeks if w not in processed_set]

    if not new_weeks:
        print("No new weeks to process.")
        print_status(state)
        return

    print(f"\n{'='*70}")
    print(f"PROCESSING NEW WEEKS")
    print(f"{'='*70}")
    print(f"\n🆕 New weeks to process: {len(new_weeks)}")

    for week in new_weeks:
        data = load_week_data(week)
        if data:
            # Add samples
            state["cumulative_samples"].extend(data["samples"])

            # Update totals
            for k in state["cumulative_totals"]:
                state["cumulative_totals"][k] += data["totals"].get(k, 0)

            # Mark as processed
            state["weeks_processed"].append(week)
            state["weeks_processed"].sort(key=lambda w: (int(w.split("_week_")[0]), int(w.split("_week_")[1])))

            tp = data["totals"].get("true_positive", 0)
            fp = data["totals"].get("false_positive", 0)
            fn = data["totals"].get("false_negative", 0)
            tn = data["totals"].get("true_negative", 0)
            samples = len(data["samples"])

            print(f"   ✅ {week}: TP={tp} FP={fp} FN={fn} TN={tn} ({samples} samples)")
        else:
            print(f"   ⚠️  {week}: Failed to load")

    # Save updated state
    save_state(state)

    # Write combined output file
    write_combined_output(state)

    # Print summary
    print_status(state)

    print(f"\n📁 Files updated:")
    print(f"   State:    {STATE_FILE}")
    print(f"   Training: {OUTPUT_FILE}")
    if OUTPUT_FILE.exists():
        print(f"   Size:     {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
