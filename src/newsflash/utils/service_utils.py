"""
Service utility functions for common service patterns.
"""
from typing import Dict, Any

from .datetime_utils import serialize_datetime_for_json


def serialize_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    """
    Serialize service statistics for JSON output.
    
    Converts datetime objects to ISO format strings recursively.
    
    Args:
        stats: Dictionary containing service statistics
        
    Returns:
        Dictionary with datetime objects serialized to ISO strings
    """
    return serialize_datetime_for_json(stats)


def build_stats_dict(**kwargs) -> Dict[str, Any]:
    """
    Build a statistics dictionary with automatic datetime serialization.
    
    Args:
        **kwargs: Key-value pairs for stats (datetimes will be serialized)
        
    Returns:
        Dictionary with datetime objects serialized
    """
    return serialize_datetime_for_json(kwargs)

