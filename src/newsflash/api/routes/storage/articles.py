"""
Article query routes.
"""
from fastapi import APIRouter, HTTPException
from ....utils.logging_config import get_logger
from ...dependencies import StorageQueryServiceDep
from ...models.responses import RecentArticlesResponse, ArchivedArticlesResponse, ArchiveStatsResponse

logger = get_logger(__name__)

router = APIRouter(tags=["articles"])


@router.get("/recent-articles", response_model=RecentArticlesResponse)
async def get_recent_articles(storage_service: StorageQueryServiceDep, hours: int = 1):
    """Get recent articles from storage."""
    try:
        # Service returns typed StoredArticle models
        stored_articles = await storage_service.get_recent_articles(hours)
        
        # Convert domain models to dictionaries for JSON serialization
        articles = [article.model_dump() for article in stored_articles]
        
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
async def get_archived_articles(storage_service: StorageQueryServiceDep, date: str):
    """Get archived articles for a specific date (YYYY-MM-DD format)."""
    try:
        # Service returns typed StoredArticle models
        stored_articles = await storage_service.get_archived_articles(date)
        
        # Convert domain models to dictionaries for JSON serialization
        articles = [article.model_dump() for article in stored_articles]
        
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
async def get_archive_stats(storage_service: StorageQueryServiceDep):
    """Get statistics about archived articles."""
    try:
        # Service returns typed ArchiveStatistics model
        archive_stats = await storage_service.get_archive_stats()
        
        # Convert domain model to dictionary for JSON serialization
        stats = archive_stats.model_dump()
        
        return ArchiveStatsResponse(stats=stats)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get archive stats", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get archive stats: {str(e)}")

