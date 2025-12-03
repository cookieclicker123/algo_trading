"""
Domain models for storage - pure business logic, immutable value objects.
"""
from datetime import datetime
from typing import FrozenSet, Optional
from pydantic import BaseModel, Field


class StoredArticle(BaseModel):
    """
    Domain model - stored article (pure business logic).
    
    This is the domain's view of a stored article - no infrastructure concerns.
    """
    article_id: str = Field(..., min_length=1, description="Unique article identifier")
    source: str = Field(..., min_length=1, description="Article source")
    source_id: str = Field(..., min_length=1, description="Source-specific article ID")
    title: str = Field(..., min_length=1, description="Article headline")
    content: Optional[str] = Field(None, description="Full article content")
    summary: Optional[str] = Field(None, description="Article summary")
    author: Optional[str] = Field(None, description="Article author")
    published_at: datetime = Field(..., description="Publication timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    url: Optional[str] = Field(None, description="Direct link to article")
    tickers: FrozenSet[str] = Field(default_factory=frozenset, description="Stock tickers (immutable)")
    tags: FrozenSet[str] = Field(default_factory=frozenset, description="Content tags (immutable)")
    categories: FrozenSet[str] = Field(default_factory=frozenset, description="Content categories (immutable)")
    stored_at: datetime = Field(default_factory=datetime.now, description="When article was stored")
    
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable
    
    def has_tickers(self) -> bool:
        """Check if the article has any tickers."""
        return len(self.tickers) > 0
    
    def get_primary_ticker(self) -> Optional[str]:
        """Get the primary ticker (first ticker)."""
        if self.tickers:
            return next(iter(self.tickers))
        return None


class ArchiveStatistics(BaseModel):
    """
    Domain model - archive statistics.
    
    Typed model for archive statistics to replace dictionaries.
    
    TODO: Expand this model with trading-specific analytics:
    - Historical article counts by classification type (IMMINENT, etc.)
    - Win rate statistics per article source/type
    - Time-based patterns (e.g., articles published during market hours vs. after-hours)
    - Ticker-specific statistics (which tickers had most profitable articles)
    - Correlation metrics between article classifications and trade outcomes
    - Backtesting data quality metrics (date ranges, coverage, gaps)
    
    Use Cases for Trading Win Rate Improvement:
    1. Backtesting: Analyze historical patterns to identify which article characteristics
       led to successful trades vs. losses
    2. Pattern Recognition: Find recurring patterns in articles that preceded profitable trades
       (e.g., specific keywords, sources, or timing)
    3. Performance Metrics: Track win rate by article source, classification confidence,
       or time of day to optimize trading strategy
    4. Strategy Refinement: Compare historical performance of different trading strategies
       based on article classifications
    5. Data Quality: Ensure sufficient historical data for reliable backtesting
    
    Users:
    - Trading researchers analyzing historical performance
    - Strategy developers backtesting new trading models
    - Analytics services providing insights to traders
    - System administrators monitoring data completeness
    """
    total_archived_dates: int = Field(default=0, description="Total number of archived dates")
    total_archived_files: int = Field(default=0, description="Total number of archived files")
    archive_directory: Optional[str] = Field(None, description="Path to archive directory")
    
    model_config = {"frozen": True}


class AuditEntry(BaseModel):
    """
    Domain model - audit trail entry (pure business logic).
    
    This is the domain's view of an audit entry - no infrastructure concerns.
    """
    article_id: str = Field(..., min_length=1, description="Article ID for audit entry")
    article_title: str = Field(..., min_length=1, description="Article headline")
    article_tickers: FrozenSet[str] = Field(default_factory=frozenset, description="Stock tickers (immutable)")
    article_published: Optional[datetime] = Field(None, description="Article publication timestamp")
    classification: str = Field(..., description="Classification category")
    confidence: str = Field(..., description="Confidence level")
    reasoning: str = Field(..., description="Reasoning for classification")
    source: str = Field(..., description="Article source")
    news_received_at: datetime = Field(..., description="When news was received")
    classified_at: datetime = Field(..., description="When classification occurred")
    logged_at: datetime = Field(default_factory=datetime.now, description="When audit entry was logged")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")
    trade_details: dict = Field(default_factory=dict, description="Trade details")
    timing_stats: dict = Field(default_factory=dict, description="Timing statistics")
    price_history: dict = Field(default_factory=dict, description="Price history")
    
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable
    
    def is_imminent(self) -> bool:
        """Check if classification is IMMINENT."""
        return self.classification.lower() == "imminent"
    
    def has_trade_details(self) -> bool:
        """Check if trade details are present."""
        return bool(self.trade_details.get("entry_price"))

