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
    slippage_from_decision_pct: Optional[float] = None  # TRUE slippage: fill vs decision price

    # Order book depth
    decision_bid_size: Optional[int] = None
    decision_ask_size: Optional[int] = None
    order_vs_depth_ratio: Optional[float] = None  # >1 means order exceeds displayed liquidity

    # Exit quality classification
    exit_quality: Optional[str] = None  # optimal, good, late, very_late, too_early

    # Filter checkpoint values (for hit rate analysis)
    filter_values: Optional[Dict[str, Any]] = None
    filters_checked: Optional[Dict[str, bool]] = None


@dataclass
class RecallAnalytics:
    """Analytics for a missed opportunity (false negative)."""
    article_id: str
    ticker: str
    date: str
    session: str
    headline: Optional[str] = None
    headline_type: Optional[str] = None

    # Why we skipped
    skip_reason: Optional[str] = None
    skip_filter: Optional[str] = None

    # Late-trade candidate flag (activity-gate skip but stock moved >= threshold)
    late_trade_candidate: Optional[Dict[str, Any]] = None

    # Retrospective AI classification (for articles filtered pre-classification
    # that ended up moving >=10% during the 10-min hold).
    # Shape mirrors retrospective_classifier.RetrospectiveClassifier.classify().
    retrospective_classification: Optional[Dict[str, Any]] = None

    # What we missed
    price_at_skip: Optional[float] = None
    max_price_after: Optional[float] = None
    potential_gain_pct: Optional[float] = None
    potential_gain_usd: Optional[float] = None  # Based on typical position size

    # Market conditions at skip
    spread_at_skip_pct: Optional[float] = None
    decision_ask_size: Optional[int] = None

    # Ticker metadata
    sector: Optional[str] = None
    industry: Optional[str] = None

    # Filter checkpoint values (for FN/TN analysis)
    filter_values: Optional[Dict[str, Any]] = None
    filters_checked: Optional[Dict[str, bool]] = None


@dataclass
class DailyAnalyticsReport:
    """Complete daily analytics report."""
    date: str
    generated_at: str

    # Market regime
    market_regime: MarketRegime

    # === CONFUSION MATRIX SUMMARY ===
    # TP: Traded, made money | FP: Traded, lost money
    # TN: Skipped, would have lost | FN: Skipped, would have made money
    true_positives: int = 0  # Profitable trades
    false_positives: int = 0  # Losing trades
    false_negatives: int = 0  # Missed opportunities (from recall data)
    # TN not tracked (would require simulating all skipped trades)

    # Trade summary (TP + FP)
    total_trades: int = 0
    profitable_trades: int = 0
    losing_trades: int = 0
    win_rate_pct: float = 0.0
    total_pnl_usd: float = 0.0
    avg_pnl_per_trade_usd: float = 0.0

    # Peak analysis
    avg_peak_profit_pct: float = 0.0
    avg_exit_profit_pct: float = 0.0
    avg_money_left_on_table_pct: float = 0.0

    # Slippage analysis (new)
    avg_slippage_from_decision_pct: float = 0.0
    max_slippage_from_decision_pct: float = 0.0

    # Depth analysis (new)
    avg_order_vs_depth_ratio: float = 0.0
    avg_decision_ask_size: float = 0.0
    pct_orders_exceed_depth: float = 0.0

    # === FILTER HIT RATE ANALYSIS ===
    # Compare filter value distributions between TP/FP to identify discriminating filters
    filter_analysis: Dict[str, Dict[str, Any]] = None
    # Structure: {
    #   "spread_pct": {"tp_avg": 1.2, "tp_max": 3.0, "fp_avg": 2.8, "fp_max": 4.5, "discriminates": True},
    #   "pub_to_recv_pct": {...},
    #   ...
    # }

    # Missed opportunity summary (FN)
    total_missed_opportunities: int = 0
    avg_missed_gain_pct: float = 0.0
    total_missed_gain_usd: float = 0.0

    # Breakdown by segment
    by_industry: Dict[str, Dict[str, Any]] = None
    by_sector: Dict[str, Dict[str, Any]] = None
    by_market_cap_bucket: Dict[str, Dict[str, Any]] = None
    by_headline_type: Dict[str, Dict[str, Any]] = None
    by_session: Dict[str, Dict[str, Any]] = None

    # Individual data
    trades: List[TradeAnalytics] = None
    missed_opportunities: List[RecallAnalytics] = None


class DailyAnalyticsJob:
    """
    Daily analytics job that runs at 1:01am UK time.

    Collects all trade data from the day, enriches with market regime data,
    and saves structured analytics for pattern analysis.
    """

    @staticmethod
    def _summarize_late_trade_candidates(
        missed_opportunities: Optional[List["RecallAnalytics"]],
    ) -> Dict[str, Any]:
        """
        Surface post-classification tape-level skips that peaked anyway.
        Flag is stamped in repository at 10-min check using peak gain (not 10-min
        change) so slow-wake winners that peaked-then-faded still surface.
        Top-level navigation aid for review.
        """
        if not missed_opportunities:
            return {"count": 0, "total_peak_gain_pct": 0.0, "by_headline_type": {}, "examples": []}

        examples = []
        by_ht: Dict[str, int] = {}
        total_pct = 0.0
        for m in missed_opportunities:
            ltc = getattr(m, "late_trade_candidate", None)
            if not ltc:
                continue
            gain = ltc.get("peak_gain_pct") or 0
            total_pct += gain
            ht = ltc.get("headline_type") or m.headline_type or "unknown"
            by_ht[ht] = by_ht.get(ht, 0) + 1
            examples.append({
                "article_id": m.article_id,
                "ticker": m.ticker,
                "headline": m.headline,
                "headline_type": ht,
                "session": m.session,
                "peak_gain_pct": gain,
                "time_to_peak_seconds": ltc.get("time_to_peak_seconds"),
                "ten_min_gain_pct": ltc.get("ten_min_gain_pct"),
                "block_reason": ltc.get("block_reason"),
                "block_telemetry": ltc.get("block_telemetry"),
                "monitoring_status": ltc.get("monitoring_status"),
                "pub_to_recv_seconds": ltc.get("pub_to_recv_seconds"),
            })
        examples.sort(key=lambda x: x["peak_gain_pct"], reverse=True)
        return {
            "count": len(examples),
            "total_peak_gain_pct": round(total_pct, 2),
            "by_headline_type": by_ht,
            "examples": examples,
        }

    @staticmethod
    def _summarize_retrospective_fns(
        missed_opportunities: Optional[List["RecallAnalytics"]],
    ) -> Dict[str, Any]:
        """
        Summarise retrospective_classification findings across recall records.

        Captures the "what would the AI have done?" signal on articles that
        got rejected by prefilter but moved >=10% during the 10-min hold.
        Surfaces true false negatives (AI says TRADE or HC bypass) separately
        from legitimate skips (AI also says SKIP).
        """
        if not missed_opportunities:
            return {
                "total_classified": 0,
                "would_have_traded": 0,
                "hc_bypass_count": 0,
                "sector_trade_count": 0,
                "sector_skip_count": 0,
                "tickers_would_have_traded": [],
            }

        would_trade = []
        hc_count = 0
        sector_trade = 0
        sector_skip = 0
        classified = 0

        for m in missed_opportunities:
            retro = m.retrospective_classification or {}
            if not retro.get("triage_type"):
                continue
            classified += 1
            hc = retro.get("hc_bypass") or {}
            if hc.get("is_hc"):
                hc_count += 1
                would_trade.append({
                    "ticker": m.ticker,
                    "triage_type": retro["triage_type"],
                    "hc_size": hc.get("size"),
                    "excursion_pct": retro.get("max_mid_excursion_pct"),
                })
            else:
                sd = retro.get("sector_decision") or {}
                if sd.get("classification") == "TRADE":
                    sector_trade += 1
                    would_trade.append({
                        "ticker": m.ticker,
                        "triage_type": retro["triage_type"],
                        "sector_size": sd.get("size"),
                        "sector": sd.get("sector"),
                        "industry": sd.get("industry"),
                        "excursion_pct": retro.get("max_mid_excursion_pct"),
                    })
                elif sd.get("classification") == "SKIP":
                    sector_skip += 1

        return {
            "total_classified": classified,
            "would_have_traded": len(would_trade),
            "hc_bypass_count": hc_count,
            "sector_trade_count": sector_trade,
            "sector_skip_count": sector_skip,
            "tickers_would_have_traded": would_trade,
        }

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

    def load_recall_records(self, target_date: date) -> List[Dict[str, Any]]:
        """Load all recall records (missed opportunities / false negatives) for a specific date."""
        records = []

        # Recall files are organized by year/month/week_N/day/session/session.json
        year = target_date.year
        month = target_date.month
        day = target_date.day
        week = target_date.isocalendar()[1]

        recall_path = Path("tmp/statistics/recall")
        base_path = recall_path / str(year) / f"{month:02d}" / f"week_{week}" / f"{day:02d}"

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
                    logger.error(f"Error loading recall file {session_file}: {e}")

        return records

    def process_recall_record(self, record: Dict[str, Any]) -> RecallAnalytics:
        """Convert a recall record to RecallAnalytics."""
        meta = record.get("ticker_metadata", {}) or {}

        # Calculate potential gain
        price_at_skip = record.get("price_at_classification") or record.get("recv_time_ask")
        max_price = record.get("max_price_1min") or record.get("max_price_5min")
        potential_gain_pct = None
        potential_gain_usd = None

        if price_at_skip and max_price and price_at_skip > 0:
            potential_gain_pct = ((max_price - price_at_skip) / price_at_skip) * 100
            # Estimate USD gain based on $500 position (typical)
            shares_estimate = int(500 / price_at_skip) if price_at_skip > 0 else 0
            potential_gain_usd = (max_price - price_at_skip) * shares_estimate

        # Fall back to retrospective triage when live headline_type is null
        # (article was filtered pre-classification but moved >=10% retroactively)
        retro = record.get("retrospective_classification") or {}
        headline_type = record.get("headline_type") or retro.get("triage_type")

        return RecallAnalytics(
            article_id=record.get("article_id", ""),
            ticker=record.get("ticker", ""),
            date=record.get("recorded_at", "")[:10] if record.get("recorded_at") else "",
            session=record.get("_session", ""),
            headline=record.get("headline"),
            headline_type=headline_type,
            retrospective_classification=retro or None,
            skip_reason=record.get("skip_reason") or record.get("reason"),
            skip_filter=record.get("skip_filter") or record.get("filter_name"),
            price_at_skip=price_at_skip,
            max_price_after=max_price,
            potential_gain_pct=round(potential_gain_pct, 2) if potential_gain_pct else None,
            potential_gain_usd=round(potential_gain_usd, 2) if potential_gain_usd else None,
            spread_at_skip_pct=record.get("spread_at_classification"),
            decision_ask_size=record.get("decision_ask_size"),
            sector=meta.get("sector"),
            industry=meta.get("industry"),
            late_trade_candidate=record.get("late_trade_candidate"),
        )

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
            slippage_from_decision_pct=record.get("slippage_from_decision"),
            decision_bid_size=record.get("decision_bid_size"),
            decision_ask_size=record.get("decision_ask_size"),
            order_vs_depth_ratio=record.get("order_vs_depth_ratio"),
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

        # New metrics: slippage and depth
        slippage_from_decision = [t.slippage_from_decision_pct for t in trades if t.slippage_from_decision_pct is not None]
        order_vs_depth = [t.order_vs_depth_ratio for t in trades if t.order_vs_depth_ratio is not None]
        decision_ask_sizes = [t.decision_ask_size for t in trades if t.decision_ask_size is not None]

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
            # Slippage analysis
            "avg_slippage_from_decision_pct": round(sum(slippage_from_decision) / len(slippage_from_decision), 3) if slippage_from_decision else None,
            "max_slippage_from_decision_pct": round(max(slippage_from_decision), 3) if slippage_from_decision else None,
            # Depth analysis
            "avg_order_vs_depth_ratio": round(sum(order_vs_depth) / len(order_vs_depth), 2) if order_vs_depth else None,
            "avg_decision_ask_size": round(sum(decision_ask_sizes) / len(decision_ask_sizes), 0) if decision_ask_sizes else None,
            "pct_orders_exceed_depth": round(len([r for r in order_vs_depth if r > 1]) / len(order_vs_depth) * 100, 1) if order_vs_depth else None,
        }

    def calculate_filter_analysis(self, records: List[Dict[str, Any]], winners: List[str], losers: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Calculate filter hit rate analysis comparing TP vs FP distributions.

        This identifies which filters discriminate between profitable and losing trades.
        A filter "discriminates" if the TP distribution differs significantly from FP.

        Args:
            records: Raw signal records with filter_values
            winners: List of trade_ids that were profitable
            losers: List of trade_ids that were losers

        Returns:
            Dict of filter_name -> {tp_avg, tp_max, fp_avg, fp_max, discriminates, p_value_approx}
        """
        filter_analysis = {}

        # Common filter names to analyze
        filter_names = [
            "spread_pct", "fill_spread_pct", "pub_to_recv_pct", "recv_to_fill_pct",
            "ask_vs_first_trade_pct", "confluence_runup_pct", "entry_delay_seconds",
            "confluence_score", "max_excursion_pct", "imbalance_ratio",
            "buying_pressure_pct", "dollar_volume", "trade_count", "first_trade_latency_ms"
        ]

        for filter_name in filter_names:
            tp_values = []
            fp_values = []

            for record in records:
                filter_values = record.get("filter_values") or {}
                trade_id = record.get("trade_id")
                val = filter_values.get(filter_name)

                if val is not None:
                    if trade_id in winners:
                        tp_values.append(val)
                    elif trade_id in losers:
                        fp_values.append(val)

            if tp_values or fp_values:
                tp_avg = sum(tp_values) / len(tp_values) if tp_values else None
                tp_max = max(tp_values) if tp_values else None
                tp_min = min(tp_values) if tp_values else None
                fp_avg = sum(fp_values) / len(fp_values) if fp_values else None
                fp_max = max(fp_values) if fp_values else None
                fp_min = min(fp_values) if fp_values else None

                # Simple discrimination check: means differ by >20%
                discriminates = False
                if tp_avg is not None and fp_avg is not None and tp_avg != 0:
                    pct_diff = abs(tp_avg - fp_avg) / abs(tp_avg) * 100
                    discriminates = pct_diff > 20

                filter_analysis[filter_name] = {
                    "tp_count": len(tp_values),
                    "tp_avg": round(tp_avg, 3) if tp_avg is not None else None,
                    "tp_max": round(tp_max, 3) if tp_max is not None else None,
                    "tp_min": round(tp_min, 3) if tp_min is not None else None,
                    "fp_count": len(fp_values),
                    "fp_avg": round(fp_avg, 3) if fp_avg is not None else None,
                    "fp_max": round(fp_max, 3) if fp_max is not None else None,
                    "fp_min": round(fp_min, 3) if fp_min is not None else None,
                    "discriminates": discriminates,
                }

        return filter_analysis

    async def run(self, target_date: Optional[date] = None) -> Optional[DailyAnalyticsReport]:
        """
        Run daily analytics for a specific date.

        Args:
            target_date: Date to analyze (defaults to yesterday in ET timezone)
        """
        # Default to today (job runs at 8 PM ET after postmarket closes — analyze the day that just ended)
        if target_date is None:
            now_et = datetime.now(ET_TZ)
            target_date = now_et.date()

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

        # Load recall records (missed opportunities / false negatives)
        recall_records = self.load_recall_records(target_date)
        missed_opportunities = [self.process_recall_record(r) for r in recall_records]

        # Calculate new metrics: slippage and depth
        slippage_vals = [t.slippage_from_decision_pct for t in trades if t.slippage_from_decision_pct is not None]
        order_vs_depth_vals = [t.order_vs_depth_ratio for t in trades if t.order_vs_depth_ratio is not None]
        decision_ask_sizes = [t.decision_ask_size for t in trades if t.decision_ask_size is not None]

        # Missed opportunity stats
        missed_gains = [m.potential_gain_pct for m in missed_opportunities if m.potential_gain_pct is not None and m.potential_gain_pct > 0]
        missed_usd = [m.potential_gain_usd for m in missed_opportunities if m.potential_gain_usd is not None and m.potential_gain_usd > 0]

        # Calculate filter hit rate analysis (TP vs FP comparison)
        winner_ids = [t.trade_id for t in completed_trades if t.profit_loss_pct is not None and t.profit_loss_pct > 0]
        loser_ids = [t.trade_id for t in completed_trades if t.profit_loss_pct is not None and t.profit_loss_pct <= 0]
        filter_analysis = self.calculate_filter_analysis(records, winner_ids, loser_ids)

        # Create report
        report = DailyAnalyticsReport(
            date=target_date.isoformat(),
            generated_at=datetime.now(UK_TZ).isoformat(),
            market_regime=market_regime,
            # Confusion matrix
            true_positives=winners,
            false_positives=len(completed_trades) - winners,
            false_negatives=len(missed_gains),  # Opportunities with positive gain
            # Trade summary
            total_trades=len(trades),
            profitable_trades=winners,
            losing_trades=len(completed_trades) - winners,
            win_rate_pct=round((winners / len(completed_trades) * 100) if completed_trades else 0, 1),
            total_pnl_usd=round(sum(pnl_usd), 2) if pnl_usd else 0,
            avg_pnl_per_trade_usd=round(sum(pnl_usd) / len(pnl_usd), 2) if pnl_usd else 0,
            # Peak analysis
            avg_peak_profit_pct=round(sum(peaks) / len(peaks), 2) if peaks else 0,
            avg_exit_profit_pct=round(sum(profits) / len(profits), 2) if profits else 0,
            avg_money_left_on_table_pct=round(sum(money_left) / len(money_left), 2) if money_left else 0,
            # Slippage analysis
            avg_slippage_from_decision_pct=round(sum(slippage_vals) / len(slippage_vals), 3) if slippage_vals else 0,
            max_slippage_from_decision_pct=round(max(slippage_vals), 3) if slippage_vals else 0,
            # Depth analysis
            avg_order_vs_depth_ratio=round(sum(order_vs_depth_vals) / len(order_vs_depth_vals), 2) if order_vs_depth_vals else 0,
            avg_decision_ask_size=round(sum(decision_ask_sizes) / len(decision_ask_sizes), 0) if decision_ask_sizes else 0,
            pct_orders_exceed_depth=round(len([r for r in order_vs_depth_vals if r > 1]) / len(order_vs_depth_vals) * 100, 1) if order_vs_depth_vals else 0,
            # Missed opportunities
            total_missed_opportunities=len(missed_opportunities),
            avg_missed_gain_pct=round(sum(missed_gains) / len(missed_gains), 2) if missed_gains else 0,
            total_missed_gain_usd=round(sum(missed_usd), 2) if missed_usd else 0,
            # Filter analysis (TP vs FP comparison)
            filter_analysis=filter_analysis,
            # Breakdowns
            by_industry={k: self.calculate_segment_stats(v) for k, v in by_industry.items()},
            by_sector={k: self.calculate_segment_stats(v) for k, v in by_sector.items()},
            by_market_cap_bucket={k: self.calculate_segment_stats(v) for k, v in by_market_cap.items()},
            by_headline_type={k: self.calculate_segment_stats(v) for k, v in by_headline_type.items()},
            by_session={k: self.calculate_segment_stats(v) for k, v in by_session.items()},
            # Individual data
            trades=trades,
            missed_opportunities=missed_opportunities,
        )

        # Save to JSON — organized by YYYY/MM/week_N/DD.json to match recall/signal trees
        year = target_date.year
        month = target_date.month
        day = target_date.day
        week = target_date.isocalendar()[1]
        output_dir = self.output_path / str(year) / f"{month:02d}" / f"week_{week}"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{day:02d}.json"

        # Convert dataclasses to dicts for JSON serialization
        report_dict = {
            "date": report.date,
            "generated_at": report.generated_at,
            "market_regime": asdict(report.market_regime),
            "confusion_matrix": {
                "true_positives": report.true_positives,
                "false_positives": report.false_positives,
                "false_negatives": report.false_negatives,
                "precision_pct": round(report.true_positives / (report.true_positives + report.false_positives) * 100, 1) if (report.true_positives + report.false_positives) > 0 else 0,
            },
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
            "slippage_analysis": {
                "avg_slippage_from_decision_pct": report.avg_slippage_from_decision_pct,
                "max_slippage_from_decision_pct": report.max_slippage_from_decision_pct,
            },
            "depth_analysis": {
                "avg_order_vs_depth_ratio": report.avg_order_vs_depth_ratio,
                "avg_decision_ask_size": report.avg_decision_ask_size,
                "pct_orders_exceed_depth": report.pct_orders_exceed_depth,
            },
            "missed_opportunities": {
                "total_count": report.total_missed_opportunities,
                "with_positive_gain": report.false_negatives,
                "avg_missed_gain_pct": report.avg_missed_gain_pct,
                "total_missed_gain_usd": report.total_missed_gain_usd,
            },
            "late_trade_candidates": self._summarize_late_trade_candidates(report.missed_opportunities),
            "by_industry": report.by_industry,
            "by_sector": report.by_sector,
            "by_market_cap_bucket": report.by_market_cap_bucket,
            "by_headline_type": report.by_headline_type,
            "by_session": report.by_session,
            "retrospective_false_negatives": self._summarize_retrospective_fns(report.missed_opportunities),
            "trades": [asdict(t) for t in report.trades],
            "recall_records": [asdict(m) for m in report.missed_opportunities] if report.missed_opportunities else [],
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
            missed_opportunities=report.total_missed_opportunities,
            avg_slippage=f"{report.avg_slippage_from_decision_pct}%",
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
