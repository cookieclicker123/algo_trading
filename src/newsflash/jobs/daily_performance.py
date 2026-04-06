"""
Daily Performance Report - Unified view of all trading decisions and outcomes.

Replaces the old trade_classification job with a comprehensive single-file report
that captures every article, every decision, every filter, and every outcome.

Output: tmp/daily_performance/YYYY-MM-DD.json

After a month of data, these files can be loaded and concatenated for large-scale
data mining to identify what works and what doesn't across all dimensions.

Schedule: Runs at 8pm ET via MarketHoursScheduler (after postmarket close).
"""
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

import pytz

from ..utils.logging_config import get_logger

logger = get_logger(__name__)

ET_TZ = pytz.timezone("America/New_York")

# Sessions to collect data from
SESSIONS = ["premarket", "market_hours", "postmarket"]

# Output directory
DEFAULT_OUTPUT_DIR = Path("tmp/daily_performance")


class DailyPerformanceJob:
    """
    Generates a unified daily performance report combining recall, signal,
    and failed trade data into a single searchable file.

    Each record represents one article/ticker decision with ALL available
    microstructure, classification, and outcome data.
    """

    def __init__(self, output_dir: Path = DEFAULT_OUTPUT_DIR):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stats_dir = Path("tmp/statistics")

    async def run(self, target_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """
        Generate daily performance report for a given date.

        Args:
            target_date: Date to analyze (defaults to today)

        Returns:
            Report dict with summary and records, or None if no data
        """
        if target_date is None:
            target_date = date.today()

        target_dt = ET_TZ.localize(
            datetime(target_date.year, target_date.month, target_date.day, 12, 0, 0)
        )

        logger.info(f"DailyPerformanceJob: Generating report for {target_date}")

        # Load all data sources
        recall_records = self._load_recall_records(target_dt)
        signal_records = self._load_signal_records(target_dt)
        failed_records = self._load_failed_trade_records(target_dt)

        if not recall_records:
            logger.info(f"DailyPerformanceJob: No recall records for {target_date}")
            return None

        # Index signal and failed records by article_id for fast lookup
        signal_by_article = {}
        for rec in signal_records:
            aid = rec.get("article_id")
            if aid:
                signal_by_article[aid] = rec

        failed_by_article = {}
        for rec in failed_records:
            aid = rec.get("article_id")
            if aid:
                failed_by_article[aid] = rec

        # Build unified records
        unified_records = []
        for recall in recall_records:
            article_id = recall.get("article_id", "")
            signal = signal_by_article.get(article_id)
            failed = failed_by_article.get(article_id)
            unified = self._build_unified_record(recall, signal, failed)
            unified_records.append(unified)

        # Classify outcomes
        for record in unified_records:
            record["outcome"], record["outcome_detail"] = self._classify_outcome(record)

        # Build summary
        summary = self._build_summary(unified_records, target_date)

        report = {
            "date": str(target_date),
            "sessions": SESSIONS,
            "generated_at": datetime.now(ET_TZ).isoformat(),
            "summary": summary,
            "records": unified_records,
        }

        # Write to file
        output_file = self.output_dir / f"{target_date}.json"
        with open(output_file, "w") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info(
            f"DailyPerformanceJob: Report saved to {output_file}",
            total_records=len(unified_records),
            traded=summary["traded"],
            missed_winners=summary["missed_winners"],
            correctly_skipped=summary["correctly_skipped"],
        )

        return report

    def _load_recall_records(self, target_dt: datetime) -> List[Dict]:
        """Load recall records from all sessions for target date."""
        all_records = []
        for session in SESSIONS:
            file_path = self._get_session_file_path("recall", session, target_dt)
            if file_path.exists():
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    records = data.get("records", [])
                    all_records.extend(records)
                    logger.debug(f"Loaded {len(records)} recall records from {session}")
                except Exception as e:
                    logger.warning(f"Failed to load recall {session}: {e}")
        return all_records

    def _load_signal_records(self, target_dt: datetime) -> List[Dict]:
        """Load signal (executed trade) records from all sessions."""
        all_records = []
        for session in SESSIONS:
            file_path = self._get_session_file_path("signal", session, target_dt)
            if file_path.exists():
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    records = data.get("records", [])
                    all_records.extend(records)
                except Exception as e:
                    logger.warning(f"Failed to load signal {session}: {e}")
        return all_records

    def _load_failed_trade_records(self, target_dt: datetime) -> List[Dict]:
        """Load failed trade records from all sessions."""
        all_records = []
        for session in SESSIONS:
            file_path = self._get_session_file_path("failed_trades", session, target_dt)
            if file_path.exists():
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    records = data.get("records", [])
                    all_records.extend(records)
                except Exception as e:
                    logger.warning(f"Failed to load failed_trades {session}: {e}")
        return all_records

    def _get_session_file_path(self, engine_type: str, session: str, dt: datetime) -> Path:
        """Calculate file path matching StatisticsRepository pattern."""
        et_dt = dt.astimezone(ET_TZ) if dt.tzinfo else ET_TZ.localize(dt)
        year = et_dt.year
        month = et_dt.month
        day = et_dt.day
        week = et_dt.isocalendar()[1]
        return (
            self.stats_dir
            / engine_type
            / str(year)
            / f"{month:02d}"
            / f"week_{week}"
            / f"{day:02d}"
            / session
            / f"{session}.json"
        )

    def _build_unified_record(
        self,
        recall: Dict,
        signal: Optional[Dict],
        failed: Optional[Dict],
    ) -> Dict[str, Any]:
        """
        Build a unified record from recall + optional signal/failed data.

        The recall record is the base — it has all microstructure and outcome data.
        Signal adds trade execution details. Failed adds failure details.
        """
        # Primary ticker (first in list)
        tickers = recall.get("tickers", [])
        ticker = tickers[0] if tickers else None
        ticker_meta = recall.get("ticker_metadata", {}).get(ticker, {}) if ticker else {}

        # Determine decision
        if recall.get("is_traded"):
            decision = "traded"
        elif failed:
            decision = "failed_execution"
        elif recall.get("postfilter_reason"):
            decision = "skipped_postfilter"
        elif recall.get("filter_reason"):
            decision = "skipped_prefilter"
        else:
            decision = "skipped_unknown"

        # Extract highest/lowest from hold period
        peak = recall.get("highest_price_during_hold") or {}
        mae = recall.get("max_adverse_excursion") or {}
        initial_nbbo = recall.get("initial_nbbo") or {}
        price_10min = recall.get("price_check_10min") or {}

        record = {
            # === Identity ===
            "article_id": recall.get("article_id"),
            "title": recall.get("title"),
            "ticker": ticker,
            "tickers": tickers,
            "session": recall.get("session"),

            # === Timing ===
            "published_at": recall.get("published_at"),
            "received_at": recall.get("received_at"),
            "latency_seconds": recall.get("volume_stats", {}).get(ticker, {}).get("pub_to_recv_seconds") if ticker else None,
            "hour": recall.get("hour"),
            "news_source": recall.get("news_source"),

            # === Metadata ===
            "sector": ticker_meta.get("sector"),
            "industry": ticker_meta.get("industry"),
            "market_cap_millions": ticker_meta.get("market_cap_millions"),
            "price": ticker_meta.get("price"),
            "exchange": ticker_meta.get("exchange"),
            "float_shares": recall.get("float_shares"),

            # === Classification ===
            "headline_type": recall.get("headline_type"),
            "ai_classification": recall.get("ai_classification"),

            # === Decision ===
            "decision": decision,
            "filter_reason": recall.get("filter_reason"),
            "postfilter_reason": recall.get("postfilter_reason"),

            # === Initial Market ===
            "initial_bid": initial_nbbo.get("bid"),
            "initial_ask": initial_nbbo.get("ask"),
            "initial_spread_pct": initial_nbbo.get("spread_pct"),
            "initial_mid": initial_nbbo.get("mid"),
            "initial_bid_size": initial_nbbo.get("bid_size"),
            "initial_ask_size": initial_nbbo.get("ask_size"),

            # === Confluence Window (0-2s) ===
            "confluence_score": recall.get("confluence_score"),
            "confluence_volume": recall.get("confluence_volume"),
            "confluence_trade_count": recall.get("confluence_trade_count"),
            "confluence_buy_volume": recall.get("confluence_buy_volume"),
            "confluence_sell_volume": recall.get("confluence_sell_volume"),
            "confluence_buying_pressure_pct": recall.get("confluence_buying_pressure_pct"),
            "confluence_imbalance_ratio": recall.get("confluence_imbalance_ratio"),
            "confluence_price_excursion_pct": recall.get("confluence_price_excursion_pct"),
            "confluence_first_price": recall.get("confluence_first_price"),
            "confluence_max_price": recall.get("confluence_max_price"),
            "confluence_min_price": recall.get("confluence_min_price"),
            "confluence_vwap": recall.get("confluence_vwap"),
            "confluence_initial_spread": recall.get("confluence_initial_spread"),
            "confluence_final_spread": recall.get("confluence_final_spread"),
            "confluence_spread_compression_pct": recall.get("confluence_spread_compression_pct"),
            "confluence_first_trade_latency_ms": recall.get("confluence_first_trade_latency_ms"),
            "confluence_avg_trade_size": recall.get("confluence_avg_trade_size"),
            "confluence_max_trade_gap_ms": recall.get("confluence_max_trade_gap_ms"),
            "confluence_has_volume_surge": recall.get("confluence_has_volume_surge"),
            "confluence_has_price_excursion": recall.get("confluence_has_price_excursion"),
            "confluence_has_buying_pressure": recall.get("confluence_has_buying_pressure"),
            "confluence_last_price": recall.get("confluence_last_price"),
            "confluence_price_direction": recall.get("confluence_price_direction"),
            "confluence_dollar_volume": recall.get("confluence_dollar_volume"),
            "confluence_max_single_trade": recall.get("confluence_max_single_trade"),
            "confluence_median_trade_size": recall.get("confluence_median_trade_size"),
            "confluence_large_trade_pct": recall.get("confluence_large_trade_pct"),
            "confluence_uptick_count": recall.get("confluence_uptick_count"),
            "confluence_downtick_count": recall.get("confluence_downtick_count"),

            # === Volume Context ===
            "volume_distribution_class": recall.get("volume_distribution_class"),
            "single_trade_dominance_pct": recall.get("single_trade_dominance_pct"),
            "remaining_flow_imbalance": recall.get("remaining_flow_imbalance"),
            "remaining_trade_count": recall.get("remaining_trade_count"),
            "remaining_sell_pct": recall.get("remaining_sell_pct"),
            "confluence_volume_float_pct": recall.get("confluence_volume_float_pct"),
            "surge_volume_float_pct": recall.get("surge_volume_float_pct"),
            "quote_churn_per_second": recall.get("quote_churn_per_second"),

            # === Volume Stats (4s window) ===
            "move_type": recall.get("volume_stats", {}).get(ticker, {}).get("move_type") if ticker else None,
            "surge_multiplier": recall.get("volume_stats", {}).get(ticker, {}).get("surge_multiplier") if ticker else None,
            "prior_avg_10min_volume": recall.get("volume_stats", {}).get(ticker, {}).get("prior_avg_10min_volume") if ticker else None,

            # === Front-running Detection ===
            "pub_time_ask": recall.get("pub_time_ask"),
            "recv_time_ask": recall.get("recv_time_ask"),
            "fill_time_ask": recall.get("fill_time_ask"),
            "pub_to_recv_pct": recall.get("pub_to_recv_pct"),
            "recv_to_fill_pct": recall.get("recv_to_fill_pct"),
            "pub_to_recv_latency_ms": recall.get("pub_to_recv_latency_ms"),

            # === Monitoring ===
            "monitoring_status": recall.get("monitoring_status"),
            "surge_detected_at": recall.get("surge_detected_at"),
            "time_to_surge_seconds": recall.get("time_to_surge_seconds"),
            "monitoring_cycles_completed": recall.get("monitoring_cycles_completed"),

            # === Outcome: 10-min price check ===
            "price_10min_ask": price_10min.get("ask"),
            "pnl_10min_pct": price_10min.get("actual_pnl"),
            "moved_1_percent": price_10min.get("moved_1_percent", False),

            # === Peak / MAE during hold ===
            "peak_price": peak.get("price"),
            "peak_pct": peak.get("percent_gain_from_entry"),
            "peak_time": peak.get("timestamp"),
            "mae_price": mae.get("price"),
            "mae_pct": mae.get("percent_loss_from_entry"),
            "mae_time": mae.get("timestamp"),

            # === Trade Execution (from signal, null if not traded) ===
            "trade_id": recall.get("trade_id"),
            "entry_price": signal.get("entry_price") if signal else None,
            "entry_shares": signal.get("entry_shares") if signal else None,
            "entry_amount_usd": signal.get("entry_amount_usd") if signal else None,
            "exit_price": signal.get("exit_price") if signal else None,
            "exit_reason": signal.get("exit_reason") if signal else None,
            "hold_duration_seconds": signal.get("hold_duration_seconds") if signal else None,
            "realized_pnl_usd": signal.get("profit_loss_usd") if signal else None,
            "realized_pnl_pct": signal.get("profit_loss_percent") if signal else None,
            "slippage_from_decision": signal.get("slippage_from_decision") if signal else None,
            "slippage_vs_ask": signal.get("slippage_vs_ask") if signal else None,
            "fill_speed_ms": signal.get("fill_speed_ms") if signal else None,
            "chase_attempts": signal.get("chase_attempts") if signal else None,
            "ai_position_size": signal.get("ai_position_size") if signal else None,
            "is_mega_trade": signal.get("is_mega_trade") if signal else None,

            # === Failed Execution (from failed_trades, null if not failed) ===
            "failure_reason": failed.get("failure_reason") if failed else None,
            "ladder_attempts": failed.get("ladder_attempts") if failed else None,

            # === Filter checkpoint values (for postfilter analysis) ===
            "filter_values": recall.get("filter_values"),
            "filters_checked": recall.get("filters_checked"),

            # === Outcome (populated by _classify_outcome) ===
            "outcome": None,
            "outcome_detail": None,
        }

        return record

    def _classify_outcome(self, record: Dict) -> tuple[str, str]:
        """
        Classify a record into one of 5 outcome categories.

        Returns:
            Tuple of (outcome, outcome_detail)
        """
        decision = record.get("decision", "")
        peak_pct = record.get("peak_pct") or 0
        realized_pnl_pct = record.get("realized_pnl_pct")
        moved_1_pct = record.get("moved_1_percent", False)
        title = (record.get("title") or "")[:80]
        headline_type = record.get("headline_type") or "unknown"
        filter_reason = record.get("filter_reason") or record.get("postfilter_reason") or ""

        if decision == "traded":
            if realized_pnl_pct is not None and realized_pnl_pct >= 0:
                return "traded_well", f"P&L {realized_pnl_pct:+.1f}%, peak {peak_pct:+.1f}%"
            elif realized_pnl_pct is not None:
                return "traded_poorly", f"P&L {realized_pnl_pct:+.1f}%, peak {peak_pct:+.1f}%"
            else:
                # No exit yet (position still open or data missing)
                return "traded_pending", f"peak {peak_pct:+.1f}%, awaiting exit"

        if decision == "failed_execution":
            failure = record.get("failure_reason") or "unknown"
            return "failed_execution", f"{failure} — peak {peak_pct:+.1f}%"

        # Skipped — was it a miss or correct?
        if moved_1_pct or peak_pct >= 1.0:
            return "missed_winner", f"{filter_reason} — {headline_type}, peak {peak_pct:+.1f}%"

        return "correctly_skipped", f"{filter_reason} — {headline_type}"

    def _build_summary(self, records: List[Dict], target_date: date) -> Dict[str, Any]:
        """Build summary statistics from classified records."""
        total = len(records)
        traded = [r for r in records if r["outcome"] in ("traded_well", "traded_poorly", "traded_pending")]
        traded_well = [r for r in records if r["outcome"] == "traded_well"]
        traded_poorly = [r for r in records if r["outcome"] == "traded_poorly"]
        missed = [r for r in records if r["outcome"] == "missed_winner"]
        correctly_skipped = [r for r in records if r["outcome"] == "correctly_skipped"]
        failed = [r for r in records if r["outcome"] == "failed_execution"]

        total_pnl = sum(r.get("realized_pnl_usd") or 0 for r in traded)
        best_missed = max((r.get("peak_pct") or 0 for r in missed), default=0)
        worst_trade = min((r.get("realized_pnl_pct") or 0 for r in traded_poorly), default=0)

        # Filter breakdown for missed winners
        missed_filter_breakdown = {}
        for r in missed:
            reason = r.get("filter_reason") or r.get("postfilter_reason") or "unknown"
            missed_filter_breakdown[reason] = missed_filter_breakdown.get(reason, 0) + 1

        # Sector breakdown for missed winners
        missed_sector_breakdown = {}
        for r in missed:
            sector = r.get("sector") or "unknown"
            missed_sector_breakdown[sector] = missed_sector_breakdown.get(sector, 0) + 1

        # Headline type breakdown for missed winners
        missed_headline_breakdown = {}
        for r in missed:
            ht = r.get("headline_type") or "unknown"
            missed_headline_breakdown[ht] = missed_headline_breakdown.get(ht, 0) + 1

        return {
            "date": str(target_date),
            "total_articles": total,
            "traded": len(traded),
            "traded_well": len(traded_well),
            "traded_poorly": len(traded_poorly),
            "missed_winners": len(missed),
            "correctly_skipped": len(correctly_skipped),
            "failed_execution": len(failed),
            "total_pnl_usd": round(total_pnl, 2),
            "best_missed_pct": round(best_missed, 2),
            "worst_trade_pct": round(worst_trade, 2),
            "missed_filter_breakdown": missed_filter_breakdown,
            "missed_sector_breakdown": missed_sector_breakdown,
            "missed_headline_breakdown": missed_headline_breakdown,
        }
