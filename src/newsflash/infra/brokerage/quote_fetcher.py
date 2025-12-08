"""
Alpaca quote fetcher - REST API quote fetching.
Pure infrastructure - fetches quotes and publishes events.
"""
from typing import Optional, Dict, Any
from datetime import datetime

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from .events import QuoteReceivedEvent
from .infrastructure_models import InfrastructureQuoteData

logger = get_logger(__name__)


class AlpacaQuoteFetcher:
    """
    Fetches market quotes via Alpaca REST API.
    
    Responsibilities:
    - Fetch realtime prices
    - Fetch NBBO snapshots
    - Publish quote events
    
    Does NOT:
    - Know about business logic
    - Send Telegram notifications
    """
    
    def __init__(self, event_bus: AsyncEventBus, market_data_client: StockHistoricalDataClient):
        """
        Initialize quote fetcher.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            market_data_client: Alpaca StockHistoricalDataClient instance for market data
        """
        self.event_bus = event_bus
        self.market_data_client = market_data_client
        
        logger.info("AlpacaQuoteFetcher initialized")
    
    async def get_realtime_price(self, symbol: str) -> Optional[float]:
        """
        Get realtime price for a symbol.
        
        Args:
            symbol: Stock ticker symbol
            
        Returns:
            Current price (bid or ask) or None if unavailable
        """
        try:
            # Get latest quote using market data client
            request = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
            quotes = self.market_data_client.get_stock_latest_quote(request)
            
            if quotes and symbol in quotes:
                quote = quotes[symbol]
                # Use ask price if available, otherwise bid
                price = quote.ask_price if quote.ask_price and quote.ask_price > 0 else quote.bid_price
                return float(price) if price else None
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get realtime price for {symbol}: {e}", exc_info=True)
            return None
    
    async def get_nbbo_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get NBBO (National Best Bid/Offer) snapshot for a symbol.
        
        Args:
            symbol: Stock ticker symbol
            
        Returns:
            Dictionary with bid, ask, spread, mid, or None if unavailable
        """
        try:
            # Get latest quote using market data client
            request = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
            quotes = self.market_data_client.get_stock_latest_quote(request)
            
            if not quotes or symbol not in quotes:
                return None
            
            quote = quotes[symbol]
            bid = float(quote.bid_price) if quote.bid_price and quote.bid_price > 0 else None
            ask = float(quote.ask_price) if quote.ask_price and quote.ask_price > 0 else None
            
            if bid is None or ask is None:
                return None
            
            spread = ask - bid
            mid = (bid + ask) / 2
            
            result = {
                "bid": bid,
                "ask": ask,
                "spread": spread,
                "mid": mid,
            }
            
            # Publish quote event
            await self._publish_quote_event(symbol, bid, ask, spread)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get NBBO snapshot for {symbol}: {e}", exc_info=True)
            return None
    
    async def _publish_quote_event(self, symbol: str, bid: float, ask: float, spread: float) -> None:
        """Publish quote received event."""
        quote_data = InfrastructureQuoteData(
            bid=bid,
            ask=ask,
            last=None,  # Alpaca quote doesn't include last price
            volume=None,  # Alpaca quote doesn't include volume
            spread=spread
        )
        
        event = QuoteReceivedEvent(
            symbol=symbol,
            nbbo=quote_data,
            received_at=datetime.now(),
            source="brokerage"
        )
        
        await self.event_bus.publish("QuoteReceived", event.model_dump())
        logger.debug(f"Published QuoteReceived event for {symbol}")
