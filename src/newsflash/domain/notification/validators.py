"""
Validators for notification domain models.

These validate domain models to ensure they meet business rules.
"""
from typing import Optional

from ...utils.logging_config import get_logger
from .models import NotificationMessage

logger = get_logger(__name__)


class NotificationMessageValidator:
    """
    Validates NotificationMessage domain models.
    """
    
    def validate(self, message: NotificationMessage) -> tuple[bool, Optional[str]]:
        """
        Validate a NotificationMessage.
        
        Args:
            message: NotificationMessage to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check required fields
        if not message.article_id:
            return False, "Article ID is required"
        
        if not message.title:
            return False, "Title is required"
        
        if not message.body:
            return False, "Body is required"
        
        if not message.channels:
            return False, "At least one notification channel is required"
        
        # Check classification (allow empty string for trade notifications)
        if message.classification and message.classification.lower() not in ["imminent", "ignore"]:
            return False, f"Invalid classification: {message.classification}"
        
        # Check confidence (allow empty string for trade notifications)
        if message.confidence and message.confidence.upper() not in ["HIGH", "MEDIUM", "LOW"]:
            return False, f"Invalid confidence: {message.confidence}"
        
        return True, None

