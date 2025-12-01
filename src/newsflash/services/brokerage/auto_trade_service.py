"""
Auto-trade service - subscribes to domain events and handles trading logic.

Service subscribes to domain events for IMMINENT articles and publishes trade requests.
"""
from decimal import Decimal
from datetime import datetime

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.brokerage.factories import TradeRequestFactory
from ...domain.brokerage.events import TradeRequestDomainEvent
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.classification.models import ClassificationCategory
from ...config.settings import AUTO_TRADING_ENABLED, AUTO_TRADE_AMOUNT_USD
from ...services.storage import StorageQueryService

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
    
    def __init__(self, event_bus: AsyncEventBus, storage_query_service: StorageQueryService):
        """
        Initialize auto-trade service.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Optional storage query service for fetching articles
        """
        self.is_enabled = AUTO_TRADING_ENABLED
        self.event_bus = event_bus
        self.trade_request_factory = TradeRequestFactory()
        self.storage_query_service = storage_query_service
        
        # Subscribe to typed Domain.ArticleClassified events
        # Store wrapper for unsubscribe
        self._article_classified_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_CLASSIFIED,
            ArticleClassifiedDomainEvent,
            self._handle_article_classified,
        )
        
        logger.info(
            "AutoTradeService initialized - subscribes to Domain.ArticleClassified events",
            enabled=self.is_enabled,
            has_storage_query=self.storage_query_service is not None
        )
    
    
    async def _handle_article_classified(
        self,
        domain_event: ArticleClassifiedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleClassified event - auto-trade if IMMINENT.
        
        This is called when classification is complete (event-driven from classification microservice).
        """
        try:
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
            
            # Fetch article from storage
            if not self.storage_query_service:
                logger.warning(
                    "AutoTradeService: Storage query service not available",
                    article_id=classification_result.article_id
                )
                return
            
            domain_article = await self.storage_query_service.fetch_article(classification_result.article_id)
            
            if not domain_article:
                logger.warning(
                    "AutoTradeService: Article not found in storage",
                    article_id=classification_result.article_id
                )
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

