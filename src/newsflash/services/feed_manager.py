"""
Unified feed manager for handling Benzinga news source.
"""
import asyncio
from typing import Dict, Any, List, Optional

from ..utils.logging_config import get_logger
from ..models.base_models import StandardizedArticle, NewsSource
from ..services.news_poller import NewsPoller
from ..services.benzinga_websocket_service import BenzingaWebSocketService
from ..services.article_processor import ArticleProcessor

logger = get_logger(__name__)


class FeedManager:
    """Manages Benzinga news feeds (HTTP polling + WebSocket) and coordinates article processing."""
    
    def __init__(self, article_processor: Optional[ArticleProcessor] = None, benzinga_token: Optional[str] = None):
        """Initialize the feed manager."""
        self.processors: Dict[NewsSource, Any] = {}
        self.is_running = False
        self.benzinga_token = benzinga_token
        self.stats = {
            "total_articles": 0,
            "last_article_time": None,
            "last_error": None
        }
        
        # Use provided article processor or create new one
        if article_processor:
            self.article_processor = article_processor
        else:
            from .article_processor import get_article_processor
            self.article_processor = get_article_processor()
        
        # Initialize source processors
        self._initialize_processors()
        
        logger.info(
            "FeedManager initialized with processors", 
            sources=list(self.processors.keys()),
            websocket_enabled=self.benzinga_token is not None,
            telegram_enabled_1=getattr(self.article_processor.telegram, 'enabled_1', False),
            telegram_enabled_2=getattr(self.article_processor.telegram, 'enabled_2', False)
        )
    
    def _initialize_processors(self):
        """Initialize processors for Benzinga news sources."""
        try:
            # Benzinga (HTTP polling via Polygon)
            self.processors[NewsSource.BENZINGA] = NewsPoller(
                article_processor=self.article_processor
            )
            logger.info("Benzinga HTTP processor initialized")
            
            # Benzinga WebSocket (direct connection)
            if self.benzinga_token:
                self.processors[NewsSource.BENZINGA_WEBSOCKET] = BenzingaWebSocketService(
                    article_processor=self.article_processor,
                    token=self.benzinga_token
                )
                logger.info("Benzinga WebSocket processor initialized")
            else:
                logger.warning("Benzinga token not provided, WebSocket feed disabled")
            
        except Exception as e:
            logger.error("Failed to initialize Benzinga processors", error=str(e))
    
    async def start_all_feeds(self):
        """Start all Benzinga news feeds (HTTP + WebSocket)."""
        logger.info("Starting Benzinga news feeds", sources=list(self.processors.keys()))
        self.is_running = True
        
        # Start Telegram notification queue processor (if enabled)
        telegram_task = None
        telegram_enabled = (getattr(self.article_processor.telegram, 'enabled_1', False) or 
                           getattr(self.article_processor.telegram, 'enabled_2', False))
        if telegram_enabled and not self.article_processor.telegram.test_mode:
            telegram_task = asyncio.create_task(
                self.article_processor.telegram.start()
            )
            logger.info("Telegram notification service started")
        
        # Start all feed tasks
        feed_tasks = []
        
        # Start HTTP polling feed
        if NewsSource.BENZINGA in self.processors:
            http_task = asyncio.create_task(
                self._start_benzinga_http_feed_with_error_handling()
            )
            feed_tasks.append(http_task)
            logger.info("Benzinga HTTP feed task started")
        
        # Start WebSocket feed
        if NewsSource.BENZINGA_WEBSOCKET in self.processors:
            websocket_task = asyncio.create_task(
                self._start_benzinga_websocket_feed_with_error_handling()
            )
            feed_tasks.append(websocket_task)
            logger.info("Benzinga WebSocket feed task started")
            
            # Start WebSocket queue processor
            websocket_queue_task = asyncio.create_task(
                self._process_websocket_queue()
            )
            feed_tasks.append(websocket_queue_task)
            logger.info("WebSocket queue processor started")
        
        # Keep the main function running
        try:
            while self.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Feed manager main loop cancelled")
        finally:
            # Cancel all feed tasks
            for task in feed_tasks:
                if not task.done():
                    task.cancel()
    
    async def _start_benzinga_http_feed_with_error_handling(self):
        """Start the Benzinga HTTP feed with independent error handling."""
        while self.is_running:
            try:
                logger.info("Starting Benzinga HTTP feed...")
                benzinga_processor = self.processors[NewsSource.BENZINGA]
                async with benzinga_processor:
                    await benzinga_processor.start()
                logger.info("Benzinga HTTP feed stopped normally")
                break
            except Exception as e:
                logger.error("Benzinga HTTP feed failed", error=str(e))
                if self.is_running:
                    logger.info("Restarting Benzinga HTTP feed in 30 seconds...")
                    await asyncio.sleep(30)
                else:
                    break
    
    async def _start_benzinga_websocket_feed_with_error_handling(self):
        """Start the Benzinga WebSocket feed with independent error handling."""
        try:
            logger.info("Starting Benzinga WebSocket feed...")
            websocket_processor = self.processors[NewsSource.BENZINGA_WEBSOCKET]
            # Start is synchronous (runs in separate thread), just call it
            websocket_processor.start()
            logger.info("Benzinga WebSocket feed started in background thread")
            
            # Keep this task alive while the service is running
            while self.is_running and websocket_processor.is_running:
                await asyncio.sleep(5)
            
            logger.info("Benzinga WebSocket feed stopped normally")
        except Exception as e:
            logger.error("Benzinga WebSocket feed failed", error=str(e))
            logger.warning("WebSocket feed will NOT auto-restart to prevent 429 rate limits")
    
    async def _process_websocket_queue(self):
        """Process articles queued by the WebSocket service."""
        logger.info("Starting WebSocket queue processor")
        
        while self.is_running:
            try:
                # Check for queued articles every second
                websocket_processor = self.processors[NewsSource.BENZINGA_WEBSOCKET]
                queued_articles = websocket_processor.get_queued_articles()
                
                if queued_articles:
                    logger.info(f"Processing {len(queued_articles)} queued WebSocket articles")
                    for article in queued_articles:
                        try:
                            await self.article_processor.process_article(article)
                        except Exception as e:
                            logger.error("Failed to process WebSocket article", error=str(e))
                
                await asyncio.sleep(1)  # Check queue every second
                
            except Exception as e:
                logger.error("Error in WebSocket queue processor", error=str(e))
                await asyncio.sleep(1)
        
        logger.info("WebSocket queue processor stopped")
    
    async def _start_benzinga_feed(self):
        """Start the Benzinga feed."""
        try:
            benzinga_processor = self.processors[NewsSource.BENZINGA]
            async with benzinga_processor:
                await benzinga_processor.start()
        except Exception as e:
            logger.error("Benzinga feed failed", error=str(e))
    
    async def stop_all_feeds(self):
        """Stop all Benzinga news feeds."""
        logger.info("Stopping Benzinga news feeds")
        self.is_running = False
        
        # Stop Telegram notification service
        if self.article_processor.telegram.enabled:
            try:
                await self.article_processor.telegram.stop()
                logger.info("Telegram notification service stopped")
            except Exception as e:
                logger.error("Error stopping Telegram service", error=str(e))
        
        # Stop HTTP feed
        if NewsSource.BENZINGA in self.processors:
            try:
                benzinga_processor = self.processors[NewsSource.BENZINGA]
                await benzinga_processor.stop_polling()
                logger.info("Benzinga HTTP feed stopped")
            except Exception as e:
                logger.error("Error stopping Benzinga HTTP feed", error=str(e))
        
        # Stop WebSocket feed
        if NewsSource.BENZINGA_WEBSOCKET in self.processors:
            try:
                websocket_processor = self.processors[NewsSource.BENZINGA_WEBSOCKET]
                await websocket_processor.stop()
                logger.info("Benzinga WebSocket feed stopped")
            except Exception as e:
                logger.error("Error stopping Benzinga WebSocket feed", error=str(e))
    
    def _update_stats(self, stats_update: Dict[str, Any]):
        """Update feed statistics."""
        self.stats.update(stats_update)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current feed statistics."""
        return self.stats.copy()
    
    def is_healthy(self) -> bool:
        """Check if all feeds are healthy."""
        healthy_sources = 0
        total_sources = len(self.processors)
        
        for source, processor in self.processors.items():
            try:
                if hasattr(processor, 'get_stats'):
                    stats = processor.get_stats()
                    if stats.get('is_running', False):
                        healthy_sources += 1
                elif hasattr(processor, 'is_healthy'):
                    if processor.is_healthy():
                        healthy_sources += 1
            except Exception as e:
                logger.error(f"Error checking {source} feed health", error=str(e))
        
        return healthy_sources > 0  # At least one feed should be healthy
    
    def get_available_sources(self) -> List[NewsSource]:
        """Get list of available sources."""
        return list(self.processors.keys())
    
    async def get_recent_articles(self, hours: int = 1, source: Optional[NewsSource] = None) -> List[StandardizedArticle]:
        """Get recent articles from storage."""
        return await self.article_processor.get_recent_articles(hours)
    
    async def get_archived_articles(self, date: str, source: Optional[NewsSource] = None) -> List[Dict[str, Any]]:
        """Get archived articles for a specific date."""
        return await self.article_processor.get_archived_articles(date)
    
    async def get_archive_stats(self) -> Dict[str, Any]:
        """Get archive statistics."""
        return await self.article_processor.get_archive_stats()
