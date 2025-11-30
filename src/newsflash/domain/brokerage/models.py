"""
Domain models for brokerage/trading - pure business logic, immutable value objects.
"""
from datetime import datetime
from typing import Optional
from enum import Enum
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator


class TradeAction(str, Enum):
    """Trade action - buy or sell."""
    BUY = "BUY"
    SELL = "SELL"


class TradeInstrument(str, Enum):
    """Trading instrument type."""
    STOCK = "stock"
    # OPTION removed per user request - no options support


class MarketSession(str, Enum):
    """Market session type."""
    PREMARKET = "premarket"
    MARKET = "market"
    POSTMARKET = "postmarket"
    CLOSED = "closed"


class TradeStatus(str, Enum):
    """Trade execution status."""
    PENDING = "pending"
    EXECUTED = "executed"
    FAILED = "failed"
    QUEUED = "queued"
    CANCELLED = "cancelled"


class TradeRequest(BaseModel):
    """
    Domain model for a trade request - immutable, validated, pure business logic.
    
    This is the domain's view of a trade request - no infrastructure concerns.
    """
    
    # Identity
    ticker: str = Field(..., min_length=1, max_length=5, description="Stock ticker symbol")
    action: TradeAction = Field(..., description="Trade action (BUY/SELL)")
    
    # Trade parameters
    amount_usd: Decimal = Field(..., gt=0, description="Notional value in USD")
    shares: Optional[int] = Field(None, gt=0, description="Number of shares (if specified)")
    leverage: Optional[Decimal] = Field(None, gt=0, le=Decimal("2.0"), description="Leverage multiplier (max 2x)")
    instrument: TradeInstrument = Field(default=TradeInstrument.STOCK, description="Instrument type")
    
    # Metadata
    article_id: Optional[str] = Field(None, description="Associated article ID if triggered by news")
    requested_at: datetime = Field(default_factory=datetime.now, description="When trade was requested")
    
    # Business logic properties
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable
    
    @field_validator('ticker')
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        """Ensure ticker is uppercase and valid format."""
        ticker = v.upper().strip()
        if not ticker.replace(".", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid ticker format: {v}")
        if len(ticker) > 5:
            raise ValueError(f"Ticker too long: {v}")
        return ticker
    
    @field_validator('amount_usd', 'leverage', mode='before')
    @classmethod
    def convert_to_decimal(cls, v):
        """Convert float/int to Decimal for precision."""
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        if isinstance(v, str):
            return Decimal(v)
        return v
    
    def calculate_shares(self, price_per_share: Decimal) -> int:
        """Calculate number of shares from amount and price."""
        if self.shares:
            return self.shares
        return int(self.amount_usd / price_per_share)
    
    def calculate_total_cost(self, price_per_share: Decimal) -> Decimal:
        """Calculate total cost including leverage."""
        shares = self.calculate_shares(price_per_share)
        base_cost = Decimal(shares) * price_per_share
        if self.leverage:
            return base_cost / self.leverage
        return base_cost
    
    def is_buy(self) -> bool:
        """Check if this is a buy order."""
        return self.action == TradeAction.BUY
    
    def is_sell(self) -> bool:
        """Check if this is a sell order."""
        return self.action == TradeAction.SELL
    
    def uses_leverage(self) -> bool:
        """Check if trade uses leverage."""
        return self.leverage is not None and self.leverage > Decimal("1.0")
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "ticker": self.ticker,
            "action": self.action.value,
            "amount_usd": str(self.amount_usd),
            "shares": self.shares,
            "leverage": str(self.leverage) if self.leverage else None,
            "instrument": self.instrument.value,
            "article_id": self.article_id,
            "requested_at": self.requested_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TradeRequest":
        """Create TradeRequest from dictionary."""
        # Convert Decimal strings back
        if "amount_usd" in data:
            data["amount_usd"] = Decimal(str(data["amount_usd"]))
        if "leverage" in data and data["leverage"]:
            data["leverage"] = Decimal(str(data["leverage"]))
        
        # Convert enums
        if "action" in data and isinstance(data["action"], str):
            data["action"] = TradeAction(data["action"])
        if "instrument" in data and isinstance(data["instrument"], str):
            data["instrument"] = TradeInstrument(data["instrument"])
        
        # Parse datetime
        if "requested_at" in data and isinstance(data["requested_at"], str):
            data["requested_at"] = datetime.fromisoformat(data["requested_at"])
        
        return cls(**data)


class TradeResult(BaseModel):
    """
    Domain model for trade execution result - immutable, validated.
    """
    
    # Identity
    trade_request: dict = Field(..., description="Original trade request as dict")
    success: bool = Field(..., description="Whether trade executed successfully")
    status: TradeStatus = Field(..., description="Trade status")
    
    # Execution details
    shares: Optional[int] = Field(None, gt=0, description="Shares executed")
    fill_price: Optional[Decimal] = Field(None, gt=0, description="Fill price per share")
    total_cost: Optional[Decimal] = Field(None, description="Total cost including commission")
    commission: Optional[Decimal] = Field(None, ge=0, description="Commission paid")
    
    # Session info
    session: MarketSession = Field(..., description="Market session when executed")
    executed_at: datetime = Field(..., description="When trade was executed")
    
    # Error info
    error: Optional[str] = Field(None, description="Error message if failed")
    
    # Business logic properties
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable
    
    @field_validator('fill_price', 'total_cost', 'commission', mode='before')
    @classmethod
    def convert_to_decimal(cls, v):
        """Convert float/int to Decimal."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        if isinstance(v, str):
            return Decimal(v)
        return v
    
    def get_trade_request(self) -> TradeRequest:
        """Extract TradeRequest domain model from result."""
        return TradeRequest.from_dict(self.trade_request)
    
    def get_ticker(self) -> str:
        """Get ticker from trade request."""
        return self.trade_request.get("ticker", "")
    
    def is_successful(self) -> bool:
        """Check if trade was successful."""
        return self.success and self.status == TradeStatus.EXECUTED
    
    def calculate_pnl(self, current_price: Decimal) -> Optional[Decimal]:
        """
        Calculate profit/loss (only for completed trades).
        
        Args:
            current_price: Current market price
            
        Returns:
            PnL in USD, or None if cannot calculate
        """
        if not self.is_successful() or not self.fill_price or not self.shares:
            return None
        
        trade_request = self.get_trade_request()
        
        if trade_request.is_buy():
            # Bought at fill_price, current value is current_price
            return (Decimal(self.shares) * current_price) - (Decimal(self.shares) * self.fill_price)
        else:
            # Sold at fill_price, current value is current_price (loss if price went up)
            return (Decimal(self.shares) * self.fill_price) - (Decimal(self.shares) * current_price)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "trade_request": self.trade_request,
            "success": self.success,
            "status": self.status.value,
            "shares": self.shares,
            "fill_price": str(self.fill_price) if self.fill_price else None,
            "total_cost": str(self.total_cost) if self.total_cost else None,
            "commission": str(self.commission) if self.commission else None,
            "session": self.session.value,
            "executed_at": self.executed_at.isoformat(),
            "error": self.error,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TradeResult":
        """Create TradeResult from dictionary."""
        # Convert Decimal strings
        for field in ["fill_price", "total_cost", "commission"]:
            if field in data and data[field]:
                data[field] = Decimal(str(data[field]))
        
        # Convert enums
        if "status" in data and isinstance(data["status"], str):
            data["status"] = TradeStatus(data["status"])
        if "session" in data and isinstance(data["session"], str):
            data["session"] = MarketSession(data["session"])
        
        # Parse datetime
        if "executed_at" in data and isinstance(data["executed_at"], str):
            data["executed_at"] = datetime.fromisoformat(data["executed_at"])
        
        return cls(**data)


class Quote(BaseModel):
    """
    Domain model for market quote/NBBO - immutable, validated.
    """
    
    ticker: str = Field(..., description="Stock ticker")
    bid: Decimal = Field(..., gt=0, description="Bid price")
    ask: Decimal = Field(..., gt=0, description="Ask price")
    last: Optional[Decimal] = Field(None, gt=0, description="Last trade price")
    volume: Optional[int] = Field(None, ge=0, description="Volume")
    received_at: datetime = Field(..., description="When quote was received")
    
    # Business logic properties
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable
    
    @field_validator('bid', 'ask', 'last', mode='before')
    @classmethod
    def convert_to_decimal(cls, v):
        """Convert to Decimal."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        if isinstance(v, str):
            return Decimal(v)
        return v
    
    def get_spread(self) -> Decimal:
        """Calculate bid-ask spread."""
        return self.ask - self.bid
    
    def get_mid_price(self) -> Decimal:
        """Calculate mid price."""
        return (self.bid + self.ask) / Decimal("2")
    
    def get_spread_percentage(self) -> Decimal:
        """Calculate spread as percentage of mid price."""
        mid = self.get_mid_price()
        if mid == 0:
            return Decimal("0")
        return (self.get_spread() / mid) * Decimal("100")
    
    def is_valid(self) -> bool:
        """Check if quote is valid (ask > bid)."""
        return self.ask > self.bid
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "ticker": self.ticker,
            "bid": str(self.bid),
            "ask": str(self.ask),
            "last": str(self.last) if self.last else None,
            "volume": self.volume,
            "received_at": self.received_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Quote":
        """Create Quote from dictionary."""
        # Convert Decimal strings
        for field in ["bid", "ask", "last"]:
            if field in data and data[field]:
                data[field] = Decimal(str(data[field]))
        
        # Parse datetime
        if "received_at" in data and isinstance(data["received_at"], str):
            data["received_at"] = datetime.fromisoformat(data["received_at"])
        
        return cls(**data)

