"""
Protocol/interface definitions for WebSocket microservice.
"""
from typing import Protocol

class WebSocketServiceProtocol(Protocol):
    """
    Protocol for WebSocket service implementations.
    
    Defines the contract that WebSocket services must implement,
    allowing different implementations (Benzinga, others) to be swapped.
    """
    
    def start(self) -> None:
        """Start the WebSocket connection."""
        ...
    
    def stop(self) -> None:
        """Stop the WebSocket connection."""
        ...
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        ...
    
    def get_stats(self) -> dict:
        """Get WebSocket service statistics."""
        ...
    
    def is_healthy(self) -> bool:
        """Check if WebSocket service is healthy."""
        ...

