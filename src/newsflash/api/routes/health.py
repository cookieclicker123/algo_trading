"""
Health and system status routes.
"""
from fastapi import APIRouter, HTTPException
from ...services.service_initialization import get_stats, is_healthy
from ...utils.logging_config import get_logger
from ..dependencies import ServicesDep
from ..models.responses import RootResponse, HealthResponse, StatsResponse

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/", response_model=RootResponse)
async def root(services: ServicesDep):
    """Root endpoint."""
    try:
        return RootResponse(
            service="NewsFlash Trading System",
            status="running",
            version="2.0.0",
            sources=["benzinga_websocket"],
            healthy=is_healthy(services)
        )
    except Exception as e:
        logger.error("Root endpoint error", error=str(e))
        raise HTTPException(status_code=503, detail="Service not available")


@router.get("/health", response_model=HealthResponse)
async def health_check(services: ServicesDep):
    """Health check endpoint."""
    try:
        if not is_healthy(services):
            raise HTTPException(status_code=503, detail="Services unhealthy")
        
        return HealthResponse(
            status="healthy",
            sources={"benzinga_websocket": True},
            available_sources=["benzinga_websocket"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        raise HTTPException(status_code=503, detail=f"Health check failed: {str(e)}")


@router.get("/stats", response_model=StatsResponse)
async def get_stats_endpoint(services: ServicesDep):
    """Get system statistics."""
    try:
        stats = await get_stats(services)
        
        return StatsResponse(
            stats=stats,
            service_status="running"
        )
        
    except Exception as e:
        logger.error("Failed to get stats", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")

