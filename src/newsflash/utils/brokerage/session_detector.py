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

