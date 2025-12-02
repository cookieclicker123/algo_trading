"""
Feed control routes.
"""
from fastapi import APIRouter, HTTPException
from ...utils.logging_config import get_logger
from ..dependencies import ServicesDep
from ..models.responses import FeedStatusResponse

logger = get_logger(__name__)

router = APIRouter(tags=["feeds"])


@router.post("/start-feeds", response_model=FeedStatusResponse)
async def start_feeds_endpoint(services: ServicesDep):
    """Manually start all feeds (if not already running)."""
    try:
        if not services.feed_manager:
            raise HTTPException(status_code=503, detail="Feed manager not available")
        
        if services.feed_manager.is_running:
            return FeedStatusResponse(message="Feeds already running", status="running")
        
        await services.feed_manager.start_all_feeds()
        return FeedStatusResponse(message="Feeds started", status="started")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start feeds", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to start feeds: {str(e)}")


@router.post("/stop-feeds", response_model=FeedStatusResponse)
async def stop_feeds_endpoint(services: ServicesDep):
    """Manually stop all feeds."""
    try:
        if not services.feed_manager:
            raise HTTPException(status_code=503, detail="Feed manager not available")
        
        await services.feed_manager.stop_all_feeds()
        return FeedStatusResponse(message="Feeds stopped", status="stopped")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to stop feeds", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to stop feeds: {str(e)}")

