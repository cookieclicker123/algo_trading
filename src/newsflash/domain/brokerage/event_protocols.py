"""
Event protocols for brokerage domain events.

Protocols define the CONTRACT using typed domain models - ensuring type safety.
"""
from typing import Protocol
from datetime import datetime

from .models import TradeRequest, TradeResult, Quote
from .events import (
    TradeRequestDomainEvent,
    TradeExecutedDomainEvent,
    TradeFailedDomainEvent,
    TradeQueuedDomainEvent,
    QuoteReceivedDomainEvent
)


class DomainTradeEventPublisher(Protocol):
    """
    Protocol for publishing trade domain events.
    
    Ensures domain events use typed domain models.
    """
    
    async def publish_trade_executed(self, trade_result: TradeResult, executed_at: datetime) -> None:
        """
        Publish TradeExecuted domain event.
        
        Args:
            trade_result: Typed domain TradeResult model (validated, immutable)
            executed_at: When trade was executed
        """
        ...
    
    async def publish_trade_failed(self, trade_request: TradeRequest, error: str, failed_at: datetime) -> None:
        """
        Publish TradeFailed domain event.
        
        Args:
            trade_request: Typed domain TradeRequest model
            error: Error message
            failed_at: When trade failed
        """
        ...
    
    async def publish_trade_queued(self, trade_request: TradeRequest, queued_at: datetime, target_premarket: datetime) -> None:
        """
        Publish TradeQueued domain event.
        
        Args:
            trade_request: Typed domain TradeRequest model
            queued_at: When trade was queued
            target_premarket: Target premarket time for execution
        """
        ...
    
    async def publish_quote_received(self, quote: Quote, received_at: datetime) -> None:
        """
        Publish QuoteReceived domain event.
        
        Args:
            quote: Typed domain Quote model (validated, immutable)
            received_at: When quote was received
        """
        ...


class DomainTradeEventSubscriber(Protocol):
    """
    Protocol for subscribing to trade domain events.
    
    Services implement this to receive typed domain events.
    """
    
    async def handle_trade_executed(self, event: TradeExecutedDomainEvent) -> None:
        """
        Handle TradeExecuted domain event.
        
        Args:
            event: Typed domain event model (contains validated TradeResult domain model)
        """
        ...
    
    async def handle_trade_failed(self, event: TradeFailedDomainEvent) -> None:
        """Handle TradeFailed domain event."""
        ...
    
    async def handle_trade_queued(self, event: TradeQueuedDomainEvent) -> None:
        """
        Handle TradeQueued domain event.
        
        Args:
            event: Typed domain event model (contains validated TradeRequest domain model)
        """
        ...
    
    async def handle_quote_received(self, event: QuoteReceivedDomainEvent) -> None:
        """
        Handle QuoteReceived domain event.
        
        Args:
            event: Typed domain event model (contains validated Quote domain model)
        """
        ...


class DomainTradeRequestPublisher(Protocol):
    """
    Protocol for publishing trade requests (use cases → domain).
    """
    
    async def publish_trade_request(self, event: TradeRequestDomainEvent) -> None:
        """
        Publish TradeRequest domain event.
        
        Args:
            event: Typed domain event model (contains validated TradeRequest domain model)
        """
        ...
