"""
Infrastructure-specific models for brokerage events.

These are infrastructure's own typed models - NOT domain models.
Infrastructure owns these completely.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any


class InfrastructureTradeRequestData(BaseModel):
    """
    Infrastructure trade request data model - infrastructure format.
    
    Infrastructure's own representation - can change without affecting domain.
    """
    ticker: str
    amount_usd: float
    action: str  # "BUY" or "SELL"
    shares: Optional[int] = None
    leverage: Optional[float] = None
    instrument: str = "stock"
    # Infrastructure-specific fields can be added here


class InfrastructureTradeExecutionRequestEvent(BaseModel):
    """
    Infrastructure event - trade execution requested (from domain to infrastructure).
    
    Typed model that infrastructure expects to receive.
    """
    trade_request: InfrastructureTradeRequestData
    article_id: Optional[str] = None
    requested_at: datetime
    source: str = "domain.brokerage"


class InfrastructureTradeExecutedEvent(BaseModel):
    """Infrastructure event - trade executed."""
    trade_request: InfrastructureTradeRequestData
    success: bool
    shares: Optional[int] = None
    fill_price: Optional[float] = None
    total_cost: Optional[float] = None
    commission: Optional[float] = None
    session: str
    order_type: str
    instrument: str
    instrument_details: Dict[str, Any] = Field(default_factory=dict)
    timing_info: Dict[str, float] = Field(default_factory=dict)
    limit_price_used: Optional[float] = None
    percentage_above_below: Optional[float] = None
    executed_at: datetime
    source: str = "brokerage"


class InfrastructureTradeFailedEvent(BaseModel):
    """Infrastructure event - trade failed."""
    trade_request: InfrastructureTradeRequestData
    error: str
    failed_at: datetime
    source: str = "brokerage"


class InfrastructureQuoteData(BaseModel):
    """Infrastructure quote data model."""
    bid: float
    ask: float
    last: Optional[float] = None
    volume: Optional[int] = None
    spread: Optional[float] = None


class InfrastructureQuoteReceivedEvent(BaseModel):
    """Infrastructure event - quote received."""
    symbol: str
    nbbo: InfrastructureQuoteData
    received_at: datetime
    source: str = "brokerage"


class InfrastructureConnectionStatusEvent(BaseModel):
    """Infrastructure event - connection status changed."""
    is_connected: bool
    paper_trading: bool
    changed_at: datetime
    reason: Optional[str] = None
    source: str = "brokerage"


class InfrastructureBrokerageHealthEvent(BaseModel):
    """Infrastructure event - brokerage health status."""
    is_healthy: bool
    reason: str
    is_connected: bool
    occurred_at: datetime
    stats: Dict[str, Any] = Field(default_factory=dict)
    source: str = "brokerage"
    is_critical: bool = False


class InfrastructureTradeQueuedEvent(BaseModel):
    """Infrastructure event - trade queued."""
    trade_request: InfrastructureTradeRequestData
    queued_at: datetime
    target_premarket: datetime
    source: str = "brokerage"

