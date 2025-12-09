"""
Base models for standardized news article handling from Benzinga.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from enum import Enum


class NewsSource(str, Enum):
    """Enumeration of supported news sources."""
    BENZINGA_WEBSOCKET = "benzinga_websocket"


class StandardizedArticle(BaseModel):
    """Standardized model for news articles from any source."""
    
    # Source identification
    source: NewsSource = Field(..., description="News source identifier")
    source_id: str = Field(..., description="Unique ID from the source system")
    
    # Core article data
    title: str = Field(..., description="Article headline")
    content: Optional[str] = Field(None, description="Full article content")
    summary: Optional[str] = Field(None, description="Article summary/teaser")
    author: Optional[str] = Field(None, description="Article author")
    
    # Timestamps
    published: datetime = Field(..., description="Article publication timestamp")
    updated: Optional[datetime] = Field(None, description="Last update timestamp")
    
    # Metadata
    url: Optional[str] = Field(None, description="Direct link to article")
    tickers: List[str] = Field(default_factory=list, description="Stock tickers mentioned")
    tags: List[str] = Field(default_factory=list, description="Content tags")
    categories: List[str] = Field(default_factory=list, description="Content categories")
    images: List[str] = Field(default_factory=list, description="Article image URLs")
    
    # Raw source data
    raw_data: Dict[str, Any] = Field(..., description="Original data from source API")
    
    @field_validator('published', 'updated', mode='before')
    @classmethod
    def parse_datetime(cls, v):
        """Parse datetime strings from various formats."""
        if v is None:
            return None
        if isinstance(v, str):
            # Handle various datetime formats
            if v.endswith('Z'):
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            elif '+' in v or v.endswith('00:00'):
                return datetime.fromisoformat(v)
            else:
                # Try parsing as ISO format without timezone
                return datetime.fromisoformat(v)
        return v
    
    @field_validator('tickers')
    @classmethod
    def validate_tickers(cls, v):
        """Ensure tickers are uppercase and valid format."""
        return [ticker.upper().strip() for ticker in v if ticker.strip()]
    
    @property
    def published_timestamp(self) -> float:
        """Get published time as Unix timestamp."""
        return self.published.timestamp()
    
    @property
    def updated_timestamp(self) -> Optional[float]:
        """Get last updated time as Unix timestamp."""
        return self.updated.timestamp() if self.updated else None
    
    def is_recent(self, hours: int = 1) -> bool:
        """Check if article is within specified hours."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return (now - self.published).total_seconds() < (hours * 3600)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.dict()


class TradeInstrument(str, Enum):
    STOCK = "stock"
    OPTION = "option"


class OptionContractParams(BaseModel):
    symbol: str
    last_trade_date_or_contract_month: str
    strike: float
    right: str = "C"
    exchange: str = "SMART"
    currency: str = "USD"
    multiplier: str = "100"
    trading_class: Optional[str] = None
    con_id: Optional[int] = None


class TradeRequest(BaseModel):
    """Model for trade requests."""

    ticker: str = Field(..., description="Stock ticker symbol")
    amount_usd: float = Field(..., description="Approximate notional value for logging")
    action: str = Field(default="BUY", description="Trade action (BUY/SELL)")
    shares: Optional[float] = Field(default=None, description="Units to trade (supports fractional shares for stocks)")
    instrument: TradeInstrument = Field(default=TradeInstrument.STOCK, description="Instrument type for the trade")
    option_contract: Optional[OptionContractParams] = Field(
        default=None,
        description="Explicit option contract parameters (if None and instrument=OPTION, service will select ATM).",
    )
    leverage: Optional[float] = Field(
        default=None, description="Leverage multiplier applied to the trade (e.g., 2.0 for 2x margin)."
    )
    close_all_positions: bool = Field(
        default=False,
        description="When true, remove all tracked positions for the ticker after a successful SELL",
    )
    position_article_id: Optional[str] = Field(
        default=None,
        description="Specific position/article identifier to update on completion",
    )


class ArticleProcessor(BaseModel):
    """Base class for article processors from different sources."""
    
    source: NewsSource = Field(..., description="Source this processor handles")
    
    def process_raw_article(self, raw_data: Dict[str, Any]) -> StandardizedArticle:
        """Convert raw article data to standardized format."""
        raise NotImplementedError("Subclasses must implement process_raw_article")
