"""
Trade executor for extended hours trading (stocks only, ladder strategy) - Alpaca implementation.
Pure infrastructure - executes trades and publishes events.
"""
import asyncio
import time
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest
from ...shared.event_bus import AsyncEventBus
from .events import TradeExecutedEvent, TradeFailedEvent
from .event_builders import build_infrastructure_trade_request_data
from .quote_fetcher import AlpacaQuoteFetcher
from .utils import (
    calculate_trade_quantity,
)
from ...utils.brokerage.ladder_algorithms import (
    calculate_ladder_base_price,
    calculate_ladder_parameters,
    calculate_limit_price,
    should_switch_to_late_step,
)
from ..notification.fast_trade_notifier import FastTradeNotifier

logger = get_logger(__name__)


# ============================================================
# EXIT RECOVERY — a SELL must NEVER be abandoned (TGL 2026-06-11)
# ============================================================
# The old chase-the-bid path ABORTED when the bid fell >5% below the initial
# bid, leaving the position live and untracked (PositionManager had already
# deregistered it). TGL's resting limit happened to fill on a bounce, but no
# TradeExecuted event ever fired — no exit stats, no telegram.
# Recovery instead: Phase A rests a limit at the floor waiting for a bounce
# (keeps the floor's don't-sell-the-flash-crash intent), then Phase B
# capitulates and chases the bid with NO floor until flat. Every fill
# publishes TradeExecuted so the exit message always arrives.
EXIT_RECOVERY_RESTING_SECONDS = 120   # Phase A: resting limit at the floor
EXIT_RECOVERY_POLL_SECONDS = 2.0      # Phase A poll interval
EXIT_CAPITULATION_MAX_ATTEMPTS = 30   # Phase B: chase bid, ~1s per attempt


class AlpacaExtendedHoursTradeExecutor:
    """
    Executes stock trades during extended hours.

    Entry (BUY) Strategy - Mid-then-Chase-the-Ask:
    - Attempt 1: place limit at mid price for a better fill (500ms)
    - If not filled in 500ms, switch to ask and chase every 500ms
    - Repeat up to 10 times (5 seconds total)
    - Abort if price exceeds 5% above initial ask (price collar)
    - Mid-first saves half the spread on calm fills while ask-chase catches runners

    Exit (SELL) Strategy - Chase-the-Bid, then guaranteed recovery:
    - Place limit order at current bid (no discount)
    - If not filled in 500ms, re-check NBBO and place at new bid
    - Repeat up to 10 times (5 seconds total)
    - If bid falls below 5% floor OR attempts exhaust: NEVER abandon —
      rest a limit at the floor for a bounce, then capitulate at the bid
      until flat (see _execute_exit_recovery)

    Responsibilities:
    - Execute limit orders (stocks only)
    - Handle trade execution
    - Publish trade events

    Does NOT:
    - Know about business logic
    - Send Telegram notifications
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        quote_fetcher: AlpacaQuoteFetcher,
        trading_client: TradingClient,
        fast_notifier: Optional[FastTradeNotifier] = None,
    ):
        """
        Initialize extended hours trade executor.

        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            quote_fetcher: Quote fetcher instance for getting prices/NBBO
            trading_client: Alpaca TradingClient instance
            fast_notifier: Optional fast trade notifier for immediate Telegram notifications
        """
        self.quote_fetcher = quote_fetcher
        self.event_bus = event_bus
        self.trading_client = trading_client
        self.fast_notifier = fast_notifier

        logger.info(
            "AlpacaExtendedHoursTradeExecutor initialized",
            fast_notifier_enabled=fast_notifier is not None
        )
    
    async def execute(
        self,
        trade_request: TradeRequest,
        session: str,
        timing_info: Dict[str, float],
        timeout_deadline: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a stock trade during extended hours using ladder strategy.

        Args:
            trade_request: Trade request
            session: Trading session name (premarket/postmarket)
            timing_info: Timing information dictionary
            timeout_deadline: Optional timeout deadline
            metadata: Optional metadata (exit_reason, tier, etc.) for notifications

        Returns:
            Trade result dictionary (for backward compatibility, also publishes events)
        """
        # Store metadata locally for event publishing (avoid race condition on self._current_metadata)
        self._current_metadata = metadata
        _local_metadata = metadata
        total_start_time = time.time()
        session_time = timing_info.get("session_detection", 0.0)
        connect_time = timing_info.get("connection", 0.0)
        capital_required: Optional[float] = None
        price_fallback_used = False
        current_order_id: Optional[str] = None  # Track current order for cancellation (accessible in exception handlers)
        
        try:
            def time_left() -> Optional[float]:
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()
            
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before execution started")
            
            action = trade_request.action.upper()
            
            # Get NBBO snapshot
            nbbo_start = time.time()
            nbbo_snapshot = await self.quote_fetcher.get_nbbo_snapshot(trade_request.ticker)
            nbbo_time = time.time() - nbbo_start
            
            if not nbbo_snapshot:
                error_result = {
                    "success": False,
                    "error": "Could not retrieve NBBO snapshot for extended hours trade",
                    "session": session,
                    "order_type": "LADDER_LIMIT",
                    "instrument": "stock",
                }
                await self._publish_failed_event(trade_request, error_result["error"])
                return error_result
            
            # NOTE: Spread filter removed per SIGNAL_OPTIMIZATION_PLAN.md
            # Wide spreads often compress rapidly on runners, and hard spread limits
            # block good trades. AI + microstructure confluence will handle filtering instead.

            # Get current price
            current_price = await self.quote_fetcher.get_realtime_price(trade_request.ticker)
            price_fallback_used = False
            leverage = getattr(trade_request, "leverage", None)

            # Handle price fallback
            if not current_price:
                fallback_price = None
                # Only use amount_usd fallback if no leverage (leverage uses price of 1 share)
                if trade_request.shares and trade_request.amount_usd and not leverage:
                    fallback_price = trade_request.amount_usd / max(trade_request.shares, 1)
                
                if fallback_price and fallback_price > 0:
                    logger.warning(
                        "⚠️ Falling back to estimated price for extended-hours trade",
                        ticker=trade_request.ticker,
                        fallback_price=fallback_price,
                    )
                    current_price = fallback_price
                    price_fallback_used = True
            
            if not current_price:
                error_result = {
                    "success": False,
                    "error": "Could not retrieve price for extended hours trade",
                    "session": session,
                    "order_type": "LADDER_LIMIT",
                    "instrument": "stock",
                }
                await self._publish_failed_event(trade_request, error_result["error"])
                return error_result
            
            # Calculate quantity
            quantity, capital_required = calculate_trade_quantity(
                trade_request, current_price, leverage or 1.0
            )
            
            trading_start = time.time()
            
            # ENTRY LOGIC: Chase-the-ask approach for BUY orders
            # Strategy: Place at ask, chase every 500ms if not filled, up to 5% price collar
            # This saves premium on normal fills while still catching runners
            if action == "BUY":
                initial_ask = nbbo_snapshot.get("ask")
                if not initial_ask or initial_ask <= 0:
                    error_result = {
                        "success": False,
                        "error": "Could not retrieve ask price for entry trade",
                        "session": session,
                        "order_type": "LIMIT",
                        "instrument": "stock",
                    }
                    await self._publish_failed_event(trade_request, error_result["error"])
                    return error_result

                # =============================================================
                # 🛡️ EXECUTION-TIME SLIPPAGE CHECK: Decision ask vs execution ask
                # =============================================================
                # The postfilters validated the ask at DECISION time, but by the
                # time we reach the executor, the ask may have spiked (race condition).
                # FBGL lesson: ask spiked $0.879 → $0.9683 (10.16%) between
                # postfilter check and execution → instant -10.78% loss.
                #
                # Rule: If slippage from decision ask exceeds the stop loss, abort.
                # Mathematically impossible to enter past your own stop.
                # Regular: 5% (stop = 5%)  |  Mega: 7.5% (stop = 7.5%)  |  High-conviction: 12% (stop = 12%)
                # Plus $0.05 absolute floor (penny stock protection).
                decision_ask = metadata.get("initial_ask") if metadata else None
                is_mega = metadata.get("is_mega_trade", False) if metadata else False
                is_high_conviction = metadata.get("is_high_conviction", False) if metadata else False
                is_clinical_breakthrough = metadata.get("is_clinical_breakthrough", False) if metadata else False

                if decision_ask and decision_ask > 0:
                    execution_slippage_pct = ((initial_ask - decision_ask) / decision_ask) * 100
                    max_execution_slippage = 12.0 if (is_high_conviction or is_clinical_breakthrough) else (7.5 if is_mega else 5.0)
                    min_absolute_slippage = 0.05  # $0.05 floor
                    absolute_slippage = abs(initial_ask - decision_ask)

                    if execution_slippage_pct > max_execution_slippage and absolute_slippage >= min_absolute_slippage:
                        error_msg = (
                            f"Execution slippage {execution_slippage_pct:.1f}% exceeds "
                            f"{max_execution_slippage}% max (ask moved ${decision_ask:.4f} → ${initial_ask:.4f})"
                        )
                        logger.warning(
                            "🛡️ TRADE ABORTED: Execution slippage exceeds stop loss threshold",
                            ticker=trade_request.ticker,
                            decision_ask=round(decision_ask, 4),
                            execution_ask=round(initial_ask, 4),
                            slippage_pct=round(execution_slippage_pct, 2),
                            max_allowed_pct=max_execution_slippage,
                            absolute_slippage=round(absolute_slippage, 4),
                            is_mega_trade=is_mega,
                        )
                        error_result = {
                            "success": False,
                            "error": error_msg,
                            "session": session,
                            "order_type": "LIMIT",
                            "instrument": "stock",
                        }
                        await self._publish_failed_event(trade_request, error_result["error"])
                        return error_result

                    if execution_slippage_pct > 1.0:
                        logger.info(
                            "⚠️ EXECUTION SLIPPAGE NOTED (within tolerance)",
                            ticker=trade_request.ticker,
                            decision_ask=round(decision_ask, 4),
                            execution_ask=round(initial_ask, 4),
                            slippage_pct=round(execution_slippage_pct, 2),
                            max_allowed_pct=max_execution_slippage,
                        )

                # ACTIVITY GATE REMOVED (2026-06-09): it read an empty realtime
                # stream cache for low-priced micro-caps (qi=0/tps=0 for every name
                # on 2026-06-08, incl. NCRA +86% and ABAT +37%), so it blocked the
                # very winners it was meant to confirm. STRENGTH/SURGE/LATE already
                # gate on real activity upstream; the 5% spread cap handles liquidity.

                # Price collar: Maximum we're willing to pay (10% above initial ask for extended chase)
                # This prevents chasing into pump-and-dumps while allowing runners
                MAX_SLIPPAGE_PCT = 0.10  # 10% max slippage for extended chase
                max_price = round(initial_ask * (1 + MAX_SLIPPAGE_PCT), 2)

                # =================================================================
                # TWO-PHASE ENTRY STRATEGY
                # =================================================================
                # Phase 1: Quick chase (5 seconds) - retry every 500ms
                #   - If filled: done
                #   - If not filled: check spread, move to Phase 2 if tight
                #
                # Phase 2: Patient mode (up to 2 minutes total) - retry every 3s
                #   - Only if spread < 5% (tight = liquid eventually)
                #   - Wait for volume to arrive on quiet names like RITR
                #   - This catches transformational headlines that start slow
                # =================================================================

                # Phase 1: Quick chase
                PHASE1_INTERVAL_MS = 500  # Check every 500ms
                PHASE1_ATTEMPTS = 10  # 10 attempts = 5 seconds

                # Phase 2: Patient mode (only if Phase 1 fails and spread tight)
                PHASE2_INTERVAL_MS = 3000  # Check every 3 seconds
                PHASE2_MAX_DURATION_S = 120  # Up to 2 minutes total
                PHASE2_SPREAD_THRESHOLD_PCT = 0.05  # Must have <5% spread to continue

                chase_attempts = []
                phase2_active = False
                entry_start_time = time.time()

                logger.info(
                    f"💰 ENTRY ORDER: Starting two-phase chase strategy",
                    ticker=trade_request.ticker,
                    initial_ask=initial_ask,
                    max_price=max_price,
                    max_slippage_pct=f"{MAX_SLIPPAGE_PCT*100:.0f}%",
                    quantity=quantity,
                    phase1_duration="5s",
                    phase2_duration="up to 2min if spread <5%",
                )

                attempt = 0
                while True:
                    attempt += 1
                    elapsed = time.time() - entry_start_time

                    # Determine which phase we're in
                    if attempt <= PHASE1_ATTEMPTS:
                        # Phase 1: Quick chase
                        interval_ms = PHASE1_INTERVAL_MS
                        phase_name = "Phase1"
                    else:
                        # Phase 2: Patient mode
                        if elapsed > PHASE2_MAX_DURATION_S:
                            logger.warning(
                                f"🛑 ENTRY TIMEOUT: Exceeded {PHASE2_MAX_DURATION_S}s without fill",
                                ticker=trade_request.ticker,
                                attempts_made=attempt - 1,
                                elapsed_seconds=round(elapsed, 1),
                            )
                            error_result = {
                                "success": False,
                                "error": f"Entry timed out after {round(elapsed)}s ({attempt-1} attempts)",
                                "session": session,
                                "order_type": "CHASE_LIMIT",
                                "instrument": "stock",
                                "chase_attempts": chase_attempts,
                            }
                            if current_order_id:
                                await self._cancel_order_safely(current_order_id)
                            await self._publish_failed_event(trade_request, error_result["error"], error_result)
                            return error_result

                        interval_ms = PHASE2_INTERVAL_MS
                        phase_name = "Phase2"

                        # First time entering Phase 2 - check spread requirement
                        if not phase2_active:
                            fresh_nbbo = await self.quote_fetcher.get_nbbo_snapshot(trade_request.ticker)
                            if fresh_nbbo:
                                spread = fresh_nbbo.get("spread", 0)
                                ask = fresh_nbbo.get("ask", initial_ask)
                                spread_pct = spread / ask if ask > 0 else 1.0

                                if spread_pct >= PHASE2_SPREAD_THRESHOLD_PCT:
                                    logger.warning(
                                        f"🛑 ENTRY ABORTED: Spread too wide for extended chase",
                                        ticker=trade_request.ticker,
                                        spread_pct=f"{spread_pct*100:.1f}%",
                                        threshold=f"{PHASE2_SPREAD_THRESHOLD_PCT*100:.0f}%",
                                        attempts_made=attempt - 1,
                                    )
                                    error_result = {
                                        "success": False,
                                        "error": f"Spread {spread_pct*100:.1f}% > {PHASE2_SPREAD_THRESHOLD_PCT*100:.0f}% threshold for extended chase",
                                        "session": session,
                                        "order_type": "CHASE_LIMIT",
                                        "instrument": "stock",
                                        "chase_attempts": chase_attempts,
                                    }
                                    if current_order_id:
                                        await self._cancel_order_safely(current_order_id)
                                    await self._publish_failed_event(trade_request, error_result["error"], error_result)
                                    return error_result

                                phase2_active = True
                                logger.info(
                                    f"📊 PHASE 2: Entering patient mode (spread {spread_pct*100:.1f}% < 5%)",
                                    ticker=trade_request.ticker,
                                    remaining_time=f"{PHASE2_MAX_DURATION_S - elapsed:.0f}s",
                                )
                    # Get fresh NBBO for each attempt (except first which uses initial)
                    if attempt > 1:
                        fresh_nbbo = await self.quote_fetcher.get_nbbo_snapshot(trade_request.ticker)
                        if fresh_nbbo:
                            current_ask = fresh_nbbo.get("ask")
                            nbbo_snapshot = fresh_nbbo  # Update for result reporting
                        else:
                            current_ask = None
                    else:
                        current_ask = initial_ask

                    if not current_ask or current_ask <= 0:
                        logger.warning(
                            f"Chase attempt {attempt}: Could not get ask price, skipping",
                            ticker=trade_request.ticker
                        )
                        await asyncio.sleep(interval_ms / 1000)
                        continue

                    # Check price collar - abort if ask exceeds max acceptable price
                    if current_ask > max_price:
                        logger.warning(
                            f"🛑 ENTRY ABORTED: Price exceeded {MAX_SLIPPAGE_PCT*100:.0f}% collar",
                            ticker=trade_request.ticker,
                            current_ask=current_ask,
                            max_price=max_price,
                            initial_ask=initial_ask,
                            slippage_pct=f"{((current_ask - initial_ask) / initial_ask * 100):.1f}%",
                            attempts_made=attempt - 1,
                        )
                        error_result = {
                            "success": False,
                            "error": f"Price exceeded {MAX_SLIPPAGE_PCT*100:.0f}% collar (ask ${current_ask} > max ${max_price})",
                            "session": session,
                            "order_type": "CHASE_LIMIT",
                            "instrument": "stock",
                            "initial_ask": initial_ask,
                            "final_ask": current_ask,
                            "max_price": max_price,
                            "chase_attempts": chase_attempts,
                            "nbbo": nbbo_snapshot,
                        }
                        if current_order_id:
                            await self._cancel_order_safely(current_order_id)
                        await self._publish_failed_event(trade_request, error_result["error"], error_result)
                        return error_result

                    # Attempt 1: try mid for a better fill, then chase ask from attempt 2+
                    if attempt == 1:
                        initial_mid = nbbo_snapshot.get("mid")
                        if initial_mid and initial_mid > 0:
                            limit_price = round(initial_mid, 2)
                        else:
                            limit_price = round(current_ask, 2)
                    else:
                        limit_price = round(current_ask, 2)

                    price_target = "mid" if attempt == 1 and limit_price != round(current_ask, 2) else "ask"
                    logger.info(
                        f"📈 {phase_name} attempt {attempt}: Placing limit at {price_target}",
                        ticker=trade_request.ticker,
                        limit_price=limit_price,
                        initial_ask=initial_ask,
                        elapsed_seconds=round(elapsed, 1),
                        slippage_so_far=f"{((limit_price - initial_ask) / initial_ask * 100):.2f}%" if limit_price > initial_ask else "0%",
                    )

                    # PARALLEL ORDER SUBMISSION: Minimize gap when replacing orders
                    # Old approach: cancel → wait → submit (200-500ms gap with no order on book)
                    # New approach: start cancel → minimal wait → submit (reduces gap to ~50ms)
                    order_data = LimitOrderRequest(
                        symbol=trade_request.ticker,
                        qty=quantity,
                        side=OrderSide.BUY,
                        limit_price=limit_price,
                        time_in_force=TimeInForce.DAY,
                        extended_hours=True
                    )

                    if current_order_id:
                        # Start cancel in background (don't await full completion)
                        cancel_task = asyncio.create_task(self._cancel_order_fire_and_forget(current_order_id))
                        # Brief wait for cancel to propagate to exchange (~50ms usually enough)
                        await asyncio.sleep(0.05)
                        current_order_id = None

                        try:
                            order = self.trading_client.submit_order(order_data=order_data)
                            current_order_id = order.id
                        except Exception as order_error:
                            # Likely buying power still tied up - wait for cancel to complete and retry
                            if "buying power" in str(order_error).lower() or "insufficient" in str(order_error).lower():
                                await cancel_task  # Ensure cancel completes
                                await asyncio.sleep(0.1)  # Extra buffer for buying power release
                                try:
                                    order = self.trading_client.submit_order(order_data=order_data)
                                    current_order_id = order.id
                                except Exception as retry_error:
                                    logger.error(
                                        f"Chase attempt {attempt}: Order submission failed after cancel",
                                        ticker=trade_request.ticker,
                                        error=str(retry_error)
                                    )
                                    chase_attempts.append({
                                        "attempt": attempt,
                                        "limit_price": limit_price,
                                        "result": "submission_failed",
                                        "error": str(retry_error)
                                    })
                                    await asyncio.sleep(interval_ms / 1000)
                                    continue
                            else:
                                logger.error(
                                    f"Chase attempt {attempt}: Order submission failed",
                                    ticker=trade_request.ticker,
                                    error=str(order_error)
                                )
                                chase_attempts.append({
                                    "attempt": attempt,
                                    "limit_price": limit_price,
                                    "result": "submission_failed",
                                    "error": str(order_error)
                                })
                                await asyncio.sleep(interval_ms / 1000)
                                continue
                    else:
                        # First attempt - no previous order to cancel
                        try:
                            order = self.trading_client.submit_order(order_data=order_data)
                            current_order_id = order.id
                        except Exception as order_error:
                            logger.error(
                                f"Chase attempt {attempt}: Order submission failed",
                                ticker=trade_request.ticker,
                                error=str(order_error)
                            )
                            chase_attempts.append({
                                "attempt": attempt,
                                "limit_price": limit_price,
                                "result": "submission_failed",
                                "error": str(order_error)
                            })
                            await asyncio.sleep(interval_ms / 1000)
                            continue

                    # Wait for fill (500ms)
                    attempt_start = time.time()
                    await asyncio.sleep(interval_ms / 1000)

                    # Check if filled
                    try:
                        order_status = self.trading_client.get_order_by_id(order.id)

                        if order_status.status == "filled":
                            # SUCCESS - Order filled
                            # CRITICAL: Always use actual fill price from Alpaca.
                            # Never fall back to limit_price — wrong fill price =
                            # wrong stop loss (SXTP bug: limit $2.17, fill $2.07,
                            # stop calculated from $2.17 instead of $2.07).
                            fill_price = await self._get_fill_price(order.id, order_status, limit_price, trade_request.ticker)
                            filled_shares = float(order_status.filled_qty) if order_status.filled_qty else quantity
                            total_trading_time = time.time() - trading_start
                            total_time = time.time() - total_start_time

                            chase_attempts.append({
                                "attempt": attempt,
                                "limit_price": limit_price,
                                "result": "filled",
                                "fill_price": fill_price,
                            })

                            logger.info(
                                f"✅ ENTRY FILLED on attempt {attempt}",
                                ticker=trade_request.ticker,
                                fill_price=fill_price,
                                shares=filled_shares,
                                initial_ask=initial_ask,
                                slippage=f"{((fill_price - initial_ask) / initial_ask * 100):.2f}%" if fill_price > initial_ask else "0%",
                            )

                            total_cost = fill_price * filled_shares if fill_price and filled_shares else None
                            commission = 0.0

                            result = {
                                "success": True,
                                "shares": filled_shares,
                                "fill_price": fill_price,
                                "total_cost": total_cost,
                                "commission": commission,
                                "session": session,
                                "order_type": "CHASE_LIMIT",
                                "instrument": "stock",
                                "limit_price_used": limit_price,
                                "timing_info": {
                                    "session_detection": session_time,
                                    "connection": connect_time,
                                    "nbbo_retrieval": nbbo_time,
                                    "trading_time": total_trading_time,
                                    "total": total_time,
                                    "chase_attempts": attempt,
                                },
                                "instrument_details": {
                                    "leverage": leverage,
                                    "capital_required": capital_required,
                                    "price_fallback_used": price_fallback_used,
                                    "nbbo": nbbo_snapshot,
                                    "initial_ask": initial_ask,
                                    "max_price": max_price,
                                    "chase_attempts_detail": chase_attempts,
                                },
                            }

                            current_order_id = None  # Clear tracking
                            await self._publish_executed_event(trade_request, result, metadata=_local_metadata)
                            return result

                        elif order_status.status in ["rejected", "canceled", "expired"]:
                            reject_reason = getattr(order_status, 'reject_reason', 'Unknown')
                            chase_attempts.append({
                                "attempt": attempt,
                                "limit_price": limit_price,
                                "result": order_status.status,
                                "reason": reject_reason,
                            })
                            logger.warning(
                                f"Chase attempt {attempt}: Order {order_status.status}",
                                ticker=trade_request.ticker,
                                reason=reject_reason
                            )
                            current_order_id = None
                            # Continue to next attempt
                        else:
                            # Order still open (pending/new) - will cancel and retry
                            chase_attempts.append({
                                "attempt": attempt,
                                "limit_price": limit_price,
                                "result": "not_filled",
                                "status": order_status.status,
                            })

                    except Exception as status_error:
                        logger.warning(
                            f"Chase attempt {attempt}: Could not check order status",
                            ticker=trade_request.ticker,
                            error=str(status_error)
                        )
                        chase_attempts.append({
                            "attempt": attempt,
                            "limit_price": limit_price,
                            "result": "status_check_failed",
                            "error": str(status_error),
                        })

            # EXIT LOGIC: Chase-the-Bid approach for SELL orders
            # Strategy: Place limit order at current bid (no discount)
            # If not filled in 500ms, re-check NBBO and place at new bid
            # Repeat up to 10 times (5 seconds total)
            # This saves money by not giving away unnecessary discounts
            # Price floor: Won't go below 5% under initial bid (prevents selling into a crash)
            if action == "SELL":
                # CRITICAL FIX: Cancel any existing open orders for this ticker before placing new sell
                # This prevents "insufficient qty available" errors when shares are held by pending orders
                await self._cancel_all_open_orders_for_ticker(trade_request.ticker)

                initial_bid = nbbo_snapshot.get("bid")
                if not initial_bid or initial_bid <= 0:
                    error_result = {
                        "success": False,
                        "error": "Could not retrieve bid price for exit trade",
                        "session": session,
                        "order_type": "CHASE_LIMIT",
                        "instrument": "stock",
                    }
                    await self._publish_failed_event(trade_request, error_result["error"])
                    return error_result

                # Price floor: Minimum we're willing to accept (5% below initial bid)
                # This prevents chasing down into a crash
                MAX_SLIPPAGE_PCT = 0.05  # 5% max slippage from decision point
                min_price = round(initial_bid * (1 - MAX_SLIPPAGE_PCT), 2)

                # Chase parameters (same as entry)
                CHASE_INTERVAL_MS = 500  # Check every 500ms
                MAX_CHASE_ATTEMPTS = 10  # 10 attempts = 5 seconds total
                chase_attempts = []

                exit_reason = metadata.get("exit_reason", "") if metadata else ""

                logger.info(
                    f"💰 EXIT ORDER: Starting chase-the-bid strategy",
                    ticker=trade_request.ticker,
                    exit_reason=exit_reason,
                    initial_bid=initial_bid,
                    min_price=min_price,
                    max_slippage_pct=f"{MAX_SLIPPAGE_PCT*100:.0f}%",
                    quantity=quantity,
                    max_attempts=MAX_CHASE_ATTEMPTS,
                )

                for attempt in range(1, MAX_CHASE_ATTEMPTS + 1):
                    # Get fresh NBBO for each attempt (except first which uses initial)
                    if attempt > 1:
                        fresh_nbbo = await self.quote_fetcher.get_nbbo_snapshot(trade_request.ticker)
                        if fresh_nbbo:
                            current_bid = fresh_nbbo.get("bid")
                            nbbo_snapshot = fresh_nbbo  # Update for result reporting
                        else:
                            current_bid = None
                    else:
                        current_bid = initial_bid

                    if not current_bid or current_bid <= 0:
                        logger.warning(
                            f"Chase attempt {attempt}: Could not get bid price, skipping",
                            ticker=trade_request.ticker
                        )
                        await asyncio.sleep(CHASE_INTERVAL_MS / 1000)
                        continue

                    # Price floor breached — stop chasing DOWN, but never abandon the
                    # exit (the old abort here orphaned TGL's live position, 2026-06-11).
                    if current_bid < min_price:
                        logger.warning(
                            f"🛟 EXIT FLOOR BREACHED: Entering guaranteed-exit recovery",
                            ticker=trade_request.ticker,
                            current_bid=current_bid,
                            min_price=min_price,
                            initial_bid=initial_bid,
                            slippage_pct=f"{((initial_bid - current_bid) / initial_bid * 100):.1f}%",
                            attempts_made=attempt - 1,
                        )
                        return await self._execute_exit_recovery(
                            trade_request=trade_request,
                            quantity=quantity,
                            min_price=min_price,
                            initial_bid=initial_bid,
                            current_order_id=current_order_id,
                            nbbo_snapshot=nbbo_snapshot,
                            session=session,
                            chase_attempts=chase_attempts,
                            metadata=_local_metadata,
                            trigger="floor_breach",
                        )

                    # Place limit order at current bid (no discount - save money)
                    limit_price = round(current_bid, 2)

                    logger.info(
                        f"📉 Chase attempt {attempt}/{MAX_CHASE_ATTEMPTS}: Placing limit at bid",
                        ticker=trade_request.ticker,
                        limit_price=limit_price,
                        initial_bid=initial_bid,
                        slippage_so_far=f"{((initial_bid - limit_price) / initial_bid * 100):.2f}%" if limit_price < initial_bid else "0%",
                    )

                    # PARALLEL ORDER SUBMISSION: Minimize gap when replacing orders
                    order_data = LimitOrderRequest(
                        symbol=trade_request.ticker,
                        qty=quantity,
                        side=OrderSide.SELL,
                        limit_price=limit_price,
                        time_in_force=TimeInForce.DAY,
                        extended_hours=True
                    )

                    if current_order_id:
                        # Start cancel in background (don't await full completion)
                        cancel_task = asyncio.create_task(self._cancel_order_fire_and_forget(current_order_id))
                        # Brief wait for cancel to propagate (~50ms)
                        await asyncio.sleep(0.05)
                        current_order_id = None

                        try:
                            order = self.trading_client.submit_order(order_data=order_data)
                            current_order_id = order.id
                        except Exception as order_error:
                            # Likely shares still held by previous order - wait for cancel and retry
                            if "insufficient" in str(order_error).lower() or "qty" in str(order_error).lower():
                                await cancel_task  # Ensure cancel completes
                                await asyncio.sleep(0.1)  # Extra buffer for shares release
                                try:
                                    order = self.trading_client.submit_order(order_data=order_data)
                                    current_order_id = order.id
                                except Exception as retry_error:
                                    logger.error(
                                        f"Chase attempt {attempt}: Order submission failed after cancel",
                                        ticker=trade_request.ticker,
                                        error=str(retry_error)
                                    )
                                    chase_attempts.append({
                                        "attempt": attempt,
                                        "limit_price": limit_price,
                                        "result": "submission_failed",
                                        "error": str(retry_error)
                                    })
                                    await asyncio.sleep(CHASE_INTERVAL_MS / 1000)
                                    continue
                            else:
                                logger.error(
                                    f"Chase attempt {attempt}: Order submission failed",
                                    ticker=trade_request.ticker,
                                    error=str(order_error)
                                )
                                chase_attempts.append({
                                    "attempt": attempt,
                                    "limit_price": limit_price,
                                    "result": "submission_failed",
                                    "error": str(order_error)
                                })
                                await asyncio.sleep(CHASE_INTERVAL_MS / 1000)
                                continue
                    else:
                        # First attempt - no previous order to cancel
                        try:
                            order = self.trading_client.submit_order(order_data=order_data)
                            current_order_id = order.id
                        except Exception as order_error:
                            logger.error(
                                f"Chase attempt {attempt}: Order submission failed",
                                ticker=trade_request.ticker,
                                error=str(order_error)
                            )
                            chase_attempts.append({
                                "attempt": attempt,
                                "limit_price": limit_price,
                                "result": "submission_failed",
                                "error": str(order_error)
                            })
                            await asyncio.sleep(CHASE_INTERVAL_MS / 1000)
                            continue

                    # Wait for fill (500ms)
                    await asyncio.sleep(CHASE_INTERVAL_MS / 1000)

                    # Check if filled
                    try:
                        order_status = self.trading_client.get_order_by_id(order.id)

                        if order_status.status == "filled":
                            # SUCCESS - Order filled
                            fill_price = await self._get_fill_price(order.id, order_status, limit_price, trade_request.ticker)
                            filled_shares = float(order_status.filled_qty) if order_status.filled_qty else quantity
                            total_trading_time = time.time() - trading_start
                            total_time = time.time() - total_start_time

                            chase_attempts.append({
                                "attempt": attempt,
                                "limit_price": limit_price,
                                "result": "filled",
                                "fill_price": fill_price,
                            })

                            logger.info(
                                f"✅ EXIT FILLED on attempt {attempt}",
                                ticker=trade_request.ticker,
                                fill_price=fill_price,
                                shares=filled_shares,
                                initial_bid=initial_bid,
                                slippage=f"{((initial_bid - fill_price) / initial_bid * 100):.2f}%" if fill_price < initial_bid else "0%",
                            )

                            total_cost = fill_price * filled_shares if fill_price and filled_shares else None
                            commission = 0.0

                            result = {
                                "success": True,
                                "shares": filled_shares,
                                "fill_price": fill_price,
                                "total_cost": total_cost,
                                "commission": commission,
                                "session": session,
                                "order_type": "CHASE_LIMIT",
                                "instrument": "stock",
                                "limit_price_used": limit_price,
                                "timing_info": {
                                    "session_detection": session_time,
                                    "connection": connect_time,
                                    "nbbo_retrieval": nbbo_time,
                                    "trading_time": total_trading_time,
                                    "total": total_time,
                                    "chase_attempts": attempt,
                                },
                                "instrument_details": {
                                    "leverage": leverage,
                                    "capital_required": capital_required,
                                    "price_fallback_used": price_fallback_used,
                                    "nbbo": nbbo_snapshot,
                                    "initial_bid": initial_bid,
                                    "min_price": min_price,
                                    "chase_attempts_detail": chase_attempts,
                                },
                            }

                            current_order_id = None  # Clear tracking
                            await self._publish_executed_event(trade_request, result, metadata=_local_metadata)
                            return result

                        elif order_status.status in ["rejected", "canceled", "expired"]:
                            reject_reason = getattr(order_status, 'reject_reason', 'Unknown')
                            chase_attempts.append({
                                "attempt": attempt,
                                "limit_price": limit_price,
                                "result": order_status.status,
                                "reason": reject_reason,
                            })
                            logger.warning(
                                f"Chase attempt {attempt}: Order {order_status.status}",
                                ticker=trade_request.ticker,
                                reason=reject_reason
                            )
                            current_order_id = None
                            # Continue to next attempt
                        else:
                            # Order still open (pending/new) - will cancel and retry
                            chase_attempts.append({
                                "attempt": attempt,
                                "limit_price": limit_price,
                                "result": "not_filled",
                                "status": order_status.status,
                            })

                    except Exception as status_error:
                        logger.warning(
                            f"Chase attempt {attempt}: Could not check order status",
                            ticker=trade_request.ticker,
                            error=str(status_error)
                        )
                        chase_attempts.append({
                            "attempt": attempt,
                            "limit_price": limit_price,
                            "result": "status_check_failed",
                            "error": str(status_error),
                        })

                # All attempts exhausted — never abandon the exit, hand off to recovery
                final_bid = nbbo_snapshot.get("bid") if nbbo_snapshot else None
                logger.warning(
                    f"🛟 EXIT CHASE EXHAUSTED: All {MAX_CHASE_ATTEMPTS} attempts — entering guaranteed-exit recovery",
                    ticker=trade_request.ticker,
                    initial_bid=initial_bid,
                    final_bid=final_bid,
                    min_price=min_price,
                    chase_attempts=chase_attempts,
                )
                return await self._execute_exit_recovery(
                    trade_request=trade_request,
                    quantity=quantity,
                    min_price=min_price,
                    initial_bid=initial_bid,
                    current_order_id=current_order_id,
                    nbbo_snapshot=nbbo_snapshot,
                    session=session,
                    chase_attempts=chase_attempts,
                    metadata=_local_metadata,
                    trigger="chase_exhausted",
                )
            
            # Legacy ladder logic (should not be reached for SELL, but kept for safety)
            # Calculate ladder base price (start at midprice for better fills)
            base_price = calculate_ladder_base_price(
                action,
                nbbo_snapshot.get("ask"),
                nbbo_snapshot.get("bid"),
                current_price,
                mid=nbbo_snapshot.get("mid"),
            )
            
            # Get ladder parameters for exits
            initial_cents, early_step, late_step, switch_after, interval_early, interval_late, max_cents_from_start = calculate_ladder_parameters(action)
            
            current_cents = initial_cents
            attempt_number = 0
            wait_time = interval_early
            
            # Track all ladder attempts for detailed statistics (exits only)
            ladder_attempts: list[Dict[str, Any]] = []
            
            # Ladder loop (should not be reached for SELL, but kept for safety)
            while abs(current_cents) <= abs(max_cents_from_start):
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    # Cancel any pending order before timeout
                    if current_order_id:
                        await self._cancel_order_safely(current_order_id)
                    raise TimeoutError("Trade timed out before ladder could fill")
                
                attempt_number += 1
                
                # Cancel previous unfilled order before placing new one
                if current_order_id:
                    logger.info(
                        f"Cancelling previous unfilled order before ladder step {attempt_number}",
                        previous_order_id=current_order_id
                    )
                    await self._cancel_order_safely(current_order_id)
                    current_order_id = None
                
                # Switch to late step if needed
                if should_switch_to_late_step(attempt_number, switch_after):
                    wait_time = interval_late
                    logger.info(f"Switching to late step after {attempt_number} attempts")
                
                # Calculate limit price
                limit_price = calculate_limit_price(base_price, current_cents)
                
                # Record attempt start time
                attempt_start_time = time.time()
                attempt_timestamp = datetime.now(timezone.utc) if hasattr(datetime, 'now') else datetime.utcnow()
                
                # Place limit order
                order_data = LimitOrderRequest(
                    symbol=trade_request.ticker,
                    qty=quantity,
                    side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
                    limit_price=limit_price,
                    time_in_force=TimeInForce.DAY,
                    extended_hours=True  # ✅ Alpaca extended hours flag
                )
                
                logger.info(
                    f"Placing ladder order attempt {attempt_number}",
                    limit_price=limit_price,
                    cents_offset=current_cents,
                    quantity=quantity
                )
                
                order = self.trading_client.submit_order(order_data=order_data)
                current_order_id = order.id  # Track this order
                
                # Wait for fill
                fill_wait_start = time.time()
                filled = await self._wait_for_fill(order.id, wait_time, timeout_deadline)
                fill_wait_time = time.time() - fill_wait_start
                attempt_end_time = time.time()
                
                # Record attempt details
                attempt_info = {
                    "attempt_number": attempt_number,
                    "timestamp": attempt_start_time,
                    "limit_price": limit_price,
                    "cents_offset": current_cents,
                    "base_price": base_price,
                    "wait_time": fill_wait_time,
                    "time_since_previous": attempt_start_time - ladder_attempts[-1]["timestamp"] if ladder_attempts else 0.0,
                    "filled": filled
                }
                
                # Add NBBO info if available
                if nbbo_snapshot:
                    attempt_info["nbbo_bid"] = nbbo_snapshot.get("bid")
                    attempt_info["nbbo_ask"] = nbbo_snapshot.get("ask")
                    attempt_info["nbbo_mid"] = nbbo_snapshot.get("mid")
                    attempt_info["nbbo_spread"] = nbbo_snapshot.get("spread")
                    
                    # Calculate distance to mid/ask/bid
                    if action == "BUY":
                        if nbbo_snapshot.get("ask"):
                            attempt_info["distance_to_ask"] = limit_price - nbbo_snapshot.get("ask")
                        if nbbo_snapshot.get("mid"):
                            attempt_info["distance_to_mid"] = limit_price - nbbo_snapshot.get("mid")
                    else:  # SELL
                        if nbbo_snapshot.get("bid"):
                            attempt_info["distance_to_bid"] = limit_price - nbbo_snapshot.get("bid")
                        if nbbo_snapshot.get("mid"):
                            attempt_info["distance_to_mid"] = limit_price - nbbo_snapshot.get("mid")
                
                ladder_attempts.append(attempt_info)
                
                if filled:
                    # Order filled - clear tracking
                    current_order_id = None
                    
                    # Get order details
                    order_status = self.trading_client.get_order_by_id(order.id)
                    fill_price = await self._get_fill_price(order.id, order_status, limit_price, trade_request.ticker)
                    filled_shares = float(order_status.filled_qty) if order_status.filled_qty else quantity

                    total_trading_time = time.time() - trading_start
                    total_time = time.time() - total_start_time

                    logger.info(
                        f"🎉 ORDER FILLED after {attempt_number} attempt(s)! Price: ${fill_price}"
                    )
                    
                    # Calculate totals
                    total_cost = fill_price * filled_shares if fill_price and filled_shares else None
                    commission = 0.0  # Alpaca paper trading has no commission
                    
                    # Calculate percentage above/below base price
                    percentage_above_below = None
                    if base_price and fill_price:
                        diff = fill_price - base_price
                        percentage_above_below = (diff / base_price) * 100
                    
                    # Calculate distance to mid/ask/bid for successful fill
                    distance_to_mid = None
                    distance_to_target = None  # ask for BUY, bid for SELL
                    if nbbo_snapshot:
                        mid = nbbo_snapshot.get("mid")
                        if mid:
                            distance_to_mid = fill_price - mid
                        
                        if action == "BUY":
                            ask = nbbo_snapshot.get("ask")
                            if ask:
                                distance_to_target = fill_price - ask
                        else:  # SELL
                            bid = nbbo_snapshot.get("bid")
                            if bid:
                                distance_to_target = fill_price - bid
                    
                    result = {
                        "success": True,
                        "shares": filled_shares,
                        "fill_price": fill_price,
                        "total_cost": total_cost,
                        "commission": commission,
                        "session": session,
                        "order_type": "LADDER_LIMIT",
                        "instrument": "stock",
                        "limit_price_used": limit_price,
                        "percentage_above_below": percentage_above_below,
                        "timing_info": {
                            "session_detection": session_time,
                            "connection": connect_time,
                            "nbbo_retrieval": nbbo_time,
                            "fill_wait": fill_wait_time,
                            "trading_time": total_trading_time,
                            "total": total_time,
                        },
                        "instrument_details": {
                            "leverage": getattr(trade_request, "leverage", None),
                            "capital_required": capital_required,
                            "price_fallback_used": price_fallback_used,
                            "nbbo": nbbo_snapshot,
                            "ladder_attempts": attempt_number,
                            "ladder_attempts_detail": ladder_attempts,  # All attempts with timestamps
                            "spread": nbbo_snapshot.get("spread") if nbbo_snapshot else None,
                            "distance_to_mid": distance_to_mid,
                            "distance_to_target": distance_to_target,  # ask for BUY, bid for SELL
                        },
                    }
                    
                    logger.info(
                        f"✅ ORDER FILLED: Detailed statistics",
                        ticker=trade_request.ticker,
                        action=action,
                        attempts=attempt_number,
                        fill_price=fill_price,
                        base_price=base_price,
                        spread=nbbo_snapshot.get("spread") if nbbo_snapshot else None,
                        distance_to_mid=distance_to_mid,
                        distance_to_target=distance_to_target,
                        ladder_attempts_detail=ladder_attempts
                    )
                    
                    await self._publish_executed_event(trade_request, result, metadata=_local_metadata)
                    return result
                
                # Order didn't fill - will be cancelled before next attempt
                # Move to next ladder step
                if attempt_number < switch_after:
                    current_cents += early_step
                else:
                    current_cents += late_step
                
                # Wait before next attempt
                await asyncio.sleep(wait_time)
            
            # Ladder exhausted - cancel any pending order
            if current_order_id:
                logger.info("Ladder exhausted - cancelling final unfilled order", order_id=current_order_id)
                await self._cancel_order_safely(current_order_id)
            
            # Log all attempts for failed trade
            logger.warning(
                f"❌ LADDER EXHAUSTED: All attempts failed",
                ticker=trade_request.ticker,
                action=action,
                total_attempts=attempt_number,
                ladder_attempts_detail=ladder_attempts,
                final_limit_price=ladder_attempts[-1]["limit_price"] if ladder_attempts else None,
                base_price=base_price
            )
            
            error_result = {
                "success": False,
                "error": f"Ladder exhausted after {attempt_number} attempts without fill",
                "session": session,
                "order_type": "LADDER_LIMIT",
                "instrument": "stock",
                "ladder_attempts": attempt_number,
                "ladder_attempts_detail": ladder_attempts,  # All attempts with timestamps
                "base_price": base_price,
                "nbbo": nbbo_snapshot,
            }
            await self._publish_failed_event(trade_request, error_result["error"], error_result)
            return error_result
            
        except TimeoutError as exc:
            # Get variables safely
            attempts_made = attempt_number if 'attempt_number' in locals() else 0
            attempts_detail = ladder_attempts if 'ladder_attempts' in locals() else []
            current_session = session if 'session' in locals() else "unknown"
            current_base_price = base_price if 'base_price' in locals() else None
            current_nbbo = nbbo_snapshot if 'nbbo_snapshot' in locals() else None
            
            logger.error(
                f"⏱️ Extended hours trade timed out: {exc}",
                ticker=trade_request.ticker,
                attempts_made=attempts_made,
                ladder_attempts_detail=attempts_detail
            )
            # Cancel any pending order on timeout
            if 'current_order_id' in locals() and current_order_id:
                await self._cancel_order_safely(current_order_id)
            error_result = {
                "success": False,
                "error": f"Trade execution timed out: {str(exc)}",
                "session": current_session,
                "order_type": "LADDER_LIMIT",
                "instrument": "stock",
                "ladder_attempts": attempts_made,
                "ladder_attempts_detail": attempts_detail,
                "base_price": current_base_price,
                "nbbo": current_nbbo,
            }
            await self._publish_failed_event(trade_request, error_result["error"], error_result)
            return error_result
            
        except Exception as exc:
            # Get variables safely
            attempts_made = attempt_number if 'attempt_number' in locals() else 0
            attempts_detail = ladder_attempts if 'ladder_attempts' in locals() else []
            current_session = session if 'session' in locals() else "unknown"
            current_base_price = base_price if 'base_price' in locals() else None
            current_nbbo = nbbo_snapshot if 'nbbo_snapshot' in locals() else None
            
            logger.error(
                f"⏱️ Extended hours trade execution failed: {exc}",
                ticker=trade_request.ticker,
                attempts_made=attempts_made,
                ladder_attempts_detail=attempts_detail,
                exc_info=True
            )
            # Cancel any pending order on error
            if 'current_order_id' in locals() and current_order_id:
                await self._cancel_order_safely(current_order_id)
            error_result = {
                "success": False,
                "error": str(exc),
                "session": current_session,
                "order_type": "LADDER_LIMIT",
                "instrument": "stock",
                "ladder_attempts": attempts_made,
                "ladder_attempts_detail": attempts_detail,
                "base_price": current_base_price,
                "nbbo": current_nbbo,
            }
            await self._publish_failed_event(trade_request, str(exc), error_result)
            return error_result
    
    async def _execute_exit_recovery(
        self,
        trade_request: TradeRequest,
        quantity: float,
        min_price: float,
        initial_bid: float,
        current_order_id,
        nbbo_snapshot: Dict[str, Any],
        session,
        chase_attempts: list,
        metadata: Optional[Dict[str, Any]],
        trigger: str,
    ) -> Dict[str, Any]:
        """
        Guaranteed-exit recovery for SELL orders. Entered when the normal chase
        breaches the 5% floor or exhausts its attempts. A position-closing SELL
        must terminate in one of exactly two states:
          - FLAT, with a TradeExecuted event (exit stats telegram always fires), or
          - a TradeFailed event that says POSITION STILL OPEN in the error text
            (only reachable if order submission itself errors repeatedly).

        Phase A: rest a limit at the floor (min_price) for a bounce — preserves the
        floor's intent (don't dump into a flash crash) without abandoning the exit.
        Phase B: capitulate — chase the bid with NO floor until flat.
        Partial fills accumulate; the published fill_price is the weighted average.
        """
        ticker = trade_request.ticker
        recovery_start = time.time()
        filled_qty_total = 0.0
        filled_notional_total = 0.0
        recovery_attempts: list = []

        def _absorb_fill_progress(order_status) -> None:
            """Accumulate (partial) fills from a finished/cancelled order."""
            nonlocal filled_qty_total, filled_notional_total
            try:
                qty = float(order_status.filled_qty or 0)
                avg = float(order_status.filled_avg_price or 0)
            except (TypeError, ValueError):
                return
            if qty > 0 and avg > 0:
                filled_qty_total += qty
                filled_notional_total += qty * avg

        async def _settle_order(order_id) -> str:
            """Read final fill progress from an order and cancel it if still open.
            Returns the order's status string ('unknown' on API errors)."""
            nonlocal filled_qty_total, filled_notional_total
            try:
                status = self.trading_client.get_order_by_id(order_id)
                if status.status == "filled":
                    # _get_fill_price retries until filled_avg_price is populated
                    fill_price = await self._get_fill_price(order_id, status, min_price, ticker)
                    qty = float(status.filled_qty or 0) or (quantity - filled_qty_total)
                    filled_qty_total += qty
                    filled_notional_total += qty * fill_price
                    return "filled"
                if status.status not in ["canceled", "expired", "rejected"]:
                    self.trading_client.cancel_order_by_id(order_id)
                    await asyncio.sleep(0.15)  # let the cancel/fill race settle
                    status = self.trading_client.get_order_by_id(order_id)
                _absorb_fill_progress(status)
                return str(status.status)
            except Exception as e:
                logger.warning("Exit recovery: could not settle order", ticker=ticker, order_id=str(order_id), error=str(e))
                return "unknown"

        async def _publish_recovered_exit(phase: str) -> Dict[str, Any]:
            avg_fill = filled_notional_total / filled_qty_total
            logger.info(
                f"✅ EXIT RECOVERED ({phase}): Position closed",
                ticker=ticker,
                fill_price=round(avg_fill, 4),
                shares=filled_qty_total,
                initial_bid=initial_bid,
                slippage=f"{((initial_bid - avg_fill) / initial_bid * 100):.2f}%" if avg_fill < initial_bid else "0%",
                recovery_seconds=round(time.time() - recovery_start, 1),
                trigger=trigger,
            )
            result = {
                "success": True,
                "shares": filled_qty_total,
                "fill_price": round(avg_fill, 4),
                "total_cost": filled_notional_total,
                "commission": 0.0,
                "session": session,
                "order_type": "CHASE_LIMIT_RECOVERY",
                "instrument": "stock",
                "limit_price_used": min_price,
                # timing_info is Dict[str, float] on the event model — numbers only
                "timing_info": {
                    "recovery_seconds": round(time.time() - recovery_start, 1),
                },
                "instrument_details": {
                    "nbbo": nbbo_snapshot,
                    "initial_bid": initial_bid,
                    "min_price": min_price,
                    "recovery_trigger": trigger,
                    "recovery_phase": phase,
                    "chase_attempts_detail": chase_attempts,
                    "recovery_attempts_detail": recovery_attempts,
                },
            }
            await self._publish_executed_event(trade_request, result, metadata=metadata)
            return result

        # ── Take over any live order left by the chase (it may even have filled
        # in the meantime — exactly what happened to TGL's resting limit) ──
        if current_order_id:
            status = await _settle_order(current_order_id)
            recovery_attempts.append({"phase": "takeover", "order_id": str(current_order_id), "status": status})
            if filled_qty_total >= quantity:
                return await _publish_recovered_exit("takeover")

        remaining = quantity - filled_qty_total

        # ── Phase A: resting limit at the floor, wait for a bounce ──
        resting_order_id = None
        try:
            order = self.trading_client.submit_order(order_data=LimitOrderRequest(
                symbol=ticker,
                qty=remaining,
                side=OrderSide.SELL,
                limit_price=round(min_price, 2),
                time_in_force=TimeInForce.DAY,
                extended_hours=True,
            ))
            resting_order_id = order.id
            logger.info(
                f"🛟 EXIT RECOVERY Phase A: Resting limit at floor, waiting up to {EXIT_RECOVERY_RESTING_SECONDS}s for a bounce",
                ticker=ticker,
                limit_price=round(min_price, 2),
                shares=remaining,
                trigger=trigger,
            )
        except Exception as e:
            logger.error("Exit recovery: Phase A submission failed — going straight to capitulation", ticker=ticker, error=str(e))
            recovery_attempts.append({"phase": "resting", "result": "submission_failed", "error": str(e)})

        if resting_order_id:
            deadline = time.time() + EXIT_RECOVERY_RESTING_SECONDS
            while time.time() < deadline:
                await asyncio.sleep(EXIT_RECOVERY_POLL_SECONDS)
                try:
                    status = self.trading_client.get_order_by_id(resting_order_id)
                except Exception as e:
                    logger.warning("Exit recovery: Phase A status check failed", ticker=ticker, error=str(e))
                    continue
                if status.status == "filled":
                    fill_price = await self._get_fill_price(resting_order_id, status, min_price, ticker)
                    qty = float(status.filled_qty or 0) or remaining
                    filled_qty_total += qty
                    filled_notional_total += qty * fill_price
                    recovery_attempts.append({"phase": "resting", "result": "filled", "fill_price": fill_price})
                    return await _publish_recovered_exit("resting_at_floor")
                if status.status in ["canceled", "expired", "rejected"]:
                    _absorb_fill_progress(status)  # keep any partial fill
                    recovery_attempts.append({"phase": "resting", "result": str(status.status)})
                    resting_order_id = None  # already settled — don't absorb twice below
                    break  # order died externally — capitulate now
            # Timeout — settle whatever the resting order did (cancel + absorb partials)
            if resting_order_id:
                status = await _settle_order(resting_order_id)
                if status == "filled" or filled_qty_total >= quantity:
                    return await _publish_recovered_exit("resting_at_floor")

        remaining = quantity - filled_qty_total

        # ── Phase B: capitulation — chase the bid with NO floor until flat ──
        logger.warning(
            f"🛟 EXIT RECOVERY Phase B: Capitulating — chasing bid with no floor",
            ticker=ticker,
            remaining_shares=remaining,
            trigger=trigger,
        )
        capitulation_order_id = None
        for attempt in range(1, EXIT_CAPITULATION_MAX_ATTEMPTS + 1):
            fresh_nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
            current_bid = fresh_nbbo.get("bid") if fresh_nbbo else None
            if not current_bid or current_bid <= 0:
                await asyncio.sleep(1.0)
                continue

            if capitulation_order_id:
                status = await _settle_order(capitulation_order_id)
                capitulation_order_id = None
                if status == "filled" or filled_qty_total >= quantity:
                    return await _publish_recovered_exit("capitulation")
                remaining = quantity - filled_qty_total

            try:
                order = self.trading_client.submit_order(order_data=LimitOrderRequest(
                    symbol=ticker,
                    qty=remaining,
                    side=OrderSide.SELL,
                    limit_price=round(current_bid, 2),
                    time_in_force=TimeInForce.DAY,
                    extended_hours=True,
                ))
                capitulation_order_id = order.id
                recovery_attempts.append({"phase": "capitulation", "attempt": attempt, "limit_price": round(current_bid, 2)})
            except Exception as e:
                logger.error(f"Exit recovery: capitulation attempt {attempt} submission failed", ticker=ticker, error=str(e))
                recovery_attempts.append({"phase": "capitulation", "attempt": attempt, "result": "submission_failed", "error": str(e)})
                await asyncio.sleep(1.0)
                continue

            await asyncio.sleep(1.0)
            try:
                status = self.trading_client.get_order_by_id(capitulation_order_id)
                if status.status == "filled":
                    fill_price = await self._get_fill_price(capitulation_order_id, status, current_bid, ticker)
                    qty = float(status.filled_qty or 0) or remaining
                    filled_qty_total += qty
                    filled_notional_total += qty * fill_price
                    return await _publish_recovered_exit("capitulation")
            except Exception as e:
                logger.warning(f"Exit recovery: capitulation attempt {attempt} status check failed", ticker=ticker, error=str(e))

        # Settle any final open order before declaring failure
        if capitulation_order_id:
            status = await _settle_order(capitulation_order_id)
            if status == "filled" or filled_qty_total >= quantity:
                return await _publish_recovered_exit("capitulation")

        # ── Terminal failure (API-level only) — be LOUD that the position is open ──
        remaining = quantity - filled_qty_total
        error_msg = (
            f"🚨 POSITION STILL OPEN — MANUAL ACTION REQUIRED: exit recovery exhausted "
            f"({remaining:g} of {quantity:g} {ticker} shares unsold after floor breach + "
            f"{EXIT_RECOVERY_RESTING_SECONDS}s resting + {EXIT_CAPITULATION_MAX_ATTEMPTS} capitulation attempts)"
        )
        logger.error(error_msg, ticker=ticker, recovery_attempts=recovery_attempts)
        error_result = {
            "success": False,
            "error": error_msg,
            "session": session,
            "order_type": "CHASE_LIMIT_RECOVERY",
            "instrument": "stock",
            "initial_bid": initial_bid,
            "min_price": min_price,
            "chase_attempts": chase_attempts,
            "recovery_attempts": recovery_attempts,
            "nbbo": nbbo_snapshot,
        }
        await self._publish_failed_event(trade_request, error_msg, error_result)
        return error_result

    async def _cancel_order_safely(self, order_id: str) -> None:
        """
        Cancel an order safely, handling cases where it may already be filled/cancelled.

        Args:
            order_id: Order ID to cancel
        """
        try:
            order_status = self.trading_client.get_order_by_id(order_id)
            if order_status.status not in ["filled", "canceled", "expired", "rejected"]:
                self.trading_client.cancel_order_by_id(order_id)
                logger.info("Cancelled unfilled ladder order", order_id=order_id)
            else:
                logger.debug(
                    "Order already in final state, skipping cancellation",
                    order_id=order_id,
                    status=order_status.status
                )
        except Exception as e:
            # Order might not exist or already be cancelled - log but don't fail
            logger.warning(
                "Failed to cancel order (may already be filled/cancelled)",
                order_id=order_id,
                error=str(e)
            )

    async def _cancel_order_fire_and_forget(self, order_id: str) -> None:
        """
        Cancel an order quickly without checking status first.

        Used for parallel order submission - we fire the cancel and immediately
        try to submit a new order, minimizing the gap with no order on book.

        Args:
            order_id: Order ID to cancel
        """
        try:
            # Skip status check - just fire cancel directly (faster)
            self.trading_client.cancel_order_by_id(order_id)
            logger.debug("Fire-and-forget cancel sent", order_id=order_id)
        except Exception as e:
            # Order might already be filled/cancelled - that's fine
            logger.debug(
                "Fire-and-forget cancel returned error (may be filled)",
                order_id=order_id,
                error=str(e)
            )

    async def _cancel_all_open_orders_for_ticker(self, ticker: str) -> int:
        """
        Cancel all open orders for a specific ticker.

        This is critical for SELL orders - if there's a pending BUY order that didn't fill,
        shares may be "held_for_orders" and unavailable for selling. Cancelling pending
        orders releases those shares.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Number of orders cancelled
        """
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus

            # Get all open orders for this ticker
            request = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[ticker]
            )
            open_orders = self.trading_client.get_orders(filter=request)

            if not open_orders:
                logger.debug(
                    "No open orders to cancel for ticker",
                    ticker=ticker
                )
                return 0

            cancelled_count = 0
            for order in open_orders:
                try:
                    self.trading_client.cancel_order_by_id(order.id)
                    cancelled_count += 1
                    logger.info(
                        "Cancelled open order before SELL",
                        ticker=ticker,
                        order_id=str(order.id),
                        order_side=order.side,
                        order_qty=order.qty,
                        order_status=order.status
                    )
                except Exception as cancel_err:
                    logger.warning(
                        "Failed to cancel individual order",
                        ticker=ticker,
                        order_id=str(order.id),
                        error=str(cancel_err)
                    )

            logger.info(
                "Cancelled all open orders for ticker before SELL",
                ticker=ticker,
                orders_cancelled=cancelled_count,
                orders_found=len(open_orders)
            )

            # Small delay to ensure order cancellation is processed
            await asyncio.sleep(0.5)

            return cancelled_count

        except Exception as e:
            logger.error(
                "Failed to cancel open orders for ticker",
                ticker=ticker,
                error=str(e),
                exc_info=True
            )
            # Don't fail the SELL - proceed anyway
            return 0

    async def _wait_for_fill(self, order_id: str, wait_time: float, timeout_deadline: Optional[float]) -> bool:
        """
        Wait for order fill via REST API polling.
        
        Args:
            order_id: Order ID to check
            wait_time: Time to wait between checks
            timeout_deadline: Optional timeout deadline
            
        Returns:
            True if filled, False otherwise
        """
        max_checks = 10
        for _ in range(max_checks):
            if timeout_deadline is not None:
                remaining = timeout_deadline - time.monotonic()
                if remaining <= 0:
                    break
            
            sleep_interval = wait_time
            if timeout_deadline is not None:
                remaining = timeout_deadline - time.monotonic()
                sleep_interval = min(wait_time, max(remaining, 0))
            
            if sleep_interval > 0:
                await asyncio.sleep(sleep_interval)
            
            order_status = self.trading_client.get_order_by_id(order_id)
            
            if order_status.status == "filled":
                return True
            
            if order_status.status in ["canceled", "expired", "rejected"]:
                return False
        
        return False
    
    async def _get_fill_price(self, order_id: str, order_status, limit_price: float, ticker: str) -> float:
        """
        Get the actual fill price from Alpaca, retrying if needed.

        Alpaca's filled_avg_price should always be populated on a filled order,
        but we retry up to 3 times to handle any API propagation delay.
        If still unavailable after retries, falls back to limit_price with loud warning.

        Bug context: SXTP entered at limit $2.17, filled at $2.07 (price improvement),
        but fill_price was set to $2.17 causing stop loss to trigger at -2.4% instead of -5%.
        """
        fill_price_raw = order_status.filled_avg_price
        if fill_price_raw:
            return float(fill_price_raw)

        # Retry up to 3 times — Alpaca should always have this for a filled order
        for retry in range(3):
            await asyncio.sleep(0.2)
            order_status = self.trading_client.get_order_by_id(order_id)
            fill_price_raw = order_status.filled_avg_price
            if fill_price_raw:
                logger.info(
                    f"filled_avg_price retrieved on retry {retry + 1}",
                    ticker=ticker,
                    fill_price=float(fill_price_raw),
                )
                return float(fill_price_raw)

        # This should never happen — log loudly so we can investigate
        logger.error(
            "🚨 CRITICAL: filled_avg_price unavailable after 3 retries on filled order! "
            "Using limit_price — stop loss will be WRONG if there was price improvement",
            ticker=ticker,
            order_id=str(order_id),
            limit_price=limit_price,
        )
        return limit_price

    async def _publish_executed_event(self, trade_request: TradeRequest, result: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
        """Publish trade executed event and send fast notification."""
        infra_trade_request = build_infrastructure_trade_request_data(trade_request)

        # Extract spread_info from instrument_details for notifications
        instrument_details = result.get("instrument_details", {})
        spread_info = {}
        if instrument_details.get("nbbo"):
            nbbo = instrument_details["nbbo"]
            spread_info = {
                "bid": nbbo.get("bid"),
                "ask": nbbo.get("ask"),
                "spread": nbbo.get("spread"),
                "mid": nbbo.get("mid")
            }

        # Publish event for stats/logging/notifications (single notification path)
        event = TradeExecutedEvent(
            trade_request=infra_trade_request,
            success=result["success"],
            shares=result.get("shares"),
            fill_price=result.get("fill_price"),
            total_cost=result.get("total_cost"),
            commission=result.get("commission"),
            session=result["session"],
            order_type=result["order_type"],
            instrument=result["instrument"],
            instrument_details=instrument_details,  # Include ladder_attempts_detail, spread, distance stats
            timing_info=result.get("timing_info", {}),
            limit_price_used=result.get("limit_price_used"),
            percentage_above_below=result.get("percentage_above_below"),
            spread_info=spread_info,  # For notifications
            executed_at=datetime.now(),
            source="brokerage",
            metadata=metadata if metadata is not None else getattr(self, '_current_metadata', None)
        )

        await self.event_bus.publish("TradeExecuted", event.model_dump())
        logger.debug("Published TradeExecuted event", ticker=trade_request.ticker)
    
    async def _publish_failed_event(self, trade_request: TradeRequest, error: str, error_result: Optional[Dict[str, Any]] = None) -> None:
        """Publish trade failed event."""
        infra_trade_request = build_infrastructure_trade_request_data(trade_request)
        
        # Include ladder attempts detail if available
        ladder_attempts_detail = error_result.get("ladder_attempts_detail", []) if error_result else []
        ladder_attempts = error_result.get("ladder_attempts", 0) if error_result else 0
        
        event = TradeFailedEvent(
            trade_request=infra_trade_request,
            error=error,
            failed_at=datetime.now(),
            source="brokerage",
            ladder_attempts=ladder_attempts,
            ladder_attempts_detail=ladder_attempts_detail if ladder_attempts_detail else None
        )
        
        await self.event_bus.publish("TradeFailed", event.model_dump())
        logger.debug("Published TradeFailed event", ticker=trade_request.ticker, error=error, attempts=ladder_attempts)
