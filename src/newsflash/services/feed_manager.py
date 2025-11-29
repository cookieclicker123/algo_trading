"""
Unified feed manager for handling Benzinga WebSocket news source.
"""
import asyncio
from typing import Dict, Any, Optional

from ..utils.logging_config import get_logger
from ..models.base_models import NewsSource
from ..services.benzinga_websocket_service import BenzingaWebSocketService
from ..services.article_processor import ArticleProcessor

logger = get_logger(__name__)


class FeedManager:
    """Manages Benzinga WebSocket news feed and coordinates article processing."""
    
    def __init__(self, article_processor: Optional[ArticleProcessor] = None, benzinga_token: Optional[str] = None):
        """Initialize the feed manager."""
        self.processors: Dict[NewsSource, Any] = {}
        self.is_running = False
        self.benzinga_token = benzinga_token
        
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
            sources=list[NewsSource](self.processors.keys()),
            websocket_enabled=self.benzinga_token is not None,
            telegram_enabled_1=getattr(self.article_processor.telegram, 'enabled_1', False),
            telegram_enabled_2=getattr(self.article_processor.telegram, 'enabled_2', False)
        )
    
    def _initialize_processors(self):
        """Initialize processor for Benzinga WebSocket feed."""
        try:
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
            logger.error("Failed to initialize Benzinga WebSocket processor", error=str(e))
    
    async def start_all_feeds(self):
        """Start Benzinga WebSocket news feed."""
        logger.info("Starting Benzinga WebSocket feed", sources=list(self.processors.keys()))
        self.is_running = True
        
        # Start Telegram notification queue processor (if enabled)
        telegram_enabled = (getattr(self.article_processor.telegram, 'enabled_1', False) or 
                           getattr(self.article_processor.telegram, 'enabled_2', False))
        if telegram_enabled and not self.article_processor.telegram.test_mode:
            asyncio.create_task(
                self.article_processor.telegram.start()
            )
            logger.info("Telegram notification service started")
        
        # Start all feed tasks
        feed_tasks = []
        
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
    
    async def stop_all_feeds(self):
        """Stop Benzinga WebSocket feed."""
        logger.info("Stopping Benzinga WebSocket feed")
        self.is_running = False
        
        # Stop Telegram notification service
        if self.article_processor.telegram.enabled:
            try:
                await self.article_processor.telegram.stop()
                logger.info("Telegram notification service stopped")
            except Exception as e:
                logger.error("Error stopping Telegram service", error=str(e))
        
        # Stop WebSocket feed
        if NewsSource.BENZINGA_WEBSOCKET in self.processors:
            try:
                websocket_processor = self.processors[NewsSource.BENZINGA_WEBSOCKET]
                await websocket_processor.stop()
                logger.info("Benzinga WebSocket feed stopped")
            except Exception as e:
                logger.error("Error stopping Benzinga WebSocket feed", error=str(e))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current feed statistics."""
        return {}  # Stats tracking removed - will be redesigned
    
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
    
