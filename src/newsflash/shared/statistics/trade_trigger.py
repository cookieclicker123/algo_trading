"""
Trade trigger module - handles trade execution when SURGE is detected.

Extracted from RecallStatsEngine to separate trade logic from monitoring logic.
"""
import asyncio
from datetime import datetime
from typing import Any, Optional, Dict, Set, Protocol

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType
from ...domain.brokerage.events import TradeRequestDomainEvent
from ...services.brokerage.auto_trade import build_trade_request_for_article

logger = get_logger(__name__)


class QuoteFetcherProtocol(Protocol):
    """Protocol for quote fetching - allows dependency injection."""
    async def get_nbbo_snapshot(self, ticker: str) -> Optional[Dict[str, Any]]: ...


class TradeTrigger:
    """
    Triggers trade execution when SURGE is detected.

    Responsibilities:
    - Build trade request from article and ticker
    - Fetch current NBBO for trade sizing
    - Publish trade request event
    - Track traded articles to prevent duplicates

    Design:
    - Receives shared state references from parent engine
    - Uses protocol for quote fetcher (testable)
    """

    def __init__(
        self,
        event_bus: AsyncEventBus,
        quote_fetcher: QuoteFetcherProtocol,
        traded_articles: Set[str],
        traded_lock: asyncio.Lock
    ):
        """
        Initialize trade trigger.

        Args:
            event_bus: Event bus for publishing trade events
            quote_fetcher: Quote fetcher for NBBO snapshots
            traded_articles: Shared set of traded article IDs (from parent engine)
            traded_lock: Lock protecting traded_articles set
        """
        self.event_bus = event_bus
        self.quote_fetcher = quote_fetcher
        self._traded_articles = traded_articles
        self._traded_lock = traded_lock

    async def trigger_trade(
        self,
        article: Any,  # Domain Article model
        ticker: str
    ) -> bool:
        """
        Trigger trade execution when SURGE is detected.

        PRIORITY #1: Place trade immediately - no delays, no blocking operations.
        Only essential operations (price fetch) happen before trade placement.

        Args:
            article: Domain Article model
            ticker: Ticker symbol that showed SURGE

        Returns:
            True if trade was triggered, False if skipped (already traded or error)
        """
        try:
            # Check if already traded
            async with self._traded_lock:
                if article.id in self._traded_articles:
                    logger.debug(
                        "TradeTrigger: Article already traded, skipping",
                        article_id=article.id,
                        ticker=ticker
                    )
                    return False

            # Get current ask price for calculating trade size
            # NOTE: Spread filter REMOVED - trade on surge regardless of spread
            # Wide spreads often compress rapidly on runners
            current_price = await self._fetch_current_price(ticker)

            # Build trade request from article
            # CRITICAL: Pass the specific ticker that showed SURGE
            trade_request = build_trade_request_for_article(
                article,
                current_price=current_price,
                ticker=ticker
            )

            if not trade_request:
                logger.warning(
                    "TradeTrigger: Could not build trade request",
                    article_id=article.id,
                    ticker=ticker
                )
                return False

            # Mark as traded BEFORE publishing (prevent race conditions)
            async with self._traded_lock:
                self._traded_articles.add(article.id)

            # CRITICAL PATH: Publish trade request domain event
            await self._publish_trade_request(article.id, trade_request)

            logger.info(
                "TradeTrigger: Trade request published",
                article_id=article.id,
                ticker=ticker,
                trade_ticker=trade_request.ticker
            )

            return True

        except Exception as e:
            logger.error(
                "TradeTrigger: Error triggering trade",
                article_id=article.id,
                ticker=ticker,
                error=str(e),
                exc_info=True
            )
            return False

    async def _fetch_current_price(self, ticker: str) -> Optional[float]:
        """Fetch current ask price for trade sizing."""
        try:
            nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
            if nbbo:
                price = nbbo.get("ask")
                logger.debug(
                    "TradeTrigger: Got NBBO for trade sizing",
                    ticker=ticker,
                    bid=nbbo.get("bid"),
                    ask=nbbo.get("ask")
                )
                return price
        except Exception as e:
            logger.debug(
                "TradeTrigger: Could not get price, will use amount_usd",
                ticker=ticker,
                error=str(e)
            )
        return None

    async def _publish_trade_request(self, article_id: str, trade_request: Any) -> None:
        """Publish trade request domain event."""
        domain_event = TradeRequestDomainEvent(
            trade_request=trade_request,
            article_id=article_id,
            requested_at=datetime.now()
        )

        await self.event_bus.publish(
            DomainEventType.TRADE_REQUESTED,
            domain_event.model_dump()
        )
