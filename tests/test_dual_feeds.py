#!/usr/bin/env python3
"""
Test script for the dual feed system (Benzinga + Finlight).
"""
import asyncio
import pytest
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.feed_manager import FeedManager
from newsflash.services.article_processor import ArticleProcessor
from newsflash.models.base_models import NewsSource, StandardizedArticle
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)


class ArticleTestHandler:
    """Test handler to track articles from both sources."""
    
    def __init__(self):
        self.benzinga_articles = []
        self.finlight_articles = []
    
    async def handle_article(self, article):
        """Handle articles from any source."""
        if isinstance(article, StandardizedArticle):
            if article.source == NewsSource.BENZINGA:
                self.benzinga_articles.append(article)
                logger.info(f"Benzinga article: {article.title[:50]}...")
            elif article.source == NewsSource.FINLIGHT:
                self.finlight_articles.append(article)
                logger.info(f"Finlight article: {article.title[:50]}...")
        else:
            # BenzingaArticle (legacy format)
            self.benzinga_articles.append(article)
            logger.info(f"Benzinga article (legacy): {article.title[:50]}...")
    
    def get_stats(self):
        """Get statistics about processed articles."""
        return {
            "benzinga_articles": len(self.benzinga_articles),
            "finlight_articles": len(self.finlight_articles),
            "total_articles": len(self.benzinga_articles) + len(self.finlight_articles)
        }


@pytest.mark.asyncio
async def test_feed_manager_initialization():
    """Test that the feed manager initializes correctly."""
    feed_manager = FeedManager()
    
    # Check that it has the expected sources
    available_sources = feed_manager.get_available_sources()
    assert NewsSource.BENZINGA in available_sources
    
    # Finlight may not be available if API key is missing
    logger.info(f"Available sources: {[s.value for s in available_sources]}")


@pytest.mark.asyncio
async def test_article_processor_multi_source():
    """Test that the article processor can handle multiple source types."""
    processor = ArticleProcessor()
    
    # Create test handler
    test_handler = ArticleTestHandler()
    processor.add_handler(test_handler.handle_article)
    
    # Test that handler was added
    assert len(processor.handlers) == 1


@pytest.mark.asyncio
async def test_dual_feeds_basic_functionality():
    """Basic functionality test for the dual feed system (no real connections)."""
    logger.info("Starting dual feed system basic functionality test")
    
    # Initialize services
    article_processor = ArticleProcessor()
    feed_manager = FeedManager()
    
    # Create test handler
    test_handler = ArticleTestHandler()
    article_processor.add_handler(test_handler.handle_article)
    
    logger.info("Available sources:", sources=[source.value for source in feed_manager.get_available_sources()])
    
    # Test basic functionality without starting real connections
    assert len(feed_manager.get_available_sources()) >= 1  # At least Benzinga should be available
    assert feed_manager.is_running is False
    
    # Test statistics collection
    stats = feed_manager.get_overall_stats()
    assert isinstance(stats.sources, dict)
    assert stats.total_articles >= 0
    
    logger.info("Basic functionality test completed")


@pytest.mark.asyncio
async def test_feed_manager_startup_shutdown():
    """Test that feed manager can start and stop without waiting for articles."""
    logger.info("Testing feed manager startup/shutdown")
    
    feed_manager = FeedManager()
    
    # Test that we can start and immediately stop feeds
    try:
        # Start feeds (this will attempt connections but we'll stop quickly)
        logger.info("Starting feeds...")
        start_task = asyncio.create_task(feed_manager.start_all_feeds())
        
        # Give it a moment to initialize
        await asyncio.sleep(0.1)
        
        # Stop immediately
        logger.info("Stopping feeds...")
        await feed_manager.stop_all_feeds()
        
        # Cancel the start task
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass
        
        logger.info("Startup/shutdown test completed")
        
    except Exception as e:
        # Don't fail if API keys are missing
        if "API_KEY not found" in str(e) or "not found" in str(e).lower():
            logger.info("Skipping startup test - API keys not available")
            return
        raise


@pytest.mark.asyncio
async def test_source_health_checks():
    """Test source health checking functionality."""
    feed_manager = FeedManager()
    
    # Test health checks for available sources
    for source in feed_manager.get_available_sources():
        is_healthy = feed_manager.is_source_healthy(source)
        logger.info(f"Source {source.value} health: {is_healthy}")
        # Health status should be a boolean
        assert isinstance(is_healthy, bool)


@pytest.mark.asyncio
async def test_statistics_collection():
    """Test that statistics are collected correctly."""
    feed_manager = FeedManager()
    
    # Get initial stats
    stats = feed_manager.get_overall_stats()
    
    # Should have sources in stats
    assert isinstance(stats.sources, dict)
    assert isinstance(stats.total_articles, int)
    assert stats.total_articles >= 0


if __name__ == "__main__":
    # Run a quick integration test when executed directly
    asyncio.run(test_dual_feeds_integration())
