"""
Alpaca Market Data WebSocket stream manager.

Manages WebSocket connection for real-time quotes and trades.
Pure infrastructure - publishes events, no business logic.
"""
import os
import asyncio
import threading
from typing import Optional, Dict, Any, Set
from datetime import datetime
from collections import deque

try:
    from alpaca.data.live.stock import StockDataStream
    from alpaca.data.enums import DataFeed
except ImportError:
    StockDataStream = None
    DataFeed = None

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from .events import QuoteReceivedEvent
from .infrastructure_models import InfrastructureQuoteData

logger = get_logger(__name__)


class AlpacaMarketDataStreamManager:
    """
    Manages Alpaca Market Data WebSocket stream for real-time quotes and trades.
    
    Responsibilities:
    - Manage WebSocket connection lifecycle
    - Subscribe/unsubscribe to symbols
    - Cache latest quotes per symbol (instant access)
    - Cache recent trades per symbol (for volume analysis)
    - Publish quote/trade events
    
    Does NOT:
    - Know about business logic
    - Send Telegram notifications
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        paper_trading: bool = True,
        feed: DataFeed = DataFeed.IEX
    ):
        """
        Initialize stream manager.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            api_key: Alpaca API key (defaults to ALPACA_KEY env var)
            api_secret: Alpaca API secret (defaults to ALPACA_SECRET env var)
            paper_trading: Whether to use paper trading
            feed: Data feed to use (IEX or SIP)
        """
        if StockDataStream is None:
            raise ImportError("alpaca-py SDK not installed - cannot use WebSocket streaming")
        
        self.event_bus = event_bus
        self.paper_trading = paper_trading
        self.feed = feed
        
        # Get API credentials
        self.api_key = api_key or os.getenv("ALPACA_KEY")
        self.api_secret = api_secret or os.getenv("ALPACA_SECRET")
        
        if not self.api_key or not self.api_secret:
            raise ValueError("ALPACA_KEY and ALPACA_SECRET must be set for WebSocket streaming")
        
        # Initialize WebSocket stream (not started yet)
        self.stream: Optional[StockDataStream] = None
        self._stream_task: Optional[asyncio.Task] = None
        self._is_running = False
        
        # Store reference to main event loop for thread-safe publishing
        self._main_event_loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Subscription tracking (thread-safe locks for cross-thread access)
        self._subscribed_symbols: Set[str] = set()
        self._subscription_lock = threading.Lock()  # Thread-safe (accessed from WebSocket thread)
        
        # Quote cache (symbol -> deque of recent quotes, max 1000 per symbol, for volume analysis)
        # Also maintain latest quote for quick NBBO access
        self._quote_cache: Dict[str, deque] = {}
        self._latest_quote_cache: Dict[str, Dict[str, Any]] = {}  # Quick access to latest
        self._quote_cache_lock = threading.Lock()  # Thread-safe (handlers run in WebSocket thread)
        
        # Trade cache (symbol -> deque of recent trades, max 1000 per symbol)
        self._trade_cache: Dict[str, deque] = {}
        self._trade_cache_lock = threading.Lock()  # Thread-safe (handlers run in WebSocket thread)
        self._max_trades_per_symbol = 1000
        
        logger.info(
            "AlpacaMarketDataStreamManager initialized",
            paper_trading=paper_trading,
            feed=feed.value if feed else None
        )
    
    async def start(self) -> None:
        """
        Start WebSocket connection and begin streaming.
        
        Idempotent: Safe to call multiple times.
        """
        if self._is_running:
            logger.debug("Stream manager already running")
            return
        
        if StockDataStream is None:
            logger.warning("StockDataStream not available - WebSocket streaming disabled")
            return
        
        logger.info("🚀 Starting Alpaca Market Data WebSocket stream")
        
        try:
            # Store reference to main event loop for thread-safe event publishing
            try:
                self._main_event_loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("No event loop available, event publishing may fail")
                self._main_event_loop = None
            
            # Initialize stream
            self.stream = StockDataStream(
                api_key=self.api_key,
                secret_key=self.api_secret,
                feed=self.feed
            )
            
            # Handlers are set up per symbol (via subscribe_symbol)
            # Start stream in background task (run() is synchronous/blocking)
            # Use asyncio.to_thread to run blocking function in thread pool
            self._is_running = True
            self._stream_task = asyncio.create_task(asyncio.to_thread(self._run_stream_sync))
            
            logger.info("✅ Alpaca Market Data WebSocket stream started")
            
        except Exception as e:
            logger.error(f"Failed to start WebSocket stream: {e}", exc_info=True)
            self._is_running = False
            raise
    
    async def stop(self) -> None:
        """
        Stop WebSocket connection.
        
        Idempotent: Safe to call multiple times.
        """
        if not self._is_running:
            return
        
        logger.info("🛑 Stopping Alpaca Market Data WebSocket stream")
        
        self._is_running = False
        
        # Cancel stream task
        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        
        # Stop stream
        if self.stream:
            try:
                # Stop WebSocket connection (stop is sync, close is async)
                self.stream.stop()  # Stop the stream (synchronous)
                await self.stream.close()  # Close the connection (async - must await)
                self.stream = None
            except Exception as e:
                logger.debug(f"Error stopping stream: {e}")
        
        # Clear caches (thread-safe locks)
        with self._quote_cache_lock:
            self._quote_cache.clear()
            self._latest_quote_cache.clear()
        with self._trade_cache_lock:
            self._trade_cache.clear()
        
        with self._subscription_lock:
            self._subscribed_symbols.clear()
        
        logger.info("Alpaca Market Data WebSocket stream stopped")
    
    def _run_stream_sync(self) -> None:
        """
        Run the WebSocket stream (synchronous/blocking).
        
        This runs in a thread since run() is blocking.
        """
        try:
            if self.stream:
                self.stream.run()  # Blocking call
        except Exception as e:
            logger.error(f"WebSocket stream error: {e}", exc_info=True)
            self._is_running = False
    
    async def subscribe_symbol(self, symbol: str) -> None:
        """
        Subscribe to real-time quotes and trades for a symbol.
        
        Args:
            symbol: Ticker symbol to subscribe to
        """
        with self._subscription_lock:  # Thread-safe lock
            if symbol in self._subscribed_symbols:
                return  # Already subscribed
            
            if not self.stream:
                logger.warning(f"Cannot subscribe to {symbol}: stream not started")
                return
            
            try:
                # Subscribe to quotes and trades for this symbol
                # subscribe_quotes/trades can be called multiple times with different symbols
                if self.stream:
                    self.stream.subscribe_quotes(self._handle_quote_update, symbol)
                    self.stream.subscribe_trades(self._handle_trade_update, symbol)
                
                self._subscribed_symbols.add(symbol)
                
                logger.info(f"✅ Subscribed to {symbol} for real-time quotes and trades")
                
            except Exception as e:
                logger.error(f"Failed to subscribe to {symbol}: {e}", exc_info=True)

    async def unsubscribe_symbol(self, symbol: str) -> None:
        """
        Unsubscribe from real-time quotes and trades for a symbol.

        Called when a position is fully exited to clean up resources and
        prevent memory leaks from accumulating subscriptions.

        Args:
            symbol: Ticker symbol to unsubscribe from
        """
        with self._subscription_lock:  # Thread-safe lock
            if symbol not in self._subscribed_symbols:
                return  # Not subscribed

            if not self.stream:
                logger.warning(f"Cannot unsubscribe from {symbol}: stream not started")
                return

            try:
                # Unsubscribe from quotes and trades
                if self.stream:
                    self.stream.unsubscribe_quotes(symbol)
                    self.stream.unsubscribe_trades(symbol)

                self._subscribed_symbols.discard(symbol)

                # Clear cached data for this symbol to free memory
                with self._quote_cache_lock:
                    self._quote_cache.pop(symbol, None)
                    self._latest_quote_cache.pop(symbol, None)
                with self._trade_cache_lock:
                    self._trade_cache.pop(symbol, None)

                logger.info(f"🔌 Unsubscribed from {symbol} quotes and trades (position exited)")

            except Exception as e:
                logger.error(f"Failed to unsubscribe from {symbol}: {e}", exc_info=True)

    async def get_latest_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get latest cached quote for a symbol.
        
        Args:
            symbol: Ticker symbol
            
        Returns:
            Quote dict with bid, ask, spread, mid, or None if not available
        """
        # Ensure subscribed
        await self.subscribe_symbol(symbol)
        
        with self._quote_cache_lock:  # Thread-safe lock
            return self._latest_quote_cache.get(symbol)
    
    async def get_recent_quotes(self, symbol: str, max_quotes: int = 1000) -> list[Dict[str, Any]]:
        """
        Get recent cached quotes for a symbol (for volume analysis).
        
        Args:
            symbol: Ticker symbol
            max_quotes: Maximum number of quotes to return
            
        Returns:
            List of quote dicts (most recent first)
        """
        # Ensure subscribed
        await self.subscribe_symbol(symbol)
        
        with self._quote_cache_lock:  # Thread-safe lock
            quotes = self._quote_cache.get(symbol, deque())
            return list(quotes)[-max_quotes:]
    
    async def get_recent_trades(self, symbol: str, max_trades: int = 1000) -> list[Dict[str, Any]]:
        """
        Get recent cached trades for a symbol.
        
        Args:
            symbol: Ticker symbol
            max_trades: Maximum number of trades to return
            
        Returns:
            List of trade dicts (most recent first)
        """
        # Ensure subscribed
        await self.subscribe_symbol(symbol)
        
        with self._trade_cache_lock:  # Thread-safe lock
            trades = self._trade_cache.get(symbol, deque())
            return list(trades)[-max_trades:]
    
    async def _handle_quote_update(self, quote) -> None:
        """
        Handle incoming quote update from WebSocket.
        
        This runs in the WebSocket thread's event loop (created by StockDataStream.run()).
        Uses thread-safe locks for cache access and thread-safe event publishing.
        
        Args:
            quote: Quote object from Alpaca SDK
        """
        try:
            symbol = quote.symbol
            bid = float(quote.bid_price) if quote.bid_price and quote.bid_price > 0 else None
            ask = float(quote.ask_price) if quote.ask_price and quote.ask_price > 0 else None
            
            if bid is None or ask is None:
                logger.debug(f"Quote update missing bid/ask for {symbol}")
                return
            
            spread = ask - bid
            mid = (bid + ask) / 2.0
            bid_size = int(quote.bid_size) if hasattr(quote, 'bid_size') and quote.bid_size else None
            ask_size = int(quote.ask_size) if hasattr(quote, 'ask_size') and quote.ask_size else None
            
            quote_dict = {
                "bid": bid,
                "ask": ask,
                "spread": spread,
                "mid": mid,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "timestamp": datetime.now()
            }
            
            # Update cache (thread-safe lock - handlers run in WebSocket thread)
            with self._quote_cache_lock:
                # Store in history deque (for volume analysis)
                if symbol not in self._quote_cache:
                    self._quote_cache[symbol] = deque(maxlen=self._max_trades_per_symbol)
                self._quote_cache[symbol].append(quote_dict)
                
                # Also store latest for quick NBBO access
                self._latest_quote_cache[symbol] = quote_dict
            
            # Publish event (thread-safe - schedules on main event loop)
            self._publish_event_threadsafe(self._publish_quote_event(symbol, bid, ask, spread))
            
            logger.info(
                "✅ WebSocket quote update",
                symbol=symbol,
                bid=bid,
                ask=ask,
                spread=spread
            )
            
        except Exception as e:
            logger.error(f"Error handling quote update: {e}", exc_info=True)
    
    async def _handle_trade_update(self, trade) -> None:
        """
        Handle incoming trade update from WebSocket.
        
        Args:
            trade: Trade object from Alpaca SDK
        """
        try:
            symbol = trade.symbol
            price = float(trade.price) if trade.price else None
            size = int(trade.size) if trade.size else None
            timestamp = trade.timestamp if hasattr(trade, 'timestamp') else datetime.now()
            
            if price is None or size is None:
                logger.debug(f"Trade update missing price/size for {symbol}")
                return
            
            trade_dict = {
                "symbol": symbol,
                "price": price,
                "size": size,
                "timestamp": timestamp
            }
            
            # Update cache (thread-safe lock - handlers run in WebSocket thread)
            with self._trade_cache_lock:
                if symbol not in self._trade_cache:
                    self._trade_cache[symbol] = deque(maxlen=self._max_trades_per_symbol)
                self._trade_cache[symbol].append(trade_dict)
            
            logger.debug(
                "✅ WebSocket trade update",
                symbol=symbol,
                price=price,
                size=size
            )
            
        except Exception as e:
            logger.error(f"Error handling trade update: {e}", exc_info=True)
    
    def _publish_event_threadsafe(self, coro) -> None:
        """
        Publish an async event from WebSocket thread, scheduling it on the main event loop.
        
        Pattern from BenzingaWebSocketMicroservice - allows async operations from threads.
        """
        if self._main_event_loop and self._main_event_loop.is_running():
            self._main_event_loop.call_soon_threadsafe(lambda: asyncio.create_task(coro))
        else:
            # Fallback: try to get current loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(coro))
                else:
                    logger.warning("Event loop not running, cannot publish event")
            except RuntimeError:
                logger.warning("No event loop available, cannot publish event")
    
    async def _publish_quote_event(self, symbol: str, bid: float, ask: float, spread: float) -> None:
        """Publish quote received event."""
        quote_data = InfrastructureQuoteData(
            bid=bid,
            ask=ask,
            last=None,  # WebSocket quotes don't include last price
            volume=None,  # WebSocket quotes don't include volume
            spread=spread
        )
        
        event = QuoteReceivedEvent(
            symbol=symbol,
            nbbo=quote_data,
            received_at=datetime.now(),
            source="brokerage.websocket"
        )
        
        await self.event_bus.publish("QuoteReceived", event.model_dump())
        logger.debug(f"Published QuoteReceived event for {symbol} (from WebSocket)")
