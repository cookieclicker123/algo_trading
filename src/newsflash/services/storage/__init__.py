"""
Storage services - provide storage operations for use cases and other services.

Services in this package subscribe to domain events and provide focused storage operations.
"""
from .query_service import StorageQueryService

__all__ = ["StorageQueryService"]

