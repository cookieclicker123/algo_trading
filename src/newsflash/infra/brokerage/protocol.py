"""
Protocol/interface definitions for brokerage microservice.
"""
from typing import Protocol, Optional

from ...models.base_models import TradeRequest


class BrokerageServiceProtocol(Protocol):
    """
    Protocol for brokerage service implementations.
    
    Defines the contract that brokerage services must implement,
    allowing different implementations (IBKR, others) to be swapped.
    """
    
    def start(self) -> None:
        """Start the brokerage service connection."""
        ...
    
    def stop(self) -> None:
        """Stop the brokerage service connection."""
        ...
    
    def is_connected(self) -> bool:
        """Check if brokerage service is connected."""
        ...
    
    def get_stats(self) -> dict:
        """Get brokerage service statistics."""
        ...
    
    def is_healthy(self) -> bool:
        """Check if brokerage service is healthy."""
        ...


class TradeExecutorProtocol(Protocol):
    """
    Protocol for trade execution implementations.
    
    Defines the contract for executing trades through brokerage.
    """
    
    async def execute_trade(self, trade_request: TradeRequest) -> dict:
        """
        Execute a trade request.
        
        Args:
            trade_request: Trade request to execute
            
        Returns:
            Trade result dictionary with success, shares, fill_price, etc.
        """
        ...


class QuoteFetcherProtocol(Protocol):
    """
    Protocol for fetching market quotes/NBBO.
    
    Defines the contract for retrieving market data.
    """
    
    async def get_quote(self, symbol: str) -> Optional[dict]:
        """
        Get current quote/NBBO for a symbol.
        
        Args:
            symbol: Stock ticker symbol
            
        Returns:
            Quote dictionary with bid, ask, spread, etc. or None if unavailable
        """
        ...

