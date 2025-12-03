"""
FastAPI dependency injection functions.

Provides reusable dependencies for route handlers.
Uses specific service dependencies for better type safety and testability.
"""
from typing import Annotated
from fastapi import Depends, Request, HTTPException

from ..services.service_initialization import Services
from ..utils.logging_config import get_logger

# Import service types
from ..services.storage.query_service import StorageQueryService
from ..services.websocket.feed_manager import FeedManager

logger = get_logger(__name__)


async def get_services(request: Request) -> Services:
    """
    Get services from app state.
    
    This dependency injects the Services instance into route handlers,
    eliminating the need for repetitive `getattr(request.app.state, "services", None)`
    calls in every endpoint.
    
    Args:
        request: FastAPI request object
        
    Returns:
        Services: Initialized services container
        
    Raises:
        HTTPException: If services are not initialized
    """
    services = getattr(request.app.state, "services", None)
    if not services:
        logger.error("Services not initialized - app may not have started properly")
        raise HTTPException(status_code=503, detail="Services not initialized")
    return services


# Type alias for cleaner endpoint signatures
ServicesDep = Annotated[Services, Depends(get_services)]


# Specific service dependencies (recommended - more type-safe and testable)

def get_storage_query_service(services: Services = Depends(get_services)) -> StorageQueryService:
    """
    Get storage query service.
    
    Args:
        services: Services container (injected via dependency)
        
    Returns:
        StorageQueryService: Storage query service instance
        
    Raises:
        HTTPException: If storage query service is not available
    """
    if not services.storage.query_service:
        raise HTTPException(status_code=503, detail="Storage query service not available")
    return services.storage.query_service


def get_feed_manager(services: Services = Depends(get_services)) -> FeedManager:
    """
    Get feed manager service.
    
    Args:
        services: Services container (injected via dependency)
        
    Returns:
        FeedManager: Feed manager service instance
        
    Raises:
        HTTPException: If feed manager is not available
    """
    if not services.websocket.feed_manager:
        raise HTTPException(status_code=503, detail="Feed manager not available")
    return services.websocket.feed_manager


# Type aliases for cleaner endpoint signatures
StorageQueryServiceDep = Annotated[StorageQueryService, Depends(get_storage_query_service)]
FeedManagerDep = Annotated[FeedManager, Depends(get_feed_manager)]
