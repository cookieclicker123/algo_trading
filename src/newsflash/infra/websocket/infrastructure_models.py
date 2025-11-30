"""
Infrastructure-specific models for WebSocket events.

These are infrastructure's own typed models - NOT domain models.
Infrastructure owns these completely.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any


class InfrastructureArticleData(BaseModel):
    """
    Infrastructure article data model - raw format from WebSocket.
    
    This is infrastructure's own representation - not domain's.
    Infrastructure can change this format without affecting domain.
    """
    # Raw WebSocket fields (Benzinga-specific or infrastructure-specific)
    benzinga_id: Optional[int] = None
    source_id: Optional[str] = None
    title: str
    headline: Optional[str] = None
    content: Optional[str] = None
    body: Optional[str] = None
    teaser: Optional[str] = None
    summary: Optional[str] = None
    author: Optional[str] = None
    published: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_updated: Optional[str] = None
    url: Optional[str] = None
    tickers: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    securities: list[Dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    
    # Raw infrastructure data
    raw_data: Dict[str, Any] = Field(default_factory=dict)


class ArticleReceivedInfrastructureEvent(BaseModel):
    """
    Infrastructure event model - typed, validated, infrastructure-specific.
    
    This is what infrastructure publishes - fully typed model.
    """
    article_data: InfrastructureArticleData = Field(..., description="Infrastructure article data model")
    received_at: datetime = Field(..., description="When article was received")
    source: str = Field(default="benzinga_websocket", description="Infrastructure source")
    
    model_config = {"frozen": False}  # Events can be mutable for serialization


class WebSocketConnectedInfrastructureEvent(BaseModel):
    """Infrastructure event - WebSocket connected."""
    connected_at: datetime
    source: str = "benzinga_websocket"


class WebSocketDisconnectedInfrastructureEvent(BaseModel):
    """Infrastructure event - WebSocket disconnected."""
    disconnected_at: datetime
    reason: Optional[str] = None
    source: str = "benzinga_websocket"


class WebSocketErrorInfrastructureEvent(BaseModel):
    """Infrastructure event - WebSocket error."""
    error: str
    occurred_at: datetime
    source: str = "benzinga_websocket"
    is_rate_limit: bool = False


class WebSocketHealthStatusInfrastructureEvent(BaseModel):
    """Infrastructure event - WebSocket health status."""
    healthy: bool
    status: str
    reason: str
    occurred_at: datetime
    source: str = "benzinga_websocket"
    details: Dict[str, Any] = Field(default_factory=dict)

