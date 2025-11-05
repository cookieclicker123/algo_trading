"""
Pydantic models for Benzinga news articles from WebSocket feed.
Based on real WebSocket message structure confirmed through testing.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

from .base_models import StandardizedArticle, NewsSource


class BenzingaArticle(BaseModel):
    """Model for a single Benzinga news article."""
    
    benzinga_id: int = Field(..., description="Unique Benzinga article ID")
    author: str = Field(..., description="Article author name")
    published: datetime = Field(..., description="Article publication timestamp")
    last_updated: datetime = Field(..., description="Last update timestamp")
    title: str = Field(..., description="Article headline")
    teaser: Optional[str] = Field(None, description="Article summary/teaser")
    body: Optional[str] = Field(None, description="Full article content in HTML")
    url: str = Field(..., description="Direct link to article")
    images: List[str] = Field(default_factory=list, description="Article image URLs")
    channels: List[str] = Field(default_factory=list, description="Content channels (e.g., 'exclusives', 'trading ideas')")
    tickers: List[str] = Field(default_factory=list, description="Stock tickers mentioned in article")
    tags: List[str] = Field(default_factory=list, description="Content tags for categorization")
    
    @field_validator('published', 'last_updated', mode='before')
    @classmethod
    def parse_datetime(cls, v):
        """Parse datetime strings from API."""
        if isinstance(v, str):
            # Handle both with and without timezone info
            if v.endswith('Z'):
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            else:
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
    def updated_timestamp(self) -> float:
        """Get last updated time as Unix timestamp."""
        return self.last_updated.timestamp()
    
    def is_recent(self, hours: int = 1) -> bool:
        """Check if article is within specified hours."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return (now - self.published).total_seconds() < (hours * 3600)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = self.dict()
        # Add source field for audit trail consistency
        data['source'] = 'benzinga'
        return data


def convert_benzinga_to_standardized(benzinga_article: BenzingaArticle) -> StandardizedArticle:
    """Convert BenzingaArticle to StandardizedArticle format."""
    return StandardizedArticle(
        source=NewsSource.BENZINGA_WEBSOCKET,
        source_id=str(benzinga_article.benzinga_id),
        title=benzinga_article.title,
        content=benzinga_article.body,
        summary=benzinga_article.teaser,
        author=benzinga_article.author,
        published=benzinga_article.published,
        updated=benzinga_article.last_updated,
        url=benzinga_article.url,
        tickers=benzinga_article.tickers,
        tags=benzinga_article.tags,
        categories=benzinga_article.channels,
        images=benzinga_article.images,
        raw_data=benzinga_article.dict()
    )


class BenzingaNewsResponse(BaseModel):
    """Model for the complete API response."""
    
    results: List[BenzingaArticle] = Field(default_factory=list, description="List of news articles")
    count: Optional[int] = Field(None, description="Total number of results")
    next_url: Optional[str] = Field(None, description="URL for next page of results")
    
    @field_validator('results', mode='before')
    @classmethod
    def parse_articles(cls, v):
        """Parse raw API results into BenzingaArticle objects."""
        if isinstance(v, list):
            articles = []
            for item in v:
                if isinstance(item, dict):
                    articles.append(BenzingaArticle(**item))
            return articles
        return v
    
    @property
    def latest_article(self) -> Optional[BenzingaArticle]:
        """Get the most recently published article."""
        if not self.results:
            return None
        return max(self.results, key=lambda x: x.published)
    
    def get_articles_by_channel(self, channel: str) -> List[BenzingaArticle]:
        """Get articles from a specific channel."""
        return [article for article in self.results if channel in article.channels]
