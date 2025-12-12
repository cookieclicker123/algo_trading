"""
Recall statistics engine - tracks all articles with tradable tickers.
Event-driven, stateless, runs alongside main trading system.
"""
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, Set

import yfinance as yf

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...shared.statistics.models import RecallRecord
from ...infra.statistics.repository import StatisticsRepository
from ...infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
from ...utils.brokerage.session_detector import get_market_session, get_market_session_from_timestamp
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.brokerage.events import TradeExecutedDomainEvent
from ...domain.brokerage.models import MarketSession

logger = get_logger(__name__)


class RecallStatsEngine:
    """
    Recall statistics engine - tracks missed trading opportunities.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events
    - Check if article has tradable tickers (NBBO available + in trading session)
    - Monitor ticker price for 5 minutes after article received
    - Record missed opportunities (1%+ moves we didn't trade)
    - Append records to JSON files in real-time
    
    Stateless: All state in repository (files), no in-memory storage.
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        repository: StatisticsRepository,
        quote_fetcher: AlpacaQuoteFetcher
    ):
        """
        Initialize recall statistics engine.
        
        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
            quote_fetcher: Quote fetcher for NBBO snapshots
        """
        self.event_bus = event_bus
        self.repository = repository
        self.quote_fetcher = quote_fetcher
        
        # Track active monitoring tasks (article_id -> task)
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_lock = asyncio.Lock()
        
        # Track which articles were traded (to exclude from recall)
        self._traded_articles: Set[str] = set()
        self._traded_lock = asyncio.Lock()
        
        # Store wrappers for unsubscribe
        self._article_received_wrapper: Optional[Any] = None
        self._article_classified_wrapper: Optional[Any] = None
        self._trade_executed_wrapper: Optional[Any] = None
        
        logger.info("RecallStatsEngine initialized")
    
    async def start(self) -> None:
        """Start engine - subscribe to events."""
        # Subscribe to typed events using subscribe_typed helper
        self._article_received_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_RECEIVED,
            ArticleReceivedDomainEvent,
            self._handle_article_received,
        )
        
        self._article_classified_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_CLASSIFIED,
            ArticleClassifiedDomainEvent,
            self._handle_article_classified,
        )
        
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent,
            self._handle_trade_executed,
        )
        
        logger.info("RecallStatsEngine started - subscribed to events")
    
    async def stop(self) -> None:
        """Stop engine - cancel monitoring tasks."""
        async with self._monitoring_lock:
            for task in self._monitoring_tasks.values():
                task.cancel()
            self._monitoring_tasks.clear()
        
        logger.info("RecallStatsEngine stopped")
    
    async def _handle_article_received(
        self,
        event: ArticleReceivedDomainEvent,
    ) -> None:
        """Handle Domain.ArticleReceived event."""
        try:
            article = event.article
            
            # Skip if no tickers
            if not article.tickers:
                return
            
            # Check current session
            session, is_extended = get_market_session()
            if session == "closed":
                return  # Don't track closed market
            
            # Fire and forget: Check if ticker is tradable and start monitoring
            asyncio.create_task(
                self._check_and_monitor_ticker(article, session, event.received_at)
            )
            
        except Exception as e:
            logger.error(
                "Error handling article received for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _check_and_monitor_ticker(
        self,
        article: Any,  # Domain Article model
        session: str,
        received_at: datetime
    ) -> None:
        """
        Check if ticker is tradable and start 5-minute monitoring.
        
        Steps:
        1. Check if article was already traded (skip if yes)
        2. For each ticker, check NBBO availability
        3. If tradable, create RecallRecord and start monitoring task
        4. Append record immediately (with initial NBBO)
        """
        try:
            # Check if already traded
            async with self._traded_lock:
                if article.id in self._traded_articles:
                    return  # Skip - we traded this
            
            # Check each ticker for tradability
            tradable_tickers = []
            initial_nbbos = {}
            
            for ticker in article.tickers:
                # Get NBBO snapshot (this checks if ticker is tradable in current session)
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                
                if nbbo:
                    # Ticker is tradable (has NBBO in current session)
                    tradable_tickers.append(ticker)
                    initial_nbbos[ticker] = nbbo
            
            # If no tradable tickers, skip
            if not tradable_tickers:
                logger.debug(
                    "Recall: No tradable tickers for article",
                    article_id=article.id,
                    tickers=list(article.tickers)
                )
                return
            
            # Map session string to MarketSession enum
            session_enum_map = {
                "premarket": MarketSession.PREMARKET,
                "market_hours": MarketSession.MARKET,
                "postmarket": MarketSession.POSTMARKET
            }
            session_enum = session_enum_map.get(session, MarketSession.MARKET)
            
            # Create recall record
            # Use first ticker's NBBO as the initial_nbbo (we'll track all tickers)
            record = RecallRecord(
                article_id=article.id,
                title=article.title,
                tickers=tradable_tickers,
                session=session_enum,
                published_at=article.published_at,
                received_at=received_at,
                initial_nbbo=initial_nbbos.get(tradable_tickers[0]) if tradable_tickers else None,
                filter_reasons=[]  # Will be populated later if needed
            )
            
            # Append record immediately (with initial NBBO)
            await self.repository.append_recall_record(record, session, received_at)
            
            # Start 5-minute monitoring task (fire and forget)
            monitoring_task = asyncio.create_task(
                self._monitor_ticker_price(article.id, tradable_tickers, initial_nbbos, session, received_at)
            )
            
            async with self._monitoring_lock:
                self._monitoring_tasks[article.id] = monitoring_task
            
            # Fetch ticker metadata asynchronously (fire and forget)
            # Pass received_at so we can determine session from timestamp (stateless)
            asyncio.create_task(
                self._fetch_and_update_metadata(article.id, tradable_tickers, session, received_at)
            )
            
            logger.debug(
                "Recall: Started monitoring ticker",
                article_id=article.id,
                tickers=tradable_tickers
            )
            
        except Exception as e:
            logger.error(
                "Error checking and monitoring ticker for recall",
                article_id=article.id,
                error=str(e),
                exc_info=True
            )
    
    async def _monitor_ticker_price(
        self,
        article_id: str,
        tickers: list[str],
        initial_nbbos: Dict[str, Dict[str, Any]],
        session: str,
        received_at: datetime
    ) -> None:
        """
        Monitor ticker price for 5 minutes, then check if it moved 1%+.
        
        Background task: Waits 5 minutes, then checks final price.
        """
        try:
            # Wait 5 minutes
            await asyncio.sleep(300)  # 300 seconds = 5 minutes
            
            # Check if article was traded (skip if yes)
            async with self._traded_lock:
                if article_id in self._traded_articles:
                    return  # We traded this, don't count as missed
            
            # Get final NBBO for each ticker
            final_nbbos = {}
            best_move = None
            best_ticker = None
            
            for ticker in tickers:
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                if nbbo and initial_nbbos.get(ticker):
                    initial_mid = initial_nbbos[ticker].get("mid")
                    final_mid = nbbo.get("mid")
                    
                    if initial_mid and final_mid and initial_mid > 0:
                        percent_change = ((final_mid - initial_mid) / initial_mid) * 100
                        final_nbbos[ticker] = {
                            **nbbo,
                            "percent_change": percent_change,
                            "moved_1_percent": percent_change >= 1.0
                        }
                        
                        # Track best move
                        if best_move is None or percent_change > best_move:
                            best_move = percent_change
                            best_ticker = ticker
            
            # Update record with price check result
            if best_ticker and final_nbbos.get(best_ticker):
                price_check = final_nbbos[best_ticker]
                
                # Update record in repository
                await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={
                        "price_check_5min": price_check,
                        "price_checked_at": datetime.now()
                    },
                    session=session,
                    date=received_at
                )
                
                logger.info(
                    "Recall: 5-minute price check completed",
                    article_id=article_id,
                    best_ticker=best_ticker,
                    percent_change=best_move,
                    moved_1_percent=price_check.get("moved_1_percent")
                )
            
        except asyncio.CancelledError:
            logger.debug("Recall: Monitoring task cancelled", article_id=article_id)
        except Exception as e:
            logger.error(
                "Error monitoring ticker price for recall",
                article_id=article_id,
                error=str(e),
                exc_info=True
            )
        finally:
            # Remove from monitoring tasks
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article_id, None)
    
    async def _handle_article_classified(
        self,
        event: ArticleClassifiedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleClassified event - update filter reasons.
        
        If article wasn't classified as IMMINENT, add filter reason.
        """
        try:
            # Check if classification is IMMINENT
            if event.result.classification.value != "IMMINENT":
                # Article was filtered - add filter reason
                filter_reason = f"not_classified_{event.result.classification.value.lower()}"
                
                # Determine session and date from classified_at timestamp
                # Classification usually happens within seconds of article received, so same session/day
                session, _ = get_market_session_from_timestamp(event.classified_at)
                if session == "closed":
                    return
                
                # Update record with filter reason
                # Use classified_at as the date (for file path calculation)
                # The repository's update_recall_record will find the record if it exists
                await self.repository.update_recall_record(
                    article_id=event.article_id,
                    updates={
                        "filter_reasons": [filter_reason]  # Will be appended to existing list in update logic
                    },
                    session=session,
                    date=event.classified_at
                )
                
                logger.debug(
                    "Recall: Updated record with filter reason",
                    article_id=event.article_id,
                    classification=event.result.classification.value,
                    filter_reason=filter_reason
                )
                
        except Exception as e:
            logger.error(
                "Error handling article classified for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _handle_trade_executed(
        self,
        event: TradeExecutedDomainEvent,
    ) -> None:
        """Handle Domain.TradeExecuted event - mark article as traded."""
        try:
            trade_result = event.trade_result
            
            # Get article_id from trade_request dict (TradeResult stores trade_request as dict)
            trade_request_dict = trade_result.trade_request
            article_id = trade_request_dict.get("article_id")
            
            if article_id:
                async with self._traded_lock:
                    self._traded_articles.add(article_id)
                
                # Cancel monitoring task if exists
                async with self._monitoring_lock:
                    task = self._monitoring_tasks.pop(article_id, None)
                    if task:
                        task.cancel()
                
                logger.debug(
                    "Recall: Marked article as traded",
                    article_id=article_id
                )
        except Exception as e:
            logger.error(
                "Error handling trade executed for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _fetch_and_update_metadata(
        self,
        article_id: str,
        tickers: list[str],
        session: str,
        received_at: datetime
    ) -> None:
        """
        Fetch ticker metadata for all tickers and update record.
        
        Fire-and-forget background task.
        Uses received_at timestamp to determine session (stateless).
        """
        try:
            # Fetch metadata for all tickers
            metadata_dict = {}
            for ticker in tickers:
                ticker_meta = await self._fetch_ticker_metadata(ticker)
                if ticker_meta:
                    metadata_dict[ticker] = ticker_meta
            
            if metadata_dict:
                # Determine session from received_at timestamp (stateless)
                session_from_timestamp, _ = get_market_session_from_timestamp(received_at)
                if session_from_timestamp == "closed":
                    # Fallback: use session parameter
                    pass  # Use session parameter
                else:
                    session = session_from_timestamp
                
                # Update record in repository
                await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={"ticker_metadata": metadata_dict},
                    session=session,
                    date=received_at
                )
                
                logger.debug(
                    "Recall: Updated record with metadata",
                    article_id=article_id,
                    tickers=list(metadata_dict.keys())
                )
        except Exception as e:
            logger.warning(
                "Error updating record with metadata",
                article_id=article_id,
                tickers=tickers,
                error=str(e)
            )
    
    async def _fetch_ticker_metadata(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch ticker metadata from yfinance.
        
        Returns:
            Dict with industry, sector, market_cap_millions, price, exchange
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Run yfinance calls in executor (they're blocking)
            stock = await loop.run_in_executor(None, lambda: yf.Ticker(ticker))
            info = await loop.run_in_executor(None, lambda: stock.info)
            
            # Extract market cap and convert to millions
            market_cap_raw = info.get('marketCap')
            market_cap_millions = None
            if market_cap_raw:
                try:
                    market_cap_millions = float(market_cap_raw) / 1_000_000
                except (TypeError, ValueError):
                    pass
            
            # Extract price (try multiple fields)
            price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
            
            return {
                "industry": info.get('industry'),
                "sector": info.get('sector'),
                "market_cap_millions": market_cap_millions,
                "price": float(price) if price else None,
                "exchange": info.get('exchange')
            }
        except Exception as e:
            logger.warning(
                "Failed to fetch ticker metadata",
                ticker=ticker,
                error=str(e)
            )
            return None
