"""
Event definitions for Notification microservice.

Infrastructure events use infrastructure-specific typed models.
"""
from pydantic import BaseModel, Field
from datetime import datetime

from .infrastructure_models import (
    NotificationSendRequestData,
)


class NotificationSendRequestedEvent(BaseModel):
    """
    Infrastructure event published when notification send is requested.
    
    Uses infrastructure-specific typed model - not domain models, not shared models.
    """
    request_data: NotificationSendRequestData = Field(..., description="Infrastructure notification request data (typed model)")
    requested_at: datetime = Field(default_factory=datetime.now)
    source: str = Field(default="notification_infrastructure")


class NotificationSentEvent(BaseModel):
    """Event published when notification is sent successfully."""
    request_data: NotificationSendRequestData
    sent_at: datetime
    source: str = Field(default="notification_infrastructure")


class NotificationFailedEvent(BaseModel):
    """Event published when notification fails."""
    request_data: NotificationSendRequestData
    error: str
    failed_at: datetime
    source: str = Field(default="notification_infrastructure")

