"""
Domain events for brokerage/trading - business events published by domain layer.

These use domain models directly - fully typed, not Dict[str, Any].
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from .models import TradeRequest, TradeResult, Quote


class TradeRequestDomainEvent(BaseModel):
    """
    Domain event published when a trade is requested by use case.

    Uses domain TradeRequest model directly - fully typed, validated.
    """
    trade_request: TradeRequest = Field(..., description="Domain TradeRequest model (validated, immutable)")
    article_id: Optional[str] = Field(None, description="Associated article ID if triggered by news")
    requested_at: datetime = Field(..., description="When trade was requested")
    source: str = Field(default="domain.brokerage", description="Event source")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata (exit_reason, tier, etc.)")

    model_config = {"frozen": True}  # Immutable


class TradeExecutedDomainEvent(BaseModel):
    """
    Domain event published when a trade is successfully executed.
    
    Uses domain TradeResult model directly - fully typed, validated.
    """
    trade_result: TradeResult = Field(..., description="Domain TradeResult model (validated, immutable)")
    executed_at: datetime = Field(..., description="When trade was executed")
    source: str = Field(default="domain.brokerage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class TradeFailedDomainEvent(BaseModel):
    """Domain event published when a trade execution fails."""
    trade_request: TradeRequest = Field(..., description="Domain TradeRequest model")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When trade failed")
    source: str = Field(default="domain.brokerage", description="Event source")
    ladder_attempts: Optional[int] = Field(None, description="Number of ladder attempts made (for extended hours)")
    ladder_attempts_detail: Optional[List[Dict[str, Any]]] = Field(None, description="Detailed ladder attempts with timestamps")
    
    model_config = {"frozen": True}


class TradeQueuedDomainEvent(BaseModel):
    """Domain event published when a trade is queued for closed market."""
    trade_request: TradeRequest = Field(..., description="Domain TradeRequest model")
    queued_at: datetime = Field(..., description="When trade was queued")
    target_premarket: datetime = Field(..., description="Target premarket time")
    source: str = Field(default="domain.brokerage", description="Event source")
    
    model_config = {"frozen": True}


class QuoteReceivedDomainEvent(BaseModel):
    """Domain event published when a market quote is received."""
    quote: Quote = Field(..., description="Domain Quote model (validated, immutable)")
    received_at: datetime = Field(..., description="When quote was received")
    source: str = Field(default="domain.brokerage", description="Event source")
    
    model_config = {"frozen": True}


class BrokerageConnectionStatusDomainEvent(BaseModel):
    """
    Domain event published when brokerage connection status changes.
    
    This is a domain-level abstraction of infrastructure connection status.
    """
    is_connected: bool = Field(..., description="Whether brokerage is connected")
    paper_trading: bool = Field(..., description="Whether in paper trading mode")
    changed_at: datetime = Field(..., description="When connection status changed")
    reason: Optional[str] = Field(None, description="Reason for status change")
    source: str = Field(default="domain.brokerage", description="Event source")
    model_config = {"frozen": True}


class BrokerageHealthStatusDomainEvent(BaseModel):
    """
    Domain event published when brokerage health status changes.
    
    This is a domain-level abstraction of infrastructure health status.
    """
    is_healthy: bool = Field(..., description="Whether brokerage service is healthy")
    is_connected: bool = Field(..., description="Whether brokerage is connected")
    reason: str = Field(..., description="Reason for current health status")
    occurred_at: datetime = Field(..., description="When health status was determined")
    is_critical: bool = Field(default=False, description="Whether this is a critical health issue")
    stats: dict = Field(default_factory=dict, description="Additional health statistics")
    source: str = Field(default="domain.brokerage", description="Event source")
    model_config = {"frozen": True}
