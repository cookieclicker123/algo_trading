#!/usr/bin/env python3
"""
Analyze trade exit timing data to find optimal exit patterns.

This script processes TradeAnalyticsEngine records to answer:
1. What tape characteristics predict "hold longer" vs "exit now"?
2. How does exit timing quality vary by industry, market cap, headline type?
3. What does "degrading tape" look like before price reversal?

Usage:
    python scripts/analyze_exit_timing.py
    python scripts/analyze_exit_timing.py --min-trades 10 --industry Biotechnology
"""

import argparse
import json
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Optional
import statistics


@dataclass
class ExitAnalysis:
    """Summary statistics for a segment."""
    segment_name: str
    trade_count: int
    avg_exit_profit_pct: float
    avg_peak_profit_pct: float
    avg_money_left_on_table: float
    pct_too_early: float  # exited and price continued higher
    pct_optimal: float    # exited near peak
    pct_late: float       # exited well below peak

    # Tape characteristics at exit for "good" vs "bad" exits
    good_exit_avg_imbalance: float
    bad_exit_avg_imbalance: float
    good_exit_avg_volume_rate: float
    bad_exit_avg_volume_rate: float


def load_records(base_path: Path) -> List[dict]:
    """Load all trade analytics records."""
    records = []
    for json_file in base_path.rglob("*.json"):
        try:
            with open(json_file) as f:
                records.append(json.load(f))
        except Exception as e:
            print(f"Error loading {json_file}: {e}")
    return records


def analyze_by_segment(records: List[dict], segment_key: str) -> Dict[str, ExitAnalysis]:
    """Analyze exit timing grouped by a segment (industry, market_cap_bucket, headline_type)."""
    segments = defaultdict(list)

    for r in records:
        if segment_key == "market_cap_bucket":
            cap = r.get("market_cap_millions", 0)
            if cap < 10:
                key = "Nano (<$10M)"
            elif cap < 50:
                key = "Micro ($10-50M)"
            elif cap < 200:
                key = "Small ($50-200M)"
            elif cap < 1000:
                key = "Mid ($200M-1B)"
            else:
                key = "Large (>$1B)"
        else:
            key = r.get(segment_key, "Unknown")

        segments[key].append(r)

    results = {}
    for segment_name, segment_records in segments.items():
        if len(segment_records) < 2:
            continue

        exit_profits = [r.get("exit_profit_pct", 0) for r in segment_records]
        peak_profits = [r.get("peak_profit_pct", 0) for r in segment_records]
        money_left = [r.get("money_left_on_table_pct", 0) for r in segment_records]

        # Exit timing quality distribution
        qualities = [r.get("exit_timing_quality", "") for r in segment_records]
        total = len(qualities)
        pct_too_early = qualities.count("too_early") / total * 100 if total > 0 else 0
        pct_optimal = (qualities.count("optimal") + qualities.count("good")) / total * 100 if total > 0 else 0
        pct_late = (qualities.count("late") + qualities.count("very_late")) / total * 100 if total > 0 else 0

        # Separate good vs bad exits for tape analysis
        good_exits = [r for r in segment_records if r.get("exit_timing_quality") in ["optimal", "good"]]
        bad_exits = [r for r in segment_records if r.get("exit_timing_quality") in ["late", "very_late", "too_early"]]

        def get_tape_at_exit_stat(records: List[dict], stat_key: str) -> float:
            values = []
            for r in records:
                tape = r.get("tape_at_exit")
                if tape and stat_key in tape:
                    values.append(tape[stat_key])
            return statistics.mean(values) if values else 0

        results[segment_name] = ExitAnalysis(
            segment_name=segment_name,
            trade_count=len(segment_records),
            avg_exit_profit_pct=statistics.mean(exit_profits) if exit_profits else 0,
            avg_peak_profit_pct=statistics.mean(peak_profits) if peak_profits else 0,
            avg_money_left_on_table=statistics.mean(money_left) if money_left else 0,
            pct_too_early=pct_too_early,
            pct_optimal=pct_optimal,
            pct_late=pct_late,
            good_exit_avg_imbalance=get_tape_at_exit_stat(good_exits, "imbalance_ratio"),
            bad_exit_avg_imbalance=get_tape_at_exit_stat(bad_exits, "imbalance_ratio"),
            good_exit_avg_volume_rate=get_tape_at_exit_stat(good_exits, "volume_rate_per_sec"),
            bad_exit_avg_volume_rate=get_tape_at_exit_stat(bad_exits, "volume_rate_per_sec"),
        )

    return results


def analyze_tape_degradation(records: List[dict]) -> dict:
    """
    Analyze what tape looks like as it degrades from peak.

    Returns patterns like:
    - "When imbalance drops below X, exit within Y seconds"
    - "Volume rate decline of Z% predicts reversal"
    """
    degradation_patterns = []

    for r in records:
        snapshots_after_peak = []

        # Reconstruct snapshots after peak from tape_snapshots
        tape_snapshots = r.get("tape_snapshots", [])
        peak_time_str = r.get("peak_time")
        if not peak_time_str or not tape_snapshots:
            continue

        # Find snapshots after peak
        for snap in tape_snapshots:
            snap_time = snap.get("timestamp", "")
            if snap_time > peak_time_str:
                snapshots_after_peak.append(snap)

        if len(snapshots_after_peak) < 2:
            continue

        # Analyze degradation pattern
        tape_at_peak = r.get("tape_at_peak", {})
        if not tape_at_peak:
            continue

        peak_imbalance = tape_at_peak.get("imbalance_ratio", 0)
        peak_volume_rate = tape_at_peak.get("volume_rate_per_sec", 0)
        peak_buying_pressure = tape_at_peak.get("buying_pressure_pct", 50)

        # Track how quickly tape degraded
        for i, snap in enumerate(snapshots_after_peak):
            degradation_patterns.append({
                "seconds_after_peak": snap.get("seconds_since_entry", 0) - r.get("seconds_to_peak", 0),
                "imbalance_change": snap.get("imbalance_ratio", 0) - peak_imbalance,
                "volume_rate_change_pct": ((snap.get("volume_rate_per_sec", 0) - peak_volume_rate) / peak_volume_rate * 100) if peak_volume_rate > 0 else 0,
                "buying_pressure_change": snap.get("buying_pressure_pct", 50) - peak_buying_pressure,
                "distance_from_peak_pct": snap.get("distance_from_peak_pct", 0),
                "price_velocity": snap.get("price_velocity", 0),
                "exit_timing_quality": r.get("exit_timing_quality", ""),
                "industry": r.get("industry", ""),
                "headline_type": r.get("headline_type", ""),
            })

    # Aggregate patterns
    if not degradation_patterns:
        return {}

    # Find thresholds that predict reversal
    imbalance_drops = [p["imbalance_change"] for p in degradation_patterns if p["distance_from_peak_pct"] > 3]
    volume_drops = [p["volume_rate_change_pct"] for p in degradation_patterns if p["distance_from_peak_pct"] > 3]
    bp_drops = [p["buying_pressure_change"] for p in degradation_patterns if p["distance_from_peak_pct"] > 3]

    return {
        "sample_size": len(degradation_patterns),
        "avg_imbalance_drop_before_3pct_decline": statistics.mean(imbalance_drops) if imbalance_drops else 0,
        "avg_volume_rate_drop_before_3pct_decline": statistics.mean(volume_drops) if volume_drops else 0,
        "avg_buying_pressure_drop_before_3pct_decline": statistics.mean(bp_drops) if bp_drops else 0,
        "patterns": degradation_patterns[:50],  # First 50 for inspection
    }


def find_exit_signals(records: List[dict]) -> dict:
    """
    Find tape characteristics that distinguish good exits from bad exits.

    Goal: Find rules like "Exit when imbalance < 0.2 and volume rate drops 50%"
    """
    good_exits = [r for r in records if r.get("exit_timing_quality") in ["optimal", "good"]]
    bad_exits = [r for r in records if r.get("exit_timing_quality") in ["late", "very_late"]]
    early_exits = [r for r in records if r.get("exit_timing_quality") == "too_early"]

    def extract_tape_features(record_list: List[dict]) -> dict:
        features = defaultdict(list)
        for r in record_list:
            tape = r.get("tape_at_exit", {})
            if tape:
                features["imbalance"].append(tape.get("imbalance_ratio", 0))
                features["buying_pressure"].append(tape.get("buying_pressure_pct", 50))
                features["volume_rate"].append(tape.get("volume_rate_per_sec", 0))
                features["spread_change"].append(tape.get("spread_change_pct", 0))
                features["price_velocity"].append(tape.get("price_velocity", 0))
        return {k: statistics.mean(v) if v else 0 for k, v in features.items()}

    good_features = extract_tape_features(good_exits)
    bad_features = extract_tape_features(bad_exits)
    early_features = extract_tape_features(early_exits)

    # Calculate differences
    signals = {}
    for key in good_features:
        good_val = good_features.get(key, 0)
        bad_val = bad_features.get(key, 0)
        early_val = early_features.get(key, 0)

        if abs(good_val - bad_val) > 0.1:  # Meaningful difference
            signals[key] = {
                "good_exit_avg": round(good_val, 3),
                "bad_exit_avg": round(bad_val, 3),
                "early_exit_avg": round(early_val, 3),
                "difference": round(good_val - bad_val, 3),
                "signal": "EXIT" if good_val < bad_val else "HOLD",
            }

    return {
        "good_exits_count": len(good_exits),
        "bad_exits_count": len(bad_exits),
        "early_exits_count": len(early_exits),
        "signals": signals,
    }


def print_report(records: List[dict]):
    """Print comprehensive analysis report."""
    print("\n" + "=" * 80)
    print("TRADE EXIT TIMING ANALYSIS")
    print("=" * 80)
    print(f"Total trades analyzed: {len(records)}")
    print()

    # Overall stats
    exit_profits = [r.get("exit_profit_pct", 0) for r in records]
    peak_profits = [r.get("peak_profit_pct", 0) for r in records]
    money_left = [r.get("money_left_on_table_pct", 0) for r in records]

    print("OVERALL PERFORMANCE:")
    print(f"  Avg exit profit: {statistics.mean(exit_profits):.1f}%")
    print(f"  Avg peak profit: {statistics.mean(peak_profits):.1f}%")
    print(f"  Avg money left on table: {statistics.mean(money_left):.1f}%")
    print()

    # Exit timing quality distribution
    qualities = [r.get("exit_timing_quality", "") for r in records]
    print("EXIT TIMING QUALITY:")
    for quality in ["optimal", "good", "late", "very_late", "too_early"]:
        count = qualities.count(quality)
        pct = count / len(qualities) * 100 if qualities else 0
        print(f"  {quality}: {count} ({pct:.0f}%)")
    print()

    # By industry
    print("-" * 80)
    print("BY INDUSTRY:")
    print("-" * 80)
    industry_analysis = analyze_by_segment(records, "industry")
    for name, analysis in sorted(industry_analysis.items(), key=lambda x: -x[1].trade_count):
        if analysis.trade_count >= 2:
            print(f"\n{name} (n={analysis.trade_count}):")
            print(f"  Exit profit: {analysis.avg_exit_profit_pct:.1f}% | Peak: {analysis.avg_peak_profit_pct:.1f}% | Left on table: {analysis.avg_money_left_on_table:.1f}%")
            print(f"  Timing: {analysis.pct_optimal:.0f}% optimal, {analysis.pct_late:.0f}% late, {analysis.pct_too_early:.0f}% too early")
            if analysis.good_exit_avg_imbalance != 0 or analysis.bad_exit_avg_imbalance != 0:
                print(f"  Tape at good exit: imbalance={analysis.good_exit_avg_imbalance:.2f}, vol_rate={analysis.good_exit_avg_volume_rate:.1f}")
                print(f"  Tape at bad exit:  imbalance={analysis.bad_exit_avg_imbalance:.2f}, vol_rate={analysis.bad_exit_avg_volume_rate:.1f}")

    # By market cap
    print("\n" + "-" * 80)
    print("BY MARKET CAP:")
    print("-" * 80)
    cap_analysis = analyze_by_segment(records, "market_cap_bucket")
    for name, analysis in sorted(cap_analysis.items(), key=lambda x: -x[1].trade_count):
        print(f"\n{name} (n={analysis.trade_count}):")
        print(f"  Exit profit: {analysis.avg_exit_profit_pct:.1f}% | Peak: {analysis.avg_peak_profit_pct:.1f}% | Left on table: {analysis.avg_money_left_on_table:.1f}%")
        print(f"  Timing: {analysis.pct_optimal:.0f}% optimal, {analysis.pct_late:.0f}% late, {analysis.pct_too_early:.0f}% too early")

    # By headline type
    print("\n" + "-" * 80)
    print("BY HEADLINE TYPE:")
    print("-" * 80)
    headline_analysis = analyze_by_segment(records, "headline_type")
    for name, analysis in sorted(headline_analysis.items(), key=lambda x: -x[1].trade_count):
        if analysis.trade_count >= 2:
            print(f"\n{name} (n={analysis.trade_count}):")
            print(f"  Exit profit: {analysis.avg_exit_profit_pct:.1f}% | Peak: {analysis.avg_peak_profit_pct:.1f}% | Left on table: {analysis.avg_money_left_on_table:.1f}%")
            print(f"  Timing: {analysis.pct_optimal:.0f}% optimal, {analysis.pct_late:.0f}% late, {analysis.pct_too_early:.0f}% too early")

    # Exit signals
    print("\n" + "-" * 80)
    print("EXIT SIGNAL ANALYSIS (What tape looks like at good vs bad exits):")
    print("-" * 80)
    signals = find_exit_signals(records)
    print(f"Good exits: {signals['good_exits_count']} | Bad exits: {signals['bad_exits_count']} | Too early: {signals['early_exits_count']}")
    print("\nTape feature differences:")
    for feature, data in signals.get("signals", {}).items():
        print(f"  {feature}:")
        print(f"    Good exit avg: {data['good_exit_avg']}")
        print(f"    Bad exit avg:  {data['bad_exit_avg']}")
        print(f"    Difference:    {data['difference']} → Signal: {data['signal']} when low")

    # Tape degradation analysis
    print("\n" + "-" * 80)
    print("TAPE DEGRADATION PATTERNS (What happens after peak):")
    print("-" * 80)
    degradation = analyze_tape_degradation(records)
    if degradation:
        print(f"Sample size: {degradation.get('sample_size', 0)} snapshots analyzed")
        print(f"Before 3%+ decline from peak:")
        print(f"  Avg imbalance drop: {degradation.get('avg_imbalance_drop_before_3pct_decline', 0):.3f}")
        print(f"  Avg volume rate drop: {degradation.get('avg_volume_rate_drop_before_3pct_decline', 0):.1f}%")
        print(f"  Avg buying pressure drop: {degradation.get('avg_buying_pressure_drop_before_3pct_decline', 0):.1f}%")

    print("\n" + "=" * 80)
    print("RECOMMENDED EXIT RULES (based on data):")
    print("=" * 80)

    # Generate recommendations based on analysis
    if signals.get("signals", {}).get("imbalance"):
        imb_data = signals["signals"]["imbalance"]
        threshold = (imb_data["good_exit_avg"] + imb_data["bad_exit_avg"]) / 2
        print(f"1. EXIT when imbalance drops below {threshold:.2f} (good exits avg: {imb_data['good_exit_avg']:.2f})")

    if signals.get("signals", {}).get("buying_pressure"):
        bp_data = signals["signals"]["buying_pressure"]
        threshold = (bp_data["good_exit_avg"] + bp_data["bad_exit_avg"]) / 2
        print(f"2. EXIT when buying pressure drops below {threshold:.0f}% (good exits avg: {bp_data['good_exit_avg']:.0f}%)")

    print()


def main():
    parser = argparse.ArgumentParser(description="Analyze trade exit timing")
    parser.add_argument("--path", type=str, default="tmp/statistics/trade_analytics",
                        help="Path to trade analytics data")
    parser.add_argument("--min-trades", type=int, default=1,
                        help="Minimum trades for segment analysis")
    parser.add_argument("--industry", type=str, help="Filter by industry")
    parser.add_argument("--export-csv", type=str, help="Export to CSV file")

    args = parser.parse_args()

    base_path = Path(args.path)
    if not base_path.exists():
        print(f"No data found at {base_path}")
        print("The TradeAnalyticsEngine needs to collect data first.")
        print("Data will be collected automatically as trades are executed.")
        return

    records = load_records(base_path)

    if not records:
        print("No trade analytics records found.")
        return

    # Filter if requested
    if args.industry:
        records = [r for r in records if r.get("industry") == args.industry]
        print(f"Filtered to {len(records)} records for industry: {args.industry}")

    print_report(records)

    # Export if requested
    if args.export_csv:
        import csv
        with open(args.export_csv, 'w', newline='') as f:
            if records:
                writer = csv.DictWriter(f, fieldnames=[
                    'trade_id', 'ticker', 'industry', 'headline_type', 'market_cap_millions',
                    'entry_price', 'exit_price', 'peak_price', 'exit_profit_pct', 'peak_profit_pct',
                    'money_left_on_table_pct', 'exit_timing_quality', 'seconds_held', 'seconds_to_peak',
                    'continued_higher', 'exit_reason'
                ])
                writer.writeheader()
                for r in records:
                    writer.writerow({
                        'trade_id': r.get('trade_id'),
                        'ticker': r.get('ticker'),
                        'industry': r.get('industry'),
                        'headline_type': r.get('headline_type'),
                        'market_cap_millions': r.get('market_cap_millions'),
                        'entry_price': r.get('entry_price'),
                        'exit_price': r.get('exit_price'),
                        'peak_price': r.get('peak_price'),
                        'exit_profit_pct': r.get('exit_profit_pct'),
                        'peak_profit_pct': r.get('peak_profit_pct'),
                        'money_left_on_table_pct': r.get('money_left_on_table_pct'),
                        'exit_timing_quality': r.get('exit_timing_quality'),
                        'seconds_held': r.get('seconds_held'),
                        'seconds_to_peak': r.get('seconds_to_peak'),
                        'continued_higher': r.get('continued_higher'),
                        'exit_reason': r.get('exit_reason'),
                    })
        print(f"\nExported to {args.export_csv}")


if __name__ == "__main__":
    main()
