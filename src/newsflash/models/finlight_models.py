"""
Pydantic models for Finlight.me news articles from WebSocket API.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, validator

from .base_models import StandardizedArticle, NewsSource, ArticleProcessor


class FinlightArticle(BaseModel):
    """Model for a single Finlight news article (raw format)."""
    
    # Note: These fields are based on typical news API structure
    # Actual fields may vary and will be updated based on real API responses
    id: Optional[str] = Field(None, description="Unique article ID")
    title: str = Field(..., description="Article headline")
    content: Optional[str] = Field(None, description="Full article content")
    summary: Optional[str] = Field(None, description="Article summary")
    author: Optional[str] = Field(None, description="Article author")
    published_at: Optional[datetime] = Field(None, description="Publication timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    url: Optional[str] = Field(None, description="Article URL")
    tickers: Optional[List[str]] = Field(default_factory=list, description="Stock tickers")
    tags: Optional[List[str]] = Field(default_factory=list, description="Content tags")
    category: Optional[str] = Field(None, description="Article category")
    source: Optional[str] = Field(None, description="Original news source")
    
    @validator('published_at', 'updated_at', pre=True)
    def parse_datetime(cls, v):
        """Parse datetime strings from API."""
        if v is None:
            return None
        if isinstance(v, str):
            if v.endswith('Z'):
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            else:
                return datetime.fromisoformat(v)
        return v
    
    @validator('tickers')
    def validate_tickers(cls, v):
        """Ensure tickers are uppercase and valid format."""
        if v is None:
            return []
        return [ticker.upper().strip() for ticker in v if ticker.strip()]


def convert_finlight_to_standardized(raw_data: Dict[str, Any]) -> StandardizedArticle:
    """Convert raw Finlight article data to standardized format."""
    
    # Parse the raw data into a FinlightArticle first
    try:
        finlight_article = FinlightArticle(**raw_data)
    except Exception as e:
        # If parsing fails, create a minimal article from available data
        finlight_article = FinlightArticle(
            title=raw_data.get('title', 'Unknown Title'),
            content=raw_data.get('content'),
            summary=raw_data.get('summary'),
            author=raw_data.get('author'),
            published_at=raw_data.get('published_at'),
            updated_at=raw_data.get('updated_at'),
            url=raw_data.get('url'),
            tickers=raw_data.get('tickers', []),
            tags=raw_data.get('tags', []),
            category=raw_data.get('category'),
            source=raw_data.get('source')
        )
    
    # Convert to standardized format
    return StandardizedArticle(
        source=NewsSource.FINLIGHT,
        source_id=str(finlight_article.id) if finlight_article.id else f"finlight_{hash(finlight_article.title)}",
        title=finlight_article.title,
        content=finlight_article.content,
        summary=finlight_article.summary,
        author=finlight_article.author,
        published=finlight_article.published_at or datetime.now(),
        updated=finlight_article.updated_at,
        url=finlight_article.url,
        tickers=finlight_article.tickers,
        tags=finlight_article.tags,
        categories=[finlight_article.category] if finlight_article.category else [],
        images=[],  # Finlight may not provide images in initial implementation
        raw_data=raw_data
    )
