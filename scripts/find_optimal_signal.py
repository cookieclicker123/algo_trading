#!/usr/bin/env python3
"""
Find optimal trading signal by searching filter combinations.

Searches through combinations of feature thresholds to find the highest
precision signal per industry (or overall). This is the step before ML -
finding rule-based filters that maximize precision while maintaining recall.

Usage:
    python scripts/find_optimal_signal.py                     # Find best overall signal
    python scripts/find_optimal_signal.py --by-industry       # Find best signal per industry
    python scripts/find_optimal_signal.py --min-precision 0.6 # Require 60%+ precision
    python scripts/find_optimal_signal.py --min-trades 5      # Require 5+ trades per signal
"""
import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@dataclass
class Signal:
    """A trading signal defined by filter conditions."""
    filters: Dict[str, Any]
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def total_trades(self) -> int:
        return self.tp + self.fp

    @property
    def precision(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.tp / self.total_trades

    @property
    def recall(self) -> float:
        if (self.tp + self.fn) == 0:
            return 0.0
        return self.tp / (self.tp + self.fn)

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    def filter_description(self) -> str:
        parts = []
        for k, v in self.filters.items():
            if isinstance(v, tuple):
                op, val = v
                parts.append(f"{k} {op} {val}")
            else:
                parts.append(f"{k}={v}")
        return " AND ".join(parts) if parts else "(no filters)"


def load_training_data(data_path: Path) -> List[Dict]:
    """Load all training samples."""
    samples = []

    combined_file = data_path / "combined_training_data.json"
    if combined_file.exists():
        with open(combined_file) as f:
            data = json.load(f)
        return data.get("samples", [])

    weekly_path = data_path / "weekly"
    if weekly_path.exists():
        for week_dir in sorted(weekly_path.iterdir()):
            if week_dir.is_dir():
                training_file = week_dir / "training_data.json"
                if training_file.exists():
                    with open(training_file) as f:
                        data = json.load(f)
                    samples.extend(data.get("samples", []))

    return samples


def matches_filter(sample: Dict, filters: Dict[str, Any]) -> bool:
    """Check if a sample matches all filter conditions."""
    for field, condition in filters.items():
        value = sample.get(field)

        if isinstance(condition, tuple):
            op, threshold = condition
            if value is None:
                return False
            try:
                v = float(value)
                if op == ">=" and v < threshold:
                    return False
                elif op == "<=" and v > threshold:
                    return False
                elif op == ">" and v <= threshold:
                    return False
                elif op == "<" and v >= threshold:
                    return False
            except (TypeError, ValueError):
                return False
        else:
            # Exact match
            if value != condition:
                return False

    return True


def evaluate_signal(samples: List[Dict], filters: Dict[str, Any]) -> Signal:
    """
    Evaluate a signal's performance on samples.

    Recall = TP / (TP + FN) where FN includes:
    1. Original TP that our filter would reject (filtered out a winner)
    2. ALL original FN (winners we already missed, regardless of filter)

    This ensures filters are penalized for:
    - Letting losers through (hurts precision)
    - Filtering out winners (hurts recall)
    - Not catching existing missed opportunities (hurts recall)
    """
    signal = Signal(filters=filters)

    for sample in samples:
        category = sample.get("category", "")

        if category == "true_positive":
            if matches_filter(sample, filters):
                signal.tp += 1  # Winner we would catch
            else:
                signal.fn += 1  # Winner we would filter out - BAD

        elif category == "false_positive":
            if matches_filter(sample, filters):
                signal.fp += 1  # Loser we would trade - BAD
            # If filtered out, good - we avoided a loser

        elif category == "false_negative":
            # These are winners we ALREADY missed (10%+ peak, IMMINENT, but didn't trade)
            # They ALL count as FN - we failed to catch these winners
            # Whether our filter matches or not, we still missed them
            signal.fn += 1

    return signal


def generate_filter_combinations(
    include_market_cap: bool = True,
    include_volume_ratio: bool = True,
    include_confluence: bool = True,
    include_pressure: bool = True,
) -> List[Dict[str, Any]]:
    """Generate filter combinations to test."""
    combinations = []

    # Base: no filters
    combinations.append({})

    # Market cap thresholds
    market_cap_options = [None]
    if include_market_cap:
        market_cap_options.extend([
            ("<=", 50),
            ("<=", 100),
            ("<=", 200),
            ("<=", 500),
        ])

    # Volume ratio thresholds
    volume_ratio_options = [None]
    if include_volume_ratio:
        volume_ratio_options.extend([
            (">=", 2),
            (">=", 3),
            (">=", 5),
            (">=", 10),
        ])

    # Confluence score thresholds
    confluence_options = [None]
    if include_confluence:
        confluence_options.extend([
            (">=", 3),
            (">=", 4),
            (">=", 5),
            (">=", 6),
        ])

    # Pressure consistency
    pressure_options = [None]
    if include_pressure:
        pressure_options.extend([True])

    # Generate all combinations
    for mc, vr, cs, pc in product(
        market_cap_options,
        volume_ratio_options,
        confluence_options,
        pressure_options
    ):
        filters = {}
        if mc is not None:
            filters["market_cap_millions"] = mc
        if vr is not None:
            filters["volume_ratio"] = vr
        if cs is not None:
            filters["confluence_score"] = cs
        if pc is not None:
            filters["pressure_consistent"] = pc

        if filters:  # Skip empty (already added)
            combinations.append(filters)

    return combinations


def find_best_signals(
    samples: List[Dict],
    min_precision: float = 0.5,
    min_trades: int = 3,
    top_n: int = 10,
) -> List[Signal]:
    """Find the best signals from all combinations."""
    combinations = generate_filter_combinations()

    signals = []
    for filters in combinations:
        signal = evaluate_signal(samples, filters)
        if signal.total_trades >= min_trades and signal.precision >= min_precision:
            signals.append(signal)

    # Sort by F1 score (balances precision and recall)
    signals.sort(key=lambda s: s.f1, reverse=True)

    return signals[:top_n]


def find_best_signals_by_industry(
    samples: List[Dict],
    min_precision: float = 0.5,
    min_trades: int = 3,
) -> Dict[str, Signal]:
    """Find the best signal for each industry."""
    # Group samples by industry
    by_industry = defaultdict(list)
    for sample in samples:
        industry = sample.get("industry") or "unknown"
        by_industry[industry].append(sample)

    best_by_industry = {}

    for industry, industry_samples in by_industry.items():
        # Count TP+FP to see if this industry has enough trades
        trades = sum(1 for s in industry_samples if s.get("category") in ["true_positive", "false_positive"])
        if trades < min_trades:
            continue

        combinations = generate_filter_combinations()

        best_signal = None
        best_f1 = 0

        for filters in combinations:
            signal = evaluate_signal(industry_samples, filters)
            if signal.total_trades >= min_trades and signal.precision >= min_precision:
                if signal.f1 > best_f1:
                    best_f1 = signal.f1
                    best_signal = signal

        if best_signal:
            best_by_industry[industry] = best_signal

    return best_by_industry


def print_signal_table(signals: List[Signal], title: str):
    """Print a table of signals."""
    if not signals:
        print(f"\n{title}: No signals found matching criteria\n")
        return

    print(f"\n{'='*100}")
    print(f"{title}")
    print(f"{'='*100}")
    print(f"{'Rank':<5} {'TP':>4} {'FP':>4} {'FN':>4} {'Prec':>7} {'Recall':>7} {'F1':>7} Filter")
    print("-" * 100)

    for i, signal in enumerate(signals, 1):
        print(
            f"{i:<5} "
            f"{signal.tp:>4} "
            f"{signal.fp:>4} "
            f"{signal.fn:>4} "
            f"{signal.precision*100:>6.0f}% "
            f"{signal.recall*100:>6.0f}% "
            f"{signal.f1*100:>6.0f}% "
            f"{signal.filter_description()}"
        )

    print()


def export_optimal_rules(signals_by_industry: Dict[str, Signal], output_file: Path):
    """Export optimal rules to JSON for use in trading system."""
    rules = {}

    for industry, signal in signals_by_industry.items():
        rules[industry] = {
            "filters": {
                k: list(v) if isinstance(v, tuple) else v
                for k, v in signal.filters.items()
            },
            "expected_precision": round(signal.precision, 3),
            "expected_recall": round(signal.recall, 3),
            "sample_size": signal.total_trades,
        }

    with open(output_file, "w") as f:
        json.dump({
            "generated_at": __import__("datetime").datetime.now().isoformat(),
            "description": "Optimal trading filters by industry",
            "rules": rules,
        }, f, indent=2)

    print(f"\nExported rules to: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Find optimal trading signal")
    parser.add_argument("--by-industry", action="store_true", help="Find best signal per industry")
    parser.add_argument("--min-precision", type=float, default=0.5, help="Minimum precision required")
    parser.add_argument("--min-trades", type=int, default=3, help="Minimum trades required")
    parser.add_argument("--top", type=int, default=10, help="Show top N signals")
    parser.add_argument("--export", type=str, help="Export rules to JSON file")
    parser.add_argument("--data-path", type=str, default="tmp/trade_classification")

    args = parser.parse_args()

    data_path = Path(args.data_path)
    samples = load_training_data(data_path)

    if not samples:
        print("\nNo training data found. Run classification first.")
        return

    # Overall stats
    categories = defaultdict(int)
    for s in samples:
        categories[s.get("category", "unknown")] += 1

    tp, fp, fn = categories["true_positive"], categories["false_positive"], categories["false_negative"]

    print(f"\n{'='*100}")
    print("CURRENT PERFORMANCE (NO FILTERS)")
    print(f"{'='*100}")
    print(f"TP: {tp}  FP: {fp}  FN: {fn}")
    if tp + fp > 0:
        print(f"Precision: {tp/(tp+fp)*100:.1f}%")
    if tp + fn > 0:
        print(f"Recall: {tp/(tp+fn)*100:.1f}%")

    if args.by_industry:
        # Find best signal per industry
        best_by_industry = find_best_signals_by_industry(
            samples,
            min_precision=args.min_precision,
            min_trades=args.min_trades,
        )

        print(f"\n{'='*100}")
        print("OPTIMAL SIGNAL BY INDUSTRY")
        print(f"{'='*100}")
        print(f"{'Industry':<30} {'TP':>4} {'FP':>4} {'Prec':>7} {'Recall':>7} {'F1':>7} Best Filter")
        print("-" * 100)

        for industry in sorted(best_by_industry.keys()):
            signal = best_by_industry[industry]
            print(
                f"{industry[:29]:<30} "
                f"{signal.tp:>4} "
                f"{signal.fp:>4} "
                f"{signal.precision*100:>6.0f}% "
                f"{signal.recall*100:>6.0f}% "
                f"{signal.f1*100:>6.0f}% "
                f"{signal.filter_description()}"
            )

        if args.export:
            export_optimal_rules(best_by_industry, Path(args.export))

    else:
        # Find best overall signals
        best_signals = find_best_signals(
            samples,
            min_precision=args.min_precision,
            min_trades=args.min_trades,
            top_n=args.top,
        )

        print_signal_table(best_signals, f"TOP {args.top} SIGNALS (min precision: {args.min_precision*100:.0f}%, min trades: {args.min_trades})")

        if best_signals:
            best = best_signals[0]
            print(f"{'='*100}")
            print("RECOMMENDED SIGNAL")
            print(f"{'='*100}")
            print(f"\n  {best.filter_description()}\n")
            print(f"  Expected Precision: {best.precision*100:.0f}%")
            print(f"  Expected Recall: {best.recall*100:.0f}%")
            print(f"  F1 Score: {best.f1*100:.0f}%")
            print(f"  Based on: {best.total_trades} trades\n")

    print(f"\n{'='*100}")
    print("NEXT STEPS")
    print(f"{'='*100}")
    print("""
1. Run weekly as more data accumulates
2. When you find a signal with 60%+ precision and 20+ trades, it's statistically significant
3. Use --by-industry to find industry-specific thresholds
4. Export rules with --export optimal_rules.json to use in trading system
5. Re-run monthly to adapt to changing market conditions
""")


if __name__ == "__main__":
    main()
