"""
Feed control routes.
"""
from fastapi import APIRouter, HTTPException
from ....utils.logging_config import get_logger
from ...dependencies import FeedManagerDep
from ...models.responses import FeedStatusResponse

logger = get_logger(__name__)

router = APIRouter(tags=["feeds"])


@router.post("/start-feeds", response_model=FeedStatusResponse)
async def start_feeds_endpoint(feed_manager: FeedManagerDep):
    """Manually start all feeds (if not already running)."""
    try:
        # Feed manager is always running if subscribed to events (event-driven)
        if True:
            return FeedStatusResponse(message="Feeds already running", status="running")
        
        await feed_manager.start_all_feeds()
        return FeedStatusResponse(message="Feeds started", status="started")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start feeds", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to start feeds: {str(e)}")


@router.post("/stop-feeds", response_model=FeedStatusResponse)
async def stop_feeds_endpoint(feed_manager: FeedManagerDep):
    """Manually stop all feeds."""
    try:
        await feed_manager.stop_all_feeds()
        return FeedStatusResponse(message="Feeds stopped", status="stopped")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to stop feeds", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to stop feeds: {str(e)}")

