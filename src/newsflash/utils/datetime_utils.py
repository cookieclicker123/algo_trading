"""
DateTime utility functions for consistent datetime handling across services.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional


def now_utc() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    """Get current UTC datetime as ISO format string."""
    return now_utc().isoformat()


def now_utc_iso_z() -> str:
    """Get current UTC datetime as ISO format string with Z suffix."""
    return now_utc().isoformat().replace('+00:00', 'Z')


def now_local() -> datetime:
    """Get current local datetime."""
    return datetime.now()


def to_iso_string(dt: Optional[datetime]) -> Optional[str]:
    """
    Convert datetime to ISO format string.
    
    Args:
        dt: Datetime object or None
        
    Returns:
        ISO format string or None
    """
    if dt is None:
        return None
    return dt.isoformat()


def serialize_datetime_for_json(value: any) -> any:
    """
    Serialize datetime objects in dicts/lists for JSON serialization.
    
    Recursively processes dicts and lists to convert datetime objects
    to ISO format strings.
    
    Args:
        value: Value that may contain datetime objects
        
    Returns:
        Value with datetime objects converted to ISO strings
    """
    if isinstance(value, datetime):
        return value.isoformat()
    elif isinstance(value, dict):
        return {k: serialize_datetime_for_json(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [serialize_datetime_for_json(item) for item in value]
    else:
        return value

