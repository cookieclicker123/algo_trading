"""
Main entry point for standalone multi-source news polling.
Run with: python -m src.main
"""
import asyncio
import signal
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from newsflash.services.feed_manager import FeedManager
from newsflash.services.article_processor import ArticleProcessor
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)


class NewsFlashStandalone:
    """Standalone multi-source news polling application."""
    
    def __init__(self):
        self.article_processor = None
        self.feed_manager = None
        self.shutdown_event = asyncio.Event()
    
    async def start(self):
        """Start the standalone multi-source polling system."""
        logger.info("Starting NewsFlash standalone multi-source polling system")
        
        try:
            # Initialize services
            self.article_processor = ArticleProcessor()
            
            # Pass the article processor to FeedManager to avoid duplication
            self.feed_manager = FeedManager(article_processor=self.article_processor)
            
            # Add custom handlers if needed
            self.article_processor.add_handler(self._log_high_relevance_articles)
            
            # Start all feeds
            await self.feed_manager.start_all_feeds()
            
            # Wait for shutdown signal
            await self.shutdown_event.wait()
                
        except Exception as e:
            logger.error("Error in standalone system", error=str(e))
            raise
        finally:
            if self.feed_manager:
                await self.feed_manager.stop_all_feeds()
            logger.info("NewsFlash standalone system stopped")
    
    async def _log_high_relevance_articles(self, article):
        """Log articles with high trading relevance."""
        if article.trading_relevance_score >= 5:
            # Handle both BenzingaArticle and StandardizedArticle
            if hasattr(article, 'source'):
                # StandardizedArticle
                logger.info(
                    "HIGH RELEVANCE ARTICLE",
                    source=article.source,
                    source_id=article.source_id,
                    title=article.title,
                    tickers=article.tickers,
                    relevance_score=article.trading_relevance_score,
                    categories=article.categories
                )
            else:
                # BenzingaArticle
                logger.info(
                    "HIGH RELEVANCE ARTICLE",
                    benzinga_id=article.benzinga_id,
                    title=article.title,
                    tickers=article.tickers,
                    relevance_score=article.trading_relevance_score,
                    channels=article.channels
                )
    
    def stop(self):
        """Stop the system gracefully."""
        logger.info("Shutdown signal received")
        self.shutdown_event.set()


async def main():
    """Main function."""
    app = NewsFlashStandalone()
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        app.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await app.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        app.stop()
    except Exception as e:
        logger.error("Fatal error", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
