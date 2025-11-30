"""
Event definitions for WebSocket microservice.

Infrastructure events use infrastructure-specific typed models.
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any

from pydantic import Field
from .infrastructure_models import InfrastructureArticleData


class ArticleReceivedEvent(BaseModel):
    """
    Infrastructure event published when a news article is received from WebSocket.
    
    Uses infrastructure-specific typed model - not domain models, not shared models.
    """
    article_data: InfrastructureArticleData = Field(..., description="Infrastructure article data (typed model)")
    received_at: datetime
    source: str = "benzinga_websocket"


class WebSocketConnectedEvent(BaseModel):
    """Event published when WebSocket connects."""
    connected_at: datetime
    source: str = "benzinga_websocket"


class WebSocketDisconnectedEvent(BaseModel):
    """Event published when WebSocket disconnects."""
    disconnected_at: datetime
    reason: Optional[str] = None
    source: str = "benzinga_websocket"


class WebSocketErrorEvent(BaseModel):
    """Event published when WebSocket encounters an error."""
    error: str
    occurred_at: datetime
    source: str = "benzinga_websocket"
    is_rate_limit: bool = False  # True if this is a 429 rate limit error


class WebSocketRateLimitEvent(BaseModel):
    """Event published when rate limit (429) is hit."""
    occurred_at: datetime
    source: str = "benzinga_websocket"
    message: str = "Rate limit exceeded - connection will not auto-reconnect"


class WebSocketHealthStatusEvent(BaseModel):
    """Event published periodically with WebSocket health status."""
    healthy: bool
    status: str  # healthy, disconnected, inactive, zombie, error
    reason: str
    occurred_at: datetime
    source: str = "benzinga_websocket"
    details: Dict[str, Any] = {}

