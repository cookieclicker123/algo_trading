"""
Infrastructure-specific models for notification operations.

These are infrastructure's own typed models - NOT domain models.
Infrastructure owns these completely.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Dict, Any


class NotificationSendRequestData(BaseModel):
    """
    Infrastructure notification send request data model.
    
    Infrastructure's own representation - can change without affecting domain.
    """
    channel: str = Field(..., description="Notification channel (e.g., 'telegram')")
    payload: Dict[str, Any] = Field(..., description="Notification payload (dict)")
    requested_at: datetime = Field(..., description="When notification was requested")


class NotificationSentInfrastructureEvent(BaseModel):
    """
    Infrastructure event - notification sent successfully.
    
    Published after notification is sent to external service.
    """
    request_data: NotificationSendRequestData = Field(..., description="Original request data")
    sent_at: datetime = Field(..., description="When notification was actually sent")
    source: str = Field(default="notification_infrastructure", description="Event source")
    
    model_config = {"frozen": False}


class NotificationFailedInfrastructureEvent(BaseModel):
    """Infrastructure event - notification failed."""
    request_data: NotificationSendRequestData = Field(..., description="Original request data")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When notification failed")
    source: str = Field(default="notification_infrastructure", description="Event source")
    
    model_config = {"frozen": False}

