"""
Domain models for classification - pure business logic, immutable value objects.
"""
from datetime import datetime
from enum import Enum
from typing import FrozenSet, Optional
from pydantic import BaseModel, Field, field_validator


class ClassificationCategory(str, Enum):
    """
    Classification categories - domain business logic.
    
    Two categories for maximum signal-to-noise ratio:
    - IMMINENT: Trade immediately (10%+ intraday moves expected)
    - IGNORE: Filter out (no actionable trading signal)
    """
    IMMINENT = "imminent"          # Immediate trading opportunity
    IGNORE = "ignore"              # Filter out - no trading signal


class ClassificationConfidence(str, Enum):
    """
    Confidence levels - domain business logic.
    """
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ClassificationRequest(BaseModel):
    """
    Domain model - classification request (pure business logic).
    
    This is the domain's view of a classification request - no infrastructure concerns.
    """
    article_id: str = Field(..., min_length=1, description="Article ID for tracking")
    article_title: str = Field(..., min_length=1, description="Article title/headline")
    article_tickers: FrozenSet[str] = Field(default_factory=frozenset, description="Stock tickers (immutable)")
    article_summary: str = Field(default="", description="Article summary/content")
    article_published_at: Optional[datetime] = Field(None, description="When article was originally published")
    requested_at: datetime = Field(default_factory=datetime.now, description="When classification was requested")
    
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable
    
    @field_validator('article_tickers', mode='before')
    @classmethod
    def validate_tickers(cls, v):
        """Ensure tickers are uppercase and non-empty."""
        if isinstance(v, list):
            return frozenset(ticker.upper().strip() for ticker in v if ticker.strip())
        if isinstance(v, frozenset):
            return v
        return frozenset()
    
    def has_tickers(self) -> bool:
        """Check if request has any tickers."""
        return len(self.article_tickers) > 0


class ClassificationResult(BaseModel):
    """
    Domain model - classification result (pure business logic).
    
    This is the domain's view of a classification result - no infrastructure concerns.
    """
    article_id: str = Field(..., min_length=1, description="Article ID that was classified")
    classification: ClassificationCategory = Field(..., description="Classification category")
    confidence: ClassificationConfidence = Field(..., description="Confidence level")
    reasoning: str = Field(..., max_length=500, description="Reasoning for classification")
    classified_at: datetime = Field(default_factory=datetime.now, description="When classification occurred")
    latency_ms: float = Field(..., ge=0, description="Classification latency in milliseconds")
    
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable
    
    def is_imminent(self) -> bool:
        """Check if classification is IMMINENT."""
        return self.classification == ClassificationCategory.IMMINENT
    
    def is_ignore(self) -> bool:
        """Check if classification is IGNORE."""
        return self.classification == ClassificationCategory.IGNORE
    
    def is_high_confidence(self) -> bool:
        """Check if confidence is HIGH."""
        return self.confidence == ClassificationConfidence.HIGH
    
    def is_medium_or_high_confidence(self) -> bool:
        """Check if confidence is MEDIUM or HIGH."""
        return self.confidence in [ClassificationConfidence.MEDIUM, ClassificationConfidence.HIGH]

