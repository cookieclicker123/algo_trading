"""
Pure functions for health monitoring operations.

Service layer - pure functions with typed inputs and outputs.
"""
from typing import Dict, Any, Optional
from datetime import datetime

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


def format_connection_status_message(
    is_connected: bool,
    paper_trading: bool,
    reason: Optional[str] = None
) -> str:
    """
    Format brokerage connection status message for Telegram.
    
    Args:
        is_connected: Whether connection is active
        paper_trading: Whether in paper trading mode
        reason: Optional reason for status
        
    Returns:
        Formatted message string
    """
    emoji = "✅" if is_connected else "❌"
    mode = "Paper Trading" if paper_trading else "Live Trading"
    status = "connected and verified" if is_connected else "disconnected"
    
    message = f"{emoji} IB Gateway {status}\n\n"
    message += f"Mode: {mode}\n"
    if reason:
        message += f"Reason: {reason}\n"
    
    return message


def format_health_alert_message(
    feed_name: str,
    is_healthy: bool,
    reason: str,
    error: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    was_healthy: Optional[bool] = None,
    state_changed: bool = False
) -> str:
    """
    Format health alert message for Telegram.
    
    Args:
        feed_name: Name of the feed
        is_healthy: Current health status
        reason: Reason for status
        error: Optional error message
        stats: Optional statistics dictionary
        was_healthy: Previous health status
        state_changed: Whether state changed
        
    Returns:
        Formatted message string
    """
    emoji = "✅" if is_healthy else "⚠️"
    status_text = "HEALTHY" if is_healthy else "UNHEALTHY"
    
    if state_changed:
        if is_healthy:
            message = f"{emoji} *Feed Recovered: {feed_name.replace('_', ' ').title()}*\n\n"
            message += f"Feed is now {status_text}.\n\n"
        else:
            message = f"{emoji} *Feed Disconnected: {feed_name.replace('_', ' ').title()}*\n\n"
            message += f"Feed status: {status_text}.\n\n"
    else:
        message = f"{emoji} *Feed Health Alert: {feed_name.replace('_', ' ').title()}*\n\n"
        message += f"Status: {status_text}\n\n"
    
    message += f"*Reason:* {reason}\n\n"
    
    if error:
        message += f"*Error:* `{error}`\n\n"
    
    if stats:
        message += "*Statistics:*\n"
        for key, value in list(stats.items())[:5]:  # Limit to first 5 stats
            if value is not None:
                message += f"• {key}: `{value}`\n"
    
    message += f"\n_Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}_"
    
    return message


def should_send_health_alert(
    is_healthy: bool,
    was_healthy: Optional[bool],
    last_alert_time: Optional[datetime],
    state_changed: bool
) -> bool:
    """
    Determine if a health alert should be sent.
    
    Args:
        is_healthy: Current health status
        was_healthy: Previous health status (None if first check)
        last_alert_time: Time of last alert (None if never alerted)
        state_changed: Whether state changed from previous
        
    Returns:
        True if alert should be sent, False otherwise
    """
    # Alert if state changed
    if state_changed:
        return True
    
    # Alert if still unhealthy after initial alert (every 5 minutes)
    if not is_healthy:
        if last_alert_time:
            time_since_alert = (datetime.now() - last_alert_time).total_seconds()
            if time_since_alert >= 300:  # 5 minutes
                return True
        else:
            # First time unhealthy
            return True
    
    return False


def update_feed_state(
    previous_state: Dict[str, Any],
    is_healthy: bool,
    last_alert_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Update feed state tracking dictionary.
    
    Args:
        previous_state: Previous state dictionary
        is_healthy: Current health status
        last_alert_time: Time of last alert (optional)
        
    Returns:
        Updated state dictionary
    """
    if not is_healthy:
        previous_state["consecutive_failures"] = previous_state.get("consecutive_failures", 0) + 1
    else:
        previous_state["consecutive_failures"] = 0
    
    previous_state["healthy"] = is_healthy
    if last_alert_time:
        previous_state["last_alert_time"] = last_alert_time
    
    return previous_state


def check_state_changed(
    was_healthy: Optional[bool],
    is_healthy: bool
) -> bool:
    """
    Check if health state changed.
    
    Args:
        was_healthy: Previous health status (None if first check)
        is_healthy: Current health status
        
    Returns:
        True if state changed, False otherwise
    """
    return (was_healthy is not None) and (was_healthy != is_healthy)

