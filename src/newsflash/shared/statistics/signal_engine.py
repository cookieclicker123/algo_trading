"""
Signal statistics engine - tracks actual trade executions.
Event-driven, stateless, runs alongside main trading system.
"""
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from decimal import Decimal

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...shared.statistics.models import SignalRecord
from ...infra.statistics.repository import StatisticsRepository
from ...utils.brokerage.session_detector import get_market_session, get_market_session_from_timestamp
from ...domain.brokerage.events import TradeExecutedDomainEvent
from ...domain.brokerage.models import MarketSession
import yfinance as yf

logger = get_logger(__name__)


class SignalStatsEngine:
    """
    Signal statistics engine - tracks actual trade executions.
    
    Responsibilities:
    - Subscribe to Domain.TradeExecuted events
    - Extract trade details (price, spread, ticker metadata)
    - Append records to JSON files in real-time
    - Track profit/loss when trades exit (future enhancement)
    
    Stateless: All state in repository (files), no in-memory storage.
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        repository: StatisticsRepository
    ):
        """
        Initialize signal statistics engine.
        
        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
        """
        self.event_bus = event_bus
        self.repository = repository
        
        # Store wrappers for unsubscribe
        self._trade_executed_wrapper: Optional[Any] = None
        
        logger.info("SignalStatsEngine initialized")
    
    async def start(self) -> None:
        """Start engine - subscribe to events."""
        # Subscribe to typed events using subscribe_typed helper
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent,
            self._handle_trade_executed,
        )
        
        logger.info("SignalStatsEngine started - subscribed to events")
    
    async def stop(self) -> None:
        """Stop engine."""
        logger.info("SignalStatsEngine stopped")
    
    async def _handle_trade_executed(
        self,
        event: TradeExecutedDomainEvent,
    ) -> None:
        """Handle Domain.TradeExecuted event."""
        try:
            trade_result = event.trade_result
            
            # Only track successful trades
            if not trade_result.success or trade_result.status.value != "executed":
                logger.debug(
                    "Signal: Skipping non-executed trade",
                    success=trade_result.success,
                    status=trade_result.status.value
                )
                return
            
            # Get session from trade_result (it has the correct session)
            session_enum = trade_result.session
            # Map MarketSession enum to string format used by repository
            if session_enum == MarketSession.MARKET:
                session = "market_hours"
            elif session_enum == MarketSession.PREMARKET:
                session = "premarket"
            elif session_enum == MarketSession.POSTMARKET:
                session = "postmarket"
            else:
                # Fallback: try to get from current market session
                current_session, _ = get_market_session()
                if current_session == "closed":
                    session = "market_hours"  # Default fallback
                else:
                    session = current_session
            
            # Extract entry details from TradeResult
            entry_price = float(trade_result.fill_price) if trade_result.fill_price else 0.0
            entry_shares = int(trade_result.shares) if trade_result.shares else 0
            entry_amount_usd = float(trade_result.total_cost) if trade_result.total_cost else 0.0
            
            # Extract NBBO from trade_request dict (stored by mapper as _spread_info)
            trade_request_dict = trade_result.trade_request
            entry_nbbo = trade_request_dict.get("_spread_info", {})
            if not entry_nbbo:
                # Try alternative location
                entry_nbbo = trade_request_dict.get("spread_info", {})
            
            # Get ticker and article_id
            ticker = trade_result.get_ticker()
            article_id = trade_request_dict.get("article_id")
            
            # Generate trade_id (use order_id if available, otherwise generate)
            trade_id = trade_request_dict.get("order_id") or trade_request_dict.get("_order_id")
            if not trade_id:
                trade_id = f"trade_{int(event.executed_at.timestamp() * 1000)}"
            
            # Map session string back to MarketSession enum for record
            if session == "market_hours":
                session_enum = MarketSession.MARKET
            elif session == "premarket":
                session_enum = MarketSession.PREMARKET
            elif session == "postmarket":
                session_enum = MarketSession.POSTMARKET
            else:
                session_enum = MarketSession.MARKET  # Default fallback
            
            # Create signal record (metadata will be added later)
            record = SignalRecord(
                trade_id=trade_id,
                article_id=article_id,
                ticker=ticker,
                session=session_enum,
                executed_at=event.executed_at,
                entry_price=entry_price,
                entry_shares=entry_shares,
                entry_amount_usd=entry_amount_usd,
                entry_nbbo=entry_nbbo if entry_nbbo else None
            )
            
            # Append record immediately (before metadata fetch)
            await self.repository.append_signal_record(
                record=record,
                session=session,
                date=event.executed_at
            )
            
            # Fetch ticker metadata asynchronously (fire and forget)
            # Pass executed_at so we can determine session from timestamp (stateless)
            asyncio.create_task(
                self._fetch_and_update_metadata(record, event.executed_at)
            )
            
            logger.debug(
                "Signal: Recorded trade execution",
                trade_id=trade_id,
                ticker=ticker,
                article_id=article_id
            )
            
        except Exception as e:
            logger.error(
                "Error handling trade executed for signal",
                error=str(e),
                exc_info=True
            )
    
    async def _fetch_and_update_metadata(
        self,
        record: SignalRecord,
        executed_at: datetime
    ) -> None:
        """
        Fetch ticker metadata and update record.
        
        Fire-and-forget background task.
        Uses executed_at timestamp to determine session (stateless).
        """
        try:
            metadata = await self._fetch_ticker_metadata(record.ticker)
            if metadata:
                # Determine session from executed_at timestamp (stateless)
                session, _ = get_market_session_from_timestamp(executed_at)
                if session == "closed":
                    # Fallback: use session from record
                    session_enum = record.session
                    if session_enum == MarketSession.MARKET:
                        session = "market_hours"
                    elif session_enum == MarketSession.PREMARKET:
                        session = "premarket"
                    elif session_enum == MarketSession.POSTMARKET:
                        session = "postmarket"
                    else:
                        session = "market_hours"  # Default fallback
                
                # Update record in repository
                await self.repository.update_signal_record(
                    trade_id=record.trade_id,
                    updates={"ticker_metadata": metadata},
                    session=session,
                    date=executed_at
                )
                
                logger.debug(
                    "Signal: Updated record with metadata",
                    trade_id=record.trade_id,
                    ticker=record.ticker
                )
        except Exception as e:
            logger.warning(
                "Error updating record with metadata",
                trade_id=record.trade_id,
                ticker=record.ticker,
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
