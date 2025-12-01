"""
Trade executor for market hours trading (stocks only).
Pure infrastructure - executes trades and publishes events.
"""
import asyncio
import time
from typing import Optional, Dict, Any
from datetime import datetime

from ib_insync import IB, Stock, MarketOrder

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest
from ...shared.event_bus import AsyncEventBus
from .events import TradeExecutedEvent, TradeFailedEvent
from .quote_fetcher import IBKRQuoteFetcher
from .event_builders import build_infrastructure_trade_request_data

logger = get_logger(__name__)


class MarketHoursTradeExecutor:
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
    
    def __init__(self, event_bus: AsyncEventBus, quote_fetcher: IBKRQuoteFetcher):
        """
        Initialize market hours trade executor.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            quote_fetcher: Quote fetcher instance for getting prices
        """
        self.quote_fetcher = quote_fetcher
        self.event_bus = event_bus
        
        logger.info("MarketHoursTradeExecutor initialized")
    
    async def execute(
        self,
        ib: IB,
        contract: Stock,
        trade_request: TradeRequest,
        timing_info: Dict[str, float],
        timeout_deadline: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Execute a stock trade during market hours.
        
        Args:
            ib: IBKR connection instance
            contract: Stock contract
            trade_request: Trade request
            timing_info: Timing information dictionary
            timeout_deadline: Optional timeout deadline
            
        Returns:
            Trade result dictionary (for backward compatibility, also publishes events)
        """
        total_start_time = time.time()
        session_time = timing_info.get("session_detection", 0.0)
        connect_time = timing_info.get("connection", 0.0)
        contract_time = timing_info.get("contract_creation", 0.0)
        
        try:
            def time_left() -> Optional[float]:
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()
            
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before order creation")
            
            action = trade_request.action.upper()
            quantity = trade_request.shares
            
            # Get quote snapshot
            quote_snapshot = self.quote_fetcher.get_last_quote_snapshot(contract.symbol)
            
            # Calculate quantity if not provided (with 2x leverage support)
            if quantity is None:
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("Trade timed out before price retrieval for quantity sizing")
                
                price_start = time.time()
                current_price = await self.quote_fetcher.get_realtime_price(ib, contract, timeout_deadline)
                price_time = time.time() - price_start
                logger.info(f"💰 Market hours price retrieval for sizing: {price_time:.3f}s")
                
                quote_snapshot = self.quote_fetcher.get_last_quote_snapshot(contract.symbol)
                
                if not current_price:
                    error_result = {
                        "success": False,
                        "error": "Could not retrieve price to size order",
                        "session": "market_hours",
                        "order_type": "MARKET",
                        "instrument": "stock",
                        "instrument_details": {
                            "leverage": getattr(trade_request, "leverage", None),
                            "target_notional": trade_request.amount_usd,
                            "nbbo": quote_snapshot,
                        },
                    }
                    await self._publish_failed_event(trade_request, error_result["error"])
                    return error_result
                
                # Calculate quantity with leverage support (2x by default)
                leverage = getattr(trade_request, "leverage", None) or 2.0
                base_notional = trade_request.amount_usd or current_price
                target_notional = max(base_notional * leverage, current_price)
                quantity = max(1, int(target_notional // current_price))
                
                logger.info(
                    "Calculated share quantity for market-hours trade",
                    quantity=quantity,
                    target_notional=target_notional,
                    leverage=leverage,
                    price=current_price,
                )
            else:
                logger.debug("Using explicit quantity for market-hours trade", quantity=quantity)
            
            # Create market order
            order_create_start = time.time()
            order = MarketOrder(action, quantity)
            order_create_time = time.time() - order_create_start
            logger.info(f"✅ Market order created: {order} (create: {order_create_time:.3f}s)")
            
            # Place order
            place_start = time.time()
            trade = ib.placeOrder(contract, order)
            place_time = time.time() - place_start
            logger.info(f"✅ Order placed: {trade} (place: {place_time:.3f}s)")
            
            # Wait for fill
            fill_wait_start = time.time()
            attempts = 0
            while True:
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    try:
                        ib.cancelOrder(order)
                    except Exception:
                        pass
                    raise TimeoutError("Trade timed out before order fill")
                
                sleep_interval = 0.5 if remaining is None else min(0.5, max(remaining, 0))
                if sleep_interval > 0:
                    await asyncio.sleep(sleep_interval)
                
                attempts += 1
                
                if trade.isDone():
                    fill_wait_time = time.time() - fill_wait_start
                    fill_price = trade.orderStatus.avgFillPrice or 0.0
                    filled_shares = int(trade.orderStatus.filled or quantity)
                    total_time = time.time() - total_start_time
                    fill_venue = self._extract_fill_venue(trade)
                    
                    logger.info(
                        f"🎉 ORDER FILLED! Price: ${fill_price} for {filled_shares} share(s)"
                    )
                    
                    result = {
                        "success": True,
                        "shares": filled_shares,
                        "fill_price": fill_price,
                        "total_cost": fill_price * filled_shares,
                        "commission": 0.0,  # IBKR reports this separately
                        "session": "market_hours",
                        "order_type": "MARKET",
                        "timing_info": {
                            **timing_info,
                            "order_creation": order_create_time,
                            "order_placement": place_time,
                            "fill_wait": fill_wait_time,
                            "total_time": total_time,
                            "attempts": attempts,
                        },
                        "instrument": "stock",
                        "instrument_details": {
                            "leverage": getattr(trade_request, "leverage", None),
                            "target_notional": trade_request.amount_usd,
                            "fill_venue": fill_venue,
                            "nbbo": quote_snapshot,
                        },
                    }
                    
                    await self._publish_executed_event(trade_request, result)
                    return result
                
                # Timeout after 120 attempts (60 seconds at 0.5s intervals)
                if remaining is None and attempts >= 120:
                    try:
                        ib.cancelOrder(order)
                    except Exception:
                        pass
                    break
            
            # Order timeout
            total_time = time.time() - total_start_time
            logger.warning("⚠️ ORDER TIMEOUT - Did not fill before timeout")
            
            error_result = {
                "success": False,
                "error": "Order timeout - did not fill before timeout",
                "session": "market_hours",
                "order_type": "MARKET",
                "timing_info": {
                    **timing_info,
                    "order_creation": order_create_time,
                    "order_placement": place_time,
                    "total_time": total_time,
                    "attempts": attempts,
                },
                "instrument": "stock",
                "instrument_details": {
                    "leverage": getattr(trade_request, "leverage", None),
                    "target_notional": trade_request.amount_usd,
                    "nbbo": quote_snapshot,
                },
            }
            
            await self._publish_failed_event(trade_request, error_result["error"])
            return error_result
        
        except TimeoutError:
            raise
        except Exception as exc:
            logger.error(f"❌ Market hours trade failed: {exc}")
            error_result = {
                "success": False,
                "error": str(exc),
                "session": "market_hours",
                "order_type": "MARKET",
                "instrument": "stock",
                "instrument_details": {
                    "leverage": getattr(trade_request, "leverage", None),
                    "target_notional": trade_request.amount_usd,
                    "nbbo": quote_snapshot,
                },
            }
            await self._publish_failed_event(trade_request, error_result["error"])
            return error_result
    
    @staticmethod
    def _extract_fill_venue(trade) -> Optional[str]:
        """Extract fill venue from trade."""
        try:
            fills = getattr(trade, "fills", None)
            if not fills:
                last_liquidity = getattr(trade.orderStatus, "lastLiquidity", None)
                return str(last_liquidity) if last_liquidity else None
            
            venues = {
                getattr(fill.execution, "exchange", "")
                for fill in fills
                if getattr(fill, "execution", None)
            }
            venues = {venue for venue in venues if venue}
            
            if not venues:
                last_liquidity = getattr(trade.orderStatus, "lastLiquidity", None)
                return str(last_liquidity) if last_liquidity else None
            
            return ",".join(sorted(venues))
        except Exception:
            return None
    
    async def _publish_executed_event(self, trade_request: TradeRequest, result: Dict[str, Any]) -> None:
        """Publish TradeExecutedEvent with typed infrastructure model."""
        # Convert shared TradeRequest to typed InfrastructureTradeRequestData
        infra_trade_request = build_infrastructure_trade_request_data(trade_request)
        
        event = TradeExecutedEvent(
            trade_request=infra_trade_request,  # ✅ Typed infrastructure model
            success=result["success"],
            shares=result["shares"],
            fill_price=result["fill_price"],
            total_cost=result["total_cost"],
            commission=result.get("commission", 0.0),
            session=result["session"],
            order_type=result["order_type"],
            instrument=result["instrument"],
            instrument_details=result["instrument_details"],
            timing_info=result.get("timing_info", {}),
            limit_price_used=None,
            percentage_above_below=None,
            executed_at=datetime.now()
        )
        await self.event_bus.publish("TradeExecuted", event.model_dump())
        logger.debug("Published TradeExecuted event", ticker=trade_request.ticker)
    
    async def _publish_failed_event(self, trade_request: TradeRequest, error: str) -> None:
        """Publish TradeFailedEvent with typed infrastructure model."""
        # Convert shared TradeRequest to typed InfrastructureTradeRequestData
        infra_trade_request = build_infrastructure_trade_request_data(trade_request)
        
        event = TradeFailedEvent(
            trade_request=infra_trade_request,  # ✅ Typed infrastructure model
            error=error,
            failed_at=datetime.now()
        )
        await self.event_bus.publish("TradeFailed", event.model_dump())
        logger.debug("Published TradeFailed event", ticker=trade_request.ticker, error=error)

