"""
Market session detection utility.
Pure function with no infrastructure dependencies.
"""
from datetime import datetime
from typing import Tuple
import pytz

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


def get_market_session() -> Tuple[str, bool]:
    """
    Determine current market session based on Eastern Time.
    
    Returns:
        Tuple of (session_name, is_extended_hours)
        - session_name: "market_hours" | "premarket" | "postmarket" | "closed"
        - is_extended_hours: True if premarket/postmarket, False if market_hours/closed
    """
    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)
    
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    premarket_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    postmarket_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
    
    logger.info(f"🕐 Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    if market_open <= now_et < market_close:
        logger.info("📈 Currently in MARKET HOURS")
        return "market_hours", False
    if premarket_start <= now_et < market_open:
        logger.info("🌅 Currently in PREMARKET")
        return "premarket", True
    if market_close <= now_et < postmarket_end:
        logger.info("🌆 Currently in POSTMARKET")
        return "postmarket", True
    
    logger.info("🌙 Currently MARKET CLOSED")
    return "closed", True


def get_next_premarket_time() -> datetime:
    """
    Get the next premarket opening time.
    
    Returns:
        datetime object for next premarket start (4:00 AM ET)
    """
    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)
    
    # Today's premarket (4:00 AM)
    today_premarket = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    
    # If we're before today's premarket, return today's
    if now_et < today_premarket:
        return today_premarket
    
    # Otherwise, return tomorrow's premarket
    from datetime import timedelta
    tomorrow_premarket = today_premarket + timedelta(days=1)
    
    # Handle weekends - skip to Monday
    while tomorrow_premarket.weekday() >= 5:  # Saturday = 5, Sunday = 6
        tomorrow_premarket += timedelta(days=1)
    
    return tomorrow_premarket


def get_market_session_from_timestamp(timestamp: datetime) -> Tuple[str, bool]:
    """
    Determine market session from a specific timestamp (not current time).
    
    Args:
        timestamp: Datetime to determine session for (can be timezone-aware or naive)
        
    Returns:
        Tuple of (session_name, is_extended_hours)
        - session_name: "market_hours" | "premarket" | "postmarket" | "closed"
        - is_extended_hours: True if premarket/postmarket, False if market_hours/closed
    """
    et_tz = pytz.timezone("US/Eastern")
    # Convert to ET if timezone-aware, otherwise assume it's UTC and convert
    if timestamp.tzinfo:
        timestamp_et = timestamp.astimezone(et_tz)
    else:
        # Assume naive timestamps are UTC (standard practice in this app)
        timestamp_et = pytz.utc.localize(timestamp).astimezone(et_tz)
    
    market_open = timestamp_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = timestamp_et.replace(hour=16, minute=0, second=0, microsecond=0)
    premarket_start = timestamp_et.replace(hour=4, minute=0, second=0, microsecond=0)
    postmarket_end = timestamp_et.replace(hour=20, minute=0, second=0, microsecond=0)
    
    if market_open <= timestamp_et < market_close:
        return "market_hours", False
    if premarket_start <= timestamp_et < market_open:
        return "premarket", True
    if market_close <= timestamp_et < postmarket_end:
        return "postmarket", True
    
    return "closed", True


def seconds_until_next_premarket() -> float:
    """
    Calculate seconds until next premarket opening.

    Returns:
        Seconds until next premarket, or None if unable to calculate
    """
    try:
        next_premarket = get_next_premarket_time()
        et_tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(et_tz)
        delta = (next_premarket - now_et).total_seconds()
        return max(0.0, delta)
    except Exception as e:
        logger.error("Failed to calculate seconds until next premarket", error=str(e))
        return 0.0


def seconds_until_extended_hours_end() -> Tuple[float, str]:
    """
    Calculate seconds until current extended hours session ends.

    OVERNIGHT RISK: Positions must be exited before extended hours close,
    otherwise they're stuck until next session (overnight gap risk).

    Returns:
        Tuple of (seconds_remaining, session_name)
        - Premarket ends at 9:30 AM ET (market open)
        - Postmarket ends at 8:00 PM ET (market close)
        - Returns (0.0, "closed") if market closed or regular hours
    """
    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)

    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    premarket_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    postmarket_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)

    # Premarket: 4 AM - 9:30 AM ET
    if premarket_start <= now_et < market_open:
        delta = (market_open - now_et).total_seconds()
        return max(0.0, delta), "premarket"

    # Postmarket: 4 PM - 8 PM ET
    if market_close <= now_et < postmarket_end:
        delta = (postmarket_end - now_et).total_seconds()
        return max(0.0, delta), "postmarket"

    # Regular hours or closed - no extended hours end to worry about
    return 0.0, "closed"

