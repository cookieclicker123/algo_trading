"""
Alpaca quote fetcher - REST API quote fetching with optional WebSocket cache.
Pure infrastructure - fetches quotes and publishes events.
"""
from typing import Optional, Dict, Any
from datetime import datetime

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from ...utils.logging_config import get_logger
from ...utils.async_alpaca import run_sync_alpaca_call
from ...shared.event_bus import AsyncEventBus
from .events import QuoteReceivedEvent
from .infrastructure_models import InfrastructureQuoteData

logger = get_logger(__name__)

# Optional WebSocket stream manager (graceful degradation if not available)
try:
    from .stream_manager import AlpacaMarketDataStreamManager
except ImportError:
    AlpacaMarketDataStreamManager = None


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
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        market_data_client: StockHistoricalDataClient,
        stream_manager: Optional[AlpacaMarketDataStreamManager] = None
    ):
        """
        Initialize quote fetcher.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            market_data_client: Alpaca StockHistoricalDataClient instance for market data
            stream_manager: Optional WebSocket stream manager for cached quotes (backward compatible)
        """
        self.event_bus = event_bus
        self.market_data_client = market_data_client
        self.stream_manager = stream_manager  # Optional - REST API fallback always available
        
        logger.info(
            "AlpacaQuoteFetcher initialized",
            websocket_available=self.stream_manager is not None
        )
    
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
            # Use SIP feed for true NBBO (requires Algo Trader Plus subscription)
            # Falls back to IEX if SIP unavailable (for testing/development)
            try:
                request = StockLatestQuoteRequest(symbol_or_symbols=[symbol], feed="sip")
                # Use async wrapper to avoid blocking event loop
                quotes = await run_sync_alpaca_call(
                    self.market_data_client.get_stock_latest_quote, request
                )
            except Exception as sip_error:
                # Fall back to IEX if SIP fails (no subscription or error)
                logger.debug(
                    "NBBO: SIP feed unavailable, falling back to IEX",
                    symbol=symbol,
                    error=str(sip_error)
                )
                request = StockLatestQuoteRequest(symbol_or_symbols=[symbol], feed="iex")
                # Use async wrapper to avoid blocking event loop
                quotes = await run_sync_alpaca_call(
                    self.market_data_client.get_stock_latest_quote, request
                )
            
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
        
        Uses WebSocket cache if available (instant), falls back to REST API.
        Method signature unchanged for backward compatibility.
        
        Args:
            symbol: Stock ticker symbol
            
        Returns:
            Dictionary with bid, ask, spread, mid, or None if unavailable
        """
        # Try WebSocket cache first (if available)
        if self.stream_manager:
            try:
                cached_quote = await self.stream_manager.get_latest_quote(symbol)
                if cached_quote:
                    logger.debug(
                        f"✅ NBBO from WebSocket cache: {symbol}",
                        bid=cached_quote.get("bid"),
                        ask=cached_quote.get("ask"),
                        spread=cached_quote.get("spread")
                    )
                    # Publish quote event (for consistency with REST API path)
                    await self._publish_quote_event(
                        symbol,
                        cached_quote["bid"],
                        cached_quote["ask"],
                        cached_quote["spread"]
                    )
                    return cached_quote
                else:
                    logger.debug(
                        f"WebSocket cache empty for {symbol}, falling back to REST API",
                        symbol=symbol,
                        reason="cache_empty"
                    )
            except Exception as e:
                logger.debug(
                    f"WebSocket cache error for {symbol}, falling back to REST API",
                    symbol=symbol,
                    error=str(e)
                )
                # Continue to REST API fallback
        
        # REST API fallback (original implementation - unchanged)
        try:
            # Get latest quote using market data client
            # Use SIP feed for true NBBO (requires Algo Trader Plus subscription)
            # Falls back to IEX if SIP unavailable (for testing/development)
            sip_feed_used = False
            try:
                request = StockLatestQuoteRequest(symbol_or_symbols=[symbol], feed="sip")
                # Use async wrapper to avoid blocking event loop
                quotes = await run_sync_alpaca_call(
                    self.market_data_client.get_stock_latest_quote, request
                )
                sip_feed_used = True
            except Exception as sip_error:
                # Fall back to IEX if SIP fails (no subscription or error)
                error_msg = str(sip_error)
                # Check if it's a subscription error (most common)
                if "subscription" in error_msg.lower() or "not permitted" in error_msg.lower():
                    logger.warning(
                        "⚠️ NBBO: SIP feed requires Algo Trader Plus subscription, falling back to IEX",
                        symbol=symbol,
                        error=error_msg
                    )
                else:
                    logger.debug(
                        "NBBO: SIP feed unavailable, falling back to IEX",
                        symbol=symbol,
                        error=error_msg
                    )
                request = StockLatestQuoteRequest(symbol_or_symbols=[symbol], feed="iex")
                # Use async wrapper to avoid blocking event loop
                quotes = await run_sync_alpaca_call(
                    self.market_data_client.get_stock_latest_quote, request
                )
            
            # Detailed logging for failure diagnosis
            if not quotes:
                logger.warning(
                    "❌ NBBO FETCH FAILED: Alpaca returned empty quotes dict",
                    symbol=symbol,
                    reason="empty_quotes_response",
                    diagnostic="API call succeeded but quotes dict is None or empty"
                )
                return None
            
            if symbol not in quotes:
                logger.warning(
                    "❌ NBBO FETCH FAILED: Symbol not in quotes response",
                    symbol=symbol,
                    reason="symbol_not_in_response",
                    available_symbols=list(quotes.keys()) if quotes else [],
                    diagnostic="API call succeeded but symbol missing from response (may not trade in extended hours)"
                )
                return None
            
            quote = quotes[symbol]
            bid = float(quote.bid_price) if quote.bid_price and quote.bid_price > 0 else None
            ask = float(quote.ask_price) if quote.ask_price and quote.ask_price > 0 else None
            bid_size = int(quote.bid_size) if hasattr(quote, 'bid_size') and quote.bid_size else None
            ask_size = int(quote.ask_size) if hasattr(quote, 'ask_size') and quote.ask_size else None
            
            # Detailed logging for missing bid/ask
            if bid is None:
                logger.warning(
                    "❌ NBBO FETCH FAILED: Missing or invalid bid price",
                    symbol=symbol,
                    reason="missing_bid",
                    raw_bid_price=quote.bid_price,
                    ask_price=ask,
                    diagnostic="Bid price is None or <= 0 (stock may not have active bid in extended hours)"
                )
                return None
            
            if ask is None:
                logger.warning(
                    "❌ NBBO FETCH FAILED: Missing or invalid ask price",
                    symbol=symbol,
                    reason="missing_ask",
                    bid_price=bid,
                    raw_ask_price=quote.ask_price,
                    diagnostic="Ask price is None or <= 0 (stock may not have active ask in extended hours)"
                )
                return None
            
            spread = ask - bid
            mid = (bid + ask) / 2
            
            spread_pct = round((spread / mid) * 100, 2) if mid > 0 else None

            result = {
                "bid": bid,
                "ask": ask,
                "spread": spread,
                "spread_pct": spread_pct,
                "mid": mid,
                "bid_size": bid_size,
                "ask_size": ask_size,
            }
            
            logger.debug(
                "✅ NBBO FETCH SUCCESS",
                symbol=symbol,
                bid=bid,
                ask=ask,
                spread=spread,
                mid=mid,
                feed="sip" if sip_feed_used else "iex"
            )
            
            # Publish quote event
            await self._publish_quote_event(symbol, bid, ask, spread)
            
            return result
            
        except Exception as e:
            # Check if this is an expected failure (invalid symbol = non-US exchange)
            error_msg = str(e)
            is_invalid_symbol = "invalid symbol" in error_msg.lower()
            
            # For invalid symbols (e.g., TSX:*, CSE:*), log at debug level (expected)
            # For other errors, log at error level (unexpected)
            if is_invalid_symbol:
                logger.debug(
                    "NBBO: Invalid symbol (likely non-US exchange)",
                    symbol=symbol,
                    reason="invalid_symbol",
                    error_message=error_msg,
                    diagnostic="Symbol not supported by Alpaca (likely Canadian or other non-US exchange)"
                )
            else:
                logger.error(
                    "❌ NBBO FETCH FAILED: Exception during API call",
                    symbol=symbol,
                    reason="api_exception",
                    error_type=type(e).__name__,
                    error_message=error_msg,
                    diagnostic="Alpaca API call raised exception (network issue, rate limit, or API error)",
                    exc_info=True
                )
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

    async def unsubscribe_symbol(self, symbol: str) -> None:
        """
        Unsubscribe from quote stream for a symbol.

        Called when monitoring period ends (e.g., after 10-minute recall window)
        to clean up resources and prevent memory leaks from accumulating subscriptions.

        Args:
            symbol: Ticker symbol to unsubscribe from
        """
        if self.stream_manager:
            try:
                await self.stream_manager.unsubscribe_symbol(symbol)
                logger.debug(f"Unsubscribed from quote stream: {symbol}")
            except Exception as e:
                logger.warning(f"Failed to unsubscribe from {symbol} quote stream: {e}")
