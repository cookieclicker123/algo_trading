"""
Infrastructure-specific models for classification events.

These are infrastructure's own typed models - NOT domain models.
Infrastructure owns these completely.
"""
from pydantic import BaseModel, Field
from datetime import datetime


class InfrastructureClassificationRequestData(BaseModel):
    """
    Infrastructure classification request data model - format sent to Groq API.
    
    Infrastructure's own representation - can change without affecting domain.
    """
    article_id: str = Field(..., description="Article ID for tracking")
    article_title: str = Field(..., description="Article title/headline")
    article_tickers: list[str] = Field(default_factory=list, description="Stock tickers")
    article_summary: str = Field(default="", description="Article summary/content")
    # Infrastructure-specific fields can be added here


class InfrastructureClassificationResponseData(BaseModel):
    """
    Infrastructure classification response data model - format received from Groq API.
    
    This is the raw response from Groq, before domain transformation.
    """
    classification: str = Field(..., description="Classification: 'imminent' or 'ignore'")
    confidence: str = Field(..., description="Confidence: 'HIGH', 'MEDIUM', or 'LOW'")
    reasoning: str = Field(..., description="Reasoning for classification")
    # Raw Groq API response fields can be added here


class ClassificationRequestedInfrastructureEvent(BaseModel):
    """
    Infrastructure event - classification requested (from domain to infrastructure).
    
    Typed model that infrastructure expects to receive.
    """
    request_data: InfrastructureClassificationRequestData = Field(..., description="Classification request data")
    requested_at: datetime = Field(..., description="When classification was requested")
    source: str = Field(default="domain.classification", description="Event source")
    
    model_config = {"frozen": False}  # Events can be mutable for serialization


class ClassificationCompletedInfrastructureEvent(BaseModel):
    """Infrastructure event - classification completed by Groq API."""
    request_data: InfrastructureClassificationRequestData = Field(..., description="Original request data")
    response_data: InfrastructureClassificationResponseData = Field(..., description="Groq API response data")
    completed_at: datetime = Field(..., description="When classification completed")
    latency_ms: float = Field(..., description="API call latency in milliseconds")
    success: bool = Field(..., description="Whether classification succeeded")
    source: str = Field(default="groq_classifier", description="Event source")
    
    model_config = {"frozen": False}


class ClassificationFailedInfrastructureEvent(BaseModel):
    """Infrastructure event - classification failed."""
    request_data: InfrastructureClassificationRequestData = Field(..., description="Original request data")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When classification failed")
    source: str = Field(default="groq_classifier", description="Event source")
    
    model_config = {"frozen": False}


class ClassificationSkippedInfrastructureEvent(BaseModel):
    """
    Infrastructure event - classification skipped (pre-filtered).
    
    Published when article doesn't meet pre-classification criteria:
    - No tickers
    - Tickers not tradeable on NASDAQ/NYSE
    """
    request_data: InfrastructureClassificationRequestData = Field(..., description="Original request data")
    skipped_at: datetime = Field(..., description="When classification was skipped")
    reason: str = Field(..., description="Skip reason: 'no_tickers' or 'not_tradeable_exchange'")
    source: str = Field(default="classification_infrastructure", description="Event source")
    
    model_config = {"frozen": False}

