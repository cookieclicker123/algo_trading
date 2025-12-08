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
        
        # Initialize Alpaca TradingClient
        self.trading_client = TradingClient(
            api_key=api_key,
            secret_key=api_secret,
            paper=paper_trading
        )
        
        # Initialize Alpaca Market Data Client for quotes
        self.market_data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=api_secret
        )
        
        self.is_connected = False
        
        mode = "Paper Trading" if paper_trading else "Live Trading"
        logger.info(
            f"Alpaca Connection Manager initialized for {mode}",
            paper_trading=paper_trading
        )
    
    def get_trading_client(self) -> TradingClient:
        """Get the current Alpaca trading client instance."""
        return self.trading_client if self.is_connected else None
    
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
