"""
Domain models for WebSocket/articles - pure business logic, immutable value objects.
"""
from datetime import datetime
from typing import Optional, FrozenSet
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator


class ArticleSource(str, Enum):
    """Enumeration of supported article sources in domain."""
    BENZINGA = "benzinga"


class ArticleId:
    """Value object for article ID (source + source_id)."""
    
    def __init__(self, source: ArticleSource, source_id: str):
        self._source = source
        self._source_id = source_id
    
    @property
    def source(self) -> ArticleSource:
        return self._source
    
    @property
    def source_id(self) -> str:
        return self._source_id
    
    def __str__(self) -> str:
        return f"{self.source.value}:{self.source_id}"
    
    def __eq__(self, other):
        if not isinstance(other, ArticleId):
            return False
        return self.source == other.source and self.source_id == other.source_id
    
    def __hash__(self):
        return hash((self.source, self.source_id))
    
    @classmethod
    def from_string(cls, value: str) -> "ArticleId":
        """Parse article ID from string format 'source:source_id'."""
        parts = value.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid article ID format: {value}")
        return cls(
            source=ArticleSource(parts[0]),
            source_id=parts[1]
        )


class Article(BaseModel):
    """
    Domain model for a news article - immutable, validated, pure business logic.
    
    This is the domain's view of an article - no infrastructure concerns.
    """
    
    # Identity
    id: str = Field(..., description="Unique article identifier (source:source_id)")
    source: ArticleSource = Field(..., description="Article source")
    source_id: str = Field(..., description="Source-specific article ID")
    
    # Content
    title: str = Field(..., min_length=1, description="Article headline")
    content: Optional[str] = Field(None, description="Full article content")
    summary: Optional[str] = Field(None, description="Article summary/teaser")
    author: Optional[str] = Field(None, description="Article author")
    
    # Timestamps
    published_at: datetime = Field(..., description="Publication timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    
    # Metadata
    url: Optional[str] = Field(None, description="Direct link to article")
    tickers: FrozenSet[str] = Field(default_factory=frozenset, description="Stock tickers (immutable)")
    tags: FrozenSet[str] = Field(default_factory=frozenset, description="Content tags (immutable)")
    categories: FrozenSet[str] = Field(default_factory=frozenset, description="Content categories (immutable)")
    
    # Business logic properties
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable after creation
    
    @field_validator('tickers', mode='before')
    @classmethod
    def validate_tickers(cls, v):
        """Ensure tickers are uppercase and non-empty."""
        if isinstance(v, list):
            return frozenset(ticker.upper().strip() for ticker in v if ticker.strip())
        if isinstance(v, frozenset):
            return v
        return frozenset()
    
    @field_validator('tags', mode='before')
    @classmethod
    def validate_tags(cls, v):
        """Ensure tags are non-empty strings."""
        if isinstance(v, list):
            return frozenset(tag.strip() for tag in v if tag.strip())
        if isinstance(v, frozenset):
            return v
        return frozenset()
    
    @field_validator('categories', mode='before')
    @classmethod
    def validate_categories(cls, v):
        """Ensure categories are non-empty strings."""
        if isinstance(v, list):
            return frozenset(cat.strip() for cat in v if cat.strip())
        if isinstance(v, frozenset):
            return v
        return frozenset()
    
    @model_validator(mode='before')
    @classmethod
    def generate_id(cls, data):
        """Generate ID from source and source_id if not provided."""
        if isinstance(data, dict):
            if 'id' not in data or not data['id']:
                source = data.get('source')
                source_id = data.get('source_id')
                if source and source_id:
                    if isinstance(source, ArticleSource):
                        data['id'] = f"{source.value}:{source_id}"
                    elif isinstance(source, str):
                        data['id'] = f"{source}:{source_id}"
        return data
    
    def has_tickers(self) -> bool:
        """Check if article has any tickers."""
        return len(self.tickers) > 0
    
    def has_ticker(self, ticker: str) -> bool:
        """Check if article mentions a specific ticker."""
        return ticker.upper() in self.tickers
    
    def is_recent(self, hours: int = 1) -> bool:
        """Check if article was published within specified hours."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        time_diff = (now - self.published_at.replace(tzinfo=timezone.utc)).total_seconds()
        return time_diff < (hours * 3600)
    
    def is_updated(self) -> bool:
        """Check if article has been updated since publication."""
        return self.updated_at is not None and self.updated_at > self.published_at
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "source": self.source.value,
            "source_id": self.source_id,
            "title": self.title,
            "content": self.content,
            "summary": self.summary,
            "author": self.author,
            "published_at": self.published_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "url": self.url,
            "tickers": list(self.tickers),
            "tags": list(self.tags),
            "categories": list(self.categories),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Article":
        """Create Article from dictionary."""
        # Convert frozen sets back
        if "tickers" in data and isinstance(data["tickers"], list):
            data["tickers"] = frozenset(data["tickers"])
        if "tags" in data and isinstance(data["tags"], list):
            data["tags"] = frozenset(data["tags"])
        if "categories" in data and isinstance(data["categories"], list):
            data["categories"] = frozenset(data["categories"])
        
        # Convert source string to enum
        if "source" in data and isinstance(data["source"], str):
            try:
                data["source"] = ArticleSource(data["source"])
            except ValueError:
                # Fallback: try mapping common values
                if data["source"] == "benzinga_websocket":
                    data["source"] = ArticleSource.BENZINGA
                else:
                    raise ValueError(f"Unknown article source: {data['source']}")
        
        # Parse datetimes
        if "published_at" in data and isinstance(data["published_at"], str):
            data["published_at"] = datetime.fromisoformat(data["published_at"].replace('Z', '+00:00'))
        if "updated_at" in data and isinstance(data["updated_at"], str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
        
        return cls(**data)

