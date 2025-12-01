"""
Domain events for classification - business events published by domain layer.

These use domain models directly - fully typed, not Dict[str, Any].
"""
from datetime import datetime
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
    """
    article_id: str = Field(..., description="Article ID that was classified")
    result: ClassificationResult = Field(..., description="Domain ClassificationResult model (validated, immutable)")
    classified_at: datetime = Field(..., description="When classification was completed")
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

