"""
Type definitions for storage infrastructure.
"""
from typing import TypedDict


class StorageConfig(TypedDict):
    """Type definition for storage configuration."""
    tmp_dir: str
    articles_json_file: str
    rolling_window_hours: int
    article_fetch_timeout_seconds: float

