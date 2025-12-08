"""
Brokerage infrastructure microservice.
Handles connection management, trade execution, and market data.

This microservice provides:
- Connection management (Alpaca REST API)
- Trade execution (market hours & extended hours)
- Quote fetching and NBBO management
- Queue management for closed-market trades
- Event publishing for trade lifecycle

All components are event-driven and decoupled from business logic.
"""

from .service import BrokerageService
from .connection_manager import AlpacaConnectionManager
from .quote_fetcher import AlpacaQuoteFetcher
from .queue_manager import TradeQueueManager
from .trade_executor_market_hours import AlpacaMarketHoursTradeExecutor
from .trade_executor_extended_hours import AlpacaExtendedHoursTradeExecutor
from .events import (
    TradeExecutedEvent,
    TradeFailedEvent,
    QuoteReceivedEvent,
    ConnectionStatusChangedEvent,
    BrokerageHealthStatusEvent,
    TradeRequestQueuedEvent,
)
from .protocol import BrokerageServiceProtocol

__all__ = [
    # Main service
    "BrokerageService",
    # Core components
    "AlpacaConnectionManager",
    "AlpacaQuoteFetcher",
    "TradeQueueManager",
    "AlpacaMarketHoursTradeExecutor",
    "AlpacaExtendedHoursTradeExecutor",
    # Events
    "TradeExecutedEvent",
    "TradeFailedEvent",
    "QuoteReceivedEvent",
    "ConnectionStatusChangedEvent",
    "BrokerageHealthStatusEvent",
    "TradeRequestQueuedEvent",
    # Protocols
    "BrokerageServiceProtocol",
]
