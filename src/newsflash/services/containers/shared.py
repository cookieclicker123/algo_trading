"""
Shared container - provides shared dependencies used across microservices.

This container manages singleton dependencies that are shared across the entire application.
"""
from dependency_injector import containers, providers

from ...shared.event_bus import AsyncEventBus
from ..metrics import MetricsService


class SharedContainer(containers.DeclarativeContainer):
    """
    Shared container for application-wide singletons.
    
    These dependencies are created once and shared across all microservices.
    """
    
    # Event bus is a singleton - created once, shared everywhere
    event_bus = providers.Singleton(AsyncEventBus)
    
    # Metrics service is a singleton - aggregates statistics from events
    metrics_service = providers.Singleton(
        MetricsService,
        event_bus=event_bus,
    )

