#!/usr/bin/env python3
"""
Aggregate Trade Statistics - Find patterns across any date range.

Analyzes both Recall (missed opportunities) and Signal (executed trades) records
to find predictive features for profitable trades.

Usage:
    python scripts/aggregate_trade_stats.py                    # From Feb 6, 2026 onwards
    python scripts/aggregate_trade_stats.py --days 7           # Last 7 days
    python scripts/aggregate_trade_stats.py --start 2026-02-06 --end 2026-02-20
    python scripts/aggregate_trade_stats.py --output report.json  # Save detailed JSON

Features analyzed:
- Confluence window stats (volume, pressure, timing, sub-slices)
- Pressure consistency (sustained buying vs fading)
- Baseline ratios (volume ratio, trade count ratio)
- Industry/sector patterns
- Market cap buckets
- Headline types
- Market regime correlation
"""
import argparse
import asyncio
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@dataclass
class FeatureStats:
    """Statistics for a single feature."""
    name: str
    values_winners: List[float] = field(default_factory=list)
    values_losers: List[float] = field(default_factory=list)
    values_all: List[float] = field(default_factory=list)

    @property
    def mean_winners(self) -> Optional[float]:
        return statistics.mean(self.values_winners) if self.values_winners else None

    @property
    def mean_losers(self) -> Optional[float]:
        return statistics.mean(self.values_losers) if self.values_losers else None

    @property
    def mean_all(self) -> Optional[float]:
        return statistics.mean(self.values_all) if self.values_all else None

    @property
    def separation(self) -> Optional[float]:
        """How different are winners vs losers? Higher = more predictive."""
        if self.mean_winners is not None and self.mean_losers is not None:
            avg = (abs(self.mean_winners) + abs(self.mean_losers)) / 2
            if avg > 0:
                return abs(self.mean_winners - self.mean_losers) / avg
        return None

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "n_winners": len(self.values_winners),
            "n_losers": len(self.values_losers),
            "mean_winners": round(self.mean_winners, 4) if self.mean_winners else None,
            "mean_losers": round(self.mean_losers, 4) if self.mean_losers else None,
            "mean_all": round(self.mean_all, 4) if self.mean_all else None,
            "separation": round(self.separation, 3) if self.separation else None,
        }


@dataclass
class SegmentStats:
    """Statistics for a segment (industry, market cap bucket, etc.)."""
    name: str
    total: int = 0
    winners: int = 0
    losers: int = 0
    breakeven: int = 0
    total_pnl_pct: float = 0.0
    peak_profits: List[float] = field(default_factory=list)
    exit_profits: List[float] = field(default_factory=list)
    mae_values: List[float] = field(default_factory=list)  # Max adverse excursion

    @property
    def win_rate(self) -> float:
        completed = self.winners + self.losers
        return (self.winners / completed * 100) if completed > 0 else 0

    @property
    def avg_peak(self) -> Optional[float]:
        return statistics.mean(self.peak_profits) if self.peak_profits else None

    @property
    def avg_exit(self) -> Optional[float]:
        return statistics.mean(self.exit_profits) if self.exit_profits else None

    @property
    def avg_mae(self) -> Optional[float]:
        return statistics.mean(self.mae_values) if self.mae_values else None

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "total": self.total,
            "winners": self.winners,
            "losers": self.losers,
            "breakeven": self.breakeven,
            "win_rate_pct": round(self.win_rate, 1),
            "avg_peak_pct": round(self.avg_peak, 2) if self.avg_peak else None,
            "avg_exit_pct": round(self.avg_exit, 2) if self.avg_exit else None,
            "avg_mae_pct": round(self.avg_mae, 2) if self.avg_mae else None,
            "avg_pnl_pct": round(self.total_pnl_pct / self.total, 2) if self.total > 0 else None,
        }


class TradeAggregator:
    """Aggregates trade statistics across date ranges."""

    # Features to extract from confluence window
    CONFLUENCE_FEATURES = [
        "total_volume", "total_trades", "total_buy_volume", "total_sell_volume",
        "price_excursion_pct", "imbalance_ratio", "buying_pressure_pct",
        "uptick_ratio", "pressure_consistent", "pressure_strengthening",
        "avg_trade_size", "max_single_trade", "large_trade_pct",
        "first_trade_latency_ms", "max_trade_gap_ms", "trades_in_first_500ms",
        "volume_in_first_500ms", "spread_compression_pct", "quote_update_count",
        "volume_ratio", "trade_count_ratio", "spread_ratio",
        "confluence_score",
    ]

    # Flat confluence fields (legacy, for backwards compatibility)
    FLAT_CONFLUENCE_FEATURES = [
        "confluence_volume", "confluence_trade_count", "confluence_buy_volume",
        "confluence_sell_volume", "confluence_buying_pressure_pct",
        "confluence_imbalance_ratio", "confluence_price_excursion_pct",
        "confluence_avg_trade_size", "confluence_max_single_trade",
        "confluence_score",
    ]

    def __init__(
        self,
        signal_path: Path = Path("tmp/statistics/signal"),
        recall_path: Path = Path("tmp/statistics/recall"),
        analytics_path: Path = Path("tmp/analytics/daily"),
    ):
        self.signal_path = signal_path
        self.recall_path = recall_path
        self.analytics_path = analytics_path

        # Aggregated data
        self.signal_records: List[Dict] = []
        self.recall_records: List[Dict] = []

        # Feature statistics
        self.feature_stats: Dict[str, FeatureStats] = {}

        # Segment statistics
        self.by_industry: Dict[str, SegmentStats] = defaultdict(lambda: SegmentStats(""))
        self.by_sector: Dict[str, SegmentStats] = defaultdict(lambda: SegmentStats(""))
        self.by_market_cap: Dict[str, SegmentStats] = defaultdict(lambda: SegmentStats(""))
        self.by_headline_type: Dict[str, SegmentStats] = defaultdict(lambda: SegmentStats(""))
        self.by_session: Dict[str, SegmentStats] = defaultdict(lambda: SegmentStats(""))
        self.by_confluence_score: Dict[int, SegmentStats] = defaultdict(lambda: SegmentStats(""))

    def load_records(self, start_date: date, end_date: date) -> Tuple[int, int]:
        """Load all signal and recall records in date range."""
        signal_count = 0
        recall_count = 0

        current = start_date
        while current <= end_date:
            year = current.year
            month = current.month
            day = current.day
            week = current.isocalendar()[1]

            for session in ["premarket", "market_hours", "postmarket"]:
                # Load signal records
                signal_file = (
                    self.signal_path / str(year) / f"{month:02d}" /
                    f"week_{week}" / f"{day:02d}" / session / f"{session}.json"
                )
                if signal_file.exists():
                    try:
                        with open(signal_file) as f:
                            data = json.load(f)
                        for record in data.get("records", []):
                            record["_date"] = current.isoformat()
                            record["_session"] = session
                            record["_source"] = "signal"
                            self.signal_records.append(record)
                            signal_count += 1
                    except Exception as e:
                        print(f"Error loading {signal_file}: {e}")

                # Load recall records
                recall_file = (
                    self.recall_path / str(year) / f"{month:02d}" /
                    f"week_{week}" / f"{day:02d}" / session / f"{session}.json"
                )
                if recall_file.exists():
                    try:
                        with open(recall_file) as f:
                            data = json.load(f)
                        for record in data.get("records", []):
                            record["_date"] = current.isoformat()
                            record["_session"] = session
                            record["_source"] = "recall"
                            self.recall_records.append(record)
                            recall_count += 1
                    except Exception as e:
                        print(f"Error loading {recall_file}: {e}")

            current += timedelta(days=1)

        return signal_count, recall_count

    def get_market_cap_bucket(self, cap: Optional[float]) -> str:
        """Categorize market cap."""
        if cap is None:
            return "Unknown"
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

    def classify_trade(self, record: Dict) -> str:
        """Classify trade as winner, loser, or breakeven."""
        pnl = record.get("profit_loss_percent")
        if pnl is None:
            # Check if still open (no exit)
            if record.get("exit_price") is None:
                return "open"
            return "unknown"

        if pnl > 2:
            return "winner"
        elif pnl < -2:
            return "loser"
        else:
            return "breakeven"

    def extract_features(self, record: Dict) -> Dict[str, Any]:
        """Extract all confluence features from a record."""
        features = {}

        # Try structured confluence_window first
        cw = record.get("confluence_window")
        if cw and isinstance(cw, dict):
            for feature in self.CONFLUENCE_FEATURES:
                if feature in cw:
                    features[feature] = cw[feature]

        # Fall back to flat confluence_ fields
        for feature in self.FLAT_CONFLUENCE_FEATURES:
            if feature not in features and feature in record:
                features[feature] = record[feature]

        return features

    def analyze_signal_records(self):
        """Analyze signal (executed trade) records."""
        for record in self.signal_records:
            outcome = self.classify_trade(record)
            if outcome == "open" or outcome == "unknown":
                continue

            is_winner = outcome == "winner"
            pnl = record.get("profit_loss_percent", 0) or 0

            # Get metadata
            meta = record.get("ticker_metadata", {})
            if isinstance(meta, dict) and record.get("ticker") in meta:
                meta = meta[record["ticker"]]

            industry = meta.get("industry", "Unknown") if isinstance(meta, dict) else "Unknown"
            sector = meta.get("sector", "Unknown") if isinstance(meta, dict) else "Unknown"
            market_cap = meta.get("market_cap_millions") if isinstance(meta, dict) else None
            headline_type = record.get("headline_type", "unknown")
            session = record.get("_session", "unknown")
            confluence_score = record.get("confluence_score", 0) or 0

            # Peak/exit data
            peak_data = record.get("highest_price_during_hold", {})
            peak_pct = peak_data.get("percent_gain_from_entry") if peak_data else None
            exit_pct = pnl

            # MAE (from price snapshots if available)
            mae = None  # TODO: Calculate from price_at_* fields

            # Update segment stats
            for segment_dict, key in [
                (self.by_industry, industry),
                (self.by_sector, sector),
                (self.by_market_cap, self.get_market_cap_bucket(market_cap)),
                (self.by_headline_type, headline_type),
                (self.by_session, session),
                (self.by_confluence_score, confluence_score),
            ]:
                if key not in segment_dict or segment_dict[key].name == "":
                    segment_dict[key] = SegmentStats(str(key))
                stats = segment_dict[key]
                stats.total += 1
                stats.total_pnl_pct += pnl
                if outcome == "winner":
                    stats.winners += 1
                elif outcome == "loser":
                    stats.losers += 1
                else:
                    stats.breakeven += 1
                if peak_pct is not None:
                    stats.peak_profits.append(peak_pct)
                if exit_pct is not None:
                    stats.exit_profits.append(exit_pct)

            # Extract and record features
            features = self.extract_features(record)
            for name, value in features.items():
                if value is None:
                    continue
                # Convert bools to int
                if isinstance(value, bool):
                    value = 1 if value else 0
                if not isinstance(value, (int, float)):
                    continue

                if name not in self.feature_stats:
                    self.feature_stats[name] = FeatureStats(name)

                fs = self.feature_stats[name]
                fs.values_all.append(value)
                if is_winner:
                    fs.values_winners.append(value)
                else:
                    fs.values_losers.append(value)

    def analyze_recall_records(self):
        """Analyze recall (missed opportunity) records for comparison."""
        # Count missed opportunities by segment
        missed_by_industry = defaultdict(int)
        missed_by_headline_type = defaultdict(int)

        for record in self.recall_records:
            # Only look at IMMINENT classifications that moved 1%+
            if record.get("ai_classification") != "IMMINENT":
                continue

            price_check = record.get("price_check_10min", {})
            if not price_check or not price_check.get("moved_1_percent"):
                continue

            # This was a missed opportunity
            tickers = record.get("tickers", [])
            if not tickers:
                continue

            meta = record.get("ticker_metadata", {}).get(tickers[0], {})
            industry = meta.get("industry", "Unknown")
            headline_type = record.get("headline_type", "unknown")

            missed_by_industry[industry] += 1
            missed_by_headline_type[headline_type] += 1

        return {
            "missed_by_industry": dict(missed_by_industry),
            "missed_by_headline_type": dict(missed_by_headline_type),
        }

    def get_feature_importance(self, min_samples: int = 5) -> List[Dict]:
        """Rank features by predictive power (separation between winners/losers)."""
        ranked = []
        for name, fs in self.feature_stats.items():
            if len(fs.values_winners) >= min_samples and len(fs.values_losers) >= min_samples:
                if fs.separation is not None:
                    ranked.append(fs.to_dict())

        # Sort by separation (higher = more predictive)
        ranked.sort(key=lambda x: x["separation"] or 0, reverse=True)
        return ranked

    def generate_report(self, start_date: date, end_date: date) -> Dict:
        """Generate comprehensive report."""
        # Analyze records
        self.analyze_signal_records()
        missed_analysis = self.analyze_recall_records()

        # Calculate overall stats
        total_trades = sum(s.total for s in self.by_session.values())
        total_winners = sum(s.winners for s in self.by_session.values())
        total_losers = sum(s.losers for s in self.by_session.values())

        report = {
            "meta": {
                "generated_at": datetime.now().isoformat(),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "signal_records": len(self.signal_records),
                "recall_records": len(self.recall_records),
            },
            "summary": {
                "total_trades": total_trades,
                "winners": total_winners,
                "losers": total_losers,
                "win_rate_pct": round(total_winners / (total_winners + total_losers) * 100, 1) if (total_winners + total_losers) > 0 else 0,
            },
            "feature_importance": self.get_feature_importance(),
            "by_industry": {k: v.to_dict() for k, v in self.by_industry.items() if v.total > 0},
            "by_sector": {k: v.to_dict() for k, v in self.by_sector.items() if v.total > 0},
            "by_market_cap": {k: v.to_dict() for k, v in self.by_market_cap.items() if v.total > 0},
            "by_headline_type": {k: v.to_dict() for k, v in self.by_headline_type.items() if v.total > 0},
            "by_session": {k: v.to_dict() for k, v in self.by_session.items() if v.total > 0},
            "by_confluence_score": {str(k): v.to_dict() for k, v in self.by_confluence_score.items() if v.total > 0},
            "missed_opportunities": missed_analysis,
        }

        return report


def print_report(report: Dict):
    """Print report to console."""
    meta = report["meta"]
    summary = report["summary"]

    print("\n" + "=" * 70)
    print(f"TRADE PATTERN ANALYSIS: {meta['start_date']} to {meta['end_date']}")
    print("=" * 70)

    print(f"\nLoaded: {meta['signal_records']} trades, {meta['recall_records']} recall records")
    print(f"Completed Trades: {summary['total_trades']} ({summary['winners']}W / {summary['losers']}L)")
    print(f"Win Rate: {summary['win_rate_pct']}%")

    # Feature importance
    print("\n" + "-" * 70)
    print("TOP PREDICTIVE FEATURES (winners vs losers)")
    print("-" * 70)
    print(f"{'Feature':<35} {'Win Avg':>10} {'Lose Avg':>10} {'Separation':>10}")
    print("-" * 70)

    for feat in report["feature_importance"][:15]:
        name = feat["name"][:35]
        win_avg = f"{feat['mean_winners']:.2f}" if feat["mean_winners"] else "N/A"
        lose_avg = f"{feat['mean_losers']:.2f}" if feat["mean_losers"] else "N/A"
        sep = f"{feat['separation']:.3f}" if feat["separation"] else "N/A"
        print(f"{name:<35} {win_avg:>10} {lose_avg:>10} {sep:>10}")

    # By confluence score
    print("\n" + "-" * 70)
    print("BY CONFLUENCE SCORE")
    print("-" * 70)
    for score in sorted(report["by_confluence_score"].keys()):
        stats = report["by_confluence_score"][score]
        print(f"  Score {score}: {stats['total']} trades, {stats['win_rate_pct']}% win rate, "
              f"avg P&L {stats['avg_pnl_pct']:+.1f}%")

    # By industry (top 10)
    print("\n" + "-" * 70)
    print("BY INDUSTRY (sorted by volume)")
    print("-" * 70)
    industries = sorted(report["by_industry"].items(), key=lambda x: -x[1]["total"])[:10]
    for name, stats in industries:
        print(f"  {name[:40]:<40}: {stats['total']:>3} trades, {stats['win_rate_pct']:>5.1f}% win, "
              f"avg {stats['avg_pnl_pct']:+.1f}%")

    # By market cap
    print("\n" + "-" * 70)
    print("BY MARKET CAP")
    print("-" * 70)
    for bucket in ["Nano (<$10M)", "Micro ($10-50M)", "Small ($50-200M)", "Mid ($200M-1B)", "Large (>$1B)"]:
        if bucket in report["by_market_cap"]:
            stats = report["by_market_cap"][bucket]
            print(f"  {bucket:<20}: {stats['total']:>3} trades, {stats['win_rate_pct']:>5.1f}% win, "
                  f"avg {stats['avg_pnl_pct']:+.1f}%")

    # By headline type
    print("\n" + "-" * 70)
    print("BY HEADLINE TYPE (top 10)")
    print("-" * 70)
    headline_types = sorted(report["by_headline_type"].items(), key=lambda x: -x[1]["total"])[:10]
    for name, stats in headline_types:
        display_name = (name or "unknown")[:35]
        pnl = stats.get('avg_pnl_pct') or 0
        print(f"  {display_name:<35}: {stats['total']:>3} trades, {stats['win_rate_pct']:>5.1f}% win, "
              f"avg {pnl:+.1f}%")


async def main():
    parser = argparse.ArgumentParser(description="Aggregate trade statistics for pattern analysis")
    parser.add_argument("--start", type=str, default="2026-02-06",
                        help="Start date (YYYY-MM-DD), default: 2026-02-06")
    parser.add_argument("--end", type=str, default=None,
                        help="End date (YYYY-MM-DD), default: today")
    parser.add_argument("--days", type=int, default=None,
                        help="Analyze last N days (overrides --start)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON file path")
    parser.add_argument("--min-samples", type=int, default=3,
                        help="Minimum samples for feature analysis")

    args = parser.parse_args()

    # Determine date range
    if args.days:
        end_date = date.today()
        start_date = end_date - timedelta(days=args.days)
    else:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()

    print(f"Analyzing trades from {start_date} to {end_date}...")

    # Load and analyze
    aggregator = TradeAggregator()
    signal_count, recall_count = aggregator.load_records(start_date, end_date)
    print(f"Loaded {signal_count} signal records, {recall_count} recall records")

    # Generate report
    report = aggregator.generate_report(start_date, end_date)

    # Print to console
    print_report(report)

    # Save to file if requested
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\n✅ Report saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
