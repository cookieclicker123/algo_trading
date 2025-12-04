"""
Connection helper functions for IBKR connection manager.

Stateless helper functions - all state is passed as parameters.
"""
from datetime import datetime, timedelta
from typing import Optional

import pytz

from ...utils.logging_config import get_logger
from ...config import settings

logger = get_logger(__name__)


def calculate_daily_restart_window() -> Optional[datetime]:
    """
    Calculate next connection time if we're in the daily restart window.
    
    Stateless function - all state passed as parameters.
    
    Returns:
        Next connect time if in restart window, None otherwise
    """
    try:
        local_tz = pytz.timezone('US/Eastern')
        now_local = datetime.now(local_tz)
        hh, mm = [int(x) for x in settings.IBKR_DAILY_RESTART_TIME.split(":", 1)]
        restart_today = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        
        # Choose the most recent restart reference
        if now_local < restart_today - timedelta(hours=12):
            restart_today = restart_today - timedelta(days=1)
        
        # Window: within +/- 5 minutes of configured restart
        window_start = restart_today - timedelta(minutes=5)
        window_end = restart_today + timedelta(minutes=5)
        
        if window_start <= now_local <= window_end:
            planned = (restart_today + timedelta(minutes=2)).astimezone(local_tz)
            # Convert to naive local time for comparison
            next_connect_time = datetime.now() + (planned - now_local)
            logger.info(
                f"⏳ Delaying reconnect until {planned.strftime('%I:%M %p %Z')} (2 min after daily restart)"
            )
            return next_connect_time
        
        return None
    except Exception as e:
        logger.debug("Could not schedule delayed reconnect window", error=str(e))
        return None


def should_delay_reconnection(next_connect_time: Optional[datetime]) -> bool:
    """
    Check if reconnection should be delayed.
    
    Stateless function - all state passed as parameters.
    
    Args:
        next_connect_time: Optional next connect time from daily restart window
        
    Returns:
        True if reconnection should be delayed
    """
    if next_connect_time is None:
        return False
    
    now = datetime.now()
    return now < next_connect_time

