"""
Price calculation logic for extended hours trading.

Stateless helper functions - all state is passed as parameters.
"""
import math
from typing import Optional, Dict, Any

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest

logger = get_logger(__name__)


async def get_trade_price(
    quote_fetcher,
    ib,
    contract,
    trade_request: TradeRequest,
    timeout_deadline: Optional[float],
) -> tuple[Optional[float], bool, Dict[str, Any]]:
    """
    Get real-time price for trade, with fallback logic.
    
    Stateless function - all state passed as parameters.
    
    Args:
        quote_fetcher: Quote fetcher instance
        ib: IBKR connection
        contract: Stock contract
        trade_request: Trade request
        timeout_deadline: Optional timeout deadline
        
    Returns:
        Tuple of (price, price_fallback_used, quote_snapshot)
    """
    # Get real-time price
    current_price = await quote_fetcher.get_realtime_price(ib, contract, timeout_deadline)
    quote_snapshot = quote_fetcher.get_last_quote_snapshot(contract.symbol) or {}
    price_fallback_used = False
    
    # Handle price fallback
    if not current_price:
        fallback_price = None
        if trade_request.shares and trade_request.amount_usd:
            fallback_price = trade_request.amount_usd / max(trade_request.shares, 1)
        
        if fallback_price and fallback_price > 0:
            logger.warning(
                "⚠️ Falling back to estimated price for extended-hours trade",
                ticker=contract.symbol,
                fallback_price=fallback_price,
            )
            current_price = fallback_price
            price_fallback_used = True
    
    return current_price, price_fallback_used, quote_snapshot


def calculate_trade_quantity(
    trade_request: TradeRequest,
    current_price: float,
    leverage: float = 2.0,
) -> tuple[int, float]:
    """
    Calculate share quantity for trade with leverage.
    
    Stateless function - all state passed as parameters.
    
    Args:
        trade_request: Trade request
        current_price: Current stock price
        leverage: Leverage multiplier (default 2.0)
        
    Returns:
        Tuple of (quantity, projected_notional)
    """
    quantity = trade_request.shares
    
    # Calculate quantity if not provided (with leverage)
    if quantity is None:
        base_notional = trade_request.amount_usd or current_price
        target_notional = max(base_notional * leverage, current_price)
        raw_quantity = target_notional / current_price
        quantity = max(1, int(math.ceil(raw_quantity - 1e-9)))
        
        logger.info(
            "Calculated share quantity for extended-hours trade",
            quantity=quantity,
            requested_notional=base_notional,
            leverage=leverage,
            target_notional=target_notional,
            price=current_price,
            raw_quantity=raw_quantity,
        )
    
    projected_notional = quantity * current_price
    return quantity, projected_notional

