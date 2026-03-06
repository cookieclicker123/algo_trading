"""
Domain events for classification - business events published by domain layer.

These use domain models directly - fully typed, not Dict[str, Any].
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from .models import ClassificationRequest, ClassificationResult


class ClassificationRequestedDomainEvent(BaseModel):
    """
    Domain event - classification requested for an article.

    Uses domain ClassificationRequest model directly - fully typed, validated.
    """
    request: ClassificationRequest = Field(..., description="Domain ClassificationRequest model (validated, immutable)")
    requested_at: datetime = Field(..., description="When classification was requested")
    source: str = Field(default="domain.classification", description="Event source")

    model_config = {"frozen": True}  # Immutable


class ArticleClassifiedDomainEvent(BaseModel):
    """
    Domain event - article has been classified.

    Uses domain ClassificationResult model directly - fully typed, validated.

    IMPORTANT: This event includes article metadata (tickers, title, published_at) to avoid
    race condition with storage. Auto-trade can use these fields directly instead of waiting
    for storage to complete.
    """
    article_id: str = Field(..., description="Article ID that was classified")
    result: ClassificationResult = Field(..., description="Domain ClassificationResult model (validated, immutable)")
    classified_at: datetime = Field(..., description="When classification was completed")
    # Article metadata included to avoid storage race condition
    tickers: List[str] = Field(default_factory=list, description="Article tickers (for immediate auto-trade)")
    title: str = Field(default="", description="Article title (for logging)")
    published_at: Optional[datetime] = Field(None, description="Article publication time (for confluence scoring)")
    # AI-determined position size for immediate trading (no confluence delay)
    position_size: Optional[str] = Field(None, description="AI position size: SMALL, MODERATE, LARGE, MAX")
    # Headline type for high-conviction bypass (e.g. military_contract, fda_approval)
    headline_type: Optional[str] = Field(None, description="Headline type from HeadlineTypeClassifier")
    source: str = Field(default="domain.classification", description="Event source")

    model_config = {"frozen": True}  # Immutable


class ClassificationFailedDomainEvent(BaseModel):
    """
    Domain event - classification failed.
    
    Published when classification cannot be completed.
    """
    article_id: str = Field(..., description="Article ID that failed to classify")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When classification failed")
    source: str = Field(default="domain.classification", description="Event source")
    
    model_config = {"frozen": True}  # Immutable

