"""
Brokerage utility functions.
Pure utility functions with no infrastructure dependencies.

These utilities provide:
- Market session detection
- Ladder algorithm calculations
- NBBO formatting and validation

All functions are stateless and have no side effects.
"""

from .session_detector import (
    get_market_session,
    get_next_premarket_time,
    seconds_until_next_premarket,
)
from .ladder_algorithms import (
    calculate_ladder_base_price,
    calculate_ladder_parameters,
    calculate_limit_price,
    should_switch_to_late_step,
)
from .nbbo_formatters import build_nbbo_info

__all__ = [
    # Session detection
    "get_market_session",
    "get_next_premarket_time",
    "seconds_until_next_premarket",
    # Ladder algorithms
    "calculate_ladder_base_price",
    "calculate_ladder_parameters",
    "calculate_limit_price",
    "should_switch_to_late_step",
    # NBBO formatting
    "build_nbbo_info",
]
