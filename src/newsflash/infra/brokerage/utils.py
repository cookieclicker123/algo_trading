"""
Brokerage utility functions.
"""
import asyncio
import math
import time
from typing import Any, Dict, Optional, Tuple

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest

logger = get_logger(__name__)


# Liquidity gate: block BUY orders whose share count is >= this fraction of the
# live displayed ask depth. Applied against a fresh NBBO snapshot taken
# immediately before order submission. Empirically calibrated on April 2026
# premarket trades — ratios >= 0.5 cluster in losses and exit-chase failures.
DEPTH_GATE_MAX_RATIO = 0.5

# ============================================================
# SUSTAINED-DEPTH PROBE
# ============================================================
# Truth-filter probe: $5K is the standard "is the book real" assay, decoupled
# from actual order size. We trade up to $10K but the gate only cares whether
# a $5K notional is < 50% of displayed ask. Empirically (April 2026 sweep across
# 85 big movers ≥+8% in 10min):
#   - Single snapshot misses bimodal books (AGPU passes 42% of the time at $10K
#     but oscillates between 100-share flickers and 10,000-share floors).
#   - Requiring 3 consecutive deep quotes catches 23/85 winners and 0 fakers
#     across the full pub+2s..pub+15s window (vs 3/85 at 750ms — way too short).
# Budget bumped 2026-04-29 from 13s → 18s: news is often received late
# (pub_to_recv 5-10s common), so a 13s probe budget meant the effective window
# closed before pub+20s. 18s budget covers up through pub+20s typical.
DEPTH_PROBE_USD = 5_000.0
DEPTH_PROBE_MIN_CONSECUTIVE = 3
DEPTH_PROBE_MAX_WAIT_S = 18.0
DEPTH_PROBE_POLL_INTERVAL_S = 0.1


async def wait_for_sustained_depth(
    quote_fetcher,
    ticker: str,
    max_wait_s: float = DEPTH_PROBE_MAX_WAIT_S,
    probe_size_usd: float = DEPTH_PROBE_USD,
    min_consecutive: int = DEPTH_PROBE_MIN_CONSECUTIVE,
    gate_ratio: float = DEPTH_GATE_MAX_RATIO,
) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Probe the live book for sustained depth at a fixed $5K truth-filter size.

    A "deep" quote = ask_size * gate_ratio * ask_price >= probe_size_usd, i.e.
    a $5K notional is < 50% of displayed ask. Counts CONSECUTIVE distinct
    quotes (deduped by timestamp) — flickers don't count, sustained depth does.

    Returns immediately on N consecutive deep quotes, aborts on max_wait_s.

    Args:
        quote_fetcher: Has .get_nbbo_snapshot(symbol) returning ask/ask_size dict
                       and optionally .stream_manager for cached quote history
        ticker: Stock ticker symbol
        max_wait_s: Maximum probe duration in seconds
        probe_size_usd: Notional for the truth-filter (default $5K)
        min_consecutive: Number of consecutive deep quotes required (default 3)
        gate_ratio: Fraction-of-book threshold (default 0.5 = "must be < 50%")

    Returns:
        (passed, last_nbbo, telemetry) where:
          passed: True if N consecutive deep quotes observed within budget
          last_nbbo: Most recent NBBO snapshot dict (bid/ask/ask_size/...)
          telemetry: dict with diagnostic fields for logging/records
    """
    start = time.time()
    consecutive = 0
    last_seen_ts = None
    last_nbbo: Optional[Dict[str, Any]] = None
    quotes_observed = 0
    deep_observed = 0
    max_consecutive = 0
    deepest_ratio = 0.0  # largest (probe_size_usd / book_dollar_value) seen

    stream_manager = getattr(quote_fetcher, "stream_manager", None)

    while True:
        elapsed = time.time() - start
        if elapsed >= max_wait_s:
            break

        # Try to read distinct quotes from the WebSocket cache history first.
        # That gives us true distinct ticks (not the same cached value polled
        # repeatedly). Fall back to the latest NBBO snapshot if cache is empty.
        candidates = []
        if stream_manager is not None:
            try:
                recent = await stream_manager.get_recent_quotes(ticker, max_quotes=20)
                # recent is oldest→newest with .timestamp; pick anything newer
                # than last_seen_ts.
                for q in recent:
                    ts = q.get("timestamp")
                    if last_seen_ts is None or (ts is not None and ts > last_seen_ts):
                        candidates.append(q)
            except Exception as e:
                logger.debug(f"depth probe: stream cache error for {ticker}: {e}")

        if not candidates:
            try:
                snap = await quote_fetcher.get_nbbo_snapshot(ticker)
            except Exception as e:
                logger.debug(f"depth probe: NBBO fetch error for {ticker}: {e}")
                snap = None
            if snap:
                # Treat as a single new observation (no timestamp dedup).
                candidates = [snap]

        for q in candidates:
            ts = q.get("timestamp")
            if ts is not None and last_seen_ts is not None and ts <= last_seen_ts:
                continue
            last_seen_ts = ts if ts is not None else last_seen_ts
            last_nbbo = q

            ask = q.get("ask")
            ask_size = q.get("ask_size")
            quotes_observed += 1
            if not (ask and ask > 0 and ask_size and ask_size > 0):
                consecutive = 0
                continue

            book_usd = ask * ask_size
            # Pass condition: $5K notional is < gate_ratio of book
            # ⇔ probe_size_usd / book_usd < gate_ratio
            # ⇔ book_usd > probe_size_usd / gate_ratio
            ratio = probe_size_usd / book_usd if book_usd > 0 else float("inf")
            deepest_ratio = max(deepest_ratio, 1.0 / ratio if ratio > 0 else 0.0)
            is_deep = ratio < gate_ratio

            if is_deep:
                deep_observed += 1
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
                if consecutive >= min_consecutive:
                    telemetry = {
                        "depth_probe_passed": True,
                        "depth_probe_elapsed_s": round(time.time() - start, 3),
                        "depth_probe_quotes_observed": quotes_observed,
                        "depth_probe_deep_observed": deep_observed,
                        "depth_probe_max_consecutive": max_consecutive,
                        "depth_probe_size_usd": probe_size_usd,
                        "depth_probe_min_consecutive": min_consecutive,
                        "depth_probe_final_ask": ask,
                        "depth_probe_final_ask_size": ask_size,
                    }
                    return True, last_nbbo, telemetry
            else:
                consecutive = 0

        await asyncio.sleep(DEPTH_PROBE_POLL_INTERVAL_S)

    telemetry = {
        "depth_probe_passed": False,
        "depth_probe_elapsed_s": round(time.time() - start, 3),
        "depth_probe_quotes_observed": quotes_observed,
        "depth_probe_deep_observed": deep_observed,
        "depth_probe_max_consecutive": max_consecutive,
        "depth_probe_size_usd": probe_size_usd,
        "depth_probe_min_consecutive": min_consecutive,
        "depth_probe_final_ask": last_nbbo.get("ask") if last_nbbo else None,
        "depth_probe_final_ask_size": last_nbbo.get("ask_size") if last_nbbo else None,
    }
    return False, last_nbbo, telemetry


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
