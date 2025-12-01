"""
Mappers for notification domain - transform infrastructure models to domain models.
"""
from typing import Dict, Any
from datetime import datetime

from ...utils.logging_config import get_logger
from ...infra.notification.infrastructure_models import (
    NotificationSendRequestData,
)
from .models import NotificationMessage, NotificationChannel

logger = get_logger(__name__)


class NotificationMapper:
    """
    Maps domain NotificationMessage ↔ infrastructure notification format.
    """
    
    @staticmethod
    def to_infrastructure_request(
        message: NotificationMessage,
        channel: NotificationChannel,
        requested_at: datetime
    ) -> NotificationSendRequestData:
        """
        Transform typed domain NotificationMessage → typed infrastructure NotificationSendRequestData.
        
        Args:
            message: Domain NotificationMessage model
            channel: Target notification channel
            requested_at: When notification was requested
            
        Returns:
            Infrastructure request data model
        """
        # Build payload dict for infrastructure
        payload = {
            "article_id": message.article_id,
            "title": message.title,
            "tickers": list(message.tickers),
            "classification": message.classification,
            "confidence": message.confidence,
            "reasoning": message.reasoning,
            "body": message.body,
        }
        
        return NotificationSendRequestData(
            channel=channel.value,
            payload=payload,
            requested_at=requested_at
        )
    
    @staticmethod
    def from_infrastructure_dict(payload: Dict[str, Any]) -> NotificationMessage:
        """
        Transform raw dict from infrastructure → typed domain NotificationMessage.
        
        Args:
            payload: Dictionary with notification data
            
        Returns:
            Domain NotificationMessage model
        """
        from .factories import NotificationMessageFactory
        
        return NotificationMessageFactory.create_from_dict(payload)

