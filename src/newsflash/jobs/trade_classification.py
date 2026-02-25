"""
Trade Classification Job - Confusion Matrix for ML Training.

Classifies all trading decisions into:
- True Positive (TP): Traded and profitable (>= +2%)
- False Positive (FP): Traded and lost money (<= -2%)
- False Negative (FN): Didn't trade but should have (10%+ peak, would have been profitable)
- True Negative (TN): Correctly ignored (wouldn't have been profitable)

FN verification uses actual Alpaca tick data to simulate:
- Entry at +3s after publication
- 1.25s soft stop confirmation in first 5 seconds
- Hard stop after 5 seconds
- Take profits: +15% (50%), +30% (25%), +40% (25%) with trailing stop

Data sources:
- Signal records: All trades we placed (entry data)
- Recall records: All opportunities we considered (peak data for FN/TN)
- Alpaca orders: Actual trade P&L (for TP/FP)
- Alpaca tick data: Simulation for FN verification
"""
import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import pytz

from ..utils.logging_config import get_logger
from .trade_simulator import simulate_trade, SimulationResult

logger = get_logger(__name__)

ET_TZ = pytz.timezone("US/Eastern")

# Classification thresholds
WINNER_THRESHOLD_PCT = 2.0      # >= +2% = winner (TP)
LOSER_THRESHOLD_PCT = -2.0      # <= -2% = loser (FP)
MIN_PEAK_FOR_FN_PCT = 10.0      # 10%+ peak for false negative
MAX_MAE_FOR_FN_PCT = 10.0       # Max 10% MAE (proxy, refined by tick simulation)
MARKET_REGIME_THRESHOLD = 0.2   # +/- 0.2% = neutral, beyond = bullish/bearish

# FN eligibility filters (must pass ALL to be considered a missed opportunity)
MAX_SPREAD_PCT = 10.0           # Spread must be < 10% of ask price
MAX_LATENCY_SECONDS = 10.0      # Article must arrive within 10 seconds of publication
MAX_MARKET_CAP_MILLIONS = 300.0 # Only small caps (< $300M market cap)
MIN_STOCK_PRICE = 0.15          # Minimum $0.15 stock price

# Tick simulation flag - when True, FN candidates are verified with actual tick data
SIMULATE_FN_WITH_TICK_DATA = True


# Move type thresholds
# SURGE: Explosive volume with strong directional buying
SURGE_MIN_VOLUME = 2000
SURGE_MIN_VOLUME_MULTIPLIER = 10.0
SURGE_MIN_TRADE_COUNT_MULTIPLIER = 10.0
SURGE_MIN_IMBALANCE_RATIO = 0.8
SURGE_MIN_EXCURSION_PCT = 5.0  # Must be positive (upward)

# STRENGTH: Good volume with directional bias and volatility
STRENGTH_MIN_VOLUME_MULTIPLIER = 5.0
STRENGTH_MIN_TRADE_COUNT_MULTIPLIER = 5.0
STRENGTH_MIN_IMBALANCE_RATIO = 0.0  # Just needs to be positive
STRENGTH_MIN_EXCURSION_PCT = 0.5  # Either direction (proves volatility)

# LOW_ACTIVITY: Some trading but not meeting strength criteria
LOW_ACTIVITY_MIN_VOLUME = 100
LOW_ACTIVITY_MIN_TRADES = 3


def derive_move_type(
    volume: Optional[int],
    trade_count: Optional[int],
    imbalance_ratio: Optional[float],
    price_excursion_pct: Optional[float],
    volume_multiplier: Optional[float] = None,
    trade_count_multiplier: Optional[float] = None,
    has_volume_surge: Optional[bool] = None,
    has_buying_pressure: Optional[bool] = None,
    has_price_excursion: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Derive move type from confluence stats with exact criteria.

    Move types:
    - surge: volume>=2000, 10x volume mult, 10x trade mult, imbalance>=0.8, excursion>=+5%
    - strength: 5x volume mult, 5x trade mult, positive imbalance, excursion>=0.5%
    - low_activity: Some volume/trades but not meeting strength criteria
    - inactive: No meaningful activity

    When multipliers aren't available, falls back to binary flags (has_volume_surge, etc.)

    Returns dict with:
    - move_type: str
    - move_type_details: dict with threshold checks
    """
    result = {
        "move_type": "inactive",
        "move_type_details": {
            "volume": volume,
            "trade_count": trade_count,
            "imbalance_ratio": imbalance_ratio,
            "price_excursion_pct": price_excursion_pct,
            "volume_multiplier": volume_multiplier,
            "trade_count_multiplier": trade_count_multiplier,
            "has_volume_surge": has_volume_surge,
            "has_buying_pressure": has_buying_pressure,
            "has_price_excursion": has_price_excursion,
        }
    }

    # Check for inactive first
    if (volume is None or volume == 0) and (trade_count is None or trade_count == 0):
        result["move_type"] = "inactive"
        return result

    # Check SURGE criteria (all must be met)
    # With multipliers available:
    is_surge_with_multipliers = (
        volume is not None and volume >= SURGE_MIN_VOLUME and
        volume_multiplier is not None and volume_multiplier >= SURGE_MIN_VOLUME_MULTIPLIER and
        trade_count_multiplier is not None and trade_count_multiplier >= SURGE_MIN_TRADE_COUNT_MULTIPLIER and
        imbalance_ratio is not None and imbalance_ratio >= SURGE_MIN_IMBALANCE_RATIO and
        price_excursion_pct is not None and price_excursion_pct >= SURGE_MIN_EXCURSION_PCT
    )

    # Fallback using binary flags when multipliers unavailable:
    # If has_volume_surge flag + meets volume/imbalance/excursion thresholds
    is_surge_fallback = (
        has_volume_surge is True and
        volume is not None and volume >= SURGE_MIN_VOLUME and
        imbalance_ratio is not None and imbalance_ratio >= SURGE_MIN_IMBALANCE_RATIO and
        price_excursion_pct is not None and price_excursion_pct >= SURGE_MIN_EXCURSION_PCT
    )

    if is_surge_with_multipliers or is_surge_fallback:
        result["move_type"] = "surge"
        result["move_type_details"]["surge_checks"] = {
            "volume_ok": volume >= SURGE_MIN_VOLUME if volume else False,
            "volume_mult_ok": volume_multiplier >= SURGE_MIN_VOLUME_MULTIPLIER if volume_multiplier else None,
            "trade_mult_ok": trade_count_multiplier >= SURGE_MIN_TRADE_COUNT_MULTIPLIER if trade_count_multiplier else None,
            "imbalance_ok": imbalance_ratio >= SURGE_MIN_IMBALANCE_RATIO if imbalance_ratio else False,
            "excursion_ok": price_excursion_pct >= SURGE_MIN_EXCURSION_PCT if price_excursion_pct else False,
            "used_fallback": is_surge_fallback and not is_surge_with_multipliers,
        }
        return result

    # Check STRENGTH criteria (all must be met)
    # With multipliers:
    is_strength_with_multipliers = (
        volume_multiplier is not None and volume_multiplier >= STRENGTH_MIN_VOLUME_MULTIPLIER and
        trade_count_multiplier is not None and trade_count_multiplier >= STRENGTH_MIN_TRADE_COUNT_MULTIPLIER and
        imbalance_ratio is not None and imbalance_ratio > STRENGTH_MIN_IMBALANCE_RATIO and
        price_excursion_pct is not None and abs(price_excursion_pct) >= STRENGTH_MIN_EXCURSION_PCT
    )

    # Fallback when multipliers unavailable (dormant stocks with no prior volume):
    # Accept volume surge OR buying pressure, combined with price excursion and positive imbalance.
    # OLB pattern: 25K shares, 127 trades, 4.76% excursion, 66.7% buying — clearly strength, not low_activity.
    is_strength_fallback = (
        (has_buying_pressure is True or has_volume_surge is True) and
        has_price_excursion is True and
        imbalance_ratio is not None and imbalance_ratio > STRENGTH_MIN_IMBALANCE_RATIO
    )

    if is_strength_with_multipliers or is_strength_fallback:
        result["move_type"] = "strength"
        result["move_type_details"]["strength_checks"] = {
            "volume_mult_ok": volume_multiplier >= STRENGTH_MIN_VOLUME_MULTIPLIER if volume_multiplier else None,
            "trade_mult_ok": trade_count_multiplier >= STRENGTH_MIN_TRADE_COUNT_MULTIPLIER if trade_count_multiplier else None,
            "imbalance_ok": imbalance_ratio > STRENGTH_MIN_IMBALANCE_RATIO if imbalance_ratio else False,
            "excursion_ok": abs(price_excursion_pct) >= STRENGTH_MIN_EXCURSION_PCT if price_excursion_pct else False,
            "used_fallback": is_strength_fallback and not is_strength_with_multipliers,
        }
        return result

    # Check LOW_ACTIVITY criteria
    is_low_activity = (
        (volume is not None and volume >= LOW_ACTIVITY_MIN_VOLUME) or
        (trade_count is not None and trade_count >= LOW_ACTIVITY_MIN_TRADES)
    )

    if is_low_activity:
        result["move_type"] = "low_activity"
        return result

    # Default to inactive if very minimal activity
    result["move_type"] = "inactive"
    return result


def get_market_regime(target_date: date) -> Dict[str, Any]:
    """
    Get market regime (SPY/QQQ direction) for a date.

    Returns:
        {
            "spy_change_pct": float,
            "qqq_change_pct": float,
            "regime": "bullish" | "bearish" | "neutral",
            "regime_strength": float (avg of SPY/QQQ change)
        }
    """
    try:
        import yfinance as yf

        # Fetch SPY and QQQ data for the date
        start = target_date
        end = target_date + timedelta(days=1)

        spy_data = yf.download("SPY", start=start, end=end, progress=False)
        qqq_data = yf.download("QQQ", start=start, end=end, progress=False)

        spy_change = None
        qqq_change = None

        if len(spy_data) > 0:
            spy_open = spy_data['Open'].iloc[0].item()
            spy_close = spy_data['Close'].iloc[0].item()
            spy_change = ((spy_close - spy_open) / spy_open) * 100

        if len(qqq_data) > 0:
            qqq_open = qqq_data['Open'].iloc[0].item()
            qqq_close = qqq_data['Close'].iloc[0].item()
            qqq_change = ((qqq_close - qqq_open) / qqq_open) * 100

        # Determine regime based on average
        if spy_change is not None and qqq_change is not None:
            avg_change = (spy_change + qqq_change) / 2

            if avg_change > MARKET_REGIME_THRESHOLD:
                regime = "bullish"
            elif avg_change < -MARKET_REGIME_THRESHOLD:
                regime = "bearish"
            else:
                regime = "neutral"

            return {
                "spy_change_pct": round(spy_change, 2),
                "qqq_change_pct": round(qqq_change, 2),
                "regime": regime,
                "regime_strength": round(avg_change, 2),
            }

    except Exception as e:
        logger.warning(f"Failed to fetch market regime: {e}")

    return {
        "spy_change_pct": None,
        "qqq_change_pct": None,
        "regime": "unknown",
        "regime_strength": None,
    }


@dataclass
class ClassifiedTrade:
    """A trade classified into confusion matrix category."""
    ticker: str
    date: str
    session: str
    category: str  # true_positive, false_positive, false_negative, true_negative

    # Outcome data
    pnl_pct: Optional[float] = None
    pnl_usd: Optional[float] = None
    peak_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    shares: Optional[int] = None

    # Context
    headline: Optional[str] = None
    headline_type: Optional[str] = None
    prefilter_reason: Optional[str] = None   # Why filtered before AI classification
    postfilter_reason: Optional[str] = None  # Why filtered after AI (IMMINENT but didn't trade)
    fn_exclusion_reason: Optional[str] = None  # Why not counted as FN (spread/latency/market_cap)

    # Ticker metadata
    industry: Optional[str] = None
    sector: Optional[str] = None
    market_cap_millions: Optional[float] = None
    price: Optional[float] = None

    # FN eligibility data
    spread_pct: Optional[float] = None       # Spread as % of ask price
    latency_seconds: Optional[float] = None  # Time from publication to reception

    # === ALL CONFLUENCE FEATURES FOR ML (captured from signal/recall records) ===
    # Core confluence scoring
    confluence_score: Optional[int] = None
    confluence_volume: Optional[int] = None
    confluence_trade_count: Optional[int] = None
    confluence_buy_volume: Optional[int] = None
    confluence_sell_volume: Optional[int] = None

    # Pressure analysis
    confluence_buying_pressure_pct: Optional[float] = None
    confluence_imbalance_ratio: Optional[float] = None
    confluence_uptick_count: Optional[int] = None
    confluence_downtick_count: Optional[int] = None

    # Price trajectory
    confluence_price_excursion_pct: Optional[float] = None
    confluence_first_price: Optional[float] = None
    confluence_last_price: Optional[float] = None
    confluence_max_price: Optional[float] = None
    confluence_min_price: Optional[float] = None
    confluence_vwap: Optional[float] = None
    confluence_price_direction: Optional[int] = None
    confluence_dollar_volume: Optional[float] = None

    # Spread/liquidity
    confluence_initial_spread: Optional[float] = None
    confluence_final_spread: Optional[float] = None
    confluence_spread_compression_pct: Optional[float] = None

    # Trade size analysis
    confluence_avg_trade_size: Optional[float] = None
    confluence_median_trade_size: Optional[float] = None
    confluence_max_single_trade: Optional[int] = None
    confluence_large_trade_pct: Optional[float] = None

    # Timing (reaction speed)
    confluence_first_trade_latency_ms: Optional[float] = None
    confluence_max_trade_gap_ms: Optional[float] = None

    # Binary signals
    confluence_has_volume_surge: Optional[bool] = None
    confluence_has_price_excursion: Optional[bool] = None
    confluence_has_buying_pressure: Optional[bool] = None

    # Volume ratio vs baseline
    volume_ratio: Optional[float] = None

    # === SURGE STATS (8-second window, only if trade was surge-based) ===
    surge_triggered: Optional[bool] = None
    surge_found: Optional[bool] = None
    surge_detection_cycle: Optional[int] = None
    surge_seconds_elapsed: Optional[float] = None
    surge_volume: Optional[int] = None
    surge_trade_count: Optional[int] = None
    surge_buy_volume: Optional[int] = None
    surge_sell_volume: Optional[int] = None
    surge_buying_pressure_pct: Optional[float] = None
    surge_imbalance_ratio: Optional[float] = None
    surge_price_excursion_pct: Optional[float] = None
    surge_volume_multiplier: Optional[float] = None
    surge_trade_count_multiplier: Optional[float] = None
    surge_ask: Optional[float] = None
    surge_bid: Optional[float] = None
    surge_mid: Optional[float] = None

    # Source tracking
    source: str = ""  # "signal", "recall", "alpaca"
    record_id: Optional[str] = None

    # === TICK SIMULATION RESULTS (for FN verification) ===
    # These fields are populated when SIMULATE_FN_WITH_TICK_DATA is True
    sim_would_have_traded: Optional[bool] = None  # False if stopped out before any TP
    sim_total_pnl_pct: Optional[float] = None     # Total realized + unrealized P&L
    sim_realized_pnl_pct: Optional[float] = None  # Realized from take profits
    sim_position_remaining_pct: Optional[int] = None  # % position still held at end
    sim_stopped_out: Optional[bool] = None        # True if hit stop-loss
    sim_stop_type: Optional[str] = None           # "soft" or "hard"
    sim_stop_pnl_pct: Optional[float] = None      # P&L at stop (if stopped out)
    sim_stop_elapsed_seconds: Optional[float] = None  # Seconds from entry to stop
    sim_stop_timestamp: Optional[str] = None      # ISO timestamp of stop
    sim_max_pnl_pct: Optional[float] = None       # Peak P&L during simulation
    sim_max_pnl_elapsed_seconds: Optional[float] = None  # Seconds from entry to peak
    sim_min_pnl_pct: Optional[float] = None       # Worst drawdown during simulation
    sim_tp_count: Optional[int] = None            # Number of take profit events
    sim_quote_count: Optional[int] = None         # Number of quotes processed
    sim_entry_timestamp: Optional[str] = None     # ISO timestamp of entry

    # FN outcome classification (for analysis)
    fn_outcome: Optional[str] = None  # "profitable", "stopped_out_loss", "stopped_out_profit"

    # Simple hold comparison (10 min hold with -5% hard stop only, no TPs)
    sim_simple_hold_pnl_pct: Optional[float] = None

    # Move progression tracking (when key price levels were crossed)
    sim_move_progression: Optional[Dict[str, Any]] = None


def get_alpaca_client():
    """Get Alpaca trading client if available."""
    try:
        from alpaca.trading.client import TradingClient

        # Support multiple env var naming conventions
        api_key = (
            os.getenv("ALPACA_KEY_PAPER") or
            os.getenv("ALPACA_PAPER_API_KEY") or
            os.getenv("ALPACA_API_KEY") or
            os.getenv("ALPACA_KEY")
        )
        secret_key = (
            os.getenv("ALPACA_SECRET_PAPER") or
            os.getenv("ALPACA_PAPER_SECRET_KEY") or
            os.getenv("ALPACA_SECRET_KEY") or
            os.getenv("ALPACA_SECRET")
        )
        paper = os.getenv("PAPER_TRADING", "true").lower() == "true"

        if api_key and secret_key:
            return TradingClient(api_key, secret_key, paper=paper)
    except ImportError:
        pass
    return None


def get_alpaca_trades_for_date(client, target_date: date) -> Dict[str, List[Dict]]:
    """
    Get all trades from Alpaca for a specific date.

    Returns dict: ticker -> list of {action, price, shares, filled_at}
    """
    if not client:
        return {}

    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        # Get orders for the date range
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

        request = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=start_dt.isoformat(),
            until=end_dt.isoformat(),
            limit=500,
        )

        orders = client.get_orders(request)

        trades_by_ticker: Dict[str, List[Dict]] = {}
        for order in orders:
            if order.status.value != "filled":
                continue

            ticker = order.symbol
            if ticker not in trades_by_ticker:
                trades_by_ticker[ticker] = []

            trades_by_ticker[ticker].append({
                "action": order.side.value.upper(),  # BUY or SELL
                "price": float(order.filled_avg_price) if order.filled_avg_price else 0,
                "shares": float(order.filled_qty) if order.filled_qty else 0,
                "filled_at": order.filled_at.isoformat() if order.filled_at else None,
                "order_id": str(order.id),
            })

        # Sort each ticker's trades by time
        for ticker in trades_by_ticker:
            trades_by_ticker[ticker].sort(key=lambda t: t.get("filled_at", ""))

        return trades_by_ticker

    except Exception as e:
        logger.warning(f"Failed to get Alpaca trades: {e}")
        return {}


def calculate_trade_pnl(trades: List[Dict]) -> List[Dict]:
    """
    Calculate P&L for each BUY/SELL pair (FIFO matching).

    Returns list of dicts with entry/exit/pnl data.
    """
    results = []
    buy_queue = []  # FIFO queue of buys

    for trade in trades:
        if trade["action"] == "BUY":
            buy_queue.append(trade)
        elif trade["action"] == "SELL" and buy_queue:
            # Match with oldest buy
            buy = buy_queue.pop(0)
            entry_price = buy["price"]
            exit_price = trade["price"]
            shares = min(buy["shares"], trade["shares"])

            pnl_usd = (exit_price - entry_price) * shares
            pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0

            results.append({
                "entry_price": entry_price,
                "exit_price": exit_price,
                "shares": int(shares),
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "entry_time": buy.get("filled_at"),
                "exit_time": trade.get("filled_at"),
            })

    return results


class TradeClassificationJob:
    """
    Daily classification of trades into confusion matrix categories.

    Data sources:
    - Alpaca: Actual trade P&L for TP/FP (authoritative)
    - Signal records: Trade metadata (ticker, article, confluence)
    - Recall records: Peak data for FN/TN
    """

    def __init__(
        self,
        signal_path: Path = Path("tmp/statistics/signal"),
        recall_path: Path = Path("tmp/statistics/recall"),
        output_path: Path = Path("tmp/trade_classification/daily"),
    ):
        self.signal_path = signal_path
        self.recall_path = recall_path
        self.output_path = output_path
        self.output_path.mkdir(parents=True, exist_ok=True)

        # Try to get Alpaca client
        self.alpaca_client = get_alpaca_client()
        if self.alpaca_client:
            logger.info("Alpaca client available - will use actual P&L data")
        else:
            logger.warning("Alpaca client not available - using signal records only")

    def _get_session_file(self, base_path: Path, target_date: date, session: str) -> Optional[Path]:
        """Get the session file path if it exists."""
        year = target_date.year
        month = target_date.month
        day = target_date.day
        week = target_date.isocalendar()[1]

        file_path = (
            base_path / str(year) / f"{month:02d}" /
            f"week_{week}" / f"{day:02d}" / session / f"{session}.json"
        )

        if file_path.exists():
            return file_path
        return None

    def load_signal_records(self, target_date: date) -> List[Dict]:
        """Load signal records for a date (all sessions)."""
        records = []
        for session in ["premarket", "market_hours", "postmarket"]:
            file_path = self._get_session_file(self.signal_path, target_date, session)
            if file_path:
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    for record in data.get("records", []):
                        record["_session"] = session
                        record["_file_path"] = str(file_path)
                        records.append(record)
                except Exception as e:
                    logger.warning(f"Failed to load {file_path}: {e}")
        return records

    def load_recall_records(self, target_date: date) -> List[Dict]:
        """Load recall records for a date (all sessions)."""
        records = []
        for session in ["premarket", "market_hours", "postmarket"]:
            file_path = self._get_session_file(self.recall_path, target_date, session)
            if file_path:
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    for record in data.get("records", []):
                        record["_session"] = session
                        record["_file_path"] = str(file_path)
                        records.append(record)
                except Exception as e:
                    logger.warning(f"Failed to load {file_path}: {e}")
        return records

    def extract_metadata(self, record: Dict, ticker: str) -> Dict:
        """Extract ticker metadata from record."""
        # Try ticker_metadata dict first
        meta = record.get("ticker_metadata", {})
        if isinstance(meta, dict):
            # Could be {ticker: {...}} or direct {...}
            if ticker in meta:
                meta = meta[ticker]

        # Extract spread_pct from entry_nbbo (for signal records)
        spread_pct = None
        if record.get("spread_at_fill") is not None:
            spread_pct = record.get("spread_at_fill")
        else:
            entry_nbbo = record.get("entry_nbbo", {})
            if entry_nbbo:
                spread = entry_nbbo.get("spread")
                ask = entry_nbbo.get("ask")
                if spread is not None and ask and ask > 0:
                    spread_pct = (spread / ask) * 100

        return {
            "industry": meta.get("industry"),
            "sector": meta.get("sector"),
            "market_cap_millions": meta.get("market_cap_millions"),
            "price": meta.get("price"),
            "spread_pct": spread_pct,
        }

    def extract_confluence_features(self, record: Dict) -> Dict:
        """Extract ALL confluence features for ML training."""
        # Try confluence_window first (structured format)
        cw = record.get("confluence_window", {}) or {}

        if cw:
            # Use structured confluence window
            return {
                # Core scoring
                "confluence_score": cw.get("confluence_score"),
                "confluence_volume": cw.get("total_volume"),
                "confluence_trade_count": cw.get("total_trades"),
                "confluence_buy_volume": cw.get("total_buy_volume"),
                "confluence_sell_volume": cw.get("total_sell_volume"),
                # Pressure analysis
                "confluence_buying_pressure_pct": cw.get("buying_pressure_pct"),
                "confluence_imbalance_ratio": cw.get("imbalance_ratio"),
                "confluence_uptick_count": cw.get("uptick_count"),
                "confluence_downtick_count": cw.get("downtick_count"),
                # Price trajectory
                "confluence_price_excursion_pct": cw.get("price_excursion_pct"),
                "confluence_first_price": cw.get("first_price"),
                "confluence_last_price": cw.get("last_price"),
                "confluence_max_price": cw.get("high_price"),
                "confluence_min_price": cw.get("low_price"),
                "confluence_vwap": cw.get("vwap"),
                "confluence_price_direction": cw.get("price_direction"),
                "confluence_dollar_volume": cw.get("dollar_volume"),
                # Spread/liquidity
                "confluence_initial_spread": cw.get("initial_spread"),
                "confluence_final_spread": cw.get("final_spread"),
                "confluence_spread_compression_pct": cw.get("spread_compression_pct"),
                # Trade size
                "confluence_avg_trade_size": cw.get("avg_trade_size"),
                "confluence_median_trade_size": cw.get("median_trade_size"),
                "confluence_max_single_trade": cw.get("max_single_trade"),
                "confluence_large_trade_pct": cw.get("large_trade_pct"),
                # Timing
                "confluence_first_trade_latency_ms": cw.get("first_trade_latency_ms"),
                "confluence_max_trade_gap_ms": cw.get("max_trade_gap_ms"),
                # Binary signals
                "confluence_has_volume_surge": cw.get("has_volume_surge"),
                "confluence_has_price_excursion": cw.get("has_price_excursion"),
                "confluence_has_buying_pressure": cw.get("has_buying_pressure"),
                # Baseline ratio
                "volume_ratio": cw.get("volume_ratio"),
            }

        # Fall back to direct confluence fields (flat format from signal/recall records)
        return {
            # Core scoring
            "confluence_score": record.get("confluence_score"),
            "confluence_volume": record.get("confluence_volume"),
            "confluence_trade_count": record.get("confluence_trade_count"),
            "confluence_buy_volume": record.get("confluence_buy_volume"),
            "confluence_sell_volume": record.get("confluence_sell_volume"),
            # Pressure analysis
            "confluence_buying_pressure_pct": record.get("confluence_buying_pressure_pct"),
            "confluence_imbalance_ratio": record.get("confluence_imbalance_ratio"),
            "confluence_uptick_count": record.get("confluence_uptick_count"),
            "confluence_downtick_count": record.get("confluence_downtick_count"),
            # Price trajectory
            "confluence_price_excursion_pct": record.get("confluence_price_excursion_pct"),
            "confluence_first_price": record.get("confluence_first_price"),
            "confluence_last_price": record.get("confluence_last_price"),
            "confluence_max_price": record.get("confluence_max_price"),
            "confluence_min_price": record.get("confluence_min_price"),
            "confluence_vwap": record.get("confluence_vwap"),
            "confluence_price_direction": record.get("confluence_price_direction"),
            "confluence_dollar_volume": record.get("confluence_dollar_volume"),
            # Spread/liquidity
            "confluence_initial_spread": record.get("confluence_initial_spread"),
            "confluence_final_spread": record.get("confluence_final_spread"),
            "confluence_spread_compression_pct": record.get("confluence_spread_compression_pct"),
            # Trade size
            "confluence_avg_trade_size": record.get("confluence_avg_trade_size"),
            "confluence_median_trade_size": record.get("confluence_median_trade_size"),
            "confluence_max_single_trade": record.get("confluence_max_single_trade"),
            "confluence_large_trade_pct": record.get("confluence_large_trade_pct"),
            # Timing
            "confluence_first_trade_latency_ms": record.get("confluence_first_trade_latency_ms"),
            "confluence_max_trade_gap_ms": record.get("confluence_max_trade_gap_ms"),
            # Binary signals
            "confluence_has_volume_surge": record.get("confluence_has_volume_surge"),
            "confluence_has_price_excursion": record.get("confluence_has_price_excursion"),
            "confluence_has_buying_pressure": record.get("confluence_has_buying_pressure"),
            # Baseline ratio
            "volume_ratio": record.get("volume_ratio"),
            # Surge fields
            **self._extract_surge_fields(record),
        }

    def _extract_surge_fields(self, record: Dict) -> Dict:
        """Extract surge window fields (8-second window, only if surge-based trade)."""
        # Check for surge_window structured data first
        sw = record.get("surge_window", {}) or {}
        if sw and sw.get("triggered"):
            return {
                "surge_triggered": sw.get("triggered"),
                "surge_found": sw.get("found"),
                "surge_detection_cycle": sw.get("detection_cycle"),
                "surge_seconds_elapsed": sw.get("seconds_elapsed"),
                "surge_volume": sw.get("volume"),
                "surge_trade_count": sw.get("trade_count"),
                "surge_buy_volume": sw.get("buy_volume"),
                "surge_sell_volume": sw.get("sell_volume"),
                "surge_buying_pressure_pct": sw.get("buying_pressure_pct"),
                "surge_imbalance_ratio": sw.get("imbalance_ratio"),
                "surge_price_excursion_pct": sw.get("price_excursion_pct"),
                "surge_volume_multiplier": sw.get("volume_multiplier"),
                "surge_trade_count_multiplier": sw.get("trade_count_multiplier"),
                "surge_ask": sw.get("ask"),
                "surge_bid": sw.get("bid"),
                "surge_mid": sw.get("mid"),
            }

        # Fall back to direct surge fields
        if record.get("surge_triggered"):
            return {
                "surge_triggered": record.get("surge_triggered"),
                "surge_found": record.get("surge_found"),
                "surge_detection_cycle": record.get("surge_detection_cycle"),
                "surge_seconds_elapsed": record.get("surge_seconds_elapsed"),
                "surge_volume": record.get("surge_volume"),
                "surge_trade_count": record.get("surge_trade_count"),
                "surge_buy_volume": record.get("surge_buy_volume"),
                "surge_sell_volume": record.get("surge_sell_volume"),
                "surge_buying_pressure_pct": record.get("surge_buying_pressure_pct"),
                "surge_imbalance_ratio": record.get("surge_imbalance_ratio"),
                "surge_price_excursion_pct": record.get("surge_price_excursion_pct"),
                "surge_volume_multiplier": record.get("surge_volume_multiplier"),
                "surge_trade_count_multiplier": record.get("surge_trade_count_multiplier"),
                "surge_ask": record.get("surge_ask"),
                "surge_bid": record.get("surge_bid"),
                "surge_mid": record.get("surge_mid"),
            }

        # No surge data
        return {
            "surge_triggered": None,
            "surge_found": None,
            "surge_detection_cycle": None,
            "surge_seconds_elapsed": None,
            "surge_volume": None,
            "surge_trade_count": None,
            "surge_buy_volume": None,
            "surge_sell_volume": None,
            "surge_buying_pressure_pct": None,
            "surge_imbalance_ratio": None,
            "surge_price_excursion_pct": None,
            "surge_volume_multiplier": None,
            "surge_trade_count_multiplier": None,
            "surge_ask": None,
            "surge_bid": None,
            "surge_mid": None,
        }

    def classify_trades_with_alpaca(
        self,
        signal_records: List[Dict],
        alpaca_trades: Dict[str, List[Dict]],
        target_date: date,
        headline_lookup: Dict[str, str] = None,
        recall_records: List[Dict] = None,
    ) -> List[ClassifiedTrade]:
        """
        Classify trades using actual Alpaca P&L data.

        This is the authoritative source for TP/FP.
        """
        classified = []
        headline_lookup = headline_lookup or {}
        recall_records = recall_records or []

        for ticker, trades in alpaca_trades.items():
            # Calculate P&L for each round-trip trade
            pnl_results = calculate_trade_pnl(trades)

            for result in pnl_results:
                pnl_pct = result["pnl_pct"]

                # Classify based on P&L
                if pnl_pct >= WINNER_THRESHOLD_PCT:
                    category = "true_positive"
                elif pnl_pct <= LOSER_THRESHOLD_PCT:
                    category = "false_positive"
                else:
                    # Between -2% and +2% - classify based on sign
                    category = "true_positive" if pnl_pct > 0 else "false_positive"

                # Find matching signal record for metadata
                matching_record = None
                for record in signal_records:
                    if record.get("ticker") == ticker:
                        matching_record = record
                        break

                # Extract metadata from signal record if available
                meta = {}
                features = {}
                headline = None
                headline_type = None
                session = ""
                record_id = None
                peak_pct = None
                mae_pct = None

                if matching_record:
                    meta = self.extract_metadata(matching_record, ticker)
                    features = self.extract_confluence_features(matching_record)
                    headline = matching_record.get("headline") or matching_record.get("title")
                    # If no headline in signal record, try to lookup from recall records
                    if not headline:
                        article_id = matching_record.get("article_id")
                        if article_id:
                            headline = headline_lookup.get(article_id)
                    headline_type = matching_record.get("headline_type")
                    session = matching_record.get("_session", "")
                    record_id = matching_record.get("article_id") or matching_record.get("trade_id")

                    # Extract peak/MAE from signal record (covers actual hold period)
                    sig_peak = matching_record.get("highest_price_during_hold", {})
                    if sig_peak:
                        peak_pct = sig_peak.get("percent_gain_from_entry")
                    sig_mae = matching_record.get("max_adverse_excursion", {})
                    if sig_mae:
                        mae_pct = sig_mae.get("percent_loss_from_entry")

                # Match recall record by article_id (precise), fall back to ticker
                article_id = matching_record.get("article_id") if matching_record else None
                matched_recall = None
                if article_id:
                    for recall_rec in recall_records:
                        if recall_rec.get("article_id") == article_id:
                            matched_recall = recall_rec
                            break
                if not matched_recall:
                    for recall_rec in recall_records:
                        if ticker in recall_rec.get("tickers", []):
                            matched_recall = recall_rec
                            break

                if matched_recall:
                    recall_peak = matched_recall.get("highest_price_during_hold", {})
                    if recall_peak:
                        recall_peak_pct = recall_peak.get("percent_gain_from_entry")
                        # Use recall peak if higher (covers full 10-min window)
                        if recall_peak_pct is not None and (peak_pct is None or recall_peak_pct > peak_pct):
                            peak_pct = recall_peak_pct
                    recall_mae = matched_recall.get("max_adverse_excursion", {})
                    if recall_mae:
                        recall_mae_pct = recall_mae.get("percent_loss_from_entry")
                        if recall_mae_pct is not None and (mae_pct is None or abs(recall_mae_pct) > abs(mae_pct)):
                            mae_pct = recall_mae_pct

                classified.append(ClassifiedTrade(
                    ticker=ticker,
                    date=target_date.isoformat(),
                    session=session,
                    category=category,
                    pnl_pct=result["pnl_pct"],
                    pnl_usd=result["pnl_usd"],
                    peak_pct=peak_pct,
                    mae_pct=mae_pct,
                    entry_price=result["entry_price"],
                    exit_price=result["exit_price"],
                    shares=result["shares"],
                    headline=headline,
                    headline_type=headline_type,
                    source="alpaca",
                    record_id=record_id,
                    **meta,
                    **features,
                ))

        return classified

    def classify_trades_from_signal(
        self,
        signal_records: List[Dict],
        target_date: date,
        headline_lookup: Dict[str, str] = None,
        recall_records: List[Dict] = None,
    ) -> List[ClassifiedTrade]:
        """
        Classify trades from signal records only (fallback if Alpaca unavailable).

        Less reliable since exit data may be missing.
        """
        classified = []
        headline_lookup = headline_lookup or {}
        recall_records = recall_records or []

        for record in signal_records:
            ticker = record.get("ticker")
            if not ticker:
                continue

            pnl = record.get("profit_loss_percent")

            # If no P&L, check highest_price_during_hold for estimate
            if pnl is None:
                peak_data = record.get("highest_price_during_hold", {})
                if peak_data:
                    peak_pct = peak_data.get("percent_gain_from_entry", 0)
                    # Use peak as rough estimate (optimistic)
                    pnl = peak_pct

            if pnl is None:
                # Can't classify without P&L - skip
                continue

            # Classify
            if pnl >= WINNER_THRESHOLD_PCT:
                category = "true_positive"
            elif pnl <= LOSER_THRESHOLD_PCT:
                category = "false_positive"
            else:
                category = "true_positive" if pnl > 0 else "false_positive"

            meta = self.extract_metadata(record, ticker)
            features = self.extract_confluence_features(record)

            # Extract peak/MAE from signal record
            peak_pct = None
            mae_pct = None
            sig_peak = record.get("highest_price_during_hold", {})
            if sig_peak:
                peak_pct = sig_peak.get("percent_gain_from_entry")
            sig_mae = record.get("max_adverse_excursion", {})
            if sig_mae:
                mae_pct = sig_mae.get("percent_loss_from_entry")

            # Match recall record by article_id (precise), fall back to ticker
            article_id = record.get("article_id")
            matched_recall = None
            if article_id:
                for recall_rec in recall_records:
                    if recall_rec.get("article_id") == article_id:
                        matched_recall = recall_rec
                        break
            if not matched_recall:
                for recall_rec in recall_records:
                    if ticker in recall_rec.get("tickers", []):
                        matched_recall = recall_rec
                        break

            if matched_recall:
                recall_peak = matched_recall.get("highest_price_during_hold", {})
                if recall_peak:
                    recall_peak_pct = recall_peak.get("percent_gain_from_entry")
                    if recall_peak_pct is not None and (peak_pct is None or recall_peak_pct > peak_pct):
                        peak_pct = recall_peak_pct
                recall_mae = matched_recall.get("max_adverse_excursion", {})
                if recall_mae:
                    recall_mae_pct = recall_mae.get("percent_loss_from_entry")
                    if recall_mae_pct is not None and (mae_pct is None or abs(recall_mae_pct) > abs(mae_pct)):
                        mae_pct = recall_mae_pct

            # Get headline from signal record, or lookup from recall records
            headline = record.get("headline") or record.get("title")
            if not headline and article_id:
                headline = headline_lookup.get(article_id)

            classified.append(ClassifiedTrade(
                ticker=ticker,
                date=target_date.isoformat(),
                session=record.get("_session", ""),
                category=category,
                pnl_pct=pnl,
                peak_pct=peak_pct,
                mae_pct=mae_pct,
                entry_price=record.get("entry_price"),
                exit_price=record.get("exit_price"),
                shares=record.get("entry_shares"),
                headline=headline,
                headline_type=record.get("headline_type"),
                source="signal",
                record_id=article_id or record.get("trade_id"),
                **meta,
                **features,
            ))

        return classified

    async def classify_recall_records(
        self,
        recall_records: List[Dict],
        traded_tickers: set,
        target_date: date,
    ) -> List[ClassifiedTrade]:
        """
        Classify missed opportunities (FN) and correctly ignored (TN).

        FN criteria (must meet ALL):
        1. Peak >= 10% at any point in 10 min window
        2. MAE <= 10% (with 1.25s soft stop, brief spikes recover)
        3. Spread < 10% of ask price
        4. Latency <= 10 seconds (article received within 10s of publication)
        5. Market cap <= $300M (small caps only)

        Note: Headline quality is subjective and reviewed manually.
        """
        classified = []

        for record in recall_records:
            tickers = record.get("tickers", [])
            if not tickers:
                continue

            ticker = tickers[0]

            # Skip if we traded this ticker (it's a TP/FP, not FN/TN)
            if ticker in traded_tickers:
                continue

            # Get peak and MAE data
            peak_data = record.get("highest_price_during_hold", {})
            mae_data = record.get("max_adverse_excursion", {})

            peak_pct = peak_data.get("percent_gain_from_entry") if peak_data else None
            mae_pct = mae_data.get("percent_loss_from_entry") if mae_data else None

            # Also check price_check_10min for older records
            if peak_pct is None:
                price_check = record.get("price_check_10min", {})
                if price_check:
                    pct_change = price_check.get("percent_change", 0)
                    if pct_change and pct_change > 0:
                        peak_pct = pct_change

            # Get classification and filter reasons
            classification = record.get("ai_classification")
            filter_reason = record.get("filter_reason", "")
            postfilter_reason = record.get("postfilter_reason", "")

            # Split prefilter from ai classification in filter_reason
            prefilter_reason = None
            if filter_reason:
                if filter_reason.startswith("prefilter_"):
                    prefilter_reason = filter_reason
                elif filter_reason.startswith("ai_classification:"):
                    # This is not a prefilter - it's the AI result
                    pass

            # === Extract FN eligibility data ===
            # Get spread from initial NBBO
            initial_nbbo = record.get("initial_nbbo", {})
            spread = initial_nbbo.get("spread")
            ask_price = initial_nbbo.get("ask")
            spread_pct = None
            if spread is not None and ask_price and ask_price > 0:
                spread_pct = (spread / ask_price) * 100

            # Get latency from volume_stats or direct field
            latency_seconds = None
            volume_stats = record.get("volume_stats", {})
            if volume_stats and ticker in volume_stats:
                latency_seconds = volume_stats[ticker].get("pub_to_recv_seconds")
            if latency_seconds is None:
                # Try to calculate from timestamps
                published_at = record.get("published_at")
                received_at = record.get("received_at")
                if published_at and received_at:
                    try:
                        from datetime import datetime
                        pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                        recv_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
                        latency_seconds = (recv_dt - pub_dt).total_seconds()
                    except Exception:
                        pass

            # Get market cap from metadata
            meta = self.extract_metadata(record, ticker)
            # Remove spread_pct from meta - we compute it separately for recall records from initial_nbbo
            meta.pop("spread_pct", None)
            market_cap = meta.get("market_cap_millions")

            # === Check FN eligibility ===
            fn_exclusion_reasons = []

            # Rule 1: Peak must be >= 10%
            has_sufficient_peak = peak_pct is not None and peak_pct >= MIN_PEAK_FOR_FN_PCT
            if not has_sufficient_peak:
                fn_exclusion_reasons.append(f"peak={peak_pct:.1f}%<{MIN_PEAK_FOR_FN_PCT}%" if peak_pct else "no_peak_data")

            # Rule 2: MAE must be <= threshold (would not have stopped out)
            would_not_stop_out = mae_pct is None or abs(mae_pct) <= MAX_MAE_FOR_FN_PCT
            if not would_not_stop_out:
                fn_exclusion_reasons.append(f"mae={abs(mae_pct):.1f}%>{MAX_MAE_FOR_FN_PCT}%")

            # Rule 3: Spread must be < 10%
            has_good_spread = spread_pct is not None and spread_pct < MAX_SPREAD_PCT
            if spread_pct is not None and spread_pct >= MAX_SPREAD_PCT:
                fn_exclusion_reasons.append(f"spread={spread_pct:.1f}%>={MAX_SPREAD_PCT}%")
            elif spread_pct is None:
                fn_exclusion_reasons.append("no_spread_data")

            # Rule 4: Latency must be <= 10 seconds
            has_good_latency = latency_seconds is not None and latency_seconds <= MAX_LATENCY_SECONDS
            if latency_seconds is not None and latency_seconds > MAX_LATENCY_SECONDS:
                fn_exclusion_reasons.append(f"latency={latency_seconds:.1f}s>{MAX_LATENCY_SECONDS}s")
            elif latency_seconds is None:
                # Don't exclude for missing latency - assume it's fine if we received it
                has_good_latency = True

            # Rule 5: Market cap must be <= $300M
            has_small_cap = market_cap is not None and market_cap <= MAX_MARKET_CAP_MILLIONS
            if market_cap is not None and market_cap > MAX_MARKET_CAP_MILLIONS:
                fn_exclusion_reasons.append(f"mktcap=${market_cap:.0f}M>${MAX_MARKET_CAP_MILLIONS}M")
            elif market_cap is None:
                # Don't exclude for missing market cap - assume it's small
                has_small_cap = True

            # Rule 6: Price must be >= $0.15
            stock_price = meta.get("price") or ask_price
            has_valid_price = stock_price is not None and stock_price >= MIN_STOCK_PRICE
            if stock_price is not None and stock_price < MIN_STOCK_PRICE:
                fn_exclusion_reasons.append(f"price=${stock_price:.2f}<${MIN_STOCK_PRICE}")
            elif stock_price is None:
                # Don't exclude for missing price
                has_valid_price = True

            # Determine initial eligibility (before tick simulation)
            is_eligible_fn = (
                has_sufficient_peak and
                would_not_stop_out and
                has_good_spread and
                has_good_latency and
                has_small_cap and
                has_valid_price
            )

            # === TICK SIMULATION for FN verification ===
            sim_result: Optional[SimulationResult] = None
            fn_outcome: Optional[str] = None

            if is_eligible_fn and SIMULATE_FN_WITH_TICK_DATA:
                # Get received time for simulation (entry is received + 3s, NOT publication + 3s)
                received_at = record.get("received_at")
                if received_at:
                    try:
                        from datetime import datetime
                        if isinstance(received_at, str):
                            recv_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
                        else:
                            recv_dt = received_at

                        # Run tick simulation (entry at received + 3 seconds)
                        sim_result = await simulate_trade(
                            ticker=ticker,
                            received_time=recv_dt,
                            entry_price_hint=ask_price,
                        )

                        if sim_result:
                            # Classify the outcome
                            if sim_result.would_have_traded and sim_result.total_pnl_pct > 0:
                                fn_outcome = "profitable"
                                logger.debug(
                                    f"FN verified: {ticker} sim_pnl={sim_result.total_pnl_pct:.1f}% "
                                    f"realized={sim_result.realized_pnl_pct:.1f}% "
                                    f"tp_events={len(sim_result.tp_events)}"
                                )
                            elif sim_result.stopped_out:
                                if sim_result.total_pnl_pct > 0:
                                    fn_outcome = "stopped_out_profit"  # Hit TP then stopped at raised stop
                                else:
                                    fn_outcome = "stopped_out_loss"
                                logger.debug(
                                    f"FN stopped out: {ticker} sim_pnl={sim_result.total_pnl_pct:.1f}% "
                                    f"stop_type={sim_result.stop_type} max={sim_result.max_pnl_pct:.1f}%"
                                )
                            else:
                                fn_outcome = "unprofitable"
                                logger.debug(f"FN unprofitable: {ticker} sim_pnl={sim_result.total_pnl_pct:.1f}%")

                            # All simulated candidates stay as FN for analysis
                            # (user wants to see stopped-out trades to tune parameters)
                        else:
                            # No quote data available - fall back to MAE proxy
                            fn_outcome = "no_data"
                            logger.debug(f"No quote data for {ticker}, using MAE proxy")
                    except Exception as e:
                        fn_outcome = "sim_error"
                        logger.warning(f"Simulation failed for {ticker}: {e}")

            if is_eligible_fn:
                category = "false_negative"
                fn_exclusion_reason = None
            else:
                category = "true_negative"
                fn_exclusion_reason = "; ".join(fn_exclusion_reasons) if fn_exclusion_reasons else None

            # Determine effective postfilter reason
            effective_postfilter = postfilter_reason
            if classification and classification.upper() == "IMMINENT" and not postfilter_reason:
                effective_postfilter = "unknown (historical)"

            features = self.extract_confluence_features(record)

            # Build simulation fields if we have results
            sim_fields = {}
            if sim_result:
                # Calculate stop P&L if stopped out
                stop_pnl = None
                if sim_result.stopped_out and sim_result.stop_price and sim_result.entry_price:
                    stop_pnl = round(((sim_result.stop_price - sim_result.entry_price) / sim_result.entry_price) * 100, 2)

                sim_fields = {
                    "sim_would_have_traded": sim_result.would_have_traded,
                    "sim_total_pnl_pct": sim_result.total_pnl_pct,
                    "sim_realized_pnl_pct": sim_result.realized_pnl_pct,
                    "sim_position_remaining_pct": sim_result.position_remaining_pct,
                    "sim_stopped_out": sim_result.stopped_out,
                    "sim_stop_type": sim_result.stop_type,
                    "sim_stop_pnl_pct": stop_pnl,
                    "sim_stop_elapsed_seconds": sim_result.stop_elapsed_seconds,
                    "sim_stop_timestamp": sim_result.stop_triggered_at.isoformat() if sim_result.stop_triggered_at else None,
                    "sim_max_pnl_pct": sim_result.max_pnl_pct,
                    "sim_max_pnl_elapsed_seconds": sim_result.max_pnl_elapsed_seconds,
                    "sim_min_pnl_pct": sim_result.min_pnl_pct,
                    "sim_tp_count": len(sim_result.tp_events),
                    "sim_quote_count": sim_result.trade_count,
                    "sim_entry_timestamp": sim_result.entry_time.isoformat() if sim_result.entry_time else None,
                    "sim_simple_hold_pnl_pct": sim_result.simple_hold_pnl_pct,
                    "sim_move_progression": sim_result.move_progression,
                    "fn_outcome": fn_outcome,
                }
            elif fn_outcome:
                sim_fields = {"fn_outcome": fn_outcome}

            classified.append(ClassifiedTrade(
                ticker=ticker,
                date=target_date.isoformat(),
                session=record.get("_session", ""),
                category=category,
                peak_pct=peak_pct,
                mae_pct=mae_pct,
                headline=record.get("title"),
                headline_type=record.get("headline_type"),
                prefilter_reason=prefilter_reason,
                postfilter_reason=effective_postfilter,
                fn_exclusion_reason=fn_exclusion_reason,
                spread_pct=spread_pct,
                latency_seconds=latency_seconds,
                source="recall",
                record_id=record.get("article_id"),
                **meta,
                **features,
                **sim_fields,
            ))

        return classified

    async def _enrich_tp_fp_with_simulation(
        self,
        tp_fp_trades: List[ClassifiedTrade],
        signal_records: List[Dict],
        recall_records: List[Dict],
    ) -> None:
        """
        Run tick simulation for TP/FP trades to get quote_count, move_progression, etc.

        Same simulator as FN verification - shows what standardized exit rules would have produced.
        Modifies trades in-place.
        """
        for trade in tp_fp_trades:
            try:
                # Find received_at from signal or recall record
                # Use article_id (stored in record_id) to match the correct article
                received_at = None
                entry_price_hint = trade.entry_price
                article_id = trade.record_id

                # Try signal record first — match by article_id, fall back to ticker
                matching_signal = None
                if article_id:
                    for record in signal_records:
                        if record.get("article_id") == article_id:
                            matching_signal = record
                            break
                if not matching_signal:
                    for record in signal_records:
                        if record.get("ticker") == trade.ticker:
                            matching_signal = record
                            break

                if matching_signal:
                    received_at = matching_signal.get("received_at")
                    if not article_id:
                        article_id = matching_signal.get("article_id")

                # Fall back to recall record — match by article_id, fall back to ticker
                if not received_at:
                    matched_recall = None
                    if article_id:
                        for recall_rec in recall_records:
                            if recall_rec.get("article_id") == article_id:
                                matched_recall = recall_rec
                                break
                    if not matched_recall:
                        for recall_rec in recall_records:
                            if trade.ticker in recall_rec.get("tickers", []):
                                matched_recall = recall_rec
                                break
                    if matched_recall:
                        received_at = matched_recall.get("received_at")
                        if not entry_price_hint:
                            nbbo = matched_recall.get("initial_nbbo", {})
                            entry_price_hint = nbbo.get("ask")

                if not received_at:
                    continue

                # Parse received_at
                if isinstance(received_at, str):
                    recv_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
                else:
                    recv_dt = received_at

                sim_result = await simulate_trade(
                    ticker=trade.ticker,
                    received_time=recv_dt,
                    entry_price_hint=entry_price_hint,
                )

                if not sim_result:
                    continue

                # Calculate stop P&L if stopped out
                stop_pnl = None
                if sim_result.stopped_out and sim_result.stop_price and sim_result.entry_price:
                    stop_pnl = round(((sim_result.stop_price - sim_result.entry_price) / sim_result.entry_price) * 100, 2)

                # Populate sim fields on the trade object
                trade.sim_would_have_traded = sim_result.would_have_traded
                trade.sim_total_pnl_pct = sim_result.total_pnl_pct
                trade.sim_realized_pnl_pct = sim_result.realized_pnl_pct
                trade.sim_position_remaining_pct = sim_result.position_remaining_pct
                trade.sim_stopped_out = sim_result.stopped_out
                trade.sim_stop_type = sim_result.stop_type
                trade.sim_stop_pnl_pct = stop_pnl
                trade.sim_stop_elapsed_seconds = sim_result.stop_elapsed_seconds
                trade.sim_stop_timestamp = sim_result.stop_triggered_at.isoformat() if sim_result.stop_triggered_at else None
                trade.sim_max_pnl_pct = sim_result.max_pnl_pct
                trade.sim_max_pnl_elapsed_seconds = sim_result.max_pnl_elapsed_seconds
                trade.sim_min_pnl_pct = sim_result.min_pnl_pct
                trade.sim_tp_count = len(sim_result.tp_events)
                trade.sim_quote_count = sim_result.trade_count
                trade.sim_entry_timestamp = sim_result.entry_time.isoformat() if sim_result.entry_time else None
                trade.sim_simple_hold_pnl_pct = sim_result.simple_hold_pnl_pct
                trade.sim_move_progression = sim_result.move_progression

                logger.debug(
                    f"TP/FP sim enriched: {trade.ticker} actual_pnl={trade.pnl_pct}% "
                    f"sim_pnl={sim_result.total_pnl_pct:.1f}% "
                    f"sim_peak={sim_result.max_pnl_pct:.1f}% "
                    f"quotes={sim_result.trade_count}"
                )

            except Exception as e:
                logger.warning(f"Simulation failed for TP/FP {trade.ticker}: {e}")

    def trade_to_dict(self, trade: ClassifiedTrade, category: str) -> Dict[str, Any]:
        """Convert a ClassifiedTrade to a dict with properly nested ML features."""
        # Derive move_type from confluence stats with exact thresholds
        # Use volume_ratio as volume_multiplier, surge fields for trade_count_multiplier
        # Falls back to binary flags when multipliers unavailable
        move_type_result = derive_move_type(
            volume=trade.confluence_volume,
            trade_count=trade.confluence_trade_count,
            imbalance_ratio=trade.confluence_imbalance_ratio,
            price_excursion_pct=trade.confluence_price_excursion_pct,
            volume_multiplier=trade.volume_ratio or trade.surge_volume_multiplier,
            trade_count_multiplier=trade.surge_trade_count_multiplier,
            has_volume_surge=trade.confluence_has_volume_surge,
            has_buying_pressure=trade.confluence_has_buying_pressure,
            has_price_excursion=trade.confluence_has_price_excursion,
        )

        # Base fields for all categories
        base = {
            "ticker": trade.ticker,
            "headline": trade.headline,
            "headline_type": trade.headline_type,
            "industry": trade.industry,
            "sector": trade.sector,
            "market_cap_millions": trade.market_cap_millions,
            "price": trade.price,
            "spread_pct": trade.spread_pct,  # Added to all categories for analysis
            "move_type": move_type_result["move_type"],  # surge/strength/low_activity/inactive
            "move_type_details": move_type_result["move_type_details"],  # Threshold checks
        }

        # === NESTED CONFLUENCE STATS (0-2 second window) ===
        confluence_stats = {
            # Core scoring
            "score": trade.confluence_score,
            "volume": trade.confluence_volume,
            "trade_count": trade.confluence_trade_count,
            "buy_volume": trade.confluence_buy_volume,
            "sell_volume": trade.confluence_sell_volume,
            # Pressure analysis
            "buying_pressure_pct": trade.confluence_buying_pressure_pct,
            "imbalance_ratio": trade.confluence_imbalance_ratio,
            "uptick_count": trade.confluence_uptick_count,
            "downtick_count": trade.confluence_downtick_count,
            # Price trajectory
            "price_excursion_pct": trade.confluence_price_excursion_pct,
            "first_price": trade.confluence_first_price,
            "last_price": trade.confluence_last_price,
            "max_price": trade.confluence_max_price,
            "min_price": trade.confluence_min_price,
            "vwap": trade.confluence_vwap,
            "price_direction": trade.confluence_price_direction,
            "dollar_volume": trade.confluence_dollar_volume,
            # Spread/liquidity
            "initial_spread": trade.confluence_initial_spread,
            "final_spread": trade.confluence_final_spread,
            "spread_compression_pct": trade.confluence_spread_compression_pct,
            # Trade size
            "avg_trade_size": trade.confluence_avg_trade_size,
            "median_trade_size": trade.confluence_median_trade_size,
            "max_single_trade": trade.confluence_max_single_trade,
            "large_trade_pct": trade.confluence_large_trade_pct,
            # Timing
            "first_trade_latency_ms": trade.confluence_first_trade_latency_ms,
            "max_trade_gap_ms": trade.confluence_max_trade_gap_ms,
            # Binary signals
            "has_volume_surge": trade.confluence_has_volume_surge,
            "has_price_excursion": trade.confluence_has_price_excursion,
            "has_buying_pressure": trade.confluence_has_buying_pressure,
            # Baseline ratio
            "volume_ratio": trade.volume_ratio,
        }

        # === NESTED SURGE STATS (8-second window, only if surge-based trade) ===
        # Will be populated from signal record's surge fields if trade was surge-based
        surge_stats = None
        if trade.surge_triggered:
            surge_stats = {
                "triggered": trade.surge_triggered,
                "found": trade.surge_found,
                "detection_cycle": trade.surge_detection_cycle,
                "seconds_elapsed": trade.surge_seconds_elapsed,
                "volume": trade.surge_volume,
                "trade_count": trade.surge_trade_count,
                "buy_volume": trade.surge_buy_volume,
                "sell_volume": trade.surge_sell_volume,
                "buying_pressure_pct": trade.surge_buying_pressure_pct,
                "imbalance_ratio": trade.surge_imbalance_ratio,
                "price_excursion_pct": trade.surge_price_excursion_pct,
                "volume_multiplier": trade.surge_volume_multiplier,
                "trade_count_multiplier": trade.surge_trade_count_multiplier,
                "ask": trade.surge_ask,
                "bid": trade.surge_bid,
                "mid": trade.surge_mid,
            }

        # Format elapsed time as "Xm Ys.XXXs"
        def format_elapsed(secs):
            if secs is None:
                return None
            mins = int(secs // 60)
            s = secs % 60
            if mins > 0:
                return f"{mins}m {s:.3f}s"
            return f"{s:.3f}s"

        # Build simulation block (reused for TP/FP and FN)
        def build_simulation_block(outcome=None):
            if trade.sim_total_pnl_pct is None:
                return None
            return {
                "outcome": outcome or trade.fn_outcome,
                "would_have_traded": trade.sim_would_have_traded,
                "total_pnl_pct": trade.sim_total_pnl_pct,
                "realized_pnl_pct": trade.sim_realized_pnl_pct,
                "position_remaining_pct": trade.sim_position_remaining_pct,
                # Stop details with timing
                "stopped_out": trade.sim_stopped_out,
                "stop_type": trade.sim_stop_type,
                "stop_pnl_pct": trade.sim_stop_pnl_pct,
                "stop_elapsed": format_elapsed(trade.sim_stop_elapsed_seconds),
                "stop_elapsed_seconds": trade.sim_stop_elapsed_seconds,
                "stop_timestamp": trade.sim_stop_timestamp,
                # Peak details with timing
                "max_pnl_pct": trade.sim_max_pnl_pct,
                "max_pnl_elapsed": format_elapsed(trade.sim_max_pnl_elapsed_seconds),
                "max_pnl_elapsed_seconds": trade.sim_max_pnl_elapsed_seconds,
                "min_pnl_pct": trade.sim_min_pnl_pct,
                # Simple hold comparison
                "simple_hold_pnl_pct": trade.sim_simple_hold_pnl_pct,
                # Move progression - when each price level was first crossed
                "move_progression": trade.sim_move_progression,
                # Other
                "tp_count": trade.sim_tp_count,
                "quote_count": trade.sim_quote_count,
                "entry_timestamp": trade.sim_entry_timestamp,
            }

        if category in ("true_positive", "false_positive"):
            # Trades we made - show P&L, peak/MAE, simulation + nested ML features
            base.update({
                "pnl_pct": trade.pnl_pct,
                "pnl_usd": trade.pnl_usd,
                "peak_pct": trade.peak_pct,     # Max gain from entry (what could have been)
                "mae_pct": trade.mae_pct,        # Max adverse excursion (worst drawdown from entry)
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "shares": trade.shares,
                "confluence_stats": confluence_stats,
                "surge_stats": surge_stats,  # null if confluence-based, populated if surge-based
            })

            # Include tick simulation (shows what standardized exit rules would have produced)
            sim_block = build_simulation_block(outcome=trade.category)
            if sim_block:
                base["simulation"] = sim_block

        elif category == "false_negative":
            # Missed winners - show peak, MAE, latency + simulation results + nested ML features
            # (spread_pct and move_type already in base for all categories)
            base.update({
                "peak_pct": trade.peak_pct,
                "mae_pct": trade.mae_pct,
                "latency_seconds": trade.latency_seconds,
                "prefilter_reason": trade.prefilter_reason,
                "postfilter_reason": trade.postfilter_reason,
                "fn_outcome": trade.fn_outcome,  # "profitable", "stopped_out_loss", "stopped_out_profit", etc.
                "confluence_stats": confluence_stats,
            })

            # Include tick simulation results if available
            sim_block = build_simulation_block()
            if sim_block:
                base["simulation"] = sim_block

        else:  # true_negative
            # Correctly ignored - show peak, filter reasons, why not FN
            # (spread_pct and move_type already in base for all categories)
            base.update({
                "peak_pct": trade.peak_pct,
                "mae_pct": trade.mae_pct,
                "latency_seconds": trade.latency_seconds,
                "prefilter_reason": trade.prefilter_reason,
                "postfilter_reason": trade.postfilter_reason,
                "fn_exclusion_reason": trade.fn_exclusion_reason,
                "confluence_stats": {
                    "score": trade.confluence_score,
                    "volume": trade.confluence_volume,
                } if trade.confluence_score is not None else None,
            })

            # Include simulation results if ran (to show why it failed)
            if trade.sim_total_pnl_pct is not None:
                base["simulation"] = {
                    "would_have_traded": trade.sim_would_have_traded,
                    "total_pnl_pct": trade.sim_total_pnl_pct,
                    "stopped_out": trade.sim_stopped_out,
                    "stop_type": trade.sim_stop_type,
                    "min_pnl_pct": trade.sim_min_pnl_pct,
                }

        return base

    def write_category_file(
        self,
        trades: List[ClassifiedTrade],
        category: str,
        output_dir: Path,
        target_date: date,
        market_regime: Dict[str, Any] = None,
    ) -> Path:
        """Write JSON file for a category (better for statistical analysis)."""
        sim_note = ", verified with tick simulation" if SIMULATE_FN_WITH_TICK_DATA else ""
        descriptions = {
            "true_positive": f"Trades we made that were profitable (>= +{WINNER_THRESHOLD_PCT}%)",
            "false_positive": f"Trades we made that lost money (<= {LOSER_THRESHOLD_PCT}%)",
            "false_negative": f"Missed winners: {MIN_PEAK_FOR_FN_PCT}%+ peak, price>=${MIN_STOCK_PRICE}, spread<{MAX_SPREAD_PCT}%, latency<={MAX_LATENCY_SECONDS}s, mktcap<=${MAX_MARKET_CAP_MILLIONS}M{sim_note}",
            "true_negative": f"Correctly ignored (peak<{MIN_PEAK_FOR_FN_PCT}%, stopped out in sim, spread too wide, latency too high, price too low, or large cap)",
        }

        # Sort by outcome
        sorted_trades = sorted(trades, key=lambda t: -(t.pnl_pct or t.peak_pct or 0))

        output_data = {
            "date": target_date.isoformat(),
            "category": category,
            "description": descriptions[category],
            "count": len(trades),
            # Market regime at file level for correlation analysis
            "market_regime": market_regime or get_market_regime(target_date),
            "records": [self.trade_to_dict(t, category) for t in sorted_trades]
        }

        output_file = output_dir / f"{category}.json"
        with open(output_file, "w") as f:
            json.dump(output_data, f, indent=2)

        return output_file

    async def run(self, target_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """
        Classify all trades for a date.

        When run after market close (8pm ET), classifies TODAY's completed trading day.
        This includes premarket, market hours, and postmarket sessions.

        Returns dict with counts, metrics, and file paths.
        """
        if target_date is None:
            now_et = datetime.now(ET_TZ)
            # Classify today's data (the day that just finished trading)
            # Job should run at 8pm ET after postmarket closes
            target_date = now_et.date()

        logger.info(f"Classifying trades for {target_date}")

        # Load records
        signal_records = self.load_signal_records(target_date)
        recall_records = self.load_recall_records(target_date)

        logger.info(f"Loaded {len(signal_records)} signal records, {len(recall_records)} recall records")

        # Create headline lookup from recall records (article_id -> title)
        headline_lookup = {}
        for record in recall_records:
            article_id = record.get("article_id")
            if article_id and record.get("title"):
                headline_lookup[article_id] = record.get("title")

        # Get Alpaca trades if available
        alpaca_trades = {}
        if self.alpaca_client:
            alpaca_trades = get_alpaca_trades_for_date(self.alpaca_client, target_date)
            logger.info(f"Loaded Alpaca trades for {len(alpaca_trades)} tickers")

        # Classify TP/FP
        if alpaca_trades:
            # Use Alpaca data (authoritative)
            tp_fp_trades = self.classify_trades_with_alpaca(
                signal_records, alpaca_trades, target_date, headline_lookup, recall_records
            )
            traded_tickers = set(alpaca_trades.keys())
        else:
            # Fall back to signal records
            tp_fp_trades = self.classify_trades_from_signal(
                signal_records, target_date, headline_lookup, recall_records
            )
            # Even if we couldn't classify P&L, mark these tickers as traded
            # so they don't appear as false negatives
            traded_tickers = set(record.get("ticker") for record in signal_records if record.get("ticker"))

        # Enrich TP/FP trades with tick simulation (same as FN - gives quote_count, move_progression)
        if SIMULATE_FN_WITH_TICK_DATA and tp_fp_trades:
            await self._enrich_tp_fp_with_simulation(tp_fp_trades, signal_records, recall_records)

        # Classify FN/TN from recall records (with tick simulation for FN verification)
        fn_tn_trades = await self.classify_recall_records(recall_records, traded_tickers, target_date)

        # Combine all classified trades
        all_trades = tp_fp_trades + fn_tn_trades

        # Group by category
        by_category = {
            "true_positive": [t for t in all_trades if t.category == "true_positive"],
            "false_positive": [t for t in all_trades if t.category == "false_positive"],
            "false_negative": [t for t in all_trades if t.category == "false_negative"],
            "true_negative": [t for t in all_trades if t.category == "true_negative"],
        }

        counts = {k: len(v) for k, v in by_category.items()}

        logger.info(f"Classification complete: TP={counts['true_positive']}, FP={counts['false_positive']}, FN={counts['false_negative']}, TN={counts['true_negative']}")

        # Calculate metrics
        tp = counts["true_positive"]
        fp = counts["false_positive"]
        fn = counts["false_negative"]

        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall and (precision + recall) > 0 else None

        # Write output files
        output_dir = self.output_path / target_date.isoformat()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Fetch market regime once (SPY/QQQ direction for the day)
        market_regime = get_market_regime(target_date)

        files = {}
        for category, trades in by_category.items():
            if trades:
                file_path = self.write_category_file(
                    trades, category, output_dir, target_date, market_regime
                )
                files[category] = str(file_path)

        # Write summary JSON
        summary = {
            "date": target_date.isoformat(),
            "generated_at": datetime.now().isoformat(),
            "market_regime": market_regime,
            "data_sources": {
                "signal_records": len(signal_records),
                "recall_records": len(recall_records),
                "alpaca_tickers": len(alpaca_trades),
                "used_alpaca": bool(alpaca_trades),
            },
            "counts": counts,
            "metrics": {
                "precision": round(precision, 4) if precision is not None else None,
                "recall": round(recall, 4) if recall is not None else None,
                "f1_score": round(f1, 4) if f1 is not None else None,
            },
            "files": files,
        }

        summary_file = output_dir / "summary.json"
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2)

        return {
            "date": target_date.isoformat(),
            "counts": counts,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1_score": round(f1, 4) if f1 is not None else None,
            "files": files,
            "summary_file": str(summary_file),
            "data_source": "alpaca" if alpaca_trades else "signal_records",
        }


class WeeklyAggregationJob:
    """
    Aggregates daily classifications into weekly training data.
    Runs every Friday at 1am after postmarket close.
    """

    def __init__(
        self,
        classification_path: Path = Path("tmp/trade_classification"),
    ):
        self.classification_path = classification_path
        self.daily_path = classification_path / "daily"
        self.weekly_path = classification_path / "weekly"
        self.weekly_path.mkdir(parents=True, exist_ok=True)

    def get_week_dates(self, target_date: date) -> Tuple[date, date]:
        """Get Monday-Friday for the week containing target_date."""
        # Get to Friday of the week
        days_until_friday = (4 - target_date.weekday()) % 7
        friday = target_date + timedelta(days=days_until_friday)
        monday = friday - timedelta(days=4)
        return monday, friday

    def load_daily_classifications(self, monday: date, friday: date) -> Dict[str, List[Dict]]:
        """Load all daily classification data for a week."""
        all_trades: Dict[str, List[Dict]] = {
            "true_positive": [],
            "false_positive": [],
            "false_negative": [],
            "true_negative": [],
        }

        current = monday
        while current <= friday:
            day_dir = self.daily_path / current.isoformat()
            if day_dir.exists():
                # Load each category file (JSON format)
                for category in all_trades.keys():
                    category_file = day_dir / f"{category}.json"
                    if category_file.exists():
                        try:
                            with open(category_file) as f:
                                data = json.load(f)
                            records = data.get("records", [])
                            # Add date to each record for weekly aggregation
                            for record in records:
                                record["date"] = current.isoformat()
                            all_trades[category].extend(records)
                        except Exception as e:
                            logger.warning(f"Failed to load {category_file}: {e}")

            current += timedelta(days=1)

        return all_trades

    def _format_trade_line(self, trade: Dict) -> str:
        """Format a trade for human-readable output."""
        date_str = trade.get("date", "")[-5:] if trade.get("date") else "??/??"
        ticker = trade.get("ticker", "???")

        if trade.get("pnl_pct") is not None:
            outcome = f"{trade['pnl_pct']:+.1f}%"
        elif trade.get("peak_pct") is not None:
            outcome = f"+{trade['peak_pct']:.1f}% peak"
        else:
            outcome = "???"

        mae_str = f" MAE:{trade.get('mae_pct', 0):.1f}%" if trade.get("mae_pct") else ""
        industry = (trade.get("industry") or "???")[:22]
        cap = f"${trade.get('market_cap_millions', 0):.0f}M" if trade.get("market_cap_millions") else "$???M"
        headline_type = (trade.get("headline_type") or "unknown")[:12]
        headline = (trade.get("headline") or "???")[:50]

        return (
            f"{date_str} | {ticker:6} | {outcome:15}{mae_str:14} | "
            f"{industry:22} | {cap:10} | "
            f"{headline_type:12} | {headline}"
        )

    def _write_category_file(
        self,
        trades: List[Dict],
        category: str,
        output_dir: Path,
        week_label: str,
        start_date: date,
        end_date: date,
    ) -> Path:
        """Write human-readable file for a category."""
        titles = {
            "true_positive": "TRUE POSITIVES - Profitable Trades",
            "false_positive": "FALSE POSITIVES - Losing Trades",
            "false_negative": "FALSE NEGATIVES - Missed Winners",
            "true_negative": "TRUE NEGATIVES - Correctly Ignored",
        }

        lines = []
        lines.append("=" * 200)
        lines.append(f"{titles[category]}")
        lines.append(f"Week: {week_label} ({start_date} to {end_date}) | Count: {len(trades)}")
        lines.append("=" * 200)
        lines.append("")
        lines.append(
            f"{'DATE':5} | {'TICKER':6} | {'OUTCOME':15}{'MAE':14} | "
            f"{'INDUSTRY':22} | {'MKT CAP':10} | "
            f"{'TYPE':12} | HEADLINE"
        )
        lines.append("-" * 200)

        for trade in sorted(trades, key=lambda t: (t.get("date", ""), -(t.get("pnl_pct") or t.get("peak_pct") or 0))):
            lines.append(self._format_trade_line(trade))

        lines.append("")
        lines.append("=" * 200)

        output_file = output_dir / f"{category}.txt"
        with open(output_file, "w") as f:
            f.write("\n".join(lines))

        return output_file

    async def run(self, target_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """
        Aggregate a week's classifications into training data.

        When run on Friday night (after 8pm ET), aggregates Mon-Fri of the current week.
        The daily job should run first to ensure Friday's data is classified.

        Args:
            target_date: Any date in the target week (defaults to today/Friday)
        """
        if target_date is None:
            now_et = datetime.now(ET_TZ)
            # Use today's date - when run Friday night, this gives us the current week
            target_date = now_et.date()

        monday, friday = self.get_week_dates(target_date)
        year = friday.year
        week_num = friday.isocalendar()[1]

        logger.info(f"Aggregating week {year}_week_{week_num}: {monday} to {friday}")

        # Load all daily data from JSON files
        all_trades = self.load_daily_classifications(monday, friday)
        totals = {k: len(v) for k, v in all_trades.items()}

        # Calculate metrics
        tp = totals["true_positive"]
        fp = totals["false_positive"]
        fn = totals["false_negative"]

        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall and (precision + recall) > 0 else None

        # Create output directory
        output_dir = self.weekly_path / f"{year}_week_{week_num}"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write aggregated stats
        stats = {
            "week": f"{year}_week_{week_num}",
            "start_date": monday.isoformat(),
            "end_date": friday.isoformat(),
            "generated_at": datetime.now().isoformat(),
            "totals": totals,
            "metrics": {
                "precision": round(precision, 4) if precision is not None else None,
                "recall": round(recall, 4) if recall is not None else None,
                "f1_score": round(f1, 4) if f1 is not None else None,
            },
        }

        stats_file = output_dir / "aggregated_stats.json"
        with open(stats_file, "w") as f:
            json.dump(stats, f, indent=2)

        # Write JSON files for each category (consistent with daily format)
        json_files = {}
        descriptions = {
            "true_positive": f"Trades we made that were profitable (>= +{WINNER_THRESHOLD_PCT}%)",
            "false_positive": f"Trades we made that lost money (<= {LOSER_THRESHOLD_PCT}%)",
            "false_negative": f"Missed winners: {MIN_PEAK_FOR_FN_PCT}%+ peak, MAE<={MAX_MAE_FOR_FN_PCT}%, spread<{MAX_SPREAD_PCT}%, latency<={MAX_LATENCY_SECONDS}s, mktcap<=${MAX_MARKET_CAP_MILLIONS}M",
            "true_negative": "Correctly ignored (peak<10%, MAE too high, spread too wide, latency too high, or large cap)",
        }
        for category, trades in all_trades.items():
            if trades:
                output_data = {
                    "week": f"{year}_week_{week_num}",
                    "start_date": monday.isoformat(),
                    "end_date": friday.isoformat(),
                    "category": category,
                    "description": descriptions[category],
                    "count": len(trades),
                    "records": sorted(trades, key=lambda t: -(t.get("pnl_pct") or t.get("peak_pct") or 0))
                }
                json_file = output_dir / f"{category}.json"
                with open(json_file, "w") as f:
                    json.dump(output_data, f, indent=2)
                json_files[category] = str(json_file)

        # Create training data (labeled for ML)
        training_samples = []
        for category, trades in all_trades.items():
            # Label: 1 for should_trade (TP + FN), 0 for should_not_trade (FP + TN)
            label = 1 if category in ["true_positive", "false_negative"] else 0
            for trade in trades:
                trade["label"] = label
                trade["category"] = category
                training_samples.append(trade)

        training_data = {
            "week": f"{year}_week_{week_num}",
            "generated_at": datetime.now().isoformat(),
            "label_definition": {
                "1": "Should trade (TP + FN)",
                "0": "Should not trade (FP + TN)",
            },
            "samples": training_samples,
        }

        training_file = output_dir / "training_data.json"
        with open(training_file, "w") as f:
            json.dump(training_data, f, indent=2, default=str)

        logger.info(
            f"Weekly aggregation complete",
            week=f"{year}_week_{week_num}",
            precision=precision,
            recall=recall,
            f1=f1,
            total_samples=len(training_samples),
        )

        return {
            "week": f"{year}_week_{week_num}",
            "start_date": monday.isoformat(),
            "end_date": friday.isoformat(),
            "stats_file": str(stats_file),
            "training_file": str(training_file),
            "json_files": json_files,
            "metrics": stats["metrics"],
            "totals": totals,
        }


class AllTimeAggregationJob:
    """
    Aggregates all weekly data into all-time statistics.
    Run after weekly aggregation to update cumulative metrics.
    """

    def __init__(
        self,
        classification_path: Path = Path("tmp/trade_classification"),
    ):
        self.classification_path = classification_path
        self.weekly_path = classification_path / "weekly"
        self.aggregated_path = classification_path / "aggregated"
        self.aggregated_path.mkdir(parents=True, exist_ok=True)

    async def run(self) -> Optional[Dict[str, Any]]:
        """
        Aggregate all weekly data into all-time statistics.
        """
        logger.info("Aggregating all-time statistics")

        # Find all weekly directories
        weekly_dirs = sorted(self.weekly_path.glob("*_week_*"))
        if not weekly_dirs:
            logger.warning("No weekly data found")
            return None

        # Load all weekly data
        all_trades: Dict[str, List[Dict]] = {
            "true_positive": [],
            "false_positive": [],
            "false_negative": [],
            "true_negative": [],
        }
        weeks_included = []

        for weekly_dir in weekly_dirs:
            week_name = weekly_dir.name
            weeks_included.append(week_name)

            for category in all_trades.keys():
                category_file = weekly_dir / f"{category}.json"
                if category_file.exists():
                    try:
                        with open(category_file) as f:
                            data = json.load(f)
                        records = data.get("records", [])
                        # Add week to each record
                        for record in records:
                            record["week"] = week_name
                        all_trades[category].extend(records)
                    except Exception as e:
                        logger.warning(f"Failed to load {category_file}: {e}")

        totals = {k: len(v) for k, v in all_trades.items()}

        # Calculate metrics
        tp = totals["true_positive"]
        fp = totals["false_positive"]
        fn = totals["false_negative"]
        tn = totals["true_negative"]

        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall and (precision + recall) > 0 else None
        accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) > 0 else None

        # Write aggregated stats
        stats = {
            "generated_at": datetime.now().isoformat(),
            "weeks_included": weeks_included,
            "first_week": weeks_included[0] if weeks_included else None,
            "last_week": weeks_included[-1] if weeks_included else None,
            "totals": totals,
            "metrics": {
                "precision": round(precision, 4) if precision is not None else None,
                "recall": round(recall, 4) if recall is not None else None,
                "f1_score": round(f1, 4) if f1 is not None else None,
                "accuracy": round(accuracy, 4) if accuracy is not None else None,
            },
        }

        stats_file = self.aggregated_path / "all_time_stats.json"
        with open(stats_file, "w") as f:
            json.dump(stats, f, indent=2)

        # Write JSON files for each category
        descriptions = {
            "true_positive": f"Trades we made that were profitable (>= +{WINNER_THRESHOLD_PCT}%)",
            "false_positive": f"Trades we made that lost money (<= {LOSER_THRESHOLD_PCT}%)",
            "false_negative": f"Missed winners: {MIN_PEAK_FOR_FN_PCT}%+ peak, MAE<={MAX_MAE_FOR_FN_PCT}%, spread<{MAX_SPREAD_PCT}%, latency<={MAX_LATENCY_SECONDS}s, mktcap<=${MAX_MARKET_CAP_MILLIONS}M",
            "true_negative": "Correctly ignored (peak<10%, MAE too high, spread too wide, latency too high, or large cap)",
        }
        json_files = {}
        for category, trades in all_trades.items():
            if trades:
                output_data = {
                    "category": category,
                    "description": descriptions[category],
                    "weeks_included": weeks_included,
                    "count": len(trades),
                    "records": sorted(trades, key=lambda t: -(t.get("pnl_pct") or t.get("peak_pct") or 0))
                }
                json_file = self.aggregated_path / f"{category}.json"
                with open(json_file, "w") as f:
                    json.dump(output_data, f, indent=2)
                json_files[category] = str(json_file)

        # Create all-time training data
        training_samples = []
        for category, trades in all_trades.items():
            label = 1 if category in ["true_positive", "false_negative"] else 0
            for trade in trades:
                trade_copy = trade.copy()
                trade_copy["label"] = label
                trade_copy["category"] = category
                training_samples.append(trade_copy)

        training_data = {
            "generated_at": datetime.now().isoformat(),
            "weeks_included": weeks_included,
            "label_definition": {
                "1": "Should trade (TP + FN)",
                "0": "Should not trade (FP + TN)",
            },
            "total_samples": len(training_samples),
            "samples": training_samples,
        }

        training_file = self.aggregated_path / "all_time_training_data.json"
        with open(training_file, "w") as f:
            json.dump(training_data, f, indent=2, default=str)

        logger.info(
            f"All-time aggregation complete",
            weeks=len(weeks_included),
            total_samples=len(training_samples),
            precision=precision,
            recall=recall,
            f1=f1,
        )

        return {
            "weeks_included": weeks_included,
            "stats_file": str(stats_file),
            "training_file": str(training_file),
            "json_files": json_files,
            "metrics": stats["metrics"],
            "totals": totals,
        }


# Convenience functions for scripts
async def run_daily_classification(target_date: Optional[date] = None) -> Optional[Dict]:
    """Run daily classification job."""
    job = TradeClassificationJob()
    return await job.run(target_date)


async def run_weekly_aggregation(target_date: Optional[date] = None) -> Optional[Dict]:
    """Run weekly aggregation job."""
    job = WeeklyAggregationJob()
    return await job.run(target_date)


async def run_all_time_aggregation() -> Optional[Dict]:
    """Run all-time aggregation job."""
    job = AllTimeAggregationJob()
    return await job.run()
