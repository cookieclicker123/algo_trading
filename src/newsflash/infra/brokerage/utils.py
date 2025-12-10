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
    
    return quantity, capital_required
