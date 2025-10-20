"""
Article processing service for handling new articles from multiple sources.
"""
import asyncio
from typing import List, Callable, Awaitable, Optional, Union
from ..models.benzinga_models import BenzingaArticle
from ..models.base_models import StandardizedArticle
from ..utils.json_storage import ArticleStorage
from ..utils.logging_config import get_logger
from ..services.dual_telegram_service import DualTelegramNotifier
from ..services.news_classifier import NewsClassifier
from ..config.settings import get_classification_config

logger = get_logger(__name__)


class ArticleProcessor:
    """
    Processes new articles through multiple handlers from various sources.
    
    Features:
    - JSON storage with rolling window
    - Multi-source article support (Benzinga, Finlight, etc.)
    - Custom article handlers
    - Error handling for each processor
    - Async processing pipeline
    """
    
    def __init__(self, telegram_notifier: Optional[DualTelegramNotifier] = None):
        self.storage = ArticleStorage()
        self.handlers: List[Callable[[Union[BenzingaArticle, StandardizedArticle]], Awaitable[None]]] = []
        
        # Initialize dual Telegram notifier if provided or from config
        if telegram_notifier:
            self.telegram = telegram_notifier
        else:
            self.telegram = DualTelegramNotifier(test_mode=False)
        
        # Initialize AI classifier
        classification_config = get_classification_config()
        self.classifier = NewsClassifier(
            api_key=classification_config["api_key"],
            model=classification_config["model"],
            enabled=classification_config["enabled"]
        )
        
        logger.info(
            "ArticleProcessor initialized",
            telegram_enabled_1=self.telegram.enabled_1,
            telegram_enabled_2=self.telegram.enabled_2,
            telegram_test_mode=self.telegram.test_mode,
            classification_enabled=self.classifier.enabled
        )
    
    def add_handler(self, handler: Callable[[Union[BenzingaArticle, StandardizedArticle]], Awaitable[None]]):
        """Add a custom article handler."""
        self.handlers.append(handler)
    
    async def process_articles(self, articles: List[BenzingaArticle]) -> List[BenzingaArticle]:
        """
        Process a list of Benzinga articles through the processing pipeline.
        
        Args:
            articles: List of articles to process
            
        Returns:
            List of newly processed articles (not duplicates)
        """
        if not articles:
            return []
        
        # Store articles in JSON (handles deduplication)
        new_articles = await self.storage.store_articles(articles)
        
        if not new_articles:
            return []
        
        # Process each new article through handlers
        for article in new_articles:
            await self._process_single_article(article)
        
        # Log processing results
        logger.info(
            "Articles processed",
            total_received=len(articles),
            new_articles=len(new_articles),
            benzinga_ids=[a.benzinga_id for a in new_articles[:5]]  # Log first 5 IDs
        )
        
        return new_articles
    
    async def process_article(self, article: StandardizedArticle):
        """
        Process a single standardized article from any source.
        
        Args:
            article: Standardized article to process
        """
        try:
            # Store the article (convert to dict for storage)
            await self.storage.store_articles([article])
            
            # Process through handlers
            await self._process_single_article(article)
            
            # Log processing results
            logger.info(
                "Standardized article processed",
                source=article.source,
                source_id=article.source_id,
                title=article.title[:100],
                tickers=article.tickers
            )
            
        except Exception as e:
            logger.error(
                "Failed to process standardized article",
                source=article.source,
                source_id=article.source_id,
                error=str(e)
            )
    
    async def _process_single_article(self, article: Union[BenzingaArticle, StandardizedArticle]):
        """Process a single article through all handlers."""
        # Log article details based on type
        if isinstance(article, StandardizedArticle):
            logger.info(
                "New standardized article received",
                source=article.source,
                source_id=article.source_id,
                title=article.title,  # Full title, not truncated
                tickers=article.tickers,
                relevance_score=article.trading_relevance_score,
                categories=article.categories,
                published=article.published.isoformat()
            )
        else:
            logger.info(
                "New Benzinga article received",
                benzinga_id=article.benzinga_id,
                title=article.title,  # Full title, not truncated
                tickers=article.tickers,
                relevance_score=article.trading_relevance_score,
                channels=article.channels,
                published=article.published.isoformat()
            )
        
        # Run AI classification
        classification = None
        if self.classifier.enabled:
            try:
                classification = await self.classifier.classify_article(article)
                logger.info(
                    "Article classified",
                    article_id=self._get_article_id(article),
                    classification=classification.classification.value,
                    confidence=classification.confidence,
                    reasoning=classification.reasoning
                )
            except Exception as e:
                logger.error(
                    "Failed to classify article",
                    article_id=self._get_article_id(article),
                    error=str(e)
                )
        
        # Send Telegram notification (classification determines if it actually gets sent)
        telegram_enabled = (self.telegram.enabled_1 or self.telegram.enabled_2)
        if telegram_enabled:
            try:
                await self.telegram.send_notification(article, classification)
                
                # Log the result
                if classification and self.classifier.should_notify(classification):
                    logger.info(
                        "Telegram notification sent for IMMINENT article",
                        article_id=self._get_article_id(article),
                        classification=classification.classification.value,
                        confidence=classification.confidence
                    )
                elif classification:
                    logger.info(
                        "Article filtered out - not IMMINENT",
                        article_id=self._get_article_id(article),
                        classification=classification.classification.value,
                        confidence=classification.confidence
                    )
                else:
                    logger.warning(
                        "No classification available for article",
                        article_id=self._get_article_id(article)
                    )
                    
            except Exception as e:
                logger.error(
                    "Failed to send Telegram notification",
                    article_id=self._get_article_id(article),
                    error=str(e)
                )
        
        # Process through custom handlers
        for handler in self.handlers:
            try:
                await handler(article)
            except Exception as e:
                article_id = self._get_article_id(article)
                logger.error(
                    "Error in article handler",
                    article_id=article_id,
                    error=str(e),
                    handler_name=handler.__name__ if hasattr(handler, '__name__') else str(handler)
                )
    
    def _get_article_id(self, article: Union[BenzingaArticle, StandardizedArticle]) -> str:
        """Get article ID for logging."""
        if isinstance(article, BenzingaArticle):
            return str(article.benzinga_id)
        return article.source_id
    
    async def get_recent_articles(self, hours: int = 1) -> List[dict]:
        """Get recent articles from storage."""
        return await self.storage.get_recent_articles(hours)
    
    async def get_archived_articles(self, date: str) -> List[dict]:
        """Get archived articles for a specific date."""
        return await self.storage.get_archived_articles(date)
    
    async def get_archive_stats(self) -> dict:
        """Get archive statistics."""
        return await self.storage.get_archive_stats()
    
    def get_stats(self) -> dict:
        """Get processing statistics."""
        storage_stats = self.storage.get_stats()
        return {
            "handlers_count": len(self.handlers),
            "storage_stats": storage_stats,
        }
