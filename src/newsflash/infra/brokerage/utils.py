"""
Brokerage utility functions.
"""
import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest

logger = get_logger(__name__)


# ============================================================
# ACTIVITY GATE — second-stage precision filter at submit time
# ============================================================
# Replaces the prior sustained-depth probe (zero edge — calm books are losers,
# bursty books are winners; depth was the wrong axis entirely).
#
# Evaluates microstructure activity since publication. Fires when ANY of:
#   - quote_intensity (qi) ≥ QI_THRESHOLD quotes/sec
#   - trade_per_sec   (tps) ≥ TPS_THRESHOLD trades/sec
#   - mid_drift_pct since pub ≥ MD_THRESHOLD_PCT
#
# Backtest evidence (April 2026, 30d, 121 winners ≥+15% vs 341 active losers
# at peak <+2% in 10min, 500-loser variant for scale):
#   - At t=20s window, 86% of winners catch, 28% of active losers (3.1× lift)
#   - Combined with STRENGTH (drift≥0.5% + ≥5 trades in 0..2s) at submit:
#     STRENGTH ∩ activity-gate = 51 winners caught / 0 losers (100% precision)
#   - Among STRENGTH-passing losers (the snapshot-flicker false positives),
#     this gate catches 25/27 — exactly the cohort the depth probe missed.
#
# Most quality winners (AGPU, OMEX, ONFO, CETX, DGNX, NEXR, LRHC, KIDZ, RECT,
# PMEC) light up at t=3s post-pub — earliest fire is fast.
# Slow burns (PMEC+243, CAPS, RVMD) fire at t=15s+ via mid-drift, which is
# why max_wait_s is 8s for the STRENGTH path; LATE entry path naturally
# evaluates the gate at later elapsed times (it runs at submit time).
#
# SELLs are NEVER gated — never block a liquidation.
QI_THRESHOLD = 5.0
TPS_THRESHOLD = 5.0
MD_THRESHOLD_PCT = 3.0
ACTIVITY_GATE_MAX_WAIT_S = 8.0
ACTIVITY_GATE_POLL_INTERVAL_S = 0.5


def _to_naive_utc(dt: datetime) -> datetime:
    """Normalise to naive UTC for comparison with stream cache timestamps."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def wait_for_activity_gate(
    quote_fetcher,
    ticker: str,
    pub_time: datetime,
    max_wait_s: float = ACTIVITY_GATE_MAX_WAIT_S,
    qi_threshold: float = QI_THRESHOLD,
    tps_threshold: float = TPS_THRESHOLD,
    md_threshold_pct: float = MD_THRESHOLD_PCT,
) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Confirm sustained microstructure activity since publication.

    Reads the WebSocket stream cache (no REST) for quotes + trades from
    pub_time to now and computes:
      qi  = quotes / elapsed_since_pub
      tps = trades / elapsed_since_pub
      md  = (last_mid - first_mid) / first_mid * 100   (averaged over first/last 5)

    Returns immediately on the first threshold trip; otherwise keeps polling
    until max_wait_s and returns False with the most recent telemetry.

    Args:
        quote_fetcher: Has .stream_manager exposing get_recent_quotes / _trades.
        ticker: Stock ticker symbol.
        pub_time: Article publication timestamp (tz-aware or naive UTC).
        max_wait_s: Maximum wait duration in seconds (default 8s — most
                    winners fire by t+3s post-pub; LATE path callers naturally
                    evaluate at higher elapsed times via current wall clock).
        qi_threshold: Quote intensity threshold (quotes/sec).
        tps_threshold: Trade-per-second threshold.
        md_threshold_pct: Mid drift threshold in percent.

    Returns:
        (passed, last_nbbo, telemetry).
    """
    start = time.time()
    pub_naive = _to_naive_utc(pub_time)
    stream_manager = getattr(quote_fetcher, "stream_manager", None)
    last_nbbo: Optional[Dict[str, Any]] = None
    last_telemetry: Dict[str, Any] = {}

    while True:
        elapsed_wait = time.time() - start
        now_naive = datetime.utcnow()
        elapsed_since_pub = (now_naive - pub_naive).total_seconds()
        elapsed_since_pub = max(elapsed_since_pub, 0.001)

        quotes: list = []
        trades: list = []
        if stream_manager is not None:
            try:
                all_q = await stream_manager.get_recent_quotes(ticker, max_quotes=1000)
                quotes = [
                    q for q in all_q
                    if q.get("timestamp") is not None
                    and _to_naive_utc(q["timestamp"]) >= pub_naive
                ]
            except Exception as e:
                logger.debug(f"activity gate: get_recent_quotes error for {ticker}: {e}")
            try:
                all_t = await stream_manager.get_recent_trades(ticker, max_trades=1000)
                trades = [
                    t for t in all_t
                    if t.get("timestamp") is not None
                    and _to_naive_utc(t["timestamp"]) >= pub_naive
                ]
            except Exception as e:
                logger.debug(f"activity gate: get_recent_trades error for {ticker}: {e}")

        n_quotes = len(quotes)
        n_trades = len(trades)
        qi = n_quotes / elapsed_since_pub
        tps = n_trades / elapsed_since_pub

        md_pct = None
        if len(quotes) >= 4:
            first_chunk = quotes[:5]
            last_chunk = quotes[-5:]
            first_mids = [(q["bid"] + q["ask"]) / 2 for q in first_chunk
                          if q.get("bid") and q.get("ask")]
            last_mids = [(q["bid"] + q["ask"]) / 2 for q in last_chunk
                         if q.get("bid") and q.get("ask")]
            if first_mids and last_mids:
                fm = sum(first_mids) / len(first_mids)
                lm = sum(last_mids) / len(last_mids)
                if fm > 0:
                    md_pct = (lm / fm - 1) * 100

        if quotes:
            last_nbbo = quotes[-1]

        qi_fires = qi >= qi_threshold
        tps_fires = tps >= tps_threshold
        md_fires = md_pct is not None and md_pct >= md_threshold_pct

        last_telemetry = {
            "activity_gate_qi": round(qi, 2),
            "activity_gate_tps": round(tps, 2),
            "activity_gate_md_pct": round(md_pct, 2) if md_pct is not None else None,
            "activity_gate_n_quotes": n_quotes,
            "activity_gate_n_trades": n_trades,
            "activity_gate_elapsed_since_pub_s": round(elapsed_since_pub, 2),
            "activity_gate_wait_s": round(elapsed_wait, 2),
        }

        if qi_fires or tps_fires or md_fires:
            triggered = []
            if qi_fires:
                triggered.append("qi")
            if tps_fires:
                triggered.append("tps")
            if md_fires:
                triggered.append("md")
            last_telemetry["activity_gate_passed"] = True
            last_telemetry["activity_gate_triggered_by"] = ",".join(triggered)
            return True, last_nbbo, last_telemetry

        if elapsed_wait >= max_wait_s:
            last_telemetry["activity_gate_passed"] = False
            last_telemetry["activity_gate_triggered_by"] = None
            return False, last_nbbo, last_telemetry

        await asyncio.sleep(ACTIVITY_GATE_POLL_INTERVAL_S)


def calculate_trade_quantity(
    trade_request: TradeRequest,
    current_price: float,
    leverage: float = 2.0,
) -> Tuple[float, float]:
    """
    Calculate share quantity for trade with leverage.
    
    Business Rule: Pay for one share, leverage the second.
    - With 2x leverage: Pay for 1 share, get 2 shares total
    - Quantity = leverage (e.g., 2.0 shares with 2x leverage)
    - Capital required = price of 1 share (we pay for 1, leverage provides the second)
    - Total cost = quantity × price (actual cost to Alpaca)
    
    IMPORTANT: When leverage is used, we IGNORE amount_usd setting completely.
    Capital is always = price of 1 share, regardless of any $100 base setting.
    
    Args:
        trade_request: Trade request
        current_price: Current stock price
        leverage: Leverage multiplier (default 2.0)
        
    Returns:
        Tuple of (quantity, capital_required) where:
        - quantity: Number of shares to buy (always = leverage when leverage is used)
        - capital_required: Capital we need to put up (always = price of 1 share when leverage is used)
    """
    quantity = trade_request.shares
    
    # Calculate quantity if not provided (with leverage)
    if quantity is None:
        if leverage and leverage > 1.0:
            # BUSINESS RULE: Pay for one share, leverage the second
            # With 2x leverage: Pay for 1 share (capital), get 2 shares total
            # We completely ignore amount_usd - capital is always price of 1 share
            quantity = float(leverage)  # Always buy exactly leverage shares (e.g., 2.0 with 2x leverage)
            capital_required = current_price  # Always pay for 1 share only (price of 1 share)
            
            logger.info(
                "Calculated share quantity with leverage: pay for 1 share, leverage provides the second",
                quantity=quantity,
                capital_required=capital_required,
                leverage=leverage,
                price_per_share=current_price,
                total_cost=quantity * current_price,
                note="amount_usd setting ignored when leverage is used"
            )
        else:
            # No leverage: use amount_usd directly
            base_notional = float(trade_request.amount_usd)
            quantity = base_notional / current_price
            capital_required = base_notional
            
            logger.info(
                "Calculated share quantity without leverage",
                quantity=quantity,
                capital_required=capital_required,
                price=current_price,
            )
    else:
        # If explicit shares provided, use as-is (supports fractional)
        quantity = float(quantity)
        # Capital required = cost of 1 share (that's what we leverage from)
        capital_required = current_price

    # Always round down to whole shares - many small-cap stocks don't support fractional trading
    # This ensures compatibility with all assets and avoids "not fractionable" errors
    original_quantity = quantity
    quantity = math.floor(quantity)
    if quantity != original_quantity:
        logger.info(
            "Rounded quantity to whole shares (fractionable safety)",
            original_quantity=round(original_quantity, 4),
            rounded_quantity=quantity,
            capital_difference=round((original_quantity - quantity) * current_price, 2)
        )

    # Ensure at least 1 share
    if quantity < 1:
        logger.warning(
            "Quantity too small for 1 share, setting to 1",
            original_quantity=original_quantity,
            price=current_price,
            capital_required=current_price
        )
        quantity = 1
        capital_required = current_price

    return quantity, capital_required
