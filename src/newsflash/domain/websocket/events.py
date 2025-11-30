"""
Domain events for WebSocket/articles - business events published by domain layer.

These use domain models directly - fully typed, not Dict[str, Any].
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from .models import Article


class ArticleReceivedDomainEvent(BaseModel):
    """
    Domain event published when a valid article is received.
    
    Uses domain Article model directly - fully typed, validated.
    """
    article: Article = Field(..., description="Domain Article model (validated, immutable)")
    received_at: datetime = Field(..., description="When article was received in domain")
    source: str = Field(default="domain.websocket", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class ArticleValidationFailedDomainEvent(BaseModel):
    """
    Domain event published when article validation fails.
    """
    article_data: dict = Field(..., description="Raw article data that failed validation")
    validation_errors: list[str] = Field(default_factory=list, description="List of validation errors")
    failed_at: datetime = Field(..., description="When validation failed")
    source: str = Field(default="domain.websocket", description="Event source")
    model_config = {"frozen": True}


class WebSocketHealthStatusDomainEvent(BaseModel):
    """
    Domain event published when WebSocket health status changes.
    
    This is a domain-level abstraction of infrastructure health status.
    """
    is_healthy: bool = Field(..., description="Whether WebSocket connection is healthy")
    status: str = Field(..., description="Health status: healthy, disconnected, inactive, zombie, error")
    reason: str = Field(..., description="Reason for current health status")
    occurred_at: datetime = Field(..., description="When health status was determined")
    source: str = Field(default="domain.websocket", description="Event source")
    details: dict = Field(default_factory=dict, description="Additional health details")
    model_config = {"frozen": True}


class WebSocketConnectedDomainEvent(BaseModel):
    """Domain event published when WebSocket connects."""
    connected_at: datetime = Field(..., description="When connection was established")
    source: str = Field(default="domain.websocket", description="Event source")
    model_config = {"frozen": True}


class WebSocketDisconnectedDomainEvent(BaseModel):
    """Domain event published when WebSocket disconnects."""
    disconnected_at: datetime = Field(..., description="When disconnection occurred")
    reason: Optional[str] = Field(None, description="Reason for disconnection")
    source: str = Field(default="domain.websocket", description="Event source")
    model_config = {"frozen": True}


class WebSocketErrorDomainEvent(BaseModel):
    """Domain event published when WebSocket encounters an error."""
    error: str = Field(..., description="Error message")
    occurred_at: datetime = Field(..., description="When error occurred")
    is_rate_limit: bool = Field(default=False, description="Whether this is a rate limit error")
    source: str = Field(default="domain.websocket", description="Event source")
    model_config = {"frozen": True}


class WebSocketRateLimitDomainEvent(BaseModel):
    """Domain event published when rate limit (429) is hit."""
    occurred_at: datetime = Field(..., description="When rate limit was hit")
    message: str = Field(default="Rate limit exceeded - connection will not auto-reconnect", description="Rate limit message")
    source: str = Field(default="domain.websocket", description="Event source")
    model_config = {"frozen": True}
