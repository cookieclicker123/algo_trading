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
    
    # 10-minute price check result (formerly 5-minute, then 15-minute)
    price_check_15min: Optional[Dict[str, Any]] = Field(
        None,
        description="10-minute price check: final_mid, percent_change, moved_1_percent (field name kept for backward compatibility)"
    )
    
    # Ticker metadata (fetched via FinnhubCoordinator - shared across all services)
    ticker_metadata: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="ticker -> {industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Metadata fetch errors (why metadata couldn't be collected)
    metadata_errors: Dict[str, str] = Field(
        default_factory=dict,
        description="ticker -> error_reason: 'api_timeout', 'api_error', 'no_data_available', 'rate_limited', etc."
    )
    
    # Filter reason (why wasn't it traded?) - SINGULAR: one reason per article
    filter_reason: Optional[str] = Field(
        None,
        description="Single filter reason: 'ai_classified_ignore', 'prefilter_no_tickers', 'prefilter_low_market_cap', etc. Set immediately from events."
    )
    
    # AI classification result (what did AI say about this article?)
    ai_classification: Optional[str] = Field(
        None,
        description="AI classification: 'IMMINENT', 'SPECULATIVE', 'ROUTINE', 'IGNORE'. Always populated so we know why wasn't traded."
    )
    
    # Volume analysis at article receive time (for future filtering research)
    # Ticker -> Stats dictionary
    volume_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="ticker -> {move_type, surge_multiplier, trade_count_multiplier, max_excursion_pct, ...}"
    )
    
    # Tracking metadata
    tracked_at: datetime = Field(default_factory=datetime.now, description="When tracking started")
    price_checked_at: Optional[datetime] = Field(None, description="When 10-minute price check completed")
    
    # Price tracking during 10-minute hold period
    highest_price_during_hold: Optional[Dict[str, Any]] = Field(
        None,
        description="Highest price reached during 10-minute hold: price, timestamp, percent_gain_from_entry, minute, second"
    )
    max_adverse_excursion: Optional[Dict[str, Any]] = Field(
        None,
        description="Lowest price during 10-minute hold (max adverse excursion): price, timestamp, percent_loss_from_entry, minute, second, stop_loss_percentage, stop_loss_dollar_per_share"
    )
    
    # Trade linkage (did we actually trade this?)
    is_traded: bool = Field(False, description="Whether this article resulted in a trade execution")
    trade_id: Optional[str] = Field(None, description="Trade ID if executed")
    
    # 2-minute monitoring for SURGE detection (for articles that didn't initially show SURGE)
    monitoring_status: Optional[str] = Field(
        None,
        description="Monitoring status: None (not monitored), 'initiated' (monitoring started), 'surge_detected' (surge found), 'completed_no_surge' (monitoring finished, no surge)"
    )
    monitoring_initiated_at: Optional[datetime] = Field(
        None,
        description="When 2-minute monitoring was initiated"
    )
    monitoring_cycles_completed: int = Field(
        0,
        description="Number of 4-second cycles completed during monitoring (max 30)"
    )
    surge_detected_at: Optional[datetime] = Field(
        None,
        description="When SURGE was detected during monitoring (if any)"
    )
    surge_detection_cycle: Optional[int] = Field(
        None,
        description="Which 4-second cycle detected the surge (0-29)"
    )
    surge_detection_window_stats: Optional[Dict[str, Any]] = Field(
        None,
        description="Volume stats from the window where surge was detected"
    )
    monitoring_completed_at: Optional[datetime] = Field(
        None,
        description="When monitoring completed (after 2 minutes or surge detected)"
    )
    
    model_config = {"frozen": False}  # Allow updates for price_check_15min and monitoring fields


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
    
    # Ticker metadata (fetched via FinnhubCoordinator - shared across all services)
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
    
    # Volume analysis at article publish time (for future filtering research)
    volume_stats: Optional[Dict[str, Any]] = Field(
        None,
        description="Volume microstructures: move_type, surge_multiplier, trade_count_multiplier, max_excursion_pct, etc."
    )
    
    # Tracking metadata
    recorded_at: datetime = Field(default_factory=datetime.now, description="When record was created")
    
    model_config = {"frozen": False}  # Allow updates for exit data


# ===== Failed Trades Engine Models =====

class FailedTradeRecord(BaseModel):
    """Record of a failed trade attempt."""
    
    trade_id: str = Field(..., description="Trade/order identifier")
    article_id: Optional[str] = Field(None, description="Associated article ID if triggered by news")
    ticker: str = Field(..., description="Ticker symbol that failed to trade")
    session: MarketSession = Field(..., description="Market session when trade failed")
    failed_at: datetime = Field(..., description="When trade failed")
    
    # Failure details
    failure_reason: str = Field(..., description="Error message/reason for failure")
    ladder_attempts: Optional[int] = Field(None, description="Number of ladder attempts made (for extended hours)")
    ladder_attempts_detail: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Detailed ladder attempts with prices and timestamps"
    )
    
    # NBBO at failure time (with bid/ask sizes)
    failure_nbbo: Optional[Dict[str, Any]] = Field(
        None,
        description="NBBO at failure: bid, ask, spread, mid, bid_size, ask_size"
    )
    
    # Time of day metrics
    hour: int = Field(..., description="Hour of day (0-23) when trade failed")
    minute: int = Field(..., description="Minute of hour (0-59) when trade failed")
    time_of_day: str = Field(..., description="Time of day string (HH:MM format)")
    
    # Ticker metadata (fetched via FinnhubCoordinator - shared across all services)
    ticker_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="{industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Trade request details
    requested_shares: Optional[int] = Field(None, description="Number of shares requested")
    requested_price: Optional[float] = Field(None, description="Requested price (if limit order)")
    order_type: Optional[str] = Field(None, description="Order type (market, limit, etc.)")
    
    # Volume analysis at failure time (for future filtering research)
    volume_stats: Optional[Dict[str, Any]] = Field(
        None,
        description="Volume microstructures: move_type, surge_multiplier, trade_count_multiplier, max_excursion_pct, etc."
    )
    
    # Tracking metadata
    recorded_at: datetime = Field(default_factory=datetime.now, description="When record was created")
    
    model_config = {"frozen": False}  # Allow updates


class FailedTradeSessionFile(BaseModel):
    """JSON file structure for a failed trades session."""
    
    session: MarketSession = Field(..., description="Market session")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    session_start: datetime = Field(..., description="Session start time")
    session_end: datetime = Field(..., description="Session end time")
    file_created_at: datetime = Field(default_factory=datetime.now, description="When file was created")
    last_updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Real-time summary (updated on each append)
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_failed_trades": 0,
            "failure_reasons_breakdown": {},
            "ticker_breakdown": {},
            "time_of_day_breakdown": {},  # Hour -> count
            "session_breakdown": {},  # Session -> count
            "avg_spread_at_failure": 0.0,
            "avg_bid_size_at_failure": 0.0,
            "avg_ask_size_at_failure": 0.0,
        },
        description="Summary statistics updated in real-time"
    )
    
    # List of records (appended in real-time)
    records: List[FailedTradeRecord] = Field(default_factory=list, description="List of failed trade records")
    
    model_config = {"frozen": False}  # Allow updates


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
