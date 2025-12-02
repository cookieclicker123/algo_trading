"""
Article query routes.
"""
from fastapi import APIRouter, HTTPException
from ...utils.logging_config import get_logger
from ..dependencies import ServicesDep
from ..models.responses import RecentArticlesResponse, ArchivedArticlesResponse, ArchiveStatsResponse

logger = get_logger(__name__)

router = APIRouter(tags=["articles"])


@router.get("/recent-articles", response_model=RecentArticlesResponse)
async def get_recent_articles(services: ServicesDep, hours: int = 1):
    """Get recent articles from storage."""
    try:
        if not services.storage_query_service:
            raise HTTPException(status_code=503, detail="Storage query service not available")
        
        articles = await services.storage_query_service.get_recent_articles(hours)
        
        return RecentArticlesResponse(
            articles=articles,
            count=len(articles),
            hours=hours,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get recent articles", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get recent articles: {str(e)}")


@router.get("/archived-articles/{date}", response_model=ArchivedArticlesResponse)
async def get_archived_articles(services: ServicesDep, date: str):
    """Get archived articles for a specific date (YYYY-MM-DD format)."""
    try:
        if not services.storage_query_service:
            raise HTTPException(status_code=503, detail="Storage query service not available")
        
        articles = await services.storage_query_service.get_archived_articles(date)
        
        return ArchivedArticlesResponse(
            articles=articles,
            count=len(articles),
            date=date,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get archived articles", error=str(e), date=date)
        raise HTTPException(status_code=500, detail=f"Failed to get archived articles: {str(e)}")


@router.get("/archive-stats", response_model=ArchiveStatsResponse)
async def get_archive_stats(services: ServicesDep):
    """Get statistics about archived articles."""
    try:
        if not services.storage_query_service:
            raise HTTPException(status_code=503, detail="Storage query service not available")
        
        stats = await services.storage_query_service.get_archive_stats()
        return ArchiveStatsResponse(stats=stats)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get archive stats", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get archive stats: {str(e)}")

