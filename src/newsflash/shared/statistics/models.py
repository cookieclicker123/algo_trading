"""
Statistics data models - shared between recall and signal engines.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from ...domain.brokerage.models import MarketSession


# ===== Recall Engine Models =====

class RecallRecord(BaseModel):
    """Record of an article that could have been traded (missed opportunity)."""
    
    article_id: str = Field(..., description="Article identifier")
    title: str = Field(..., description="Article title")
    tickers: List[str] = Field(..., description="All tickers from article")
    session: MarketSession = Field(..., description="Market session when article was received")
    published_at: datetime = Field(..., description="When article was published")
    received_at: datetime = Field(..., description="When article was received")
    
    # Initial NBBO snapshot (when article received)
    initial_nbbo: Optional[Dict[str, Any]] = Field(
        None,
        description="Initial NBBO: bid, ask, spread, mid"
    )
    
    # 5-minute price check result
    price_check_5min: Optional[Dict[str, Any]] = Field(
        None,
        description="5-minute price check: final_mid, percent_change, moved_1_percent"
    )
    
    # Ticker metadata (fetched via yfinance)
    ticker_metadata: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="ticker -> {industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Filter reasons (why wasn't it traded?)
    filter_reasons: List[str] = Field(
        default_factory=list,
        description="e.g., ['not_classified_imminent', 'no_nbbo_available', 'ticker_not_tradeable_extended_hours']"
    )
    
    # Tracking metadata
    tracked_at: datetime = Field(default_factory=datetime.now, description="When tracking started")
    price_checked_at: Optional[datetime] = Field(None, description="When 5-minute price check completed")
    
    model_config = {"frozen": False}  # Allow updates for price_check_5min


class RecallSessionFile(BaseModel):
    """JSON file structure for a recall session."""
    
    session: MarketSession = Field(..., description="Market session")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    session_start: datetime = Field(..., description="Session start time")
    session_end: datetime = Field(..., description="Session end time")
    file_created_at: datetime = Field(default_factory=datetime.now, description="When file was created")
    last_updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Real-time summary (updated on each append)
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_articles_tracked": 0,
            "articles_with_1_percent_move": 0,
            "articles_traded": 0,
            "missed_opportunities": 0,
            "filter_breakdown": {},
            "ticker_breakdown": {}
        },
        description="Summary statistics updated in real-time"
    )
    
    # List of records (appended in real-time)
    records: List[RecallRecord] = Field(default_factory=list, description="List of recall records")
    
    model_config = {"frozen": False}  # Allow updates


# ===== Signal Engine Models =====

class SignalRecord(BaseModel):
    """Record of an actual trade execution."""
    
    trade_id: str = Field(..., description="Trade/order identifier")
    article_id: Optional[str] = Field(None, description="Associated article ID if triggered by news")
    ticker: str = Field(..., description="Ticker symbol traded")
    session: MarketSession = Field(..., description="Market session when trade executed")
    executed_at: datetime = Field(..., description="When trade was executed")
    
    # Entry details (from TradeResult)
    entry_price: float = Field(..., description="Entry fill price")
    entry_shares: int = Field(..., description="Number of shares")
    entry_amount_usd: float = Field(..., description="Total entry amount in USD")
    entry_nbbo: Optional[Dict[str, Any]] = Field(
        None,
        description="NBBO at entry: bid, ask, spread, mid"
    )
    
    # Ticker metadata (fetched via yfinance)
    ticker_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="{industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Trade outcome (if available)
    exit_price: Optional[float] = Field(None, description="Exit fill price")
    exit_shares: Optional[int] = Field(None, description="Number of shares exited")
    exit_amount_usd: Optional[float] = Field(None, description="Total exit amount in USD")
    profit_loss_usd: Optional[float] = Field(None, description="Profit/loss in USD")
    profit_loss_percent: Optional[float] = Field(None, description="Profit/loss percentage")
    
    # Tracking metadata
    recorded_at: datetime = Field(default_factory=datetime.now, description="When record was created")
    
    model_config = {"frozen": False}  # Allow updates for exit data


class SignalSessionFile(BaseModel):
    """JSON file structure for a signal session."""
    
    session: MarketSession = Field(..., description="Market session")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    session_start: datetime = Field(..., description="Session start time")
    session_end: datetime = Field(..., description="Session end time")
    file_created_at: datetime = Field(default_factory=datetime.now, description="When file was created")
    last_updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Real-time summary (updated on each append)
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_trades": 0,
            "profitable_trades": 0,
            "losing_trades": 0,
            "total_profit_loss_usd": 0.0,
            "average_spread_at_entry": 0.0,
            "ticker_breakdown": {},
            "industry_breakdown": {},
            "sector_breakdown": {}
        },
        description="Summary statistics updated in real-time"
    )
    
    # List of records (appended in real-time)
    records: List[SignalRecord] = Field(default_factory=list, description="List of signal records")
    
    model_config = {"frozen": False}  # Allow updates
