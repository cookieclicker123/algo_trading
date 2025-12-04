"""
Order execution logic for extended hours trading.

Stateless helper functions - all state is passed as parameters.
"""
import asyncio
from typing import Optional, Dict, Any

from ib_insync import IB, Stock, LimitOrder, Trade

from ...utils.logging_config import get_logger
from ...utils.brokerage.ladder_algorithms import (
    calculate_ladder_base_price,
    calculate_ladder_parameters,
    calculate_limit_price,
)

logger = get_logger(__name__)


async def place_ladder_order(
    ib: IB,
    contract: Stock,
    action: str,
    quantity: int,
    base_price: float,
    current_cents: float,
) -> Trade:
    """
    Place a ladder limit order.
    
    Stateless function - all state passed as parameters.
    
    Args:
        ib: IBKR connection
        contract: Stock contract
        action: Trade action (BUY/SELL)
        quantity: Share quantity
        base_price: Base price for ladder
        current_cents: Current cents offset
        
    Returns:
        Trade object
    """
    limit_price = calculate_limit_price(base_price, current_cents)
    order = LimitOrder(action, quantity, limit_price)
    order.outsideRth = True  # Extended hours
    order.tif = "IOC"  # Immediate or cancel
    
    return ib.placeOrder(contract, order)


async def wait_for_fill(
    trade: Trade,
    wait_time: float,
    timeout_deadline: Optional[float],
    max_checks: int = 10,
) -> bool:
    """
    Wait for order fill, checking periodically.
    
    Stateless function - all state passed as parameters.
    
    Args:
        trade: Trade object to check
        wait_time: Time to wait between checks
        timeout_deadline: Optional timeout deadline
        max_checks: Maximum number of checks
        
    Returns:
        True if filled, False otherwise
    """
    for _ in range(max_checks):
        if timeout_deadline is not None:
            remaining = timeout_deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
        
        sleep_interval = wait_time
        if timeout_deadline is not None:
            remaining = timeout_deadline - asyncio.get_event_loop().time()
            sleep_interval = min(wait_time, max(remaining, 0))
        
        if sleep_interval > 0:
            await asyncio.sleep(sleep_interval)
        
        if trade.isDone():
            return True
        
        if trade.orderStatus and trade.orderStatus.status in ["Cancelled", "Rejected"]:
            return False
    
    return False


def extract_fill_details(trade: Trade, limit_price: float, quantity: int) -> Dict[str, Any]:
    """
    Extract fill details from trade.
    
    Stateless function - all state passed as parameters.
    
    Args:
        trade: Trade object
        limit_price: Limit price used
        quantity: Requested quantity
        
    Returns:
        Dictionary with fill details
    """
    fill_price = trade.orderStatus.avgFillPrice or limit_price
    filled_shares = int(trade.orderStatus.filled or quantity)
    fill_venue = _extract_fill_venue(trade)
    
    return {
        "fill_price": fill_price,
        "filled_shares": filled_shares,
        "fill_venue": fill_venue,
    }


def _extract_fill_venue(trade: Trade) -> Optional[str]:
    """Extract fill venue from trade (helper function)."""
    if not trade.orderStatus:
        return None
    
    # Try to extract venue from order status
    status = trade.orderStatus
    if hasattr(status, "lastFillPrice") and status.lastFillPrice:
        # Venue might be in contract details or order status
        return getattr(status, "venue", None)
    
    return None


def calculate_ladder_base(
    action: str,
    ask: Optional[float],
    bid: Optional[float],
    current_price: float,
) -> float:
    """
    Calculate ladder base price.
    
    Stateless function - all state passed as parameters.
    
    Args:
        action: Trade action (BUY/SELL)
        ask: Ask price from NBBO
        bid: Bid price from NBBO
        current_price: Current market price
        
    Returns:
        Base price for ladder
    """
    return calculate_ladder_base_price(action, ask, bid, current_price)


def get_ladder_parameters(action: str) -> tuple:
    """
    Get ladder parameters for action.
    
    Stateless function - all state passed as parameters.
    
    Args:
        action: Trade action (BUY/SELL)
        
    Returns:
        Tuple of ladder parameters
    """
    return calculate_ladder_parameters(action)

