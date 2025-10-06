"""
Base models for standardized news article handling across multiple sources.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, validator
from enum import Enum


class NewsSource(str, Enum):
    """Enumeration of supported news sources."""
    BENZINGA = "benzinga"
    FINLIGHT = "finlight"


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
    
    @validator('published', 'updated', pre=True)
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
    
    @validator('tickers')
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
    
    @property
    def trading_relevance_score(self) -> int:
        """
        Calculate trading relevance score based on content indicators.
        This is a stub - AI classification will replace this.
        """
        score = 0
        
        # High-impact keywords
        high_impact_keywords = [
            'merger', 'acquisition', 'earnings', 'contract', 'partnership',
            'fda', 'approval', 'clinical trial', 'ipo', 'bankruptcy',
            'dividend', 'split', 'buyback', 'guidance', 'beat', 'miss'
        ]
        
        # Combine title, summary, and content for analysis
        content_text = (self.title + ' ' + (self.summary or '') + ' ' + (self.content or '')).lower()
        for keyword in high_impact_keywords:
            if keyword in content_text:
                score += 1
        
        # Multiple tickers often indicate broader market impact
        if len(self.tickers) > 3:
            score += 1
        
        # High-value categories
        if any(cat.lower() in ['exclusives', 'trading ideas', 'breaking'] for cat in self.categories):
            score += 2
        
        return min(score, 10)  # Cap at 10
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.dict()


class ArticleProcessor(BaseModel):
    """Base class for article processors from different sources."""
    
    source: NewsSource = Field(..., description="Source this processor handles")
    
    def process_raw_article(self, raw_data: Dict[str, Any]) -> StandardizedArticle:
        """Convert raw article data to standardized format."""
        raise NotImplementedError("Subclasses must implement process_raw_article")


class MultiSourceStats(BaseModel):
    """Statistics for multiple news sources."""
    
    sources: Dict[NewsSource, Dict[str, Any]] = Field(default_factory=dict, description="Stats per source")
    total_articles: int = Field(default=0, description="Total articles across all sources")
    last_updated: datetime = Field(default_factory=lambda: datetime.now(), description="Last update time")
    
    def add_source_stats(self, source: NewsSource, stats: Dict[str, Any]):
        """Add statistics for a specific source."""
        self.sources[source] = stats
        self.last_updated = datetime.now()
    
    def get_total_articles(self) -> int:
        """Calculate total articles across all sources."""
        total = 0
        for source_stats in self.sources.values():
            if 'articles_processed' in source_stats:
                total += source_stats['articles_processed']
        return total
