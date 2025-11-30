"""
Price ladder building algorithms for extended hours trading.
Pure functions with no infrastructure dependencies.
"""
from typing import Optional
from ...config import settings


def calculate_ladder_base_price(
    action: str,
    ask: Optional[float],
    bid: Optional[float],
    current_price: float,
) -> float:
    """
    Calculate the base price for ladder building based on action and NBBO.
    
    Args:
        action: "BUY" or "SELL"
        ask: Current ask price
        bid: Current bid price
        current_price: Fallback current price
        
    Returns:
        Base price for ladder calculations
    """
    if action == "BUY":
        return ask if ask and ask > 0 else current_price
    else:
        return bid if bid and bid > 0 else current_price


def calculate_ladder_parameters(action: str) -> tuple[int, int, int, int, float, float, int]:
    """
    Get ladder configuration parameters.
    
    Args:
        action: "BUY" or "SELL" (determines sign of cents)
        
    Returns:
        Tuple of (initial_cents, early_step_cents, late_step_cents, switch_after, 
                  interval_early, interval_late, max_cents_from_start)
    """
    initial_cents = settings.LADDER_INITIAL_CENTS
    early_step = settings.LADDER_STEP_CENTS
    late_step = settings.LADDER_STEP_CENTS_AFTER
    switch_after = settings.LADDER_SWITCH_ATTEMPT
    interval_early = settings.LADDER_INTERVAL_MS / 1000.0
    interval_late = settings.LADDER_INTERVAL_MS_LATE / 1000.0
    max_cents_from_start = settings.LADDER_MAX_CENTS
    
    # Adjust signs for SELL orders
    if action == "SELL":
        initial_cents = -initial_cents
        early_step = -early_step
        late_step = -late_step
    
    return initial_cents, early_step, late_step, switch_after, interval_early, interval_late, max_cents_from_start


def calculate_limit_price(base_price: float, cents_offset: int) -> float:
    """
    Calculate limit price from base price and cents offset.
    
    Args:
        base_price: Base price to offset from
        cents_offset: Offset in cents (can be negative for SELL)
        
    Returns:
        Rounded limit price
    """
    return round(base_price + (cents_offset / 100.0), 2)


def should_switch_to_late_step(attempt_number: int, switch_after: int) -> bool:
    """
    Determine if we should switch to late step based on attempt number.
    
    Args:
        attempt_number: Current attempt number (1-indexed)
        switch_after: Attempt number to switch after
        
    Returns:
        True if should switch to late step
    """
    return attempt_number == switch_after

