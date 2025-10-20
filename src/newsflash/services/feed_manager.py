"""
Unified feed manager for handling multiple news sources.
"""
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..utils.logging_config import get_logger
from ..models.base_models import StandardizedArticle, NewsSource, MultiSourceStats
from ..services.news_poller import NewsPoller
from ..services.finlight_service import FinlightWebSocketService
from ..services.article_processor import ArticleProcessor

logger = get_logger(__name__)


class FeedManager:
    """Manages multiple news feeds and coordinates article processing."""
    
    def __init__(self, article_processor: Optional[ArticleProcessor] = None):
        """Initialize the feed manager."""
        self.processors: Dict[NewsSource, Any] = {}
        self.is_running = False
        self.stats = MultiSourceStats()
        
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
        """Initialize processors for each news source."""
        try:
            # Benzinga (HTTP polling)
            self.processors[NewsSource.BENZINGA] = NewsPoller(
                article_processor=self.article_processor
            )
            logger.info("Benzinga processor initialized")
            
        except Exception as e:
            logger.error("Failed to initialize Benzinga processor", error=str(e))
        
        try:
            # Finlight (WebSocket)
            self.processors[NewsSource.FINLIGHT] = FinlightWebSocketService(
                article_callback=self._handle_finlight_article
            )
            logger.info("Finlight processor initialized")
            
        except Exception as e:
            logger.error("Failed to initialize Finlight processor", error=str(e))
    
    def _handle_finlight_article(self, article: StandardizedArticle):
        """Handle articles from Finlight WebSocket."""
        try:
            # Process the article through the standard processor
            asyncio.create_task(self.article_processor.process_article(article))
            
            # Update stats
            self._update_source_stats(NewsSource.FINLIGHT, {"last_article_time": datetime.now()})
            
        except Exception as e:
            logger.error("Failed to handle Finlight article", error=str(e))
    
    async def start_all_feeds(self):
        """Start all configured news feeds independently."""
        logger.info("Starting all news feeds independently")
        self.is_running = True
        
        # Start Telegram notification queue processor (if enabled)
        telegram_task = None
        telegram_enabled = (getattr(self.article_processor.telegram, 'enabled_1', False) or 
                           getattr(self.article_processor.telegram, 'enabled_2', False))
        if telegram_enabled and not self.article_processor.telegram.test_mode:
            telegram_task = asyncio.create_task(
                self.article_processor.telegram.start()
            )
            logger.info("Dual Telegram notification service started")
        
        # Start Benzinga polling (independent task)
        if NewsSource.BENZINGA in self.processors:
            benzinga_task = asyncio.create_task(
                self._start_benzinga_feed_with_error_handling()
            )
            logger.info("Benzinga feed task started")
        
        # Start Finlight WebSocket (independent task)
        if NewsSource.FINLIGHT in self.processors:
            finlight_task = asyncio.create_task(
                self._start_finlight_feed_with_error_handling()
            )
            logger.info("Finlight feed task started")
        
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
    
    async def _start_finlight_feed_with_error_handling(self):
        """Start the Finlight feed with independent error handling."""
        while self.is_running:
            try:
                logger.info("Starting Finlight feed...")
                finlight_processor = self.processors[NewsSource.FINLIGHT]
                await finlight_processor.start()
                logger.info("Finlight feed stopped normally")
                break
            except Exception as e:
                logger.error("Finlight feed failed", error=str(e))
                if self.is_running:
                    logger.info("Restarting Finlight feed in 60 seconds...")
                    await asyncio.sleep(60)
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
    
    async def _start_finlight_feed(self):
        """Start the Finlight feed."""
        try:
            finlight_processor = self.processors[NewsSource.FINLIGHT]
            await finlight_processor.start()
        except Exception as e:
            logger.error("Finlight feed failed", error=str(e))
    
    async def stop_all_feeds(self):
        """Stop all news feeds."""
        logger.info("Stopping all news feeds")
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
        
        # Stop Finlight
        if NewsSource.FINLIGHT in self.processors:
            try:
                finlight_processor = self.processors[NewsSource.FINLIGHT]
                await finlight_processor.stop()
            except Exception as e:
                logger.error("Error stopping Finlight feed", error=str(e))
    
    def _update_source_stats(self, source: NewsSource, stats_update: Dict[str, Any]):
        """Update statistics for a specific source."""
        current_stats = self.stats.sources.get(source, {})
        current_stats.update(stats_update)
        self.stats.add_source_stats(source, current_stats)
    
    def get_overall_stats(self) -> MultiSourceStats:
        """Get overall statistics for all feeds."""
        # Update stats from individual processors
        for source, processor in self.processors.items():
            try:
                if hasattr(processor, 'get_stats'):
                    processor_stats = processor.get_stats()
                    self._update_source_stats(source, processor_stats)
            except Exception as e:
                logger.error(f"Failed to get stats for {source}", error=str(e))
        
        return self.stats
    
    def get_source_stats(self, source: NewsSource) -> Optional[Dict[str, Any]]:
        """Get statistics for a specific source."""
        return self.stats.sources.get(source)
    
    def is_source_healthy(self, source: NewsSource) -> bool:
        """Check if a specific source is healthy."""
        if source not in self.processors:
            return False
        
        try:
            processor = self.processors[source]
            if hasattr(processor, 'get_stats'):
                stats = processor.get_stats()
                # For Benzinga: check if polling is running
                if source == NewsSource.BENZINGA:
                    return stats.get('is_running', False)
                # For Finlight: check if connected
                elif source == NewsSource.FINLIGHT:
                    return stats.get('is_connected', False)
            return False
        except Exception as e:
            logger.error(f"Error checking health for {source}", error=str(e))
            return False
    
    def get_available_sources(self) -> List[NewsSource]:
        """Get list of available/configured sources."""
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
