"""
Brokerage utility functions.
"""
from typing import Tuple

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest

logger = get_logger(__name__)


def calculate_trade_quantity(
    trade_request: TradeRequest,
    current_price: float,
    leverage: float = 2.0,
) -> Tuple[float, float]:
    """
    Calculate share quantity for trade with leverage (supports fractional shares).
    
    Args:
        trade_request: Trade request
        current_price: Current stock price
        leverage: Leverage multiplier (default 2.0)
        
    Returns:
        Tuple of (quantity, projected_notional) - quantity can be fractional
    """
    quantity = trade_request.shares
    
    # Calculate quantity if not provided (with leverage)
    if quantity is None:
        # Use the requested notional amount as base capital
        base_notional = float(trade_request.amount_usd)
        
        # Apply leverage to get buying power
        # With 2x leverage, buying power = base_notional × 2
        buying_power = base_notional * leverage
        
        # Calculate how many shares we can buy with this buying power
        # This allows fractional shares (Alpaca supports fractional)
        quantity = buying_power / current_price
        
        logger.info(
            "Calculated share quantity for trade with leverage (fractional allowed)",
            quantity=quantity,
            base_notional=base_notional,
            leverage=leverage,
            buying_power=buying_power,
            price=current_price,
        )
    else:
        # If explicit shares provided, use as-is (supports fractional)
        quantity = float(quantity)
    
    # Projected notional is based on actual shares we'll buy at current price
    projected_notional = quantity * current_price
    return quantity, projected_notional
