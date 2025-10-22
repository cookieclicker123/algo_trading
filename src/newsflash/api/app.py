"""
FastAPI application for the news trading system.
Uses service container for proper dependency injection.
"""
from fastapi import FastAPI, HTTPException
import asyncio

from ..services.service_container import get_service_container, initialize_services
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="NewsFlash Trading System",
        description="Real-time news polling and processing system",
        version="2.0.0"
    )
    
    @app.on_event("startup")
    async def startup_event():
        """Initialize services on startup."""
        logger.info("Starting NewsFlash API server")
        
        try:
            # Initialize service container
            container = initialize_services()
            
            # Start all services
            await container.start_all_services()
            
            logger.info("API server startup completed successfully")
            
        except Exception as e:
            logger.error("Failed to start API server", error=str(e))
            raise
    
    @app.on_event("shutdown")
    async def shutdown_event():
        """Cleanup on shutdown."""
        logger.info("Shutting down NewsFlash API server")
        
        try:
            container = get_service_container()
            await container.stop_all_services()
            
            logger.info("API server shutdown completed")
            
        except Exception as e:
            logger.error("Error during API server shutdown", error=str(e))
    
    @app.get("/")
    async def root():
        """Root endpoint."""
        try:
            container = get_service_container()
            feed_manager = container.get_feed_manager()
            
            return {
                "service": "NewsFlash Trading System",
                "status": "running",
                "version": "2.0.0",
                "sources": ["benzinga"],  # Single source now
                "healthy": container.is_healthy()
            }
        except Exception as e:
            logger.error("Root endpoint error", error=str(e))
            raise HTTPException(status_code=503, detail="Service not available")
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        try:
            container = get_service_container()
            
            if not container.is_healthy():
                raise HTTPException(status_code=503, detail="Services unhealthy")
            
            return {
                "status": "healthy",
                "sources": {"benzinga": True},
                "available_sources": ["benzinga"]
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            raise HTTPException(status_code=503, detail=f"Health check failed: {str(e)}")
    
    @app.get("/stats")
    async def get_stats():
        """Get system statistics."""
        try:
            container = get_service_container()
            stats = container.get_stats()
            
            return {
                "stats": stats,
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
