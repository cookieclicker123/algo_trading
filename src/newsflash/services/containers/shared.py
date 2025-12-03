"""
Shared container - provides shared dependencies used across microservices.

This container manages singleton dependencies that are shared across the entire application.
"""
from dependency_injector import containers, providers

from ...shared.event_bus import AsyncEventBus


class SharedContainer(containers.DeclarativeContainer):
    """
    Shared container for application-wide singletons.
    
    These dependencies are created once and shared across all microservices.
    """
    
    # Event bus is a singleton - created once, shared everywhere
    event_bus = providers.Singleton(AsyncEventBus)

