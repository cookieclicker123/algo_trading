"""
Alpaca Market Data WebSocket stream manager.

Manages WebSocket connection for real-time quotes and trades.
Pure infrastructure - publishes events, no business logic.
"""
import os
import asyncio
import threading
import time
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
        
        # Subscription tracking with reference counting (thread-safe locks for cross-thread access)
        # Multiple components (price_monitor, position_manager) may subscribe to same symbol
        # Only unsubscribe from WebSocket when refcount reaches 0
        self._subscribed_symbols: Set[str] = set()  # Actual WebSocket subscriptions
        self._subscription_refcount: Dict[str, int] = {}  # Reference counting per symbol
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

        # Quote event throttling - publish at most once per 100ms per symbol
        # Cache is ALWAYS updated (real-time), events are throttled to reduce event loop load
        # This prevents event loop saturation at market open while keeping cache fully accurate
        self._quote_event_throttle_ms = 100  # Publish events at most every 100ms per symbol
        self._last_quote_publish: Dict[str, float] = {}  # symbol -> timestamp (time.time())
        self._quote_publish_lock = threading.Lock()

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
        with self._quote_publish_lock:
            self._last_quote_publish.clear()

        with self._subscription_lock:
            self._subscribed_symbols.clear()
            self._subscription_refcount.clear()

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

        Uses reference counting - multiple components can subscribe to the same symbol.
        WebSocket subscription only happens on first subscribe.

        Args:
            symbol: Ticker symbol to subscribe to
        """
        # Check refcount and mark as pending (fast, under lock)
        needs_subscription = False
        with self._subscription_lock:
            current_count = self._subscription_refcount.get(symbol, 0)
            self._subscription_refcount[symbol] = current_count + 1

            if symbol in self._subscribed_symbols:
                logger.debug(f"Incremented subscription refcount for {symbol} to {current_count + 1}")
                return

            if not self.stream:
                logger.warning(f"Cannot subscribe to {symbol}: stream not started")
                return

            # Mark that we need to subscribe (will do outside lock)
            needs_subscription = True
            self._subscribed_symbols.add(symbol)

        if needs_subscription:
            try:
                # Run blocking SDK calls in thread pool to avoid blocking event loop
                # The alpaca-py SDK's subscribe methods do synchronous network I/O
                await asyncio.to_thread(self._subscribe_symbol_sync, symbol)
                logger.info(f"✅ Subscribed to {symbol} for real-time quotes and trades (refcount=1)")

            except Exception as e:
                logger.error(f"Failed to subscribe to {symbol}: {e}", exc_info=True)
                # Rollback on failure
                with self._subscription_lock:
                    self._subscribed_symbols.discard(symbol)
                    self._subscription_refcount[symbol] = self._subscription_refcount.get(symbol, 1) - 1

    def _subscribe_symbol_sync(self, symbol: str) -> None:
        """Synchronous subscription - runs in thread pool to avoid blocking event loop."""
        if self.stream:
            self.stream.subscribe_quotes(self._handle_quote_update, symbol)
            self.stream.subscribe_trades(self._handle_trade_update, symbol)

    def _unsubscribe_symbol_sync(self, symbol: str) -> None:
        """Synchronous unsubscription - runs in thread pool to avoid blocking event loop."""
        if self.stream:
            self.stream.unsubscribe_quotes(symbol)
            self.stream.unsubscribe_trades(symbol)

    async def unsubscribe_symbol(self, symbol: str) -> None:
        """
        Unsubscribe from real-time quotes and trades for a symbol.

        Uses reference counting - only actually unsubscribes from WebSocket when
        refcount reaches 0. Safe to call even if another component is still using quotes.

        Args:
            symbol: Ticker symbol to unsubscribe from
        """
        needs_unsubscription = False
        with self._subscription_lock:
            current_count = self._subscription_refcount.get(symbol, 0)
            if current_count <= 0:
                logger.debug(f"Unsubscribe called for {symbol} but refcount already 0")
                return

            new_count = current_count - 1
            self._subscription_refcount[symbol] = new_count

            if new_count > 0:
                logger.debug(f"Decremented subscription refcount for {symbol} to {new_count}")
                return

            # Refcount is now 0 - mark for unsubscription
            if symbol not in self._subscribed_symbols:
                return

            if not self.stream:
                logger.warning(f"Cannot unsubscribe from {symbol}: stream not started")
                return

            needs_unsubscription = True
            self._subscribed_symbols.discard(symbol)
            self._subscription_refcount.pop(symbol, None)

        if needs_unsubscription:
            try:
                # Run blocking SDK calls in thread pool to avoid blocking event loop
                await asyncio.to_thread(self._unsubscribe_symbol_sync, symbol)

                # Clear cached data for this symbol to free memory
                with self._quote_cache_lock:
                    self._quote_cache.pop(symbol, None)
                    self._latest_quote_cache.pop(symbol, None)
                with self._trade_cache_lock:
                    self._trade_cache.pop(symbol, None)
                with self._quote_publish_lock:
                    self._last_quote_publish.pop(symbol, None)

                logger.info(f"🔌 Unsubscribed from {symbol} quotes and trades (refcount=0)")

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
            
            spread_pct = round((spread / mid) * 100, 2) if mid > 0 else None

            quote_dict = {
                "bid": bid,
                "ask": ask,
                "spread": spread,
                "spread_pct": spread_pct,
                "mid": mid,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "timestamp": datetime.now()
            }
            
            # Update cache (thread-safe lock - handlers run in WebSocket thread)
            # ALWAYS update cache - this is the real-time data source for execution
            with self._quote_cache_lock:
                # Store in history deque (for volume analysis)
                if symbol not in self._quote_cache:
                    self._quote_cache[symbol] = deque(maxlen=self._max_trades_per_symbol)
                self._quote_cache[symbol].append(quote_dict)

                # Also store latest for quick NBBO access
                self._latest_quote_cache[symbol] = quote_dict

            # Throttled event publishing - reduces event loop load during high-volume periods
            # Cache is real-time, events are sampled at 10Hz max per symbol
            # This prevents WebSocket disconnections at market open while keeping execution fast
            should_publish = False
            current_time = time.time()
            with self._quote_publish_lock:
                last_publish = self._last_quote_publish.get(symbol, 0)
                elapsed_ms = (current_time - last_publish) * 1000
                if elapsed_ms >= self._quote_event_throttle_ms:
                    self._last_quote_publish[symbol] = current_time
                    should_publish = True

            if should_publish:
                self._publish_event_threadsafe(self._publish_quote_event(symbol, bid, ask, spread))
                logger.debug(
                    "WebSocket quote event published",
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
