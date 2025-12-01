"""
Domain events for notification - business events published by domain layer.

These use domain models directly - fully typed, not Dict[str, Any].
"""
from datetime import datetime
from pydantic import BaseModel, Field

from .models import NotificationMessage, NotificationChannel


class NotificationRequestedDomainEvent(BaseModel):
    """
    Domain event - notification requested.
    
    Uses domain NotificationMessage model directly - fully typed, validated.
    """
    message: NotificationMessage = Field(..., description="Domain NotificationMessage model (validated, immutable)")
    requested_at: datetime = Field(..., description="When notification was requested")
    source: str = Field(default="domain.notification", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class NotificationSentDomainEvent(BaseModel):
    """
    Domain event - notification has been sent.
    
    Published when notification is successfully sent to a channel.
    """
    message: NotificationMessage = Field(..., description="Notification message that was sent")
    channel: NotificationChannel = Field(..., description="Channel where notification was sent")
    sent_at: datetime = Field(..., description="When notification was sent")
    source: str = Field(default="domain.notification", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class NotificationFailedDomainEvent(BaseModel):
    """
    Domain event - notification failed.
    
    Published when notification cannot be sent.
    """
    message: NotificationMessage = Field(..., description="Notification message that failed")
    channel: NotificationChannel = Field(..., description="Channel where notification failed")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When notification failed")
    source: str = Field(default="domain.notification", description="Event source")
    
    model_config = {"frozen": True}  # Immutable

