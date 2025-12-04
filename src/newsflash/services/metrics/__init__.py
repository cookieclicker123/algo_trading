"""
Metrics service - aggregates statistics from events.

This service subscribes to domain and infrastructure events and aggregates
statistics. Services no longer need to maintain their own stats dictionaries.
"""

from .metrics_service import MetricsService

__all__ = ["MetricsService"]

