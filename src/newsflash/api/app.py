"""
FastAPI application for the multi-source news trading system.
"""
from fastapi import FastAPI, HTTPException
import asyncio

from ..services.feed_manager import FeedManager
from ..services.article_processor import ArticleProcessor
from ..models.base_models import NewsSource
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Global instances (will be initialized in main)
feed_manager: FeedManager = None
article_processor: ArticleProcessor = None


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="NewsFlash Trading System",
        description="Real-time multi-source news polling and processing system",
        version="2.0.0"
    )
    
    @app.on_event("startup")
    async def startup_event():
        """Initialize services on startup."""
        global feed_manager, article_processor
        
        logger.info("Starting NewsFlash API server with multi-source feeds")
        
        # Initialize services
        article_processor = ArticleProcessor()
        
        # Pass article processor to FeedManager to avoid duplication
        feed_manager = FeedManager(article_processor=article_processor)
        
        # Start all feeds in background
        asyncio.create_task(start_all_feeds())
    
    @app.on_event("shutdown")
    async def shutdown_event():
        """Cleanup on shutdown."""
        global feed_manager
        
        logger.info("Shutting down NewsFlash API server")
        
        if feed_manager:
            await feed_manager.stop_all_feeds()
    
    @app.get("/")
    async def root():
        """Root endpoint."""
        return {
            "service": "NewsFlash Trading System",
            "status": "running",
            "version": "2.0.0",
            "sources": [source.value for source in feed_manager.get_available_sources()] if feed_manager else []
        }
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        try:
            if not feed_manager:
                raise HTTPException(status_code=503, detail="Feed manager not initialized")
            
            # Check health of all sources
            source_health = {}
            overall_healthy = True
            
            for source in feed_manager.get_available_sources():
                is_healthy = feed_manager.is_source_healthy(source)
                source_health[source.value] = is_healthy
                if not is_healthy:
                    overall_healthy = False
            
            return {
                "status": "healthy" if overall_healthy else "degraded",
                "sources": source_health,
                "available_sources": [source.value for source in feed_manager.get_available_sources()]
            }
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            raise HTTPException(status_code=503, detail=f"Health check failed: {str(e)}")
    
    @app.get("/stats")
    async def get_stats():
        """Get system statistics."""
        try:
            if not feed_manager or not article_processor:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            overall_stats = feed_manager.get_overall_stats()
            processor_stats = article_processor.get_stats()
            
            return {
                "overall": overall_stats.dict(),
                "processor": processor_stats,
                "service_status": "running"
            }
        except Exception as e:
            logger.error("Failed to get stats", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")
    
    @app.get("/recent-articles")
    async def get_recent_articles(hours: int = 1, source: str = None):
        """Get recent articles from storage."""
        try:
            if not feed_manager:
                raise HTTPException(status_code=503, detail="Feed manager not initialized")
            
            # Filter by source if specified
            source_filter = None
            if source:
                try:
                    source_filter = NewsSource(source)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid source: {source}")
            
            articles = await feed_manager.get_recent_articles(hours, source_filter)
            
            return {
                "articles": articles,
                "count": len(articles),
                "hours": hours,
                "source_filter": source
            }
        except Exception as e:
            logger.error("Failed to get recent articles", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to get recent articles: {str(e)}")
    
    @app.get("/archived-articles/{date}")
    async def get_archived_articles(date: str, source: str = None):
        """Get archived articles for a specific date (YYYY-MM-DD format)."""
        try:
            if not feed_manager:
                raise HTTPException(status_code=503, detail="Feed manager not initialized")
            
            # Filter by source if specified
            source_filter = None
            if source:
                try:
                    source_filter = NewsSource(source)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid source: {source}")
            
            articles = await feed_manager.get_archived_articles(date, source_filter)
            
            return {
                "articles": articles,
                "count": len(articles),
                "date": date,
                "source_filter": source
            }
        except Exception as e:
            logger.error("Failed to get archived articles", error=str(e), date=date)
            raise HTTPException(status_code=500, detail=f"Failed to get archived articles: {str(e)}")
    
    @app.get("/archive-stats")
    async def get_archive_stats():
        """Get statistics about archived articles."""
        try:
            if not feed_manager:
                raise HTTPException(status_code=503, detail="Feed manager not initialized")
            
            stats = await feed_manager.get_archive_stats()
            return stats
        except Exception as e:
            logger.error("Failed to get archive stats", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to get archive stats: {str(e)}")
    
    @app.post("/start-feeds")
    async def start_feeds_endpoint():
        """Manually start all feeds (if not already running)."""
        try:
            if not feed_manager:
                raise HTTPException(status_code=503, detail="Feed manager not initialized")
            
            if feed_manager.is_running:
                return {"message": "Feeds already running", "status": "running"}
            
            await feed_manager.start_all_feeds()
            return {"message": "Feeds started", "status": "started"}
            
        except Exception as e:
            logger.error("Failed to start feeds", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to start feeds: {str(e)}")
    
    @app.post("/stop-feeds")
    async def stop_feeds_endpoint():
        """Manually stop all feeds."""
        try:
            if not feed_manager:
                raise HTTPException(status_code=503, detail="Feed manager not initialized")
            
            await feed_manager.stop_all_feeds()
            return {"message": "Feeds stopped", "status": "stopped"}
            
        except Exception as e:
            logger.error("Failed to stop feeds", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to stop feeds: {str(e)}")
    
    return app


async def start_all_feeds():
    """Start all news feeds."""
    global feed_manager
    
    if feed_manager:
        try:
            await feed_manager.start_all_feeds()
                
        except asyncio.CancelledError:
            logger.info("Feed manager task cancelled")
        except Exception as e:
            logger.error("Error in feed manager task", error=str(e))
