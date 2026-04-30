"""
Trade executor for market hours trading (stocks only) - Alpaca implementation.
Pure infrastructure - executes trades and publishes events.
"""
import time
from typing import Optional, Dict, Any
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest
from ...shared.event_bus import AsyncEventBus
from .events import TradeExecutedEvent, TradeFailedEvent
from .event_builders import build_infrastructure_trade_request_data
from .quote_fetcher import AlpacaQuoteFetcher
from .utils import (
    calculate_trade_quantity,
    wait_for_sustained_depth,
    DEPTH_GATE_MAX_RATIO,
    DEPTH_PROBE_USD,
    DEPTH_PROBE_MIN_CONSECUTIVE,
    DEPTH_PROBE_MAX_WAIT_S,
)
from ..notification.fast_trade_notifier import FastTradeNotifier

logger = get_logger(__name__)


class AlpacaMarketHoursTradeExecutor:
    """
    Executes stock trades during market hours using market orders.
    
    Responsibilities:
    - Execute market orders (stocks only)
    - Handle 2x leverage
    - Monitor fills
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
        Initialize market hours trade executor.

        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            quote_fetcher: Quote fetcher instance for getting prices
            trading_client: Alpaca TradingClient instance
            fast_notifier: Optional fast trade notifier for immediate Telegram notifications
        """
        self.quote_fetcher = quote_fetcher
        self.event_bus = event_bus
        self.trading_client = trading_client
        self.fast_notifier = fast_notifier

        logger.info(
            "AlpacaMarketHoursTradeExecutor initialized",
            fast_notifier_enabled=fast_notifier is not None
        )
    
    async def execute(
        self,
        trade_request: TradeRequest,
        timing_info: Dict[str, float],
        timeout_deadline: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a stock trade during market hours.

        Args:
            trade_request: Trade request
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
        
        try:
            def time_left() -> Optional[float]:
                """Calculate remaining time until timeout deadline."""
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()
            
            # Check timeout before starting execution
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before execution started")
            
            action = trade_request.action.upper()
            quantity = trade_request.shares
            
            # Calculate quantity if not provided (with 2x leverage support)
            if quantity is None:
                price_start = time.time()
                current_price = await self.quote_fetcher.get_realtime_price(trade_request.ticker)
                price_time = time.time() - price_start
                logger.info(f"💰 Market hours price retrieval for sizing: {price_time:.3f}s")
                
                if not current_price:
                    error_result = {
                        "success": False,
                        "error": "Could not retrieve price to size order",
                        "session": "market_hours",
                        "order_type": "MARKET",
                        "instrument": "stock",
                    }
                    await self._publish_failed_event(trade_request, error_result["error"])
                    return error_result
                
                # Calculate quantity with leverage support (2x by default)
                leverage = getattr(trade_request, "leverage", None) or 2.0
                quantity, capital_required = calculate_trade_quantity(
                    trade_request, current_price, leverage
                )
                
                logger.info(
                    "Calculated share quantity for market-hours trade",
                    quantity=quantity,
                    capital_required=capital_required,
                    leverage=leverage,
                    price=current_price,
                    total_cost=quantity * current_price,
                )
            else:
                logger.debug("Using explicit quantity for market-hours trade", quantity=quantity)

            # ===============================================================
            # 🚦 LIQUIDITY GATE: Sustained-depth probe at $5K truth-filter
            # ===============================================================
            # Replaces the prior single-snapshot ask_size check. Bimodal books
            # flicker between thin and deep; one snapshot is coin-flip noise.
            # Polls up to ~13s for N consecutive distinct quotes where $5K is
            # < 50% of book. SELLs are NEVER gated — never block a liquidation.
            if action == "BUY":
                gate_passed, last_nbbo, depth_telemetry = await wait_for_sustained_depth(
                    quote_fetcher=self.quote_fetcher,
                    ticker=trade_request.ticker,
                    max_wait_s=DEPTH_PROBE_MAX_WAIT_S,
                    probe_size_usd=DEPTH_PROBE_USD,
                    min_consecutive=DEPTH_PROBE_MIN_CONSECUTIVE,
                    gate_ratio=DEPTH_GATE_MAX_RATIO,
                )

                if not gate_passed:
                    error_msg = (
                        f"Liquidity gate: no sustained depth (probe ${DEPTH_PROBE_USD:,.0f} "
                        f"< {int(DEPTH_GATE_MAX_RATIO*100)}% of book × {DEPTH_PROBE_MIN_CONSECUTIVE} "
                        f"consecutive quotes within {DEPTH_PROBE_MAX_WAIT_S:.0f}s) — "
                        f"max_consec={depth_telemetry.get('depth_probe_max_consecutive', 0)} "
                        f"deep={depth_telemetry.get('depth_probe_deep_observed', 0)}/"
                        f"{depth_telemetry.get('depth_probe_quotes_observed', 0)}"
                    )
                    logger.warning(
                        "🚦 TRADE ABORTED: Liquidity gate (sustained depth probe failed)",
                        ticker=trade_request.ticker,
                        **depth_telemetry,
                    )
                    error_result = {
                        "success": False,
                        "error": error_msg,
                        "session": "market_hours",
                        "order_type": "MARKET",
                        "instrument": "stock",
                        "depth_probe": depth_telemetry,
                    }
                    await self._publish_failed_event(trade_request, error_result["error"])
                    return error_result

                logger.info(
                    "✅ Liquidity gate: sustained depth probe passed",
                    ticker=trade_request.ticker,
                    **depth_telemetry,
                )

            # Create market order
            order_create_start = time.time()
            order_data = MarketOrderRequest(
                symbol=trade_request.ticker,
                qty=quantity,
                side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY
            )
            order_create_time = time.time() - order_create_start
            logger.info(f"✅ Market order created: {order_data.symbol} x {order_data.qty} (create: {order_create_time:.3f}s)")
            
            # Submit order
            place_start = time.time()
            order = self.trading_client.submit_order(order_data=order_data)
            place_time = time.time() - place_start
            logger.info(f"✅ Order placed: {order.id} (place: {place_time:.3f}s)")
            
            # Wait for fill (check order status)
            fill_wait_start = time.time()
            fill_price = None
            filled_shares = None
            
            # Poll for order status
            import asyncio
            check_interval = 0.5
            max_wait_time = 30.0  # Default max wait time if no timeout deadline provided
            
            # Calculate actual max wait time based on timeout deadline
            remaining = time_left()
            if remaining is not None:
                # Use the smaller of: remaining timeout or default max wait time
                max_wait_time = min(remaining, max_wait_time)
                if max_wait_time <= 0:
                    raise TimeoutError("Trade timed out before waiting for fill")
            
            waited = 0.0
            
            while waited < max_wait_time:
                # Check timeout deadline on each iteration
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("Trade timed out while waiting for fill")
                
                order_status = self.trading_client.get_order_by_id(order.id)
                
                if order_status.status == "filled":
                    fill_price = float(order_status.filled_avg_price) if order_status.filled_avg_price else None
                    filled_shares = float(order_status.filled_qty) if order_status.filled_qty else quantity
                    break
                elif order_status.status in ["canceled", "expired", "rejected"]:
                    error_result = {
                        "success": False,
                        "error": f"Order {order_status.status}: {getattr(order_status, 'reject_reason', 'Unknown reason')}",
                        "session": "market_hours",
                        "order_type": "MARKET",
                        "instrument": "stock",
                    }
                    await self._publish_failed_event(trade_request, error_result["error"])
                    return error_result
                
                # Calculate sleep interval respecting timeout
                sleep_interval = check_interval
                if remaining is not None:
                    sleep_interval = min(check_interval, max(remaining, 0))
                
                if sleep_interval > 0:
                    await asyncio.sleep(sleep_interval)
                    waited += sleep_interval
                else:
                    # No time left, break out of loop
                    break
            
            fill_wait_time = time.time() - fill_wait_start
            
            if fill_price is None:
                error_result = {
                    "success": False,
                    "error": "Order did not fill within timeout period",
                    "session": "market_hours",
                    "order_type": "MARKET",
                    "instrument": "stock",
                }
                await self._publish_failed_event(trade_request, error_result["error"])
                return error_result
            
            # Calculate totals
            total_cost = fill_price * filled_shares if fill_price and filled_shares else None
            commission = 0.0  # Alpaca paper trading has no commission
            
            # Get NBBO snapshot for spread information
            nbbo_snapshot = await self.quote_fetcher.get_nbbo_snapshot(trade_request.ticker)
            spread_info = {}
            if nbbo_snapshot:
                spread_info = {
                    "bid": nbbo_snapshot.get("bid"),
                    "ask": nbbo_snapshot.get("ask"),
                    "spread": nbbo_snapshot.get("spread"),
                    "mid": nbbo_snapshot.get("mid"),
                }
            
            total_time = time.time() - total_start_time
            
            result = {
                "success": True,
                "shares": filled_shares,
                "fill_price": fill_price,
                "total_cost": total_cost,
                "commission": commission,
                "session": "market_hours",
                "order_type": "MARKET",
                "instrument": "stock",
                "spread_info": spread_info,  # Include spread information
                "timing_info": {
                    "session_detection": session_time,
                    "connection": connect_time,
                    "order_creation": order_create_time,
                    "order_placement": place_time,
                    "fill_wait": fill_wait_time,
                    "total": total_time,
                },
            }
            
            # Publish success event
            await self._publish_executed_event(trade_request, result, metadata=_local_metadata)
            
            logger.info(
                f"🎉 MARKET ORDER FILLED! Price: ${fill_price}, Shares: {filled_shares}, Total: ${total_cost:.2f}"
            )
            
            return result
            
        except TimeoutError as exc:
            logger.error(f"⏱️ Market hours trade timed out: {exc}")
            error_result = {
                "success": False,
                "error": f"Trade execution timed out: {str(exc)}",
                "session": "market_hours",
                "order_type": "MARKET",
                "instrument": "stock",
            }
            await self._publish_failed_event(trade_request, error_result["error"])
            return error_result
            
        except Exception as exc:
            logger.error(f"⏱️ Market hours trade execution failed: {exc}", exc_info=True)
            error_result = {
                "success": False,
                "error": str(exc),
                "session": "market_hours",
                "order_type": "MARKET",
                "instrument": "stock",
            }
            await self._publish_failed_event(trade_request, str(exc))
            return error_result
    
    async def _publish_executed_event(self, trade_request: TradeRequest, result: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
        """Publish trade executed event and send fast notification."""
        infra_trade_request = build_infrastructure_trade_request_data(trade_request)

        # Extract spread_info for notifications
        spread_info = result.get("spread_info", {})

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
            timing_info=result.get("timing_info", {}),
            spread_info=spread_info,
            executed_at=datetime.now(),
            source="brokerage",
            metadata=metadata if metadata is not None else getattr(self, '_current_metadata', None)
        )
        
        await self.event_bus.publish("TradeExecuted", event.model_dump())
        logger.debug("Published TradeExecuted event", ticker=trade_request.ticker)
    
    async def _publish_failed_event(self, trade_request: TradeRequest, error: str) -> None:
        """Publish trade failed event."""
        infra_trade_request = build_infrastructure_trade_request_data(trade_request)
        
        event = TradeFailedEvent(
            trade_request=infra_trade_request,
            error=error,
            failed_at=datetime.now(),
            source="brokerage"
        )
        
        await self.event_bus.publish("TradeFailed", event.model_dump())
        logger.debug("Published TradeFailed event", ticker=trade_request.ticker, error=error)
