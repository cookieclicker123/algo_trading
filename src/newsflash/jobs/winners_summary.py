"""
Winners Summary - Human-readable list of trades and potential winners.

Creates a simple, scannable file with:
- All executed trades
- All potential winners (IMMINENT + moved 10%+ in 10 min period)

Format: One line per trade, easy to scan for patterns.

Output: tmp/winners/{date}.txt
"""
import asyncio
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import pytz

from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class WinnersSummaryJob:
    """
    Generates human-readable winners summary.

    Output format (one line per trade/opportunity):
    [TRADED/MISSED] | {ticker} | {pnl}% | {industry} | ${market_cap}M | ${price} | {headline_type} | {headline}
    """

    def __init__(
        self,
        signal_path: Path = Path("tmp/statistics/signal"),
        recall_path: Path = Path("tmp/statistics/recall"),
        output_path: Path = Path("tmp/winners"),
    ):
        self.signal_path = signal_path
        self.recall_path = recall_path
        self.output_path = output_path
        self.output_path.mkdir(parents=True, exist_ok=True)

    def load_signal_records(self, target_date: date) -> List[Dict]:
        """Load executed trades for a date."""
        records = []
        year = target_date.year
        month = target_date.month
        day = target_date.day
        week = target_date.isocalendar()[1]

        for session in ["premarket", "market_hours", "postmarket"]:
            file_path = (
                self.signal_path / str(year) / f"{month:02d}" /
                f"week_{week}" / f"{day:02d}" / session / f"{session}.json"
            )
            if file_path.exists():
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    for record in data.get("records", []):
                        record["_session"] = session
                        record["_type"] = "signal"
                        records.append(record)
                except Exception as e:
                    logger.error(f"Error loading {file_path}: {e}")

        return records

    def load_recall_records(self, target_date: date) -> List[Dict]:
        """Load potential winners (IMMINENT + moved 10%+)."""
        records = []
        year = target_date.year
        month = target_date.month
        day = target_date.day
        week = target_date.isocalendar()[1]

        for session in ["premarket", "market_hours", "postmarket"]:
            file_path = (
                self.recall_path / str(year) / f"{month:02d}" /
                f"week_{week}" / f"{day:02d}" / session / f"{session}.json"
            )
            if file_path.exists():
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    for record in data.get("records", []):
                        # Only include IMMINENT that moved 10%+
                        if record.get("ai_classification") != "IMMINENT":
                            continue

                        # Check if moved 10%+ (using highest_price_during_hold)
                        peak_data = record.get("highest_price_during_hold", {})
                        peak_pct = peak_data.get("percent_gain_from_entry") if peak_data else None

                        # Also check price_check_10min for older records
                        if peak_pct is None:
                            price_check = record.get("price_check_10min", {})
                            if price_check:
                                pct_change = price_check.get("percent_change", 0)
                                if pct_change and pct_change >= 10:
                                    peak_pct = pct_change

                        if peak_pct is not None and peak_pct >= 10:
                            record["_session"] = session
                            record["_type"] = "recall"
                            record["_peak_pct"] = peak_pct
                            records.append(record)
                except Exception as e:
                    logger.error(f"Error loading {file_path}: {e}")

        return records

    def format_trade_line(self, record: Dict) -> str:
        """Format a single trade/opportunity as one line."""
        is_traded = record["_type"] == "signal"
        status = "TRADED" if is_traded else "MISSED"

        # Get ticker
        ticker = record.get("ticker") or (record.get("tickers", ["???"])[0])

        # Get P&L or peak
        if is_traded:
            pnl = record.get("profit_loss_percent")
            pnl_str = f"{pnl:+.1f}%" if pnl is not None else "open"
        else:
            peak = record.get("_peak_pct", 0)
            pnl_str = f"+{peak:.1f}% peak"

        # Get metadata
        meta = record.get("ticker_metadata", {})
        if isinstance(meta, dict) and ticker in meta:
            meta = meta[ticker]
        elif isinstance(meta, dict) and len(meta) > 0:
            # Use first ticker's metadata
            meta = list(meta.values())[0] if meta.values() else {}

        industry = meta.get("industry", "???") if isinstance(meta, dict) else "???"
        market_cap = meta.get("market_cap_millions") if isinstance(meta, dict) else None
        price = meta.get("price") if isinstance(meta, dict) else None

        cap_str = f"${market_cap:.0f}M" if market_cap else "$???M"
        price_str = f"${price:.2f}" if price else "$???"

        # Get headline info
        headline = record.get("title") or record.get("headline") or "???"
        headline_type = record.get("headline_type") or "unknown"

        # Truncate headline for readability
        max_headline_len = 80
        if len(headline) > max_headline_len:
            headline = headline[:max_headline_len-3] + "..."

        # Format line
        return (
            f"[{status:6}] | {ticker:6} | {pnl_str:12} | "
            f"{industry[:30]:30} | {cap_str:10} | {price_str:8} | "
            f"{headline_type[:20]:20} | {headline}"
        )

    async def run(self, target_date: Optional[date] = None) -> Optional[Path]:
        """
        Generate winners summary for a date.

        Args:
            target_date: Date to analyze (default: yesterday)

        Returns:
            Path to output file, or None if no data
        """
        if target_date is None:
            et_tz = pytz.timezone("America/New_York")
            now_et = datetime.now(et_tz)
            target_date = (now_et - timedelta(days=1)).date()

        logger.info(f"Generating winners summary for {target_date}")

        # Load data
        signal_records = self.load_signal_records(target_date)
        recall_records = self.load_recall_records(target_date)

        if not signal_records and not recall_records:
            logger.info(f"No trades or potential winners for {target_date}")
            return None

        # Generate output
        lines = []
        lines.append("=" * 160)
        lines.append(f"WINNERS SUMMARY: {target_date}")
        lines.append(f"Generated: {datetime.now().isoformat()}")
        lines.append("=" * 160)
        lines.append("")

        # Executed trades section
        lines.append("-" * 160)
        lines.append(f"EXECUTED TRADES ({len(signal_records)} total)")
        lines.append("-" * 160)
        lines.append(
            f"{'[STATUS]':8} | {'TICKER':6} | {'P&L':12} | "
            f"{'INDUSTRY':30} | {'MKT CAP':10} | {'PRICE':8} | "
            f"{'HEADLINE TYPE':20} | HEADLINE"
        )
        lines.append("-" * 160)

        for record in sorted(signal_records, key=lambda r: r.get("executed_at", "")):
            lines.append(self.format_trade_line(record))

        lines.append("")

        # Missed winners section
        lines.append("-" * 160)
        lines.append(f"POTENTIAL WINNERS - MISSED ({len(recall_records)} with 10%+ peak)")
        lines.append("-" * 160)
        lines.append(
            f"{'[STATUS]':8} | {'TICKER':6} | {'PEAK':12} | "
            f"{'INDUSTRY':30} | {'MKT CAP':10} | {'PRICE':8} | "
            f"{'HEADLINE TYPE':20} | HEADLINE"
        )
        lines.append("-" * 160)

        for record in sorted(recall_records, key=lambda r: -r.get("_peak_pct", 0)):
            lines.append(self.format_trade_line(record))

        lines.append("")
        lines.append("=" * 160)
        lines.append(f"Total: {len(signal_records)} traded, {len(recall_records)} missed with 10%+ potential")
        lines.append("=" * 160)

        # Write to file
        output_file = self.output_path / f"{target_date}.txt"
        with open(output_file, "w") as f:
            f.write("\n".join(lines))

        logger.info(
            f"Winners summary saved",
            file=str(output_file),
            traded=len(signal_records),
            missed_winners=len(recall_records),
        )

        return output_file


async def run_winners_summary(target_date: Optional[date] = None) -> Optional[Path]:
    """Entry point for winners summary generation."""
    job = WinnersSummaryJob()
    return await job.run(target_date)


if __name__ == "__main__":
    import sys
    target = None
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    result = asyncio.run(run_winners_summary(target))
    if result:
        print(f"Saved to: {result}")
        # Also print the file
        with open(result) as f:
            print(f.read())
