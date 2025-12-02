"""
FastAPI lifespan event handlers for service initialization and cleanup.

Replaces deprecated @app.on_event("startup") and @app.on_event("shutdown").
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from ..services.service_initialization import initialize_services, start_services, stop_services
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan context manager for FastAPI application.
    
    Handles:
    - Service initialization on startup
    - Service startup
    - Service shutdown on application exit
    - Proper cleanup of background tasks and connections
    
    This ensures graceful shutdown when:
    - Control+C is pressed
    - Server is stopped via signal
    - Application is terminated
    
    Args:
        app: FastAPI application instance
        
    Yields:
        None - application runs during yield
    """
    # Startup
    logger.info("Starting NewsFlash API server")
    
    try:
        # Initialize services (async for future database connections)
        services = await initialize_services()
        
        # Start all services
        await start_services(services)
        
        # Store services in app.state for access in endpoints
        app.state.services = services
        
        logger.info("API server startup completed successfully")
        
    except Exception as e:
        logger.error("Failed to start API server", error=str(e))
        raise
    
    # Application runs here
    yield
    
    # Shutdown
    logger.info("Shutting down NewsFlash API server")
    
    try:
        # Get services from app.state
        services = getattr(app.state, "services", None)
        
        if services:
            # Stop all services (this handles service-specific cleanup)
            await stop_services(services)
        
        # Cancel any remaining background tasks
        # This ensures all tasks are cleaned up even if stop_services missed some
        tasks = [task for task in asyncio.all_tasks() if not task.done()]
        if tasks:
            logger.info(f"Cancelling {len(tasks)} remaining background tasks")
            for task in tasks:
                # Don't cancel the current task (ourselves)
                if task != asyncio.current_task():
                    task.cancel()
            
            # Wait for all tasks to complete cancellation
            await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info("API server shutdown completed")
        
    except Exception as e:
        logger.error("Error during API server shutdown", error=str(e))
        # Don't raise - we want to ensure cleanup completes even if there are errors

