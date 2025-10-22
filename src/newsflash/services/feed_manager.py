"""
Unified feed manager for handling Benzinga news source.
"""
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..utils.logging_config import get_logger
from ..models.base_models import StandardizedArticle, NewsSource
from ..services.news_poller import NewsPoller
from ..services.article_processor import ArticleProcessor

logger = get_logger(__name__)


class FeedManager:
    """Manages Benzinga news feed and coordinates article processing."""
    
    def __init__(self, article_processor: Optional[ArticleProcessor] = None):
        """Initialize the feed manager."""
        self.processors: Dict[NewsSource, Any] = {}
        self.is_running = False
        self.stats = {
            "total_articles": 0,
            "last_article_time": None,
            "last_error": None
        }
        
        # Use provided article processor or create new one
        if article_processor:
            self.article_processor = article_processor
        else:
            self.article_processor = ArticleProcessor()
        
        # Initialize source processors
        self._initialize_processors()
        
        logger.info(
            "FeedManager initialized with processors", 
            sources=list(self.processors.keys()),
            telegram_enabled_1=getattr(self.article_processor.telegram, 'enabled_1', False),
            telegram_enabled_2=getattr(self.article_processor.telegram, 'enabled_2', False)
        )
    
    def _initialize_processors(self):
        """Initialize processors for Benzinga news source."""
        try:
            # Benzinga (HTTP polling)
            self.processors[NewsSource.BENZINGA] = NewsPoller(
                article_processor=self.article_processor
            )
            logger.info("Benzinga processor initialized")
            
        except Exception as e:
            logger.error("Failed to initialize Benzinga processor", error=str(e))
    
    async def start_all_feeds(self):
        """Start Benzinga news feed."""
        logger.info("Starting Benzinga news feed")
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
        
        # Start Benzinga polling (independent task)
        if NewsSource.BENZINGA in self.processors:
            benzinga_task = asyncio.create_task(
                self._start_benzinga_feed_with_error_handling()
            )
            logger.info("Benzinga feed task started")
        
        # Keep the main function running
        try:
            while self.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Feed manager main loop cancelled")
    
    async def _start_benzinga_feed_with_error_handling(self):
        """Start the Benzinga feed with independent error handling."""
        while self.is_running:
            try:
                logger.info("Starting Benzinga feed...")
                benzinga_processor = self.processors[NewsSource.BENZINGA]
                async with benzinga_processor:
                    await benzinga_processor.start()
                logger.info("Benzinga feed stopped normally")
                break
            except Exception as e:
                logger.error("Benzinga feed failed", error=str(e))
                if self.is_running:
                    logger.info("Restarting Benzinga feed in 30 seconds...")
                    await asyncio.sleep(30)
                else:
                    break
    
    async def _start_benzinga_feed(self):
        """Start the Benzinga feed."""
        try:
            benzinga_processor = self.processors[NewsSource.BENZINGA]
            async with benzinga_processor:
                await benzinga_processor.start()
        except Exception as e:
            logger.error("Benzinga feed failed", error=str(e))
    
    async def stop_all_feeds(self):
        """Stop Benzinga news feed."""
        logger.info("Stopping Benzinga news feed")
        self.is_running = False
        
        # Stop Telegram notification service
        if self.article_processor.telegram.enabled:
            try:
                await self.article_processor.telegram.stop()
                logger.info("Telegram notification service stopped")
            except Exception as e:
                logger.error("Error stopping Telegram service", error=str(e))
        
        # Stop Benzinga
        if NewsSource.BENZINGA in self.processors:
            try:
                benzinga_processor = self.processors[NewsSource.BENZINGA]
                await benzinga_processor.stop_polling()
            except Exception as e:
                logger.error("Error stopping Benzinga feed", error=str(e))
    
    def _update_stats(self, stats_update: Dict[str, Any]):
        """Update feed statistics."""
        self.stats.update(stats_update)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current feed statistics."""
        return self.stats.copy()
    
    def is_healthy(self) -> bool:
        """Check if Benzinga feed is healthy."""
        if NewsSource.BENZINGA not in self.processors:
            return False
        
        try:
            processor = self.processors[NewsSource.BENZINGA]
            if hasattr(processor, 'get_stats'):
                stats = processor.get_stats()
                return stats.get('is_running', False)
            return False
        except Exception as e:
            logger.error(f"Error checking feed health", error=str(e))
            return False
    
    def get_available_sources(self) -> List[NewsSource]:
        """Get list of available sources (always Benzinga)."""
        return [NewsSource.BENZINGA]
    
    async def get_recent_articles(self, hours: int = 1, source: Optional[NewsSource] = None) -> List[StandardizedArticle]:
        """Get recent articles from storage."""
        return await self.article_processor.get_recent_articles(hours)
    
    async def get_archived_articles(self, date: str, source: Optional[NewsSource] = None) -> List[Dict[str, Any]]:
        """Get archived articles for a specific date."""
        return await self.article_processor.get_archived_articles(date)
    
    async def get_archive_stats(self) -> Dict[str, Any]:
        """Get archive statistics."""
        return await self.article_processor.get_archive_stats()
