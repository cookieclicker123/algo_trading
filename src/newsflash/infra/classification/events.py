"""
Event definitions for Classification microservice.

Infrastructure events use infrastructure-specific typed models.
"""
from pydantic import BaseModel, Field
from datetime import datetime

from .infrastructure_models import (
    InfrastructureClassificationRequestData,
    InfrastructureClassificationResponseData,
)


class ClassificationRequestedEvent(BaseModel):
    """
    Infrastructure event published when classification is requested.
    
    Uses infrastructure-specific typed model - not domain models, not shared models.
    """
    request_data: InfrastructureClassificationRequestData = Field(..., description="Infrastructure classification request data (typed model)")
    requested_at: datetime
    source: str = "groq_classifier"


class ClassificationCompletedEvent(BaseModel):
    """Event published when classification completes successfully."""
    request_data: InfrastructureClassificationRequestData
    response_data: InfrastructureClassificationResponseData
    completed_at: datetime
    latency_ms: float
    success: bool = True
    source: str = "groq_classifier"


class ClassificationFailedEvent(BaseModel):
    """Event published when classification fails."""
    request_data: InfrastructureClassificationRequestData
    error: str
    failed_at: datetime
    source: str = "groq_classifier"

