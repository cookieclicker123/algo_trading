"""
FastAPI application for the news trading system.
"""
from fastapi import FastAPI, HTTPException
from ..services.service_initialization import initialize_services, start_services, stop_services, get_stats, is_healthy
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Global services instance
_services = None


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
        global _services
        logger.info("Starting NewsFlash API server")
        
        try:
            # Initialize services
            _services = initialize_services()
            
            # Start all services
            await start_services(_services)
            
            logger.info("API server startup completed successfully")
            
        except Exception as e:
            logger.error("Failed to start API server", error=str(e))
            raise
    
    @app.on_event("shutdown")
    async def shutdown_event():
        """Cleanup on shutdown."""
        global _services
        logger.info("Shutting down NewsFlash API server")
        
        try:
            if _services:
                await stop_services(_services)
            
            logger.info("API server shutdown completed")
            
        except Exception as e:
            logger.error("Error during API server shutdown", error=str(e))
    
    @app.get("/")
    async def root():
        """Root endpoint."""
        global _services
        try:
            if not _services:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            return {
                "service": "NewsFlash Trading System",
                "status": "running",
                "version": "2.0.0",
                "sources": ["benzinga_websocket"],
                "healthy": is_healthy(_services)
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Root endpoint error", error=str(e))
            raise HTTPException(status_code=503, detail="Service not available")
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        global _services
        try:
            if not _services:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            if not is_healthy(_services):
                raise HTTPException(status_code=503, detail="Services unhealthy")
            
            return {
                "status": "healthy",
                "sources": {"benzinga_websocket": True},
                "available_sources": ["benzinga_websocket"]
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            raise HTTPException(status_code=503, detail=f"Health check failed: {str(e)}")
    
    @app.get("/stats")
    async def get_stats_endpoint():
        """Get system statistics."""
        global _services
        try:
            if not _services:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            stats = get_stats(_services)
            
            return {
                "stats": stats,
                "service_status": "running"
            }
            
        except Exception as e:
            logger.error("Failed to get stats", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")
    
    @app.get("/recent-articles")
    async def get_recent_articles(hours: int = 1):
        """Get recent articles from storage."""
        global _services
        try:
            if not _services or not _services.article_processor:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            articles = await _services.article_processor.get_recent_articles(hours)
            
            return {
                "articles": articles,
                "count": len(articles),
                "hours": hours,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to get recent articles", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to get recent articles: {str(e)}")
    
    @app.get("/archived-articles/{date}")
    async def get_archived_articles(date: str):
        """Get archived articles for a specific date (YYYY-MM-DD format)."""
        global _services
        try:
            if not _services or not _services.article_processor:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            articles = await _services.article_processor.get_archived_articles(date)
            
            return {
                "articles": articles,
                "count": len(articles),
                "date": date,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to get archived articles", error=str(e), date=date)
            raise HTTPException(status_code=500, detail=f"Failed to get archived articles: {str(e)}")
    
    @app.get("/archive-stats")
    async def get_archive_stats():
        """Get statistics about archived articles."""
        global _services
        try:
            if not _services or not _services.article_processor:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            stats = await _services.article_processor.get_archive_stats()
            return stats
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to get archive stats", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to get archive stats: {str(e)}")
    
    @app.post("/start-feeds")
    async def start_feeds_endpoint():
        """Manually start all feeds (if not already running)."""
        global _services
        try:
            if not _services or not _services.feed_manager:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            if _services.feed_manager.is_running:
                return {"message": "Feeds already running", "status": "running"}
            
            await _services.feed_manager.start_all_feeds()
            return {"message": "Feeds started", "status": "started"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to start feeds", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to start feeds: {str(e)}")
    
    @app.post("/stop-feeds")
    async def stop_feeds_endpoint():
        """Manually stop all feeds."""
        global _services
        try:
            if not _services or not _services.feed_manager:
                raise HTTPException(status_code=503, detail="Services not initialized")
            
            await _services.feed_manager.stop_all_feeds()
            return {"message": "Feeds stopped", "status": "stopped"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to stop feeds", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to stop feeds: {str(e)}")
    
    return app
