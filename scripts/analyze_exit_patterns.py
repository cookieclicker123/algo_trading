#!/usr/bin/env python3
"""
Analyze Exit Patterns - Find optimal exit timing by industry, market cap, and headline type.

This script analyzes ALL traded signals from Feb 6th 2026 onwards to find:
1. Which segments need EARLIER exits (taking profit too late)
2. Which segments can HOLD LONGER (leaving profit on table)
3. Probability distributions for optimal exit timing

Data sources:
- tmp/statistics/signal/ - Actual executed trades with outcomes
- tmp/statistics/trade_analytics/ - Detailed tape data (when available)

Usage:
    python scripts/analyze_exit_patterns.py
    python scripts/analyze_exit_patterns.py --export-csv exit_analysis.csv
    python scripts/analyze_exit_patterns.py --industry Biotechnology
"""

import argparse
import json
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple
import statistics
import csv


@dataclass
class TradeOutcome:
    """Processed trade outcome for analysis."""
    trade_id: str
    ticker: str
    date: str

    # Context (for segmentation)
    industry: str
    sector: str
    market_cap_millions: float
    headline: str
    headline_type: str

    # Entry
    entry_price: float
    entry_time: datetime
    position_size: float

    # Peak during hold
    peak_price: float
    peak_profit_pct: float
    seconds_to_peak: float

    # Exit
    exit_price: float
    exit_profit_pct: float
    exit_reason: str
    seconds_held: float

    # Exit quality
    money_left_on_table_pct: float  # peak - exit
    exit_vs_peak_pct: float         # distance from peak at exit
    exit_quality: str               # "optimal", "good", "late", "very_late", "too_early"

    # Post-exit (if available)
    continued_higher: bool = False
    post_exit_high_pct: float = 0.0


@dataclass
class SegmentStats:
    """Statistics for a segment (industry, market cap bucket, etc.)."""
    segment_name: str
    trade_count: int = 0

    # Profit stats
    avg_exit_profit_pct: float = 0.0
    avg_peak_profit_pct: float = 0.0
    avg_money_left_on_table: float = 0.0
    median_peak_profit_pct: float = 0.0

    # Timing stats
    avg_seconds_to_peak: float = 0.0
    avg_seconds_held: float = 0.0
    median_seconds_to_peak: float = 0.0

    # Exit quality distribution
    pct_optimal: float = 0.0    # Within 1% of peak
    pct_good: float = 0.0       # Within 3% of peak
    pct_late: float = 0.0       # 3-5% below peak
    pct_very_late: float = 0.0  # >5% below peak
    pct_too_early: float = 0.0  # Exited and price continued higher

    # Win/loss
    win_rate: float = 0.0       # % trades with positive exit

    # Recommendation
    recommendation: str = ""     # "EXIT_EARLIER", "HOLD_LONGER", "OPTIMAL"

    # Raw data
    trades: List[TradeOutcome] = field(default_factory=list)


def classify_headline(headline: str) -> str:
    """Classify headline into type for segmentation."""
    headline_lower = headline.lower()

    # M&A
    if any(word in headline_lower for word in ["acquisition", "acquire", "merger", "buyout", "to be acquired"]):
        return "acquisition"

    # FDA/Regulatory
    if any(word in headline_lower for word in ["fda approval", "fda approves", "fda clears", "breakthrough therapy", "fda grants"]):
        return "fda_approval"
    if "fda" in headline_lower:
        return "fda_other"

    # Clinical trials
    if any(word in headline_lower for word in ["phase 3", "phase 2", "primary endpoint", "pivotal"]):
        return "clinical_trial"

    # Partnerships
    if any(word in headline_lower for word in ["partnership", "partners with", "collaboration", "agreement", "license"]):
        return "partnership"

    # Contracts
    if any(word in headline_lower for word in ["contract", "awarded", "wins"]):
        return "contract"

    # Offerings
    if any(word in headline_lower for word in ["offering", "placement", "financing"]):
        return "offering"

    return "other"


def get_market_cap_bucket(cap: float) -> str:
    """Categorize market cap into bucket."""
    if cap < 10:
        return "Nano (<$10M)"
    elif cap < 50:
        return "Micro ($10-50M)"
    elif cap < 200:
        return "Small ($50-200M)"
    elif cap < 1000:
        return "Mid ($200M-1B)"
    else:
        return "Large (>$1B)"


def load_signal_records(base_path: Path, start_date: date) -> List[dict]:
    """Load all signal records from start_date onwards."""
    records = []

    for json_file in sorted(base_path.rglob("*.json")):
        try:
            # Check if file is from start_date onwards
            path_parts = str(json_file).split("/")
            # Find year/month/day pattern
            for i, part in enumerate(path_parts):
                if part.isdigit() and len(part) == 4:  # Year
                    if i + 2 < len(path_parts):
                        try:
                            year = int(path_parts[i])
                            month = int(path_parts[i + 1])
                            # Day might be in week_X/DD format
                            day_part = path_parts[i + 3] if "week" in path_parts[i + 2] else path_parts[i + 2]
                            day = int(day_part)
                            file_date = date(year, month, day)
                            if file_date < start_date:
                                continue
                        except (ValueError, IndexError):
                            pass
                    break

            with open(json_file) as f:
                data = json.load(f)

            # Handle both single record and list formats
            if isinstance(data, list):
                records.extend(data)
            elif isinstance(data, dict):
                if "records" in data:
                    records.extend(data["records"])
                else:
                    records.append(data)

        except Exception as e:
            print(f"Error loading {json_file}: {e}")

    return records


def process_signal_record(record: dict) -> Optional[TradeOutcome]:
    """Convert a signal record to TradeOutcome."""
    try:
        ticker = record.get("ticker") or (record.get("tickers", [None])[0])
        if not ticker:
            return None

        # Get metadata - signal records have flat ticker_metadata dict
        meta = record.get("ticker_metadata", {})
        if isinstance(meta, dict) and ticker in meta:
            meta = meta[ticker]

        # Entry data
        entry_price = float(record.get("entry_price") or record.get("filled_avg_price") or 0)
        if entry_price == 0:
            return None

        # Exit data - signal records have flat exit_price field
        exit_price = record.get("exit_price")
        if exit_price is None:
            # Try to compute from P&L
            pnl_pct = record.get("profit_loss_percent")
            if pnl_pct is not None:
                exit_price = entry_price * (1 + pnl_pct / 100)
            else:
                # No exit data available - skip this trade
                return None
        exit_price = float(exit_price)
        if exit_price == 0:
            return None

        # Peak data - signal records use highest_price_during_hold dict
        peak_data = record.get("highest_price_during_hold") or {}
        if peak_data and peak_data.get("price"):
            peak_price = float(peak_data["price"])
        else:
            # Estimate peak from post-trade price tracking if available
            # Find highest price from available data points
            candidate_prices = [exit_price]  # At minimum, use exit price
            for price_key in ["price_at_5s", "price_at_10s", "price_at_30s", "price_at_1min", "price_at_5min"]:
                price_val = record.get(price_key)
                if price_val is not None:
                    candidate_prices.append(float(price_val))
            peak_price = max(candidate_prices)

        # Calculate profits
        exit_profit_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        peak_profit_pct = ((peak_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        money_left = peak_profit_pct - exit_profit_pct
        exit_vs_peak = ((peak_price - exit_price) / entry_price * 100) if entry_price > 0 else 0

        # Timing
        entry_time_str = record.get("executed_at") or record.get("entry_time") or record.get("created_at")
        entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00")) if entry_time_str else datetime.now()

        exit_time_str = record.get("exited_at")
        if exit_time_str:
            exit_time = datetime.fromisoformat(exit_time_str.replace("Z", "+00:00"))
            seconds_held = (exit_time - entry_time).total_seconds()
        else:
            seconds_held = record.get("hold_duration_seconds") or 0

        # Estimate seconds to peak from peak_data if available
        seconds_to_peak = 0
        if peak_data:
            if peak_data.get("minute") is not None:
                seconds_to_peak = peak_data.get("minute", 0) * 60 + peak_data.get("second", 0)
            elif peak_data.get("timestamp"):
                try:
                    peak_time = datetime.fromisoformat(peak_data["timestamp"].replace("Z", "+00:00"))
                    seconds_to_peak = (peak_time - entry_time).total_seconds()
                except:
                    pass

        # Classify exit quality
        if exit_vs_peak < 1:
            exit_quality = "optimal"
        elif exit_vs_peak < 3:
            exit_quality = "good"
        elif exit_vs_peak < 5:
            exit_quality = "late"
        elif exit_vs_peak < 10:
            exit_quality = "very_late"
        else:
            exit_quality = "very_late"

        # Check if continued higher (if we have post-exit data)
        continued_higher = record.get("continued_higher", False)
        post_exit_high = record.get("post_exit_high_pct", 0)
        if post_exit_high > exit_profit_pct + 3:  # Continued 3%+ higher
            exit_quality = "too_early"
            continued_higher = True

        headline = record.get("title") or record.get("headline") or ""

        return TradeOutcome(
            trade_id=record.get("trade_id") or record.get("id") or "unknown",
            ticker=ticker,
            date=entry_time.strftime("%Y-%m-%d"),
            industry=meta.get("industry", "Unknown") if isinstance(meta, dict) else "Unknown",
            sector=meta.get("sector", "Unknown") if isinstance(meta, dict) else "Unknown",
            market_cap_millions=meta.get("market_cap_millions", 0) if isinstance(meta, dict) else 0,
            headline=headline[:100],
            headline_type=record.get("headline_type") or classify_headline(headline),
            entry_price=entry_price,
            entry_time=entry_time,
            position_size=float(record.get("entry_amount_usd") or record.get("position_size_usd") or record.get("cost") or 0),
            peak_price=peak_price,
            peak_profit_pct=peak_profit_pct,
            seconds_to_peak=seconds_to_peak,
            exit_price=exit_price,
            exit_profit_pct=exit_profit_pct,
            exit_reason=record.get("exit_reason") or "unknown",
            seconds_held=seconds_held,
            money_left_on_table_pct=money_left,
            exit_vs_peak_pct=exit_vs_peak,
            exit_quality=exit_quality,
            continued_higher=continued_higher,
            post_exit_high_pct=post_exit_high,
        )

    except Exception as e:
        # print(f"Error processing record: {e}")
        return None


def calculate_segment_stats(trades: List[TradeOutcome], segment_name: str) -> SegmentStats:
    """Calculate statistics for a segment."""
    if not trades:
        return SegmentStats(segment_name=segment_name)

    stats = SegmentStats(
        segment_name=segment_name,
        trade_count=len(trades),
        trades=trades,
    )

    # Profit stats
    exit_profits = [t.exit_profit_pct for t in trades]
    peak_profits = [t.peak_profit_pct for t in trades]
    money_left = [t.money_left_on_table_pct for t in trades]

    stats.avg_exit_profit_pct = statistics.mean(exit_profits)
    stats.avg_peak_profit_pct = statistics.mean(peak_profits)
    stats.avg_money_left_on_table = statistics.mean(money_left)
    stats.median_peak_profit_pct = statistics.median(peak_profits)

    # Timing stats
    times_to_peak = [t.seconds_to_peak for t in trades if t.seconds_to_peak > 0]
    times_held = [t.seconds_held for t in trades if t.seconds_held > 0]

    if times_to_peak:
        stats.avg_seconds_to_peak = statistics.mean(times_to_peak)
        stats.median_seconds_to_peak = statistics.median(times_to_peak)
    if times_held:
        stats.avg_seconds_held = statistics.mean(times_held)

    # Exit quality distribution
    qualities = [t.exit_quality for t in trades]
    n = len(qualities)
    stats.pct_optimal = qualities.count("optimal") / n * 100
    stats.pct_good = qualities.count("good") / n * 100
    stats.pct_late = qualities.count("late") / n * 100
    stats.pct_very_late = qualities.count("very_late") / n * 100
    stats.pct_too_early = qualities.count("too_early") / n * 100

    # Win rate
    winners = len([t for t in trades if t.exit_profit_pct > 0])
    stats.win_rate = winners / n * 100

    # Recommendation
    if stats.avg_money_left_on_table > 5:
        stats.recommendation = "EXIT_EARLIER"
    elif stats.pct_too_early > 20:
        stats.recommendation = "HOLD_LONGER"
    else:
        stats.recommendation = "OPTIMAL"

    return stats


def analyze_by_segment(trades: List[TradeOutcome], segment_key: str) -> Dict[str, SegmentStats]:
    """Analyze trades grouped by a segment key."""
    segments = defaultdict(list)

    for trade in trades:
        if segment_key == "industry":
            key = trade.industry
        elif segment_key == "market_cap_bucket":
            key = get_market_cap_bucket(trade.market_cap_millions)
        elif segment_key == "headline_type":
            key = trade.headline_type
        elif segment_key == "sector":
            key = trade.sector
        else:
            key = "all"

        segments[key].append(trade)

    return {name: calculate_segment_stats(trades, name) for name, trades in segments.items()}


def print_segment_analysis(stats: Dict[str, SegmentStats], title: str, min_trades: int = 2):
    """Print analysis for a set of segments."""
    print(f"\n{'='*80}")
    print(f"{title}")
    print(f"{'='*80}")

    # Sort by trade count
    sorted_stats = sorted(stats.values(), key=lambda s: -s.trade_count)

    for s in sorted_stats:
        if s.trade_count < min_trades:
            continue

        rec_emoji = {"EXIT_EARLIER": "🔻", "HOLD_LONGER": "📈", "OPTIMAL": "✅"}.get(s.recommendation, "")

        print(f"\n{s.segment_name} (n={s.trade_count}) {rec_emoji} {s.recommendation}")
        print(f"  Exit: {s.avg_exit_profit_pct:+.1f}% | Peak: {s.avg_peak_profit_pct:+.1f}% | Left on table: {s.avg_money_left_on_table:.1f}%")
        print(f"  Win rate: {s.win_rate:.0f}% | Median peak: {s.median_peak_profit_pct:+.1f}%")
        print(f"  Time to peak: {s.avg_seconds_to_peak:.0f}s ({s.avg_seconds_to_peak/60:.1f}min) | Held: {s.avg_seconds_held:.0f}s ({s.avg_seconds_held/60:.1f}min)")
        print(f"  Quality: {s.pct_optimal:.0f}% optimal, {s.pct_good:.0f}% good, {s.pct_late:.0f}% late, {s.pct_very_late:.0f}% very late, {s.pct_too_early:.0f}% too early")


def print_probability_table(trades: List[TradeOutcome]):
    """Print probability distribution table for exit timing."""
    print(f"\n{'='*80}")
    print("PROBABILITY DISTRIBUTIONS FOR EXIT TIMING")
    print(f"{'='*80}")

    # By industry and market cap combined
    print("\n[Industry x Market Cap] → Recommendation")
    print("-" * 80)

    combos = defaultdict(list)
    for t in trades:
        key = (t.industry, get_market_cap_bucket(t.market_cap_millions))
        combos[key].append(t)

    for (industry, cap_bucket), combo_trades in sorted(combos.items()):
        if len(combo_trades) < 2:
            continue
        stats = calculate_segment_stats(combo_trades, f"{industry} / {cap_bucket}")
        rec_emoji = {"EXIT_EARLIER": "🔻", "HOLD_LONGER": "📈", "OPTIMAL": "✅"}.get(stats.recommendation, "")
        print(f"{industry[:20]:<20} | {cap_bucket:<15} | n={len(combo_trades):>3} | "
              f"Peak: {stats.avg_peak_profit_pct:>+5.1f}% | Left: {stats.avg_money_left_on_table:>4.1f}% | {rec_emoji} {stats.recommendation}")

    # Peak profit distribution
    print(f"\n{'='*80}")
    print("PEAK PROFIT DISTRIBUTION (What % moves are typical?)")
    print("-" * 80)

    peak_buckets = defaultdict(int)
    for t in trades:
        if t.peak_profit_pct < 5:
            bucket = "0-5%"
        elif t.peak_profit_pct < 10:
            bucket = "5-10%"
        elif t.peak_profit_pct < 15:
            bucket = "10-15%"
        elif t.peak_profit_pct < 20:
            bucket = "15-20%"
        elif t.peak_profit_pct < 30:
            bucket = "20-30%"
        else:
            bucket = "30%+"
        peak_buckets[bucket] += 1

    total = len(trades)
    for bucket in ["0-5%", "5-10%", "10-15%", "15-20%", "20-30%", "30%+"]:
        count = peak_buckets[bucket]
        pct = count / total * 100 if total > 0 else 0
        bar = "█" * int(pct / 2)
        print(f"  {bucket:>8}: {count:>4} ({pct:>5.1f}%) {bar}")


def main():
    parser = argparse.ArgumentParser(description="Analyze exit patterns from signal data")
    parser.add_argument("--signal-path", type=str, default="tmp/statistics/signal",
                        help="Path to signal data")
    parser.add_argument("--analytics-path", type=str, default="tmp/statistics/trade_analytics",
                        help="Path to trade analytics data")
    parser.add_argument("--start-date", type=str, default="2026-02-06",
                        help="Start date for analysis (YYYY-MM-DD)")
    parser.add_argument("--min-trades", type=int, default=2,
                        help="Minimum trades for segment analysis")
    parser.add_argument("--industry", type=str, help="Filter by industry")
    parser.add_argument("--export-csv", type=str, help="Export to CSV file")

    args = parser.parse_args()

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()

    # Load signal records
    signal_path = Path(args.signal_path)
    if not signal_path.exists():
        print(f"Signal path {signal_path} does not exist")
        return

    print(f"Loading signal records from {signal_path} (from {start_date})...")
    records = load_signal_records(signal_path, start_date)
    print(f"Found {len(records)} raw records")

    # Process into TradeOutcomes
    trades = []
    for record in records:
        outcome = process_signal_record(record)
        if outcome:
            trades.append(outcome)

    print(f"Processed {len(trades)} valid trades")

    if not trades:
        print("No trades found to analyze")
        return

    # Filter if requested
    if args.industry:
        trades = [t for t in trades if t.industry == args.industry]
        print(f"Filtered to {len(trades)} trades for industry: {args.industry}")

    # Overall stats
    print(f"\n{'='*80}")
    print(f"OVERALL STATISTICS ({len(trades)} trades from {start_date})")
    print(f"{'='*80}")

    overall = calculate_segment_stats(trades, "Overall")
    print(f"Avg Exit Profit: {overall.avg_exit_profit_pct:+.1f}%")
    print(f"Avg Peak Profit: {overall.avg_peak_profit_pct:+.1f}%")
    print(f"Avg Money Left on Table: {overall.avg_money_left_on_table:.1f}%")
    print(f"Win Rate: {overall.win_rate:.0f}%")
    print(f"Avg Time to Peak: {overall.avg_seconds_to_peak:.0f}s ({overall.avg_seconds_to_peak/60:.1f}min)")

    # By industry
    industry_stats = analyze_by_segment(trades, "industry")
    print_segment_analysis(industry_stats, "BY INDUSTRY", args.min_trades)

    # By market cap
    cap_stats = analyze_by_segment(trades, "market_cap_bucket")
    print_segment_analysis(cap_stats, "BY MARKET CAP", args.min_trades)

    # By headline type
    headline_stats = analyze_by_segment(trades, "headline_type")
    print_segment_analysis(headline_stats, "BY HEADLINE TYPE", args.min_trades)

    # Probability distributions
    print_probability_table(trades)

    # Actionable recommendations
    print(f"\n{'='*80}")
    print("ACTIONABLE RECOMMENDATIONS")
    print(f"{'='*80}")

    exit_earlier = [s for s in industry_stats.values() if s.recommendation == "EXIT_EARLIER" and s.trade_count >= args.min_trades]
    hold_longer = [s for s in industry_stats.values() if s.recommendation == "HOLD_LONGER" and s.trade_count >= args.min_trades]

    if exit_earlier:
        print("\n🔻 EXIT EARLIER for these industries (leaving too much on table):")
        for s in sorted(exit_earlier, key=lambda x: -x.avg_money_left_on_table):
            print(f"   {s.segment_name}: avg {s.avg_money_left_on_table:.1f}% left on table")

    if hold_longer:
        print("\n📈 HOLD LONGER for these industries (exiting too early):")
        for s in sorted(hold_longer, key=lambda x: -x.pct_too_early):
            print(f"   {s.segment_name}: {s.pct_too_early:.0f}% exited too early")

    # Export if requested
    if args.export_csv:
        with open(args.export_csv, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'trade_id', 'date', 'ticker', 'industry', 'sector', 'market_cap_bucket',
                'headline_type', 'entry_price', 'exit_price', 'peak_price',
                'exit_profit_pct', 'peak_profit_pct', 'money_left_on_table_pct',
                'seconds_to_peak', 'seconds_held', 'exit_quality', 'exit_reason'
            ])
            writer.writeheader()
            for t in trades:
                writer.writerow({
                    'trade_id': t.trade_id,
                    'date': t.date,
                    'ticker': t.ticker,
                    'industry': t.industry,
                    'sector': t.sector,
                    'market_cap_bucket': get_market_cap_bucket(t.market_cap_millions),
                    'headline_type': t.headline_type,
                    'entry_price': t.entry_price,
                    'exit_price': t.exit_price,
                    'peak_price': t.peak_price,
                    'exit_profit_pct': t.exit_profit_pct,
                    'peak_profit_pct': t.peak_profit_pct,
                    'money_left_on_table_pct': t.money_left_on_table_pct,
                    'seconds_to_peak': t.seconds_to_peak,
                    'seconds_held': t.seconds_held,
                    'exit_quality': t.exit_quality,
                    'exit_reason': t.exit_reason,
                })
        print(f"\n✅ Exported {len(trades)} trades to {args.export_csv}")


if __name__ == "__main__":
    main()
