"""
Daily Analytics Job - Runs at 1:01am UK time after postmarket closes.

Analyzes all trades from the day and saves structured analytics to JSON files.
Includes market regime data (NASDAQ 100 / S&P 500 performance) for correlation analysis.

Schedule: 1:01am UK time (after 8pm ET postmarket close = 1am UK)
"""
import asyncio
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
import pytz

from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# UK timezone for scheduling
UK_TZ = pytz.timezone("Europe/London")
ET_TZ = pytz.timezone("America/New_York")


@dataclass
class MarketRegime:
    """Daily market regime data for correlation analysis."""
    date: str

    # NASDAQ 100 (QQQ as proxy)
    nasdaq_open: Optional[float] = None
    nasdaq_close: Optional[float] = None
    nasdaq_change_pct: Optional[float] = None

    # S&P 500 (SPY as proxy)
    sp500_open: Optional[float] = None
    sp500_close: Optional[float] = None
    sp500_change_pct: Optional[float] = None

    # Regime classification
    regime: str = "neutral"  # bullish, bearish, neutral
    consecutive_bearish_days: int = 0
    consecutive_bullish_days: int = 0


@dataclass
class TradeAnalytics:
    """Analytics for a single trade."""
    trade_id: str
    ticker: str
    date: str
    session: str

    # Entry
    entry_price: float
    entry_time: str
    entry_shares: int
    position_size_usd: float

    # Exit
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    hold_duration_seconds: Optional[float] = None

    # P&L
    profit_loss_usd: Optional[float] = None
    profit_loss_pct: Optional[float] = None

    # Peak tracking
    peak_price: Optional[float] = None
    peak_profit_pct: Optional[float] = None
    money_left_on_table_pct: Optional[float] = None

    # Ticker metadata
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap_millions: Optional[float] = None
    price_at_entry: Optional[float] = None
    exchange: Optional[str] = None

    # Headline
    headline: Optional[str] = None
    headline_type: Optional[str] = None

    # Confluence signals
    confluence_score: Optional[int] = None
    confluence_volume: Optional[int] = None
    confluence_buying_pressure_pct: Optional[float] = None
    confluence_imbalance_ratio: Optional[float] = None
    confluence_price_excursion_pct: Optional[float] = None

    # Spread/slippage
    spread_at_fill_pct: Optional[float] = None
    slippage_vs_mid_pct: Optional[float] = None

    # Exit quality classification
    exit_quality: Optional[str] = None  # optimal, good, late, very_late, too_early


@dataclass
class DailyAnalyticsReport:
    """Complete daily analytics report."""
    date: str
    generated_at: str

    # Market regime
    market_regime: MarketRegime

    # Trade summary
    total_trades: int
    profitable_trades: int
    losing_trades: int
    win_rate_pct: float
    total_pnl_usd: float
    avg_pnl_per_trade_usd: float

    # Peak analysis
    avg_peak_profit_pct: float
    avg_exit_profit_pct: float
    avg_money_left_on_table_pct: float

    # Breakdown by segment
    by_industry: Dict[str, Dict[str, Any]]
    by_sector: Dict[str, Dict[str, Any]]
    by_market_cap_bucket: Dict[str, Dict[str, Any]]
    by_headline_type: Dict[str, Dict[str, Any]]
    by_session: Dict[str, Dict[str, Any]]

    # Individual trades
    trades: List[TradeAnalytics]


class DailyAnalyticsJob:
    """
    Daily analytics job that runs at 1:01am UK time.

    Collects all trade data from the day, enriches with market regime data,
    and saves structured analytics for pattern analysis.
    """

    def __init__(
        self,
        signal_data_path: Path = Path("tmp/statistics/signal"),
        output_path: Path = Path("tmp/analytics/daily"),
        alpaca_api_key: Optional[str] = None,
        alpaca_api_secret: Optional[str] = None,
    ):
        self.signal_data_path = signal_data_path
        self.output_path = output_path
        self.output_path.mkdir(parents=True, exist_ok=True)

        # Alpaca credentials for market data
        self.alpaca_api_key = alpaca_api_key
        self.alpaca_api_secret = alpaca_api_secret

        # Track consecutive regime days (loaded from previous reports)
        self._consecutive_bearish = 0
        self._consecutive_bullish = 0

    async def fetch_market_regime(self, target_date: date) -> MarketRegime:
        """Fetch NASDAQ 100 and S&P 500 performance for the day."""
        regime = MarketRegime(date=target_date.isoformat())

        try:
            # Use yfinance for market data (simple, no auth needed)
            import yfinance as yf

            # Get data for QQQ (NASDAQ 100 proxy) and SPY (S&P 500 proxy)
            start_date = target_date
            end_date = target_date + timedelta(days=1)

            for symbol, prefix in [("QQQ", "nasdaq"), ("SPY", "sp500")]:
                try:
                    ticker = yf.Ticker(symbol)
                    hist = ticker.history(start=start_date, end=end_date, interval="1d")

                    if len(hist) > 0:
                        row = hist.iloc[0]
                        open_price = float(row["Open"])
                        close_price = float(row["Close"])
                        change_pct = ((close_price - open_price) / open_price) * 100

                        setattr(regime, f"{prefix}_open", round(open_price, 2))
                        setattr(regime, f"{prefix}_close", round(close_price, 2))
                        setattr(regime, f"{prefix}_change_pct", round(change_pct, 2))
                except Exception as e:
                    logger.warning(f"Failed to fetch {symbol} data: {e}")

            # Classify regime based on both indices
            nasdaq_change = regime.nasdaq_change_pct or 0
            sp500_change = regime.sp500_change_pct or 0
            avg_change = (nasdaq_change + sp500_change) / 2

            if avg_change > 0.5:
                regime.regime = "bullish"
                self._consecutive_bullish += 1
                self._consecutive_bearish = 0
            elif avg_change < -0.5:
                regime.regime = "bearish"
                self._consecutive_bearish += 1
                self._consecutive_bullish = 0
            else:
                regime.regime = "neutral"
                # Don't reset streaks on neutral days

            regime.consecutive_bearish_days = self._consecutive_bearish
            regime.consecutive_bullish_days = self._consecutive_bullish

        except ImportError:
            logger.warning("yfinance not installed - market regime data unavailable")
        except Exception as e:
            logger.error(f"Error fetching market regime: {e}")

        return regime

    def load_signal_records(self, target_date: date) -> List[Dict[str, Any]]:
        """Load all signal records for a specific date."""
        records = []

        # Signal files are organized by year/month/week_N/day/session/session.json
        year = target_date.year
        month = target_date.month
        day = target_date.day
        week = target_date.isocalendar()[1]

        base_path = self.signal_data_path / str(year) / f"{month:02d}" / f"week_{week}" / f"{day:02d}"

        for session in ["premarket", "market_hours", "postmarket"]:
            session_file = base_path / session / f"{session}.json"
            if session_file.exists():
                try:
                    with open(session_file) as f:
                        data = json.load(f)
                    if "records" in data:
                        for record in data["records"]:
                            record["_session"] = session
                        records.extend(data["records"])
                except Exception as e:
                    logger.error(f"Error loading {session_file}: {e}")

        return records

    def process_record(self, record: Dict[str, Any]) -> TradeAnalytics:
        """Convert a signal record to TradeAnalytics."""
        meta = record.get("ticker_metadata", {}) or {}

        # Calculate peak and money left on table
        entry_price = record.get("entry_price", 0)
        exit_price = record.get("exit_price")
        exit_profit_pct = record.get("profit_loss_percent")

        # Get peak from highest_price_during_hold
        peak_data = record.get("highest_price_during_hold") or {}
        peak_price = peak_data.get("price") if peak_data else None
        peak_profit_pct = peak_data.get("percent_gain_from_entry") if peak_data else None

        # If no peak data, estimate from price snapshots
        if peak_price is None and entry_price:
            candidate_prices = []
            for key in ["price_at_5s", "price_at_10s", "price_at_30s", "price_at_1min"]:
                if record.get(key):
                    candidate_prices.append(record[key])
            if exit_price:
                candidate_prices.append(exit_price)
            if candidate_prices:
                peak_price = max(candidate_prices)
                peak_profit_pct = ((peak_price - entry_price) / entry_price) * 100 if entry_price else None

        # Calculate money left on table
        money_left = None
        if peak_profit_pct is not None and exit_profit_pct is not None:
            money_left = peak_profit_pct - exit_profit_pct

        # Classify exit quality
        exit_quality = None
        if money_left is not None:
            if money_left < 1:
                exit_quality = "optimal"
            elif money_left < 3:
                exit_quality = "good"
            elif money_left < 5:
                exit_quality = "late"
            elif money_left < 10:
                exit_quality = "very_late"
            else:
                exit_quality = "very_late"

        return TradeAnalytics(
            trade_id=record.get("trade_id", ""),
            ticker=record.get("ticker", ""),
            date=record.get("executed_at", "")[:10] if record.get("executed_at") else "",
            session=record.get("_session", ""),
            entry_price=entry_price,
            entry_time=record.get("executed_at", ""),
            entry_shares=record.get("entry_shares", 0),
            position_size_usd=record.get("entry_amount_usd", 0),
            exit_price=exit_price,
            exit_time=record.get("exited_at"),
            exit_reason=record.get("exit_reason"),
            hold_duration_seconds=record.get("hold_duration_seconds"),
            profit_loss_usd=record.get("profit_loss_usd"),
            profit_loss_pct=exit_profit_pct,
            peak_price=peak_price,
            peak_profit_pct=peak_profit_pct,
            money_left_on_table_pct=money_left,
            sector=meta.get("sector"),
            industry=meta.get("industry"),
            market_cap_millions=meta.get("market_cap_millions"),
            price_at_entry=meta.get("price"),
            exchange=meta.get("exchange"),
            headline=record.get("headline"),
            headline_type=record.get("headline_type"),
            confluence_score=record.get("confluence_score"),
            confluence_volume=record.get("confluence_volume"),
            confluence_buying_pressure_pct=record.get("confluence_buying_pressure_pct"),
            confluence_imbalance_ratio=record.get("confluence_imbalance_ratio"),
            confluence_price_excursion_pct=record.get("confluence_price_excursion_pct"),
            spread_at_fill_pct=record.get("spread_at_fill"),
            slippage_vs_mid_pct=record.get("slippage_vs_mid"),
            exit_quality=exit_quality,
        )

    def get_market_cap_bucket(self, cap: Optional[float]) -> str:
        """Categorize market cap into bucket."""
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

    def calculate_segment_stats(self, trades: List[TradeAnalytics]) -> Dict[str, Any]:
        """Calculate summary statistics for a segment."""
        if not trades:
            return {"count": 0}

        completed = [t for t in trades if t.exit_price is not None]

        profits = [t.profit_loss_pct for t in completed if t.profit_loss_pct is not None]
        peaks = [t.peak_profit_pct for t in completed if t.peak_profit_pct is not None]
        money_left = [t.money_left_on_table_pct for t in completed if t.money_left_on_table_pct is not None]

        winners = len([p for p in profits if p > 0])

        return {
            "count": len(trades),
            "completed": len(completed),
            "winners": winners,
            "losers": len(completed) - winners,
            "win_rate_pct": round((winners / len(completed) * 100) if completed else 0, 1),
            "avg_profit_pct": round(sum(profits) / len(profits), 2) if profits else None,
            "avg_peak_pct": round(sum(peaks) / len(peaks), 2) if peaks else None,
            "avg_money_left_pct": round(sum(money_left) / len(money_left), 2) if money_left else None,
        }

    async def run(self, target_date: Optional[date] = None) -> Optional[DailyAnalyticsReport]:
        """
        Run daily analytics for a specific date.

        Args:
            target_date: Date to analyze (defaults to yesterday in ET timezone)
        """
        # Default to yesterday (since we run at 1am, we analyze the previous trading day)
        if target_date is None:
            now_et = datetime.now(ET_TZ)
            target_date = (now_et - timedelta(days=1)).date()

        logger.info(f"Running daily analytics for {target_date}")

        # Load signal records
        records = self.load_signal_records(target_date)
        if not records:
            logger.info(f"No trades found for {target_date}")
            return None

        # Process records into analytics
        trades = [self.process_record(r) for r in records]

        # Fetch market regime
        market_regime = await self.fetch_market_regime(target_date)

        # Calculate overall stats
        completed_trades = [t for t in trades if t.exit_price is not None]
        profits = [t.profit_loss_pct for t in completed_trades if t.profit_loss_pct is not None]
        peaks = [t.peak_profit_pct for t in completed_trades if t.peak_profit_pct is not None]
        money_left = [t.money_left_on_table_pct for t in completed_trades if t.money_left_on_table_pct is not None]
        pnl_usd = [t.profit_loss_usd for t in completed_trades if t.profit_loss_usd is not None]

        winners = len([p for p in profits if p > 0])

        # Group by segments
        from collections import defaultdict

        by_industry = defaultdict(list)
        by_sector = defaultdict(list)
        by_market_cap = defaultdict(list)
        by_headline_type = defaultdict(list)
        by_session = defaultdict(list)

        for t in trades:
            by_industry[t.industry or "Unknown"].append(t)
            by_sector[t.sector or "Unknown"].append(t)
            by_market_cap[self.get_market_cap_bucket(t.market_cap_millions)].append(t)
            by_headline_type[t.headline_type or "unknown"].append(t)
            by_session[t.session or "unknown"].append(t)

        # Create report
        report = DailyAnalyticsReport(
            date=target_date.isoformat(),
            generated_at=datetime.now(UK_TZ).isoformat(),
            market_regime=market_regime,
            total_trades=len(trades),
            profitable_trades=winners,
            losing_trades=len(completed_trades) - winners,
            win_rate_pct=round((winners / len(completed_trades) * 100) if completed_trades else 0, 1),
            total_pnl_usd=round(sum(pnl_usd), 2) if pnl_usd else 0,
            avg_pnl_per_trade_usd=round(sum(pnl_usd) / len(pnl_usd), 2) if pnl_usd else 0,
            avg_peak_profit_pct=round(sum(peaks) / len(peaks), 2) if peaks else 0,
            avg_exit_profit_pct=round(sum(profits) / len(profits), 2) if profits else 0,
            avg_money_left_on_table_pct=round(sum(money_left) / len(money_left), 2) if money_left else 0,
            by_industry={k: self.calculate_segment_stats(v) for k, v in by_industry.items()},
            by_sector={k: self.calculate_segment_stats(v) for k, v in by_sector.items()},
            by_market_cap_bucket={k: self.calculate_segment_stats(v) for k, v in by_market_cap.items()},
            by_headline_type={k: self.calculate_segment_stats(v) for k, v in by_headline_type.items()},
            by_session={k: self.calculate_segment_stats(v) for k, v in by_session.items()},
            trades=trades,
        )

        # Save to JSON
        output_file = self.output_path / f"{target_date.isoformat()}.json"

        # Convert dataclasses to dicts for JSON serialization
        report_dict = {
            "date": report.date,
            "generated_at": report.generated_at,
            "market_regime": asdict(report.market_regime),
            "summary": {
                "total_trades": report.total_trades,
                "profitable_trades": report.profitable_trades,
                "losing_trades": report.losing_trades,
                "win_rate_pct": report.win_rate_pct,
                "total_pnl_usd": report.total_pnl_usd,
                "avg_pnl_per_trade_usd": report.avg_pnl_per_trade_usd,
                "avg_peak_profit_pct": report.avg_peak_profit_pct,
                "avg_exit_profit_pct": report.avg_exit_profit_pct,
                "avg_money_left_on_table_pct": report.avg_money_left_on_table_pct,
            },
            "by_industry": report.by_industry,
            "by_sector": report.by_sector,
            "by_market_cap_bucket": report.by_market_cap_bucket,
            "by_headline_type": report.by_headline_type,
            "by_session": report.by_session,
            "trades": [asdict(t) for t in report.trades],
        }

        with open(output_file, "w") as f:
            json.dump(report_dict, f, indent=2, default=str)

        logger.info(
            f"Daily analytics saved",
            date=target_date.isoformat(),
            output_file=str(output_file),
            total_trades=report.total_trades,
            win_rate=f"{report.win_rate_pct}%",
            total_pnl=f"${report.total_pnl_usd}",
            market_regime=market_regime.regime,
        )

        return report


async def run_daily_analytics(target_date: Optional[date] = None):
    """Entry point for running daily analytics."""
    job = DailyAnalyticsJob()
    return await job.run(target_date)


if __name__ == "__main__":
    # Run for yesterday by default
    asyncio.run(run_daily_analytics())
