"""
Auto-trade service - subscribes to domain events and handles trading logic.

Pure functions for trade processing logic, with minimal service class for event subscriptions.
"""
from decimal import Decimal
from datetime import datetime
from typing import Optional

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.brokerage.events import TradeRequestDomainEvent
from ...domain.brokerage.models import TradeRequest, TradeAction
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.classification.models import ClassificationResult, ClassificationCategory
from ...domain.websocket.models import Article
from ...services.storage import StorageQueryService
from .trade_builder import build_trade_request_from_article

logger = get_logger(__name__)


def should_process_classification(result: ClassificationResult, enabled: bool) -> bool:
    """
    Determine if a classification result should trigger auto-trade.
    
    Args:
        result: Classification result to check
        enabled: Whether auto-trading is enabled
        
    Returns:
        True if should process, False otherwise
    """
    if not enabled:
        logger.info(f"⏭️ AUTO-TRADE SKIPPED: Auto-trading disabled", article_id=result.article_id)
        return False
    
    if result.classification != ClassificationCategory.IMMINENT:
        logger.debug(
            "AutoTradeService: Skipping non-IMMINENT classification",
            article_id=result.article_id,
            classification=result.classification.value
        )
        return False
    
    return True


async def fetch_article_for_trade(
    storage_service: StorageQueryService,
    article_id: str,
    max_retries: int = 3,
    initial_delay: float = 0.5
) -> Optional[Article]:
    """
    Fetch an article from storage for trade processing with retry logic.
    
    Handles race condition where classification completes before storage finishes.
    
    Args:
        storage_service: Storage query service
        article_id: Article ID to fetch
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay before first retry in seconds
        
    Returns:
        Domain Article model, or None if not found after retries
    """
    import asyncio
    
    if not storage_service:
        logger.warning(
            "AutoTradeService: Storage query service not available",
            article_id=article_id
        )
        return None
    
    # Try fetching with exponential backoff retry
    for attempt in range(max_retries):
        domain_article = await storage_service.fetch_article(article_id)
        
        if domain_article:
            if attempt > 0:
                logger.info(
                    "AutoTradeService: Article found after retry",
                    article_id=article_id,
                    attempt=attempt + 1
                )
            return domain_article
        
        # If not found and we have retries left, wait before retrying
        if attempt < max_retries - 1:
            delay = initial_delay * (2 ** attempt)  # Exponential backoff
            logger.info(
                "AutoTradeService: Article not found, retrying",
                article_id=article_id,
                attempt=attempt + 1,
                max_retries=max_retries,
                delay_seconds=delay
            )
            await asyncio.sleep(delay)
    
    # All retries exhausted
    logger.warning(
        "AutoTradeService: Article not found in storage after retries",
        article_id=article_id,
        max_retries=max_retries
    )
    return None


def build_trade_request_for_article(article: Article, trade_amount_usd: Decimal) -> Optional[TradeRequest]:
    """
    Build a trade request from an article using provided trade amount.
    
    Args:
        article: Domain Article model
        trade_amount_usd: Trade amount in USD
        
    Returns:
        Domain TradeRequest model, or None if invalid
    """
    trade_request = build_trade_request_from_article(
        article=article,
        amount_usd=trade_amount_usd,
        leverage=Decimal("2.0"),
        action=TradeAction.BUY
    )
    
    if not trade_request:
        logger.info(
            "⏭️ AUTO-TRADE SKIPPED: Article has no tickers or invalid for trading",
            article_id=article.id
        )
        return None
    
    return trade_request


async def publish_trade_request(
    event_bus: AsyncEventBus,
    trade_request: TradeRequest,
    article_id: str
) -> None:
    """
    Publish a trade request domain event.
    
    Args:
        event_bus: Event bus instance
        trade_request: Domain TradeRequest to publish
        article_id: Associated article ID
    """
    domain_trade_event = TradeRequestDomainEvent(
        trade_request=trade_request,
        article_id=article_id,
        requested_at=datetime.now()
    )
    
    await event_bus.publish("Domain.TradeRequested", domain_trade_event.model_dump())
    
    logger.info(
        "✅ AUTO-TRADE REQUEST PUBLISHED",
        ticker=trade_request.ticker,
        article_id=article_id
    )


async def process_imminent_article(
    event_bus: AsyncEventBus,
    storage_service: StorageQueryService,
    classification_result: ClassificationResult,
    enabled: bool,
    trade_amount_usd: Decimal
) -> None:
    """
    Process an IMMINENT classification result and publish trade request if valid.
    
    Pure function that orchestrates the auto-trade workflow.
    
    Args:
        event_bus: Event bus instance for publishing events
        storage_service: Storage query service for fetching articles
        classification_result: Classification result to process
        enabled: Whether auto-trading is enabled
    """
    try:
        # Check if we should process this classification
        if not should_process_classification(classification_result, enabled):
            return
        
        # Fetch article from storage
        logger.info(
            "🔍 AUTO-TRADE: Attempting to fetch article from storage",
            article_id=classification_result.article_id,
            classification=classification_result.classification.value
        )
        
        domain_article = await fetch_article_for_trade(
            storage_service,
            classification_result.article_id
        )
        
        if not domain_article:
            logger.warning(
                "⏭️ AUTO-TRADE SKIPPED: Article not found in storage",
                article_id=classification_result.article_id,
                classification=classification_result.classification.value
            )
            return
        
        # Log processing
        tickers_list = list(domain_article.tickers) if domain_article.tickers else []
        logger.info(
            "🤖 AUTO-TRADE: Processing IMMINENT article",
            article_id=domain_article.id,
            title=domain_article.title[:100] if domain_article.title else "",
            tickers=tickers_list,
            has_tickers=len(tickers_list) > 0,
            ticker_count=len(tickers_list)
        )
        
        # Build trade request
        trade_request = build_trade_request_for_article(domain_article, trade_amount_usd)
        
        if not trade_request:
            return
        
        # Publish trade request
        logger.info(
            "🚀 AUTO-TRADING: Publishing trade request domain event",
            ticker=trade_request.ticker,
            article_id=domain_article.id
        )
        
        await publish_trade_request(event_bus, trade_request, domain_article.id)
        
    except Exception as e:
        logger.error(
            "❌ AUTO-TRADE EXCEPTION",
            error=str(e),
            article_id=classification_result.article_id,
            exc_info=True
        )


class AutoTradeService:
    """
    Trading service that subscribes to domain events and handles trade requests.
    
    Minimal wrapper for event subscription - business logic is in pure functions above.
    
    Responsibilities:
    - Subscribe to Domain.ArticleClassified events for IMMINENT articles
    - Delegate to pure functions for processing
    
    Does NOT:
    - Execute trades (brokerage microservice does that)
    - Know about infrastructure details
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        storage_query_service: StorageQueryService,
        enabled: bool,
        trade_amount_usd: Decimal
    ):
        """
        Initialize auto-trade service.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Storage query service for fetching articles
            enabled: Whether auto-trading is enabled (injected via DI)
            trade_amount_usd: Trade amount in USD (injected via DI)
        """
        self.is_enabled = enabled
        self.trade_amount_usd = trade_amount_usd
        self.event_bus = event_bus
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
        Handle Domain.ArticleClassified event - delegate to pure function.
        
        This is called when classification is complete (event-driven from classification microservice).
        """
        logger.info(
            "🎯 AUTO-TRADE: Received ArticleClassified event",
            article_id=domain_event.result.article_id,
            classification=domain_event.result.classification.value,
            enabled=self.is_enabled
        )
        await process_imminent_article(
            self.event_bus,
            self.storage_query_service,
            domain_event.result,
            self.is_enabled,
            self.trade_amount_usd
        )
    
    async def start(self) -> None:
        """Start the service (already subscribed in __init__)."""
        logger.info("AutoTradeService started")
    
    async def stop(self) -> None:
        """Stop the service."""
        self.event_bus.unsubscribe(DomainEventType.ARTICLE_CLASSIFIED, self._article_classified_wrapper)
        logger.info("AutoTradeService stopped")

