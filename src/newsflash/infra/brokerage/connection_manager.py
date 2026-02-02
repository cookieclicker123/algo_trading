"""
Alpaca connection manager - simple async HTTP client.
Pure infrastructure - publishes events, no business logic.
"""
import os
from typing import Optional
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from dotenv import load_dotenv

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from .events import ConnectionStatusChangedEvent

load_dotenv()

logger = get_logger(__name__)

# Optional WebSocket stream manager (graceful degradation if not available)
try:
    from .stream_manager import AlpacaMarketDataStreamManager
except ImportError:
    AlpacaMarketDataStreamManager = None


class AlpacaConnectionManager:
    """
    Manages Alpaca API connection via REST API.
    
    Responsibilities:
    - Connection establishment and verification
    - Publishing connection status events
    
    Does NOT:
    - Execute trades
    - Know about business logic
    - Send Telegram notifications (publishes events instead)
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        metrics_service,  # Required - injected via DI
        paper_trading: bool = True,
    ):
        """
        Initialize connection manager.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            paper_trading: Whether to use paper trading
            metrics_service: Metrics service for statistics (injected via DI)
        """
        self.paper_trading = paper_trading
        self.metrics_service = metrics_service
        self.event_bus = event_bus
        
        # Get API credentials from environment
        api_key = os.getenv("ALPACA_KEY")
        api_secret = os.getenv("ALPACA_SECRET")

        if not api_key or not api_secret:
            raise ValueError("ALPACA_KEY and ALPACA_SECRET must be set in environment")

        # Initialize Alpaca TradingClient (primary - live or paper based on paper_trading flag)
        self.trading_client = TradingClient(
            api_key=api_key,
            secret_key=api_secret,
            paper=paper_trading
        )

        # Initialize shadow paper trading client for parallel paper trades
        # Only create if we're doing live trading AND paper keys are available
        self.paper_shadow_client: Optional[TradingClient] = None
        if not paper_trading:  # We're in live mode
            paper_key = os.getenv("ALPACA_KEY_PAPER")
            paper_secret = os.getenv("ALPACA_SECRET_PAPER")
            if paper_key and paper_secret:
                try:
                    self.paper_shadow_client = TradingClient(
                        api_key=paper_key,
                        secret_key=paper_secret,
                        paper=True  # Always paper for shadow
                    )
                    logger.info("✅ Shadow paper trading client initialized (will mirror live trades)")
                except Exception as e:
                    logger.warning(f"Could not initialize shadow paper client: {e}")
                    self.paper_shadow_client = None
            else:
                logger.info("No ALPACA_KEY_PAPER/ALPACA_SECRET_PAPER - shadow paper trading disabled")

        # Initialize Alpaca Market Data Client for quotes (use primary keys)
        self.market_data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=api_secret
        )
        
        # Initialize WebSocket stream manager (optional - graceful degradation)
        # Try SIP first (requires Algo Trader Plus subscription), fall back to IEX
        self.stream_manager: Optional[AlpacaMarketDataStreamManager] = None
        if AlpacaMarketDataStreamManager is not None:
            try:
                from alpaca.data.enums import DataFeed
                # Use SIP feed for true NBBO (requires Algo Trader Plus subscription)
                # SIP provides consolidated data from all US exchanges
                self.stream_manager = AlpacaMarketDataStreamManager(
                    event_bus=event_bus,
                    api_key=api_key,
                    api_secret=api_secret,
                    paper_trading=paper_trading,
                    feed=DataFeed.SIP  # SIP for true NBBO (algo trader subscription)
                )
                logger.info("WebSocket stream manager initialized with SIP feed (true NBBO)")
            except Exception as e:
                # SIP failed - try IEX as fallback
                logger.warning(f"WebSocket SIP feed failed, trying IEX fallback: {e}")
                try:
                    from alpaca.data.enums import DataFeed
                    self.stream_manager = AlpacaMarketDataStreamManager(
                        event_bus=event_bus,
                        api_key=api_key,
                        api_secret=api_secret,
                        paper_trading=paper_trading,
                        feed=DataFeed.IEX  # IEX fallback
                    )
                    logger.info("WebSocket stream manager initialized with IEX feed (fallback)")
                except Exception as e2:
                    logger.warning(f"WebSocket stream manager not available (fallback to REST): {e2}")
                    self.stream_manager = None
        else:
            logger.debug("WebSocket stream manager not available (alpaca-py SDK version)")
        
        self.is_connected = False
        
        mode = "Paper Trading" if paper_trading else "Live Trading"
        logger.info(
            f"Alpaca Connection Manager initialized for {mode}",
            paper_trading=paper_trading,
            websocket_available=self.stream_manager is not None
        )
    
    def get_trading_client(self) -> TradingClient:
        """Get the current Alpaca trading client instance."""
        return self.trading_client if self.is_connected else None

    def get_paper_shadow_client(self) -> Optional[TradingClient]:
        """Get the paper shadow trading client for parallel paper trades."""
        return self.paper_shadow_client if self.is_connected else None
    
    async def start(self) -> None:
        """
        Start the connection manager and verify connection.
        
        Idempotent: Safe to call multiple times.
        """
        logger.info("🚀 Starting Alpaca Connection Manager")
        
        try:
            # Verify connection with a simple API call
            account = self.trading_client.get_account()
            self.is_connected = True
            
            logger.info(
                "✅ Alpaca Connection Manager connected",
                account_number=account.account_number,
                buying_power=float(account.buying_power)
            )
            
            # Publish connection status
            await self._publish_connection_status(True, "Connected successfully")
            
            # Start WebSocket stream manager (optional - failures don't affect REST API)
            if self.stream_manager:
                try:
                    await self.stream_manager.start()
                    logger.info("WebSocket stream manager started")
                except Exception as e:
                    logger.warning(f"WebSocket stream manager failed to start (REST API still available): {e}")
                    # Don't fail connection - REST API is still available
            
        except Exception as e:
            logger.error(f"Failed to connect to Alpaca: {e}", exc_info=True)
            self.is_connected = False
            await self._publish_connection_status(False, f"Connection failed: {str(e)}")
            raise
    
    async def stop(self) -> None:
        """
        Stop the connection manager.
        
        Idempotent: Safe to call multiple times.
        """
        logger.info("🛑 Stopping Alpaca Connection Manager")
        
        self.is_connected = False
        
        # Stop WebSocket stream manager (optional - failures don't affect cleanup)
        if self.stream_manager:
            try:
                await self.stream_manager.stop()
                logger.info("WebSocket stream manager stopped")
            except Exception as e:
                logger.warning(f"Error stopping WebSocket stream manager: {e}")
        
        # Publish disconnection event
        await self._publish_connection_status(False, "Connection manager stopped")
        
        logger.info("Alpaca Connection Manager stopped")
    
    async def ensure_connected(self, timeout_seconds: Optional[float] = None) -> TradingClient:
        """
        Ensure connection is available.
        
        Args:
            timeout_seconds: Not used for REST API (always available)
            
        Returns:
            TradingClient instance
        """
        if not self.is_connected:
            await self.start()
        
        return self.trading_client
    
    async def _publish_connection_status(self, is_connected: bool, reason: Optional[str] = None) -> None:
        """Publish connection status event."""
        event = ConnectionStatusChangedEvent(
            is_connected=is_connected,
            paper_trading=self.paper_trading,
            changed_at=datetime.now(),
            reason=reason,
            source="brokerage"
        )
        await self.event_bus.publish("ConnectionStatusChanged", event.model_dump())
        logger.debug("Published ConnectionStatusChanged event", is_connected=is_connected, reason=reason)
