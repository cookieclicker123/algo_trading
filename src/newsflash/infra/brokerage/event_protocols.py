"""
Event protocols for brokerage infrastructure events.

Protocols define the CONTRACT using typed models - ensuring type safety.
"""
from typing import Protocol

from .infrastructure_models import (
    InfrastructureTradeExecutionRequestEvent,
    InfrastructureTradeExecutedEvent,
    InfrastructureTradeFailedEvent,
    InfrastructureQuoteReceivedEvent,
    InfrastructureConnectionStatusEvent,
    InfrastructureBrokerageHealthEvent,
    InfrastructureTradeQueuedEvent
)


class InfrastructureTradeExecutionRequestEventPublisher(Protocol):
    """
    Protocol for publishing TradeExecutionRequest infrastructure events.
    
    Ensures infrastructure events match the typed model contract.
    """
    
    async def publish_trade_execution_request(self, event: InfrastructureTradeExecutionRequestEvent) -> None:
        """
        Publish TradeExecutionRequest infrastructure event.
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        ...


class InfrastructureTradeExecutionRequestEventSubscriber(Protocol):
    """
    Protocol for subscribing to TradeExecutionRequest infrastructure events.
    
    Infrastructure services implement this to receive typed infrastructure events.
    """
    
    async def handle_trade_execution_request(self, event: InfrastructureTradeExecutionRequestEvent) -> None:
        """
        Handle TradeExecutionRequest infrastructure event.
        
        Args:
            event: Typed infrastructure event model
        """
        ...


class InfrastructureTradeExecutedEventPublisher(Protocol):
    """Protocol for publishing TradeExecuted infrastructure events."""
    
    async def publish_trade_executed(self, event: InfrastructureTradeExecutedEvent) -> None:
        """Publish TradeExecuted infrastructure event."""
        ...


class InfrastructureTradeExecutedEventSubscriber(Protocol):
    """Protocol for subscribing to TradeExecuted infrastructure events."""
    
    async def handle_trade_executed(self, event: InfrastructureTradeExecutedEvent) -> None:
        """Handle TradeExecuted infrastructure event."""
        ...


class InfrastructureTradeFailedEventPublisher(Protocol):
    """Protocol for publishing TradeFailed infrastructure events."""
    
    async def publish_trade_failed(self, event: InfrastructureTradeFailedEvent) -> None:
        """Publish TradeFailed infrastructure event."""
        ...


class InfrastructureTradeFailedEventSubscriber(Protocol):
    """Protocol for subscribing to TradeFailed infrastructure events."""
    
    async def handle_trade_failed(self, event: InfrastructureTradeFailedEvent) -> None:
        """Handle TradeFailed infrastructure event."""
        ...


class InfrastructureQuoteReceivedEventPublisher(Protocol):
    """Protocol for publishing QuoteReceived infrastructure events."""
    
    async def publish_quote_received(self, event: InfrastructureQuoteReceivedEvent) -> None:
        """Publish QuoteReceived infrastructure event."""
        ...


class InfrastructureQuoteReceivedEventSubscriber(Protocol):
    """Protocol for subscribing to QuoteReceived infrastructure events."""
    
    async def handle_quote_received(self, event: InfrastructureQuoteReceivedEvent) -> None:
        """Handle QuoteReceived infrastructure event."""
        ...


class InfrastructureConnectionStatusEventPublisher(Protocol):
    """Protocol for publishing ConnectionStatus infrastructure events."""
    
    async def publish_connection_status(self, event: InfrastructureConnectionStatusEvent) -> None:
        """Publish ConnectionStatus infrastructure event."""
        ...


class InfrastructureBrokerageHealthEventPublisher(Protocol):
    """Protocol for publishing BrokerageHealth infrastructure events."""
    
    async def publish_brokerage_health(self, event: InfrastructureBrokerageHealthEvent) -> None:
        """Publish BrokerageHealth infrastructure event."""
        ...


class InfrastructureTradeQueuedEventPublisher(Protocol):
    """Protocol for publishing TradeQueued infrastructure events."""
    
    async def publish_trade_queued(self, event: InfrastructureTradeQueuedEvent) -> None:
        """Publish TradeQueued infrastructure event."""
        ...
