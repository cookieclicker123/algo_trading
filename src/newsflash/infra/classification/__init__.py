"""
Classification infrastructure microservice.

Handles external Groq API dependency and publishes infrastructure events.
"""
from .service import ClassificationInfrastructureService

__all__ = ["ClassificationInfrastructureService"]

