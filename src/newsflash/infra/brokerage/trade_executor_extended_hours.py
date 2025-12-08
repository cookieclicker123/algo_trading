"""
Trade executor for extended hours trading (stocks only, ladder strategy).
Pure infrastructure - executes trades and publishes events.
"""
import asyncio
import time
from typing import Optional, Dict, Any
from datetime import datetime

from ib_insync import IB, Stock

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest
from ...shared.event_bus import AsyncEventBus
from .events import TradeExecutedEvent, TradeFailedEvent
from .event_builders import build_infrastructure_trade_request_data
from .quote_fetcher import IBKRQuoteFetcher
from .price_calculator import get_trade_price, calculate_trade_quantity
from .order_executor import (
    calculate_ladder_base,
    extract_fill_details,
    get_ladder_parameters,
    place_ladder_order,
    wait_for_fill,
)
from ...utils.brokerage.ladder_algorithms import (
    calculate_limit_price,
    should_switch_to_late_step,
)

logger = get_logger(__name__)


class ExtendedHoursTradeExecutor:
    """
    Executes stock trades during extended hours using ladder limit order strategy.
    
    Responsibilities:
    - Execute ladder limit orders (stocks only)
    - Handle 2x leverage
    - Manage ladder progression
    - Publish trade events
    
    Does NOT:
    - Know about business logic
    - Send Telegram notifications
    """
    
    def __init__(self, event_bus: AsyncEventBus, quote_fetcher: IBKRQuoteFetcher):
        """
        Initialize extended hours trade executor.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            quote_fetcher: Quote fetcher instance for getting prices/NBBO
        """
        self.quote_fetcher = quote_fetcher
        self.event_bus = event_bus
        
        logger.info("ExtendedHoursTradeExecutor initialized")
    
    async def execute(
        self,
        ib: IB,
        contract: Stock,
        trade_request: TradeRequest,
        session: str,
        timing_info: Dict[str, float],
        timeout_deadline: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Execute a stock trade during extended hours using ladder strategy.
        
        Args:
            ib: IBKR connection instance
            contract: Stock contract
            trade_request: Trade request
            session: Trading session name (premarket/postmarket)
            timing_info: Timing information dictionary
            timeout_deadline: Optional timeout deadline
            
        Returns:
            Trade result dictionary (for backward compatibility, also publishes events)
        """
        total_start_time = time.time()
        session_time = timing_info.get("session_detection", 0.0)
        connect_time = timing_info.get("connection", 0.0)
        contract_time = timing_info.get("contract_creation", 0.0)
        projected_notional: Optional[float] = None
        price_fallback_used = False
        
        try:
            #TODO: Review: this is a common pattern in the codebase, could be moved to a utility file, or use existing logic which i know already exists for this in infra/brokerage. mostly we use dependenices which is good but much of this file could be smaller through using what we already have i think please reiew.
            def time_left() -> Optional[float]:
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()
            
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before price retrieval")
            
            # Get real-time price (using stateless helper)
            price_start = time.time()
            current_price, price_fallback_used, quote_snapshot = await get_trade_price(
                self.quote_fetcher, ib, contract, trade_request, timeout_deadline
            )
            price_time = time.time() - price_start
            logger.info(f"💰 Price retrieval: {price_time:.3f}s")
            
            if not current_price:
                error_result = {
                    "success": False,
                    "error": "Could not get real-time price",
                    "session": session,
                    "order_type": "LIMIT",
                    "instrument": "stock",
                    "instrument_details": {
                        "leverage": getattr(trade_request, "leverage", None),
                        "target_notional": trade_request.amount_usd,
                        "requested_notional": trade_request.amount_usd,
                        "projected_notional": 0.0,
                        "effective_notional": 0.0,
                        "nbbo": quote_snapshot,
                    },
                }
                await self._publish_failed_event(trade_request, error_result["error"])
                return error_result
            
            action = trade_request.action.upper()
            leverage = getattr(trade_request, "leverage", None) or 2.0  # Default 2x leverage
            
            # Calculate quantity (using stateless helper)
            quantity, projected_notional = calculate_trade_quantity(
                trade_request, current_price, leverage
            )
            
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before preparing ladder")
            
            # Get NBBO snapshot for ladder base price
            nbbo_snapshot = await self.quote_fetcher.get_nbbo_snapshot(ib, contract, timeout_deadline)
            nbbo_info = nbbo_snapshot or quote_snapshot
            
            # Extract bid/ask from snapshot
            bid = nbbo_info.get("bid") if isinstance(nbbo_info, dict) else None
            ask = nbbo_info.get("ask") if isinstance(nbbo_info, dict) else None
            
            # Calculate ladder parameters (using stateless helper)
            (
                initial_cents,
                early_step_cents,
                late_step_cents,
                switch_after,
                interval_early,
                interval_late,
                max_cents_from_start,
            ) = get_ladder_parameters(action)
            
            # Calculate base price (using stateless helper)
            base_price = calculate_ladder_base(action, ask, bid, current_price)
            
            if action == "BUY":
                current_cents = initial_cents
                step_cents = early_step_cents
            else:
                current_cents = -initial_cents
                step_cents = -early_step_cents
            
            wait_time = interval_early
            attempt_number = 1
            trading_start = time.time()
            
            # Ladder loop
            while abs(current_cents) <= abs(max_cents_from_start):
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("Trade timed out before ladder could fill")
                
                # Place order and wait for fill (using stateless helpers)
                limit_price = calculate_limit_price(base_price, current_cents)
                trade = await place_ladder_order(ib, contract, action, quantity, base_price, current_cents)
                fill_wait_start = time.time()
                
                # Wait for fill
                filled = await wait_for_fill(trade, wait_time, timeout_deadline)
                
                if filled:
                    fill_wait_time = time.time() - fill_wait_start
                    fill_details = extract_fill_details(trade, limit_price, quantity)
                    total_trading_time = time.time() - trading_start
                    total_time = time.time() - total_start_time
                    
                    logger.info(
                        f"🎉 ORDER FILLED after {attempt_number} attempt(s)! Price: ${fill_details['fill_price']}"
                    )
                    
                    result = {
                        "success": True,
                        "shares": fill_details["filled_shares"],
                        "fill_price": fill_details["fill_price"],
                        "total_cost": fill_details["fill_price"] * fill_details["filled_shares"],
                        "commission": 0.0,
                        "session": session,
                        "order_type": "LIMIT",
                        "timing_info": {
                            **timing_info,
                            "price_retrieval": price_time,
                            "trading_time": total_trading_time,
                            "total_time": total_time,
                            "fill_wait_time": fill_wait_time,
                            "attempts": attempt_number,
                        },
                        "limit_price_used": limit_price,
                        "instrument": "stock",
                        "instrument_details": {
                            "leverage": leverage,
                            "target_notional": trade_request.amount_usd,
                            "requested_notional": trade_request.amount_usd,
                            "projected_notional": projected_notional,
                            "effective_notional": fill_details["fill_price"] * fill_details["filled_shares"],
                            "nbbo": nbbo_info,
                            "fill_venue": fill_details["fill_venue"],
                            "used_price_fallback": price_fallback_used,
                        },
                    }
                    
                    await self._publish_executed_event(trade_request, result, session)
                    return result
                
                # Cancel order if not filled
                try:
                    ib.cancelOrder(trade.order)
                except Exception:
                    pass
                
                # Switch to late step if needed
                if should_switch_to_late_step(attempt_number, switch_after):
                    step_cents = late_step_cents if action == "BUY" else -late_step_cents
                    wait_time = interval_late
                
                # Move to next ladder step
                current_cents += step_cents
                attempt_number += 1
                
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("Trade timed out during ladder progression")
                
                sleep_interval = wait_time if remaining is None else min(wait_time, max(remaining, 0))
                if sleep_interval > 0:
                    await asyncio.sleep(sleep_interval)
            
            # Ladder failed
            total_time = time.time() - total_start_time
            logger.error(
                f"❌ LADDER FAILED - no fill within ${abs(max_cents_from_start) / 100:.2f} "
                f"{'above' if action == 'BUY' else 'below'}"
            )
            
            error_result = {
                "success": False,
                "error": "Ladder failed - no fill within configured range",
                "session": session,
                "order_type": "LIMIT",
                "timing_info": {
                    **timing_info,
                    "price_retrieval": price_time,
                    "total_time": total_time,
                    "attempts": attempt_number,
                },
                "instrument": "stock",
                "instrument_details": {
                    "leverage": leverage,
                    "target_notional": trade_request.amount_usd,
                    "requested_notional": trade_request.amount_usd,
                    "projected_notional": projected_notional,
                    "effective_notional": 0.0,
                    "nbbo": nbbo_info,
                    "fill_venue": None,
                    "used_price_fallback": price_fallback_used,
                },
            }
            
            await self._publish_failed_event(trade_request, error_result["error"])
            return error_result
        
        except TimeoutError:
            raise
        except Exception as exc:
            logger.error(f"❌ Extended hours trade failed: {exc}")
            error_result = {
                "success": False,
                "error": str(exc),
                "session": session,
                "order_type": "LIMIT",
                "instrument": "stock",
                "instrument_details": {
                    "leverage": getattr(trade_request, "leverage", None) or 2.0,
                    "target_notional": trade_request.amount_usd,
                    "requested_notional": trade_request.amount_usd,
                    "projected_notional": projected_notional if projected_notional is not None else 0.0,
                    "effective_notional": 0.0,
                    "nbbo": nbbo_info if 'nbbo_info' in locals() else {},
                },
            }
            await self._publish_failed_event(trade_request, error_result["error"])
            return error_result
    
    @staticmethod
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
    
    async def _publish_executed_event(
        self,
        trade_request: TradeRequest,
        result: Dict[str, Any],
        session: str
    ) -> None:
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
            session=session,
            order_type=result["order_type"],
            instrument=result["instrument"],
            instrument_details=result["instrument_details"],
            timing_info=result.get("timing_info", {}),
            limit_price_used=result.get("limit_price_used"),
            percentage_above_below=None,
            executed_at=datetime.now()
        )
        await self.event_bus.publish("TradeExecuted", event.model_dump())
        logger.debug("Published TradeExecuted event", ticker=trade_request.ticker, session=session)
    
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

