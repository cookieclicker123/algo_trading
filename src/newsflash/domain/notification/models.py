"""
Domain models for notification - pure business logic, immutable value objects.
"""
from datetime import datetime
from enum import Enum
from typing import FrozenSet
from pydantic import BaseModel, Field


class NotificationChannel(str, Enum):
    """
    Notification channels - domain business logic.
    
    Supported channels for sending notifications.
    """
    TELEGRAM = "telegram"
    CONSOLE = "console"
    WEBHOOK = "webhook"  # Future support


class NotificationMessage(BaseModel):
    """
    Domain model - notification message (pure business logic).
    
    This is the domain's view of a notification - no infrastructure concerns.
    """
    article_id: str = Field(..., min_length=1, description="Article ID")
    title: str = Field(..., min_length=1, description="Article title/headline")
    tickers: FrozenSet[str] = Field(default_factory=frozenset, description="Stock tickers (immutable)")
    classification: str = Field(..., description="Classification category (e.g., 'imminent')")
    confidence: str = Field(..., description="Confidence level (e.g., 'HIGH')")
    reasoning: str = Field(default="", description="Classification reasoning")
    body: str = Field(..., min_length=1, description="Human-readable notification message")
    channels: FrozenSet[NotificationChannel] = Field(..., description="Target notification channels (immutable)")
    created_at: datetime = Field(default_factory=datetime.now, description="When notification was created")
    
    model_config = {"frozen": True, "validate_assignment": False}  # Immutable
    
    def has_tickers(self) -> bool:
        """Check if the notification has any tickers."""
        return len(self.tickers) > 0
    
    def is_telegram(self) -> bool:
        """Check if Telegram channel is included."""
        return NotificationChannel.TELEGRAM in self.channels
    
    def is_imminent(self) -> bool:
        """Check if classification is IMMINENT."""
        return self.classification.lower() == "imminent"

