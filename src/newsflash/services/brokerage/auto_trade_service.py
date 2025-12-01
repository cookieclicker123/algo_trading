"""
Auto-trade service - subscribes to domain events and handles trading logic.

Service subscribes to domain events for IMMINENT articles and publishes trade requests.
"""
from decimal import Decimal
from datetime import datetime
from typing import Dict, Any, Optional

from ...utils.logging_config import get_logger
from ...shared.event_bus import get_event_bus
from ...domain.brokerage.factories import TradeRequestFactory
from ...domain.brokerage.events import TradeRequestDomainEvent
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.classification.models import ClassificationCategory
from ...domain.websocket.models import Article
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...config.settings import AUTO_TRADING_ENABLED, AUTO_TRADE_AMOUNT_USD

logger = get_logger(__name__)


class AutoTradeService:
    """
    Trading service that subscribes to domain events and handles trade requests.
    
    Responsibilities:
    - Subscribe to Domain.ArticleClassified events for IMMINENT articles
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
        
        # Cache articles by article_id (for building trade requests)
        # TODO: Will be replaced by storage microservice
        self._article_cache: Dict[str, Article] = {}
        
        # Subscribe to domain events
        self.event_bus.subscribe("Domain.ArticleReceived", self._handle_article_received)
        self.event_bus.subscribe("Domain.ArticleClassified", self._handle_article_classified)
        
        logger.info(
            "AutoTradeService initialized - subscribes to Domain.ArticleClassified events",
            enabled=self.is_enabled
        )
    
    async def _handle_article_received(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle Domain.ArticleReceived event - cache article for trading.
        
        We cache articles so we can use them when building trade requests from classification events.
        """
        try:
            # Reconstruct typed domain event
            domain_event = ArticleReceivedDomainEvent(**event_data)
            domain_article = domain_event.article
            
            # Cache article by ID for trade request building
            self._article_cache[domain_article.id] = domain_article
            logger.debug(
                "AutoTradeService: Cached article for trading",
                article_id=domain_article.id
            )
            
        except Exception as e:
            logger.error(
                "AutoTradeService: Error handling Domain.ArticleReceived event",
                error=str(e),
                event_type=event_type,
                exc_info=True
            )
    
    async def _handle_article_classified(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle Domain.ArticleClassified event - auto-trade if IMMINENT.
        
        This is called when classification is complete (event-driven from classification microservice).
        """
        try:
            # Reconstruct typed domain event
            domain_event = ArticleClassifiedDomainEvent(**event_data)
            classification_result = domain_event.result
            
            # Only process IMMINENT classifications
            if classification_result.classification != ClassificationCategory.IMMINENT:
                logger.debug(
                    "AutoTradeService: Skipping non-IMMINENT classification",
                    article_id=classification_result.article_id,
                    classification=classification_result.classification.value
                )
                return
            
            # Check if auto-trading is enabled
            if not self.is_enabled:
                reason = "Auto-trading disabled (AUTO_TRADING_ENABLED=false)"
                logger.info(f"⏭️ AUTO-TRADE SKIPPED: {reason}", article_id=classification_result.article_id)
                return
            
            # Get article from cache
            domain_article = self._article_cache.get(classification_result.article_id)
            
            if not domain_article:
                logger.warning(
                    "AutoTradeService: Article not found in cache for trading",
                    article_id=classification_result.article_id
                )
                # TODO: Fetch from storage microservice when available
                return
            
            # Process domain article
            logger.info(
                "🤖 AUTO-TRADE: Processing IMMINENT article",
                article_id=domain_article.id,
                title=domain_article.title[:100] if domain_article.title else "",
                tickers=list(domain_article.tickers) if domain_article.tickers else []
            )
            
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
            
            domain_trade_event = TradeRequestDomainEvent(
                trade_request=trade_request,
                article_id=domain_article.id,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish("Domain.TradeRequested", domain_trade_event.model_dump())
            
            logger.info(
                "✅ AUTO-TRADE REQUEST PUBLISHED",
                ticker=trade_request.ticker,
                article_id=domain_article.id
            )
            
        except Exception as e:
            logger.error(
                "❌ AUTO-TRADE EXCEPTION",
                error=str(e),
                article_id=classification_result.article_id if 'classification_result' in locals() else 'unknown',
                exc_info=True
            )
    
    async def process_imminent_article(
        self,
        article: "StandardizedArticle",  # type: ignore
        classification_result: "ClassificationResult",  # type: ignore
    ) -> None:
        """
        Legacy method - kept for backward compatibility.
        
        This method is called by old code paths that haven't been migrated yet.
        New code should use event-driven approach via Domain.ArticleClassified events.
        """
        logger.warning(
            "AutoTradeService: process_imminent_article called directly (legacy method)",
            article_id=getattr(article, 'source_id', 'unknown')
        )
        # Legacy method - can be removed once all code is event-driven

