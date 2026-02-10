#!/usr/bin/env python3
"""
Statistical pattern analysis for threshold tuning.

Analyzes labeled trade data to find optimal thresholds and segment performance
BEFORE training ML models. Uses simple conditional probability analysis.

Usage:
    python scripts/analyze_patterns.py                    # Analyze all data
    python scripts/analyze_patterns.py --min-samples 10   # Require 10+ samples per bucket
    python scripts/analyze_patterns.py --feature market_cap_millions  # Focus on one feature
"""
import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@dataclass
class SegmentStats:
    """Statistics for a segment."""
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def total_trades(self) -> int:
        return self.tp + self.fp

    @property
    def total_opportunities(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def precision(self) -> Optional[float]:
        if self.total_trades == 0:
            return None
        return self.tp / self.total_trades

    @property
    def recall(self) -> Optional[float]:
        if (self.tp + self.fn) == 0:
            return None
        return self.tp / (self.tp + self.fn)

    @property
    def win_rate_str(self) -> str:
        if self.precision is None:
            return "N/A"
        return f"{self.precision * 100:.0f}%"


def load_training_data(data_path: Path) -> List[Dict]:
    """Load all training samples from combined or weekly data."""
    samples = []

    # Try combined first
    combined_file = data_path / "combined_training_data.json"
    if combined_file.exists():
        with open(combined_file) as f:
            data = json.load(f)
        samples.extend(data.get("samples", []))
        print(f"Loaded {len(samples)} samples from combined_training_data.json")
        return samples

    # Fall back to weekly files
    weekly_path = data_path / "weekly"
    if weekly_path.exists():
        for week_dir in sorted(weekly_path.iterdir()):
            if week_dir.is_dir():
                training_file = week_dir / "training_data.json"
                if training_file.exists():
                    with open(training_file) as f:
                        data = json.load(f)
                    week_samples = data.get("samples", [])
                    samples.extend(week_samples)
                    print(f"Loaded {len(week_samples)} samples from {week_dir.name}")

    print(f"Total samples: {len(samples)}")
    return samples


def categorize_value(value: Any, buckets: List[Tuple[float, str]]) -> str:
    """Categorize a numeric value into buckets."""
    if value is None:
        return "unknown"

    try:
        v = float(value)
        for threshold, label in buckets:
            if v < threshold:
                return label
        return buckets[-1][1] if buckets else "unknown"
    except (TypeError, ValueError):
        return str(value)[:20]


def analyze_categorical(samples: List[Dict], field: str, min_samples: int) -> Dict[str, SegmentStats]:
    """Analyze performance by categorical field."""
    stats = defaultdict(SegmentStats)

    for sample in samples:
        value = sample.get(field)
        if value is None:
            value = "unknown"
        else:
            value = str(value)[:30]

        category = sample.get("category", "")
        if category == "true_positive":
            stats[value].tp += 1
        elif category == "false_positive":
            stats[value].fp += 1
        elif category == "false_negative":
            stats[value].fn += 1
        elif category == "true_negative":
            stats[value].tn += 1

    # Filter by min samples
    return {k: v for k, v in stats.items() if v.total_trades >= min_samples or v.fn >= min_samples}


def analyze_numeric(
    samples: List[Dict],
    field: str,
    buckets: List[Tuple[float, str]],
    min_samples: int
) -> Dict[str, SegmentStats]:
    """Analyze performance by numeric field with buckets."""
    stats = defaultdict(SegmentStats)

    for sample in samples:
        value = sample.get(field)
        bucket = categorize_value(value, buckets)

        category = sample.get("category", "")
        if category == "true_positive":
            stats[bucket].tp += 1
        elif category == "false_positive":
            stats[bucket].fp += 1
        elif category == "false_negative":
            stats[bucket].fn += 1
        elif category == "true_negative":
            stats[bucket].tn += 1

    return {k: v for k, v in stats.items() if v.total_trades >= min_samples or v.fn >= min_samples}


def print_segment_analysis(title: str, stats: Dict[str, SegmentStats], sort_by_precision: bool = True):
    """Print segment analysis table."""
    if not stats:
        print(f"\n{title}: No data\n")
        return

    print(f"\n{'='*80}")
    print(f"{title}")
    print(f"{'='*80}")
    print(f"{'Segment':<25} {'TP':>5} {'FP':>5} {'FN':>5} {'Precision':>10} {'Recall':>10} {'Recommendation'}")
    print("-" * 80)

    # Sort by precision (best first)
    items = list(stats.items())
    if sort_by_precision:
        items.sort(key=lambda x: (x[1].precision or 0), reverse=True)

    best_precision = max((s.precision or 0) for s in stats.values())

    for segment, s in items:
        prec_str = f"{s.precision * 100:.0f}%" if s.precision is not None else "N/A"
        recall_str = f"{s.recall * 100:.0f}%" if s.recall is not None else "N/A"

        # Recommendation
        rec = ""
        if s.precision is not None:
            if s.precision >= 0.6 and s.total_trades >= 3:
                rec = "TRADE"
            elif s.precision <= 0.3 and s.total_trades >= 3:
                rec = "AVOID"
            elif s.precision == best_precision and s.total_trades >= 2:
                rec = "BEST"

        print(f"{segment:<25} {s.tp:>5} {s.fp:>5} {s.fn:>5} {prec_str:>10} {recall_str:>10} {rec:>15}")

    print()


def analyze_threshold(
    samples: List[Dict],
    field: str,
    thresholds: List[float],
    min_samples: int
) -> None:
    """Find optimal threshold for a numeric feature."""
    print(f"\n{'='*80}")
    print(f"THRESHOLD ANALYSIS: {field}")
    print(f"{'='*80}")
    print(f"{'Threshold':<15} {'TP':>5} {'FP':>5} {'Precision':>10} {'Samples':>10} {'Δ vs All':>10}")
    print("-" * 80)

    # Baseline (all trades)
    all_tp = sum(1 for s in samples if s.get("category") == "true_positive")
    all_fp = sum(1 for s in samples if s.get("category") == "false_positive")
    all_precision = all_tp / (all_tp + all_fp) if (all_tp + all_fp) > 0 else 0

    print(f"{'(all trades)':<15} {all_tp:>5} {all_fp:>5} {all_precision*100:>9.0f}% {all_tp+all_fp:>10} {'baseline':>10}")
    print("-" * 80)

    best_threshold = None
    best_improvement = 0

    for threshold in thresholds:
        # Filter samples where field >= threshold
        filtered = [s for s in samples if s.get(field) is not None and float(s.get(field, 0)) >= threshold]

        tp = sum(1 for s in filtered if s.get("category") == "true_positive")
        fp = sum(1 for s in filtered if s.get("category") == "false_positive")

        if tp + fp < min_samples:
            continue

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        improvement = precision - all_precision

        prec_str = f"{precision * 100:.0f}%"
        imp_str = f"{improvement * 100:+.0f}%" if improvement != 0 else "0%"

        if improvement > best_improvement:
            best_improvement = improvement
            best_threshold = threshold

        marker = " ← BEST" if improvement == best_improvement and improvement > 0 else ""
        print(f">= {threshold:<12} {tp:>5} {fp:>5} {prec_str:>10} {tp+fp:>10} {imp_str:>10}{marker}")

    if best_threshold and best_improvement > 0.05:
        print(f"\n→ RECOMMENDATION: Set {field} >= {best_threshold} (+{best_improvement*100:.0f}% precision)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Analyze patterns for threshold tuning")
    parser.add_argument("--min-samples", type=int, default=3, help="Min samples per bucket")
    parser.add_argument("--feature", type=str, help="Focus on specific feature")
    parser.add_argument("--data-path", type=str, default="tmp/trade_classification",
                        help="Path to classification data")

    args = parser.parse_args()

    data_path = Path(args.data_path)
    samples = load_training_data(data_path)

    if not samples:
        print("\nNo training data found. Run classification first.")
        return

    # Count categories
    categories = defaultdict(int)
    for s in samples:
        categories[s.get("category", "unknown")] += 1

    print(f"\n{'='*80}")
    print("OVERALL STATISTICS")
    print(f"{'='*80}")
    print(f"True Positives:  {categories['true_positive']:>5}")
    print(f"False Positives: {categories['false_positive']:>5}")
    print(f"False Negatives: {categories['false_negative']:>5}")
    print(f"True Negatives:  {categories['true_negative']:>5}")

    tp, fp = categories['true_positive'], categories['false_positive']
    fn = categories['false_negative']
    if tp + fp > 0:
        print(f"\nCurrent Precision: {tp/(tp+fp)*100:.1f}%")
    if tp + fn > 0:
        print(f"Current Recall: {tp/(tp+fn)*100:.1f}%")

    min_samples = args.min_samples

    # Industry analysis
    if not args.feature or args.feature == "industry":
        stats = analyze_categorical(samples, "industry", min_samples)
        print_segment_analysis("INDUSTRY ANALYSIS", stats)

    # Sector analysis
    if not args.feature or args.feature == "sector":
        stats = analyze_categorical(samples, "sector", min_samples)
        print_segment_analysis("SECTOR ANALYSIS", stats)

    # Headline type analysis
    if not args.feature or args.feature == "headline_type":
        stats = analyze_categorical(samples, "headline_type", min_samples)
        print_segment_analysis("HEADLINE TYPE ANALYSIS", stats)

    # Market cap analysis
    if not args.feature or args.feature == "market_cap_millions":
        buckets = [
            (25, "<$25M"),
            (50, "$25-50M"),
            (100, "$50-100M"),
            (200, "$100-200M"),
            (500, "$200-500M"),
            (float('inf'), ">$500M"),
        ]
        stats = analyze_numeric(samples, "market_cap_millions", buckets, min_samples)
        print_segment_analysis("MARKET CAP ANALYSIS", stats)

    # Confluence score analysis
    if not args.feature or args.feature == "confluence_score":
        buckets = [
            (3, "score 0-2"),
            (5, "score 3-4"),
            (7, "score 5-6"),
            (float('inf'), "score 7+"),
        ]
        stats = analyze_numeric(samples, "confluence_score", buckets, min_samples)
        print_segment_analysis("CONFLUENCE SCORE ANALYSIS", stats)

    # Volume ratio analysis
    if not args.feature or args.feature == "volume_ratio":
        buckets = [
            (2, "< 2x"),
            (5, "2-5x"),
            (10, "5-10x"),
            (20, "10-20x"),
            (float('inf'), "> 20x"),
        ]
        stats = analyze_numeric(samples, "volume_ratio", buckets, min_samples)
        print_segment_analysis("VOLUME RATIO ANALYSIS", stats)

    # Pressure consistency analysis
    if not args.feature or args.feature == "pressure_consistent":
        stats = analyze_categorical(samples, "pressure_consistent", min_samples)
        print_segment_analysis("PRESSURE CONSISTENCY ANALYSIS", stats)

    # Threshold optimization examples
    if not args.feature or args.feature == "thresholds":
        # Market cap threshold
        analyze_threshold(
            samples, "market_cap_millions",
            [10, 25, 50, 100, 200, 300, 500],
            min_samples
        )

        # Volume ratio threshold
        analyze_threshold(
            samples, "volume_ratio",
            [1, 2, 3, 5, 10, 15, 20],
            min_samples
        )

        # Confluence score threshold
        analyze_threshold(
            samples, "confluence_score",
            [2, 3, 4, 5, 6, 7, 8],
            min_samples
        )

    print("\n" + "="*80)
    print("INTERPRETATION GUIDE")
    print("="*80)
    print("""
- Precision = TP / (TP + FP) = What % of trades we made were winners?
- Recall = TP / (TP + FN) = What % of winners did we catch?

- TRADE: Segment with >= 60% precision and enough samples
- AVOID: Segment with <= 30% precision - consider filtering out
- BEST: Highest precision segment

THRESHOLD RECOMMENDATIONS:
- If a threshold shows +10% precision improvement, consider adding as filter
- Balance precision gain vs sample reduction (don't over-filter)

RUN WEEKLY to see patterns emerge as data accumulates.
""")


if __name__ == "__main__":
    main()
