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
from .utils import calculate_trade_quantity
from ...utils.brokerage.ladder_algorithms import (
    calculate_ladder_base_price,
    calculate_ladder_parameters,
    calculate_limit_price,
    should_switch_to_late_step,
)
from ..notification.fast_trade_notifier import FastTradeNotifier

logger = get_logger(__name__)


class AlpacaExtendedHoursTradeExecutor:
    """
    Executes stock trades during extended hours.
    
    Entry (BUY) Strategy:
    - Place single limit order at ask price
    - No retry if order doesn't fill (fast-moving markets require immediate execution)
    
    Exit (SELL) Strategy:
    - Place single marketable limit order at bid price (with discount to ensure immediate fill)
    - No retry if order doesn't fill (fast-moving markets require immediate execution)
    
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
        # Store metadata for event publishing
        self._current_metadata = metadata
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
            leverage = getattr(trade_request, "leverage", None)
            quantity, capital_required = calculate_trade_quantity(
                trade_request, current_price, leverage or 1.0
            )
            
            trading_start = time.time()
            
            # SIMPLIFIED ENTRY LOGIC: For BUY orders, use marketable limit order to ensure fill
            if action == "BUY":
                ask_price = nbbo_snapshot.get("ask")
                ask_size = nbbo_snapshot.get("ask_size")
                if not ask_price or ask_price <= 0:
                    error_result = {
                        "success": False,
                        "error": "Could not retrieve ask price for entry trade",
                        "session": session,
                        "order_type": "LIMIT",
                        "instrument": "stock",
                    }
                    await self._publish_failed_event(trade_request, error_result["error"])
                    return error_result
                
                # Use marketable limit order: price above ask to ensure immediate fill
                # This is critical for fast-moving markets - we need to get filled NOW
                # Strategy: Dynamic premium based on liquidity and spread conditions
                # Since our system rarely makes losing trades, we prioritize fill probability over cost
                
                # Base premium: 0.5% or $0.02 minimum
                base_premium_pct = 0.005  # 0.5%
                base_premium_dollar = 0.02
                price_premium = max(ask_price * base_premium_pct, base_premium_dollar)
                
                # Dynamic adjustment 1: Increase premium if ask_size is insufficient
                # If we need more shares than available at ask, we need to pay more to reach higher price levels
                liquidity_multiplier = 1.0
                if ask_size is not None and ask_size > 0 and ask_size < quantity:
                    shortfall_ratio = quantity / ask_size  # e.g., 1188 / 200 = 5.94x
                    # Scale multiplier: more aggressive for larger shortfalls, capped at 3x
                    # Formula: 1.0 + (shortfall_ratio - 1.0) * 0.15, max 3.0
                    liquidity_multiplier = min(1.0 + (shortfall_ratio - 1.0) * 0.15, 3.0)
                
                # Dynamic adjustment 2: Increase premium for wide spreads
                # Wide spreads indicate lower liquidity or higher volatility
                bid_price = nbbo_snapshot.get("bid")
                spread_multiplier = 1.0
                if bid_price and bid_price > 0:
                    spread = ask_price - bid_price
                    spread_pct = (spread / ask_price) * 100
                    # If spread > 0.5%, increase premium (wider spread = more premium needed)
                    if spread_pct > 0.5:
                        # Scale: 1.0x for 0.5% spread, up to 1.5x for 2%+ spread
                        spread_multiplier = min(1.0 + (spread_pct - 0.5) * 0.25, 1.5)
                
                # Apply multipliers to base premium
                price_premium = price_premium * liquidity_multiplier * spread_multiplier
                
                # Cap at reasonable maximum: 2% or $0.05 (whichever is larger)
                # This ensures we don't overpay excessively, but still prioritize fills
                max_premium_pct = 0.02  # 2%
                max_premium_dollar = 0.05
                price_premium = min(price_premium, max(ask_price * max_premium_pct, max_premium_dollar))
                
                limit_price = round(ask_price + price_premium, 2)
                
                # Enhanced logging with dynamic adjustments
                liquidity_info = ""
                spread_info = ""
                if ask_size is not None:
                    if ask_size < quantity:
                        liquidity_info = f" | ask_size={ask_size} < qty={quantity} → {liquidity_multiplier:.2f}x premium"
                    else:
                        liquidity_info = f" | ask_size={ask_size} >= qty={quantity} (sufficient)"
                
                if bid_price:
                    spread = ask_price - bid_price
                    spread_pct = (spread / ask_price) * 100
                    if spread_pct > 0.5:
                        spread_info = f" | spread={spread_pct:.2f}% (wide) → {spread_multiplier:.2f}x premium"
                    else:
                        spread_info = f" | spread={spread_pct:.2f}% (normal)"
                
                logger.info(
                    f"💰 ENTRY ORDER: Placing marketable limit order with dynamic premium to maximize fill probability",
                    ticker=trade_request.ticker,
                    limit_price=limit_price,
                    ask_price=ask_price,
                    price_premium=price_premium,
                    premium_pct=(price_premium / ask_price * 100) if ask_price > 0 else 0,
                    quantity=quantity,
                    ask_size=ask_size,
                    bid=bid_price,
                    liquidity_multiplier=liquidity_multiplier,
                    spread_multiplier=spread_multiplier,
                    liquidity_info=liquidity_info,
                    spread_info=spread_info
                )
                
                order_data = LimitOrderRequest(
                    symbol=trade_request.ticker,
                    qty=quantity,
                    side=OrderSide.BUY,
                    limit_price=limit_price,
                    time_in_force=TimeInForce.DAY,
                    extended_hours=True
                )
                
                order = self.trading_client.submit_order(order_data=order_data)
                current_order_id = order.id
                
                # Immediately check if order was rejected (catch broker rejections early)
                try:
                    immediate_status = self.trading_client.get_order_by_id(order.id)
                    if immediate_status.status in ["rejected", "canceled", "expired"]:
                        reject_reason = getattr(immediate_status, 'reject_reason', 'Unknown')
                        error_result = {
                            "success": False,
                            "error": f"Order immediately {immediate_status.status}: {reject_reason}",
                            "session": session,
                            "order_type": "LIMIT",
                            "instrument": "stock",
                            "limit_price_attempted": limit_price,
                            "nbbo": nbbo_snapshot,
                        }
                        logger.warning(
                            f"❌ ENTRY ORDER IMMEDIATELY REJECTED: Order was {immediate_status.status}",
                            ticker=trade_request.ticker,
                            limit_price=limit_price,
                            ask=ask_price,
                            reject_reason=reject_reason
                        )
                        await self._publish_failed_event(trade_request, error_result["error"], error_result)
                        return error_result
                except Exception as status_error:
                    logger.debug(
                        "Could not check immediate order status (will check during fill wait)",
                        ticker=trade_request.ticker,
                        error=str(status_error)
                    )
                
                # Wait for fill with timeout (10 seconds for extended hours)
                # News trades usually have high volume, but extended hours can have lower liquidity
                # 10 seconds balances speed vs reliability
                fill_wait_start = time.time()
                fill_timeout = 10.0  # 10 seconds max wait for entry in extended hours
                deadline_for_fill = min(timeout_deadline, time.monotonic() + fill_timeout) if timeout_deadline else time.monotonic() + fill_timeout
                filled = await self._wait_for_fill(order.id, 0.5, deadline_for_fill)
                fill_wait_time = time.time() - fill_wait_start
                
                if filled:
                    # Order filled - get details
                    order_status = self.trading_client.get_order_by_id(order.id)
                    fill_price = float(order_status.filled_avg_price) if order_status.filled_avg_price else limit_price
                    filled_shares = float(order_status.filled_qty) if order_status.filled_qty else quantity
                    total_trading_time = time.time() - trading_start
                    total_time = time.time() - total_start_time
                    
                    logger.info(
                        f"✅ ENTRY ORDER FILLED at ask price",
                        ticker=trade_request.ticker,
                        fill_price=fill_price,
                        shares=filled_shares
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
                        "order_type": "LIMIT",
                        "instrument": "stock",
                        "limit_price_used": limit_price,
                        "timing_info": {
                            "session_detection": session_time,
                            "connection": connect_time,
                            "nbbo_retrieval": nbbo_time,
                            "fill_wait": fill_wait_time,
                            "trading_time": total_trading_time,
                            "total": total_time,
                        },
                        "instrument_details": {
                            "leverage": leverage,
                            "capital_required": capital_required,
                            "price_fallback_used": price_fallback_used,
                            "nbbo": nbbo_snapshot,
                        },
                    }
                    
                    await self._publish_executed_event(trade_request, result)
                    return result
                else:
                    # Entry order didn't fill - this should be rare with marketable limit order
                    # Check order status to see if it was rejected
                    try:
                        order_status = self.trading_client.get_order_by_id(order.id)
                        if order_status.status in ["rejected", "canceled", "expired"]:
                            reject_reason = getattr(order_status, 'reject_reason', 'Unknown')
                            error_result = {
                                "success": False,
                                "error": f"Order {order_status.status}: {reject_reason}",
                                "session": session,
                                "order_type": "LIMIT",
                                "instrument": "stock",
                                "limit_price_attempted": limit_price,
                                "nbbo": nbbo_snapshot,
                            }
                            logger.warning(
                                f"❌ ENTRY ORDER REJECTED: Order was {order_status.status}",
                                ticker=trade_request.ticker,
                                limit_price=limit_price,
                                ask=ask_price,
                                reject_reason=reject_reason
                            )
                            await self._publish_failed_event(trade_request, error_result["error"], error_result)
                            return error_result
                    except Exception as status_error:
                        logger.debug(
                            "Could not check order status after timeout",
                            ticker=trade_request.ticker,
                            error=str(status_error)
                        )
                    
                    # Order didn't fill within timeout - cancel and fail
                    await self._cancel_order_safely(order.id)
                    error_result = {
                        "success": False,
                        "error": "Entry order did not fill within timeout (unexpected with marketable limit)",
                        "session": session,
                        "order_type": "LIMIT",
                        "instrument": "stock",
                        "limit_price_attempted": limit_price,
                        "ask_price": ask_price,
                        "price_premium": price_premium,
                        "nbbo": nbbo_snapshot,
                    }
                    logger.warning(
                        f"❌ ENTRY ORDER FAILED: Did not fill within timeout (unexpected)",
                        ticker=trade_request.ticker,
                        limit_price=limit_price,
                        ask=ask_price,
                        ask_size=ask_size,
                        quantity=quantity
                    )
                    await self._publish_failed_event(trade_request, error_result["error"], error_result)
                    return error_result
            
            # EXIT LOGIC: For SELL orders, use marketable limit order at bid price to ensure fill
            if action == "SELL":
                # CRITICAL FIX: Cancel any existing open orders for this ticker before placing new sell
                # This prevents "insufficient qty available" errors when shares are held by pending orders
                await self._cancel_all_open_orders_for_ticker(trade_request.ticker)

                bid_price = nbbo_snapshot.get("bid")
                bid_size = nbbo_snapshot.get("bid_size")
                if not bid_price or bid_price <= 0:
                    error_result = {
                        "success": False,
                        "error": "Could not retrieve bid price for exit trade",
                        "session": session,
                        "order_type": "LIMIT",
                        "instrument": "stock",
                    }
                    await self._publish_failed_event(trade_request, error_result["error"])
                    return error_result
                
                # Use marketable limit order: price below bid to ensure immediate fill
                # This is critical for fast-moving markets - we need to get filled NOW
                # Strategy: Dynamic discount based on liquidity and spread conditions
                # Since our system rarely makes losing trades, we prioritize fill probability over cost
                
                # Base discount: 0.5% or $0.02 minimum
                base_discount_pct = 0.005  # 0.5%
                base_discount_dollar = 0.02
                price_discount = max(bid_price * base_discount_pct, base_discount_dollar)
                
                # Dynamic adjustment 1: Increase discount if bid_size is insufficient
                # If we need to sell more shares than available at bid, we need to discount more to reach lower price levels
                liquidity_multiplier = 1.0
                if bid_size is not None and bid_size > 0 and bid_size < quantity:
                    shortfall_ratio = quantity / bid_size  # e.g., 1188 / 200 = 5.94x
                    # Scale multiplier: more aggressive for larger shortfalls, capped at 3x
                    # Formula: 1.0 + (shortfall_ratio - 1.0) * 0.15, max 3.0
                    liquidity_multiplier = min(1.0 + (shortfall_ratio - 1.0) * 0.15, 3.0)
                
                # Dynamic adjustment 2: Increase discount for wide spreads
                # Wide spreads indicate lower liquidity or higher volatility
                ask_price = nbbo_snapshot.get("ask")
                spread_multiplier = 1.0
                if ask_price and ask_price > 0:
                    spread = ask_price - bid_price
                    spread_pct = (spread / bid_price) * 100
                    # If spread > 0.5%, increase discount (wider spread = more discount needed)
                    if spread_pct > 0.5:
                        # Scale: 1.0x for 0.5% spread, up to 1.5x for 2%+ spread
                        spread_multiplier = min(1.0 + (spread_pct - 0.5) * 0.25, 1.5)
                
                # Apply multipliers to base discount
                price_discount = price_discount * liquidity_multiplier * spread_multiplier
                
                # Cap at reasonable maximum: 2% or $0.05 (whichever is larger)
                # This ensures we don't discount excessively, but still prioritize fills
                max_discount_pct = 0.02  # 2%
                max_discount_dollar = 0.05
                price_discount = min(price_discount, max(bid_price * max_discount_pct, max_discount_dollar))
                
                limit_price = round(bid_price - price_discount, 2)
                
                # Enhanced logging with dynamic adjustments
                liquidity_info = ""
                spread_info = ""
                if bid_size is not None:
                    if bid_size < quantity:
                        liquidity_info = f" | bid_size={bid_size} < qty={quantity} → {liquidity_multiplier:.2f}x discount"
                    else:
                        liquidity_info = f" | bid_size={bid_size} >= qty={quantity} (sufficient)"
                
                if ask_price:
                    spread = ask_price - bid_price
                    spread_pct = (spread / bid_price) * 100
                    if spread_pct > 0.5:
                        spread_info = f" | spread={spread_pct:.2f}% (wide) → {spread_multiplier:.2f}x discount"
                    else:
                        spread_info = f" | spread={spread_pct:.2f}% (normal)"
                
                logger.info(
                    f"💰 EXIT ORDER: Placing marketable limit order with dynamic discount to maximize fill probability",
                    ticker=trade_request.ticker,
                    limit_price=limit_price,
                    bid_price=bid_price,
                    price_discount=price_discount,
                    discount_pct=(price_discount / bid_price * 100) if bid_price > 0 else 0,
                    quantity=quantity,
                    bid_size=bid_size,
                    ask=ask_price,
                    liquidity_multiplier=liquidity_multiplier,
                    spread_multiplier=spread_multiplier,
                    liquidity_info=liquidity_info,
                    spread_info=spread_info
                )
                
                order_data = LimitOrderRequest(
                    symbol=trade_request.ticker,
                    qty=quantity,
                    side=OrderSide.SELL,
                    limit_price=limit_price,
                    time_in_force=TimeInForce.DAY,
                    extended_hours=True
                )
                
                order = self.trading_client.submit_order(order_data=order_data)
                
                # Immediately check if order was rejected (catch broker rejections early)
                try:
                    immediate_status = self.trading_client.get_order_by_id(order.id)
                    if immediate_status.status in ["rejected", "canceled", "expired"]:
                        reject_reason = getattr(immediate_status, 'reject_reason', 'Unknown')
                        error_result = {
                            "success": False,
                            "error": f"Order immediately {immediate_status.status}: {reject_reason}",
                            "session": session,
                            "order_type": "LIMIT",
                            "instrument": "stock",
                            "limit_price_attempted": limit_price,
                            "nbbo": nbbo_snapshot,
                        }
                        logger.warning(
                            f"❌ EXIT ORDER IMMEDIATELY REJECTED: Order was {immediate_status.status}",
                            ticker=trade_request.ticker,
                            limit_price=limit_price,
                            bid=bid_price,
                            reject_reason=reject_reason
                        )
                        await self._publish_failed_event(trade_request, error_result["error"], error_result)
                        return error_result
                except Exception as status_error:
                    logger.debug(
                        "Could not check immediate order status (will check during fill wait)",
                        ticker=trade_request.ticker,
                        error=str(status_error)
                    )
                
                # Wait for fill with timeout (10 seconds for extended hours)
                fill_wait_start = time.time()
                fill_timeout = 10.0  # 10 seconds max wait for exit in extended hours
                deadline_for_fill = min(timeout_deadline, time.monotonic() + fill_timeout) if timeout_deadline else time.monotonic() + fill_timeout
                filled = await self._wait_for_fill(order.id, 0.5, deadline_for_fill)
                fill_wait_time = time.time() - fill_wait_start
                
                if filled:
                    # Order filled - get details
                    order_status = self.trading_client.get_order_by_id(order.id)
                    fill_price = float(order_status.filled_avg_price) if order_status.filled_avg_price else limit_price
                    filled_shares = float(order_status.filled_qty) if order_status.filled_qty else quantity
                    total_trading_time = time.time() - trading_start
                    total_time = time.time() - total_start_time
                    
                    logger.info(
                        f"✅ EXIT ORDER FILLED at bid price",
                        ticker=trade_request.ticker,
                        fill_price=fill_price,
                        shares=filled_shares
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
                        "order_type": "LIMIT",
                        "instrument": "stock",
                        "limit_price_used": limit_price,
                        "timing_info": {
                            "session_detection": session_time,
                            "connection": connect_time,
                            "nbbo_retrieval": nbbo_time,
                            "fill_wait": fill_wait_time,
                            "trading_time": total_trading_time,
                            "total": total_time,
                        },
                        "instrument_details": {
                            "leverage": leverage,
                            "capital_required": capital_required,
                            "price_fallback_used": price_fallback_used,
                            "nbbo": nbbo_snapshot,
                        },
                    }
                    
                    await self._publish_executed_event(trade_request, result)
                    return result
                else:
                    # Exit order didn't fill - this should be rare with marketable limit order
                    # Check order status to see if it was rejected
                    try:
                        order_status = self.trading_client.get_order_by_id(order.id)
                        if order_status.status in ["rejected", "canceled", "expired"]:
                            reject_reason = getattr(order_status, 'reject_reason', 'Unknown')
                            error_result = {
                                "success": False,
                                "error": f"Order {order_status.status}: {reject_reason}",
                                "session": session,
                                "order_type": "LIMIT",
                                "instrument": "stock",
                                "limit_price_attempted": limit_price,
                                "bid_price": bid_price,
                                "nbbo": nbbo_snapshot,
                            }
                            logger.warning(
                                f"❌ EXIT ORDER REJECTED: Order was {order_status.status}",
                                ticker=trade_request.ticker,
                                limit_price=limit_price,
                                bid=bid_price,
                                reject_reason=reject_reason
                            )
                            await self._publish_failed_event(trade_request, error_result["error"], error_result)
                            return error_result
                    except Exception as status_error:
                        logger.debug(
                            "Could not check order status after timeout",
                            ticker=trade_request.ticker,
                            error=str(status_error)
                        )
                    
                    # Order didn't fill within timeout - cancel and fail
                    await self._cancel_order_safely(order.id)
                    error_result = {
                        "success": False,
                        "error": "Exit order did not fill within timeout (unexpected with marketable limit)",
                        "session": session,
                        "order_type": "LIMIT",
                        "instrument": "stock",
                        "limit_price_attempted": limit_price,
                        "bid_price": bid_price,
                        "price_discount": price_discount,
                        "nbbo": nbbo_snapshot,
                    }
                    logger.warning(
                        f"❌ EXIT ORDER FAILED: Did not fill within timeout (unexpected)",
                        ticker=trade_request.ticker,
                        limit_price=limit_price,
                        bid=bid_price,
                        bid_size=bid_size,
                        quantity=quantity
                    )
                    await self._publish_failed_event(trade_request, error_result["error"], error_result)
                    return error_result
            
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
                    fill_price = float(order_status.filled_avg_price) if order_status.filled_avg_price else limit_price
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
                    
                    await self._publish_executed_event(trade_request, result)
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
    
    async def _publish_executed_event(self, trade_request: TradeRequest, result: Dict[str, Any]) -> None:
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

        # FAST PATH: Send Telegram notification immediately (fire-and-forget)
        # This bypasses the event bus layers for minimal latency
        if self.fast_notifier and result.get("success") and trade_request.action.upper() == "BUY":
            self.fast_notifier.notify_trade_executed(
                ticker=trade_request.ticker,
                action=trade_request.action.upper(),
                shares=result.get("shares", 0),
                fill_price=result.get("fill_price", 0),
                total_cost=result.get("total_cost", 0),
                session=result.get("session", "unknown"),
                order_type=result.get("order_type", "LIMIT"),
                spread_info=spread_info,
                article_title=None,  # Not available at this layer
                publication_time=None,  # Not available at this layer
            )
            logger.info("FastTradeNotifier: Triggered immediate notification", ticker=trade_request.ticker)

        # STANDARD PATH: Publish event for stats/logging (continues in parallel)
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
            metadata=getattr(self, '_current_metadata', None)  # Exit metadata (tier, exit_reason, etc.)
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
