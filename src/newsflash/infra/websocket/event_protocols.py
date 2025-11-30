"""
Event protocols for WebSocket infrastructure events.

Protocols define the CONTRACT using typed models - ensuring type safety.
All protocols use typed infrastructure models, not Dict[str, Any].
"""
from typing import Protocol

from .infrastructure_models import (
    ArticleReceivedInfrastructureEvent,
    WebSocketConnectedInfrastructureEvent,
    WebSocketDisconnectedInfrastructureEvent,
    WebSocketErrorInfrastructureEvent,
    WebSocketHealthStatusInfrastructureEvent
)


class InfrastructureArticleEventPublisher(Protocol):
    """
    Protocol for publishing ArticleReceived infrastructure events.
    
    Ensures infrastructure events match the typed model contract.
    """
    
    async def publish_article_received(self, event: ArticleReceivedInfrastructureEvent) -> None:
        """
        Publish ArticleReceived infrastructure event.
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        ...


class InfrastructureWebSocketHealthEventPublisher(Protocol):
    """Protocol for publishing WebSocket health infrastructure events."""
    
    async def publish_health_status(self, event: WebSocketHealthStatusInfrastructureEvent) -> None:
        """
        Publish WebSocket health status event.
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        ...


class InfrastructureWebSocketConnectedEventPublisher(Protocol):
    """Protocol for publishing WebSocket connected infrastructure events."""
    
    async def publish_connected(self, event: WebSocketConnectedInfrastructureEvent) -> None:
        """Publish WebSocket connected event."""
        ...


class InfrastructureWebSocketDisconnectedEventPublisher(Protocol):
    """Protocol for publishing WebSocket disconnected infrastructure events."""
    
    async def publish_disconnected(self, event: WebSocketDisconnectedInfrastructureEvent) -> None:
        """Publish WebSocket disconnected event."""
        ...


class InfrastructureWebSocketErrorEventPublisher(Protocol):
    """Protocol for publishing WebSocket error infrastructure events."""
    
    async def publish_error(self, event: WebSocketErrorInfrastructureEvent) -> None:
        """Publish WebSocket error event."""
        ...


class InfrastructureArticleEventSubscriber(Protocol):
    """
    Protocol for subscribing to ArticleReceived infrastructure events.
    
    Domain listeners implement this to receive typed infrastructure events.
    """
    
    async def handle_article_received(self, event: ArticleReceivedInfrastructureEvent) -> None:
        """
        Handle ArticleReceived infrastructure event.
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        ...


class InfrastructureWebSocketHealthEventSubscriber(Protocol):
    """Protocol for subscribing to WebSocket health infrastructure events."""
    
    async def handle_health_status(self, event: WebSocketHealthStatusInfrastructureEvent) -> None:
        """Handle WebSocket health status event."""
        ...
