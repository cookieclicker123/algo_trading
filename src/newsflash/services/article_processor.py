"""
Article processing service for handling new articles from Benzinga.

Service subscribes to Domain.ArticleReceived events and processes articles.
"""
import asyncio
from typing import List, Callable, Awaitable, Optional, Union, Any
from datetime import datetime
from ..models.benzinga_models import BenzingaArticle, convert_benzinga_to_standardized
from ..models.base_models import StandardizedArticle
from ..utils.json_storage import ArticleStorage
from ..utils.logging_config import get_logger
from ..utils.article_utils import get_article_id
from ..services.telegram_service import TelegramNotifier
from ..services.news_classifier import NewsClassifier
from ..services.classification_audit_trail import ClassificationAuditTrail
from ..config.settings import get_classification_config
from ..shared.event_bus import get_event_bus
from ..domain.websocket.events import ArticleReceivedDomainEvent
# YFinance removed - no longer used

logger = get_logger(__name__)


class ArticleProcessor:
    """
    Processes new articles through multiple handlers from Benzinga.
    
    Features:
    - JSON storage with rolling window
    - Benzinga article support
    - Custom article handlers
    - Error handling for each processor
    - Async processing pipeline
    """
    
    def __init__(
        self, 
        telegram_notifier: Optional[TelegramNotifier] = None,
        classifier: Optional[NewsClassifier] = None,
        storage: Optional[ArticleStorage] = None,
        auto_trade_service: Optional[Any] = None,
    ):
        """
        Initialize article processor with optional dependencies.
        
        Args:
            telegram_notifier: Optional Telegram notifier (injected dependency)
            classifier: Optional news classifier (injected dependency)
            storage: Optional article storage (injected dependency)
            auto_trade_service: Optional auto-trade service (injected dependency)
        """
        # Use injected dependencies or create defaults
        self.storage = storage or ArticleStorage()
        self.telegram = telegram_notifier or TelegramNotifier(test_mode=False)
        self.classifier = classifier or self._create_default_classifier()
        self.audit_trail = ClassificationAuditTrail()
        self.auto_trade_service = auto_trade_service  # Optional auto-trade service
        # YFinance removed - no longer used for metadata
        
        self.handlers: List[Callable[[Union[BenzingaArticle, StandardizedArticle]], Awaitable[None]]] = []
        
        # Services don't subscribe to domain events - use cases orchestrate by calling service methods
        # This service provides focused operations, not orchestration
        
        logger.info(
            "ArticleProcessor initialized - provides focused operations for use cases to call",
            telegram_enabled_1=self.telegram.enabled_1,
            telegram_enabled_2=self.telegram.enabled_2,
            telegram_test_mode=self.telegram.test_mode,
            classification_enabled=self.classifier.enabled
        )
    
    def _create_default_classifier(self) -> NewsClassifier:
        """Create default classifier from config."""
        classification_config = get_classification_config()
        return NewsClassifier(
            api_key=classification_config["api_key"],
            model=classification_config["model"],
            enabled=classification_config["enabled"]
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
        # Capture news reception timestamp
        news_received_at = datetime.now()
        
        # Log article details based on type
        if isinstance(article, StandardizedArticle):
            logger.info(
                "New standardized article received",
                source=article.source,
                source_id=article.source_id,
                title=article.title,  # Full title, not truncated
                tickers=article.tickers,
                categories=article.categories,
                published=article.published.isoformat()
            )
        else:
            logger.info(
                "New Benzinga article received",
                benzinga_id=article.benzinga_id,
                title=article.title,  # Full title, not truncated
                tickers=article.tickers,
                channels=article.channels,
                published=article.published.isoformat()
            )

        primary_ticker = self._extract_primary_ticker(article)

        if not primary_ticker:
            logger.info(
                "Skipping article without ticker",
                article_id=self._get_article_id(article),
                title=getattr(article, "title", "")
            )
            return

        # All filtering removed - articles with tickers proceed directly to AI classification
        # Run AI classification
        classification = None
        classified_at = None
        if self.classifier.enabled:
            try:
                classification = await self.classifier.classify_article(article)
                classified_at = datetime.now()
                
                logger.info(
                    "Article classified",
                    article_id=self._get_article_id(article),
                    classification=classification.classification.value,
                    confidence=classification.confidence,
                    reasoning=classification.reasoning,
                    classification_time_ms=(classified_at - news_received_at).total_seconds() * 1000
                )
                
                # Log IMMINENT classifications to audit trail with enhanced data
                if classification and classification.classification.value.lower() == "imminent":
                    # Start metadata gathering in background (non-blocking)
                    # Log immediately, then update with metadata when available
                    article_id = self.audit_trail.log_imminent_classification(
                        article=article,
                        classification=classification,
                        news_received_at=news_received_at,
                        classified_at=classified_at,
                        metadata={}  # Will be updated asynchronously
                    )
                    
                    # Store article_id for later updates
                    if hasattr(article, 'source_id'):
                        article._audit_article_id = article_id
                    
                    # Gather metadata asynchronously (non-blocking background task)
                    asyncio.create_task(
                        self._update_metadata_in_audit_trail(article, article_id)
                    )
                    
                    # Services don't orchestrate - trading is handled separately
                    # Auto-trade service subscribes to domain events or is called by use case
            except Exception as e:
                logger.error(
                    "Failed to classify article",
                    article_id=self._get_article_id(article),
                    error=str(e)
                )
        
        # Send Telegram notification - ALL IMMINENT articles go through, no gates
        telegram_enabled = (self.telegram.enabled_1 or self.telegram.enabled_2)
        if telegram_enabled:
            try:
                # Only send if classification is IMMINENT - simple rule, no gates
                if classification and classification.classification.value.lower() == "imminent":
                    await self.telegram.send_notification(article, classification)
                    logger.info(
                        "Telegram notification sent for IMMINENT article",
                        article_id=self._get_article_id(article),
                        classification=classification.classification.value,
                        confidence=classification.confidence,
                        note="All IMMINENT articles are sent regardless of confidence or gates"
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
    
    async def _update_metadata_in_audit_trail(self, article: Union[BenzingaArticle, StandardizedArticle], article_id: str):
        """
        Update audit trail entry with metadata asynchronously (non-blocking).
        
        Args:
            article: Article to gather metadata for
            article_id: Article ID in audit trail
        """
        try:
            metadata = await self._gather_metadata(article)
            
            # Update audit trail entry with metadata
            self.audit_trail.update_metadata(article_id, metadata)
            
        except Exception as e:
            logger.error("Failed to update metadata in audit trail", article_id=article_id, error=str(e))
    
    async def _gather_metadata(self, article: Union[BenzingaArticle, StandardizedArticle]) -> dict:
        """
        Gather metadata (market cap, sector, industry) for audit trail.
        Runs asynchronously to avoid blocking main processing.
        
        Args:
            article: Article to gather metadata for
            
        Returns:
            Dictionary with metadata
        """
        metadata = {}
        
        # Get ticker if available
        ticker = None
        if hasattr(article, 'tickers') and article.tickers:
            ticker = article.tickers[0]
        
        # YFinance removed - no metadata gathering
        metadata = {
            "company_name": ticker if ticker else "Unknown",
        }
        logger.debug("Metadata (YFinance removed)", ticker=ticker)
        
        return metadata
    
    def _get_article_id(self, article: Union[BenzingaArticle, StandardizedArticle]) -> str:
        """Get article ID for logging."""
        return get_article_id(article)
    
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

    def _extract_primary_ticker(self, article: Union[BenzingaArticle, StandardizedArticle, Any]) -> Optional[str]:
        tickers: List[Any] = []

        if isinstance(article, BenzingaArticle):
            tickers = article.tickers or []
        elif isinstance(article, StandardizedArticle):
            tickers = article.tickers or []
        else:
            tickers = getattr(article, "tickers", []) or []

        if not tickers:
            return None

        primary = tickers[0]
        if isinstance(primary, str):
            return primary.strip()

        return str(primary).strip()
    
    # Removed _handle_domain_article_received - services don't subscribe to domain events
    # Use cases orchestrate by calling service methods
    
    def _convert_domain_article_to_standardized(self, domain_article) -> Optional[StandardizedArticle]:
        """Convert domain Article to StandardizedArticle for processing."""
        try:
            from ..domain.websocket.models import Article
            from ..models.base_models import NewsSource
            
            if not isinstance(domain_article, Article):
                logger.error("ArticleProcessor: domain_article is not a domain Article model")
                return None
            
            # Map domain source to StandardizedArticle source
            # Domain uses "benzinga", StandardizedArticle uses "benzinga_websocket"
            source = NewsSource.BENZINGA_WEBSOCKET  # Always benzinga_websocket for now
            
            return StandardizedArticle(
                source=source,
                source_id=str(domain_article.source_id),
                title=domain_article.title,
                content=domain_article.content,
                summary=domain_article.summary,
                author=domain_article.author,
                published=domain_article.published_at,  # Map published_at -> published
                updated=domain_article.updated_at,  # Map updated_at -> updated
                url=domain_article.url,
                tickers=list(domain_article.tickers) if domain_article.tickers else [],
                tags=list(domain_article.tags) if domain_article.tags else [],
                categories=list(domain_article.categories) if domain_article.categories else [],
                images=list(domain_article.images) if hasattr(domain_article, 'images') and domain_article.images else [],
                raw_data={}  # Required field - empty dict for now (original raw data not preserved in domain model)
            )
        except Exception as e:
            logger.error(
                "ArticleProcessor: Error converting domain Article to StandardizedArticle",
                error=str(e),
                exc_info=True
            )
            return None


def get_article_processor(
    telegram_notifier: Optional[TelegramNotifier] = None,
    classifier: Optional[NewsClassifier] = None,
    storage: Optional[ArticleStorage] = None,
    auto_trade_service: Optional[Any] = None,
) -> ArticleProcessor:
    """
    Get article processor instance with optional dependencies.
    
    Args:
        telegram_notifier: Optional Telegram notifier (injected dependency)
        classifier: Optional news classifier (injected dependency)
        storage: Optional article storage (injected dependency)
        
    Returns:
        ArticleProcessor instance
    """
    return ArticleProcessor(
        telegram_notifier=telegram_notifier,
        classifier=classifier,
        storage=storage,
        auto_trade_service=auto_trade_service
    )
