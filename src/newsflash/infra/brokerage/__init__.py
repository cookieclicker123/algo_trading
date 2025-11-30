"""
Brokerage infrastructure microservice.
Handles connection management, trade execution, and market data.

This microservice provides:
- Connection management (IBKR Gateway)
- Trade execution (market hours & extended hours)
- Quote fetching and NBBO management
- Queue management for closed-market trades
- Event publishing for trade lifecycle

All components are event-driven and decoupled from business logic.
"""

from .service import IBKRBrokerageService
from .connection_manager import IBKRConnectionManager
from .quote_fetcher import IBKRQuoteFetcher
from .queue_manager import TradeQueueManager
from .trade_executor_market_hours import MarketHoursTradeExecutor
from .trade_executor_extended_hours import ExtendedHoursTradeExecutor
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
    "IBKRBrokerageService",
    # Core components
    "IBKRConnectionManager",
    "IBKRQuoteFetcher",
    "TradeQueueManager",
    "MarketHoursTradeExecutor",
    "ExtendedHoursTradeExecutor",
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
