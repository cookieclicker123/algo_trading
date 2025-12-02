"""
FastAPI dependency injection functions.

Provides reusable dependencies for route handlers.
"""
from typing import Annotated
from fastapi import Depends, Request, HTTPException

from ..services.service_initialization import Services
from ..utils.logging_config import get_logger

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

