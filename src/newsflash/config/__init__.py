"""Configuration management for the news trading system."""

from .settings import get_api_key, get_polling_config, API_BASE_URL

__all__ = ["get_api_key", "get_polling_config", "API_BASE_URL"]
