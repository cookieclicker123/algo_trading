"""
Auto-trade service - subscribes to domain events and handles trading logic.

Service subscribes to domain events for IMMINENT articles and publishes trade requests.
"""
from decimal import Decimal
from datetime import datetime

from ...utils.logging_config import get_logger
from ...shared.event_bus import get_event_bus
from ...domain.brokerage.factories import TradeRequestFactory
from ...domain.brokerage.events import TradeRequestDomainEvent
from ...models.classification_models import ClassificationResult
from ...config.settings import AUTO_TRADING_ENABLED, AUTO_TRADE_AMOUNT_USD

logger = get_logger(__name__)


class AutoTradeService:
    """
    Trading service that subscribes to domain events and handles trade requests.
    
    Responsibilities:
    - Subscribe to domain events for IMMINENT articles
    - Build trade requests using domain factories
    - Publish trade request domain events
    
    Does NOT:
    - Execute trades (brokerage microservice does that)
    - Know about infrastructure details
    """
    
    def __init__(self):
        """Initialize auto-trade service."""
        self.is_enabled = AUTO_TRADING_ENABLED
        self.event_bus = get_event_bus()
        self.trade_request_factory = TradeRequestFactory()
        
        # Subscribe to domain events (will be triggered when articles are classified)
        # For now, we'll be called directly by article_processor, but this is the service interface
        logger.info(
            "AutoTradeService initialized",
            enabled=self.is_enabled
        )
    
    async def process_imminent_article(
        self,
        article: "StandardizedArticle",  # type: ignore
        classification_result: ClassificationResult,
    ) -> None:
        """
        Process IMMINENT article and publish trade request domain event.
        
        This method is called by article_processor service when an article is classified as IMMINENT.
        In the future, this service will subscribe to domain events instead.
        
        Args:
            article: StandardizedArticle (legacy model, will be converted to domain)
            classification_result: Classification result confirming IMMINENT
            
        Returns:
            None - trade result will come via domain events
        """
        try:
            # Convert StandardizedArticle to domain Article
            from ...domain.websocket.factories import ArticleFactory
            
            article_factory = ArticleFactory()
            domain_article = article_factory.create_from_standardized(article)
            
            if not domain_article:
                article_id = getattr(article, 'source_id', 'unknown')
                logger.warning(
                    "AutoTradeService: Failed to convert StandardizedArticle to domain Article",
                    article_id=article_id
                )
                return
            
            # Process domain article
            logger.info(
                "🤖 AUTO-TRADE: Processing IMMINENT article",
                article_id=domain_article.id,
                title=domain_article.title[:100] if domain_article.title else "",
                tickers=list(domain_article.tickers) if domain_article.tickers else []
            )
            
            # Check if auto-trading is enabled
            if not self.is_enabled:
                reason = "Auto-trading disabled (AUTO_TRADING_ENABLED=false)"
                logger.info(f"⏭️ AUTO-TRADE SKIPPED: {reason}", article_id=domain_article.id)
                return
            
            # Verify classification
            if classification_result.classification.value.lower() != "imminent":
                reason = f"Classification is {classification_result.classification.value}, not IMMINENT"
                logger.warning(
                    f"⏭️ AUTO-TRADE SKIPPED: {reason}",
                    classification=classification_result.classification.value,
                    article_id=domain_article.id
                )
                return
            
            # Build domain TradeRequest using domain factory
            trade_request = self.trade_request_factory.create_from_article(
                article=domain_article,
                amount_usd=Decimal(str(AUTO_TRADE_AMOUNT_USD)),
                leverage=Decimal("2.0")
            )
            
            if not trade_request:
                reason = "Article has no tickers or invalid for trading"
                logger.info(f"⏭️ AUTO-TRADE SKIPPED: {reason}", article_id=domain_article.id)
                return
            
            # Publish typed domain event (domain listener will handle transformation to infrastructure)
            logger.info(
                "🚀 AUTO-TRADING: Publishing trade request domain event",
                ticker=trade_request.ticker,
                article_id=domain_article.id
            )
            
            domain_event = TradeRequestDomainEvent(
                trade_request=trade_request,
                article_id=domain_article.id,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish("Domain.TradeRequested", domain_event.model_dump())
            
            logger.info(
                "✅ AUTO-TRADE REQUEST PUBLISHED",
                ticker=trade_request.ticker,
                article_id=domain_article.id
            )
            
        except Exception as exc:
            logger.error(
                "❌ AUTO-TRADE EXCEPTION",
                error=str(exc),
                article_id=getattr(article, 'source_id', 'unknown'),
                exc_info=True
            )

