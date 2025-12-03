"""
Dependency injection containers for the application.

This module provides containers that manage dependency injection for all microservices.
"""
from .application import ApplicationContainer
from .configuration import ConfigurationContainer
from .shared import SharedContainer

__all__ = [
    "ApplicationContainer",
    "ConfigurationContainer",
    "SharedContainer",
]

