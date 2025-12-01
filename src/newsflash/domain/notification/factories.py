"""
Factories for creating notification domain models from various inputs.
"""
from datetime import datetime
from typing import Dict, Any, Optional, FrozenSet

from ...utils.logging_config import get_logger
from .models import NotificationMessage, NotificationChannel
from ...domain.websocket.models import Article as DomainArticle
from ...domain.classification.models import ClassificationResult

logger = get_logger(__name__)


class NotificationMessageFactory:
    """
    Factory for creating NotificationMessage domain models.
    """
    
    @staticmethod
    def create_from_dict(data: Dict[str, Any]) -> NotificationMessage:
        """
        Create NotificationMessage from dictionary.
        
        Args:
            data: Dictionary with notification data
            
        Returns:
            NotificationMessage domain model
        """
        # Parse channels
        channels = set()
        if "channels" in data:
            if isinstance(data["channels"], list):
                channels = {NotificationChannel(c) for c in data["channels"]}
            elif isinstance(data["channels"], (set, frozenset)):
                channels = {NotificationChannel(c) for c in data["channels"]}
        else:
            # Default to Telegram if not specified
            channels = {NotificationChannel.TELEGRAM}
        
        return NotificationMessage(
            article_id=data["article_id"],
            title=data["title"],
            tickers=frozenset(data.get("tickers", [])),
            classification=data.get("classification", ""),
            confidence=data.get("confidence", ""),
            reasoning=data.get("reasoning", ""),
            body=data["body"],
            channels=frozenset(channels),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else (data.get("created_at") if isinstance(data.get("created_at"), datetime) else datetime.now())
        )
    
    @staticmethod
    def create_from_article_and_classification(
        article: DomainArticle,
        classification_result: ClassificationResult,
        channels: FrozenSet[NotificationChannel],
        body: Optional[str] = None
    ) -> Optional[NotificationMessage]:
        """
        Create NotificationMessage from article and classification result.
        
        Args:
            article: Domain Article model
            classification_result: ClassificationResult domain model
            channels: Target notification channels
            body: Optional custom body text (if None, will be generated)
            
        Returns:
            NotificationMessage domain model, or None if creation fails
        """
        try:
            # Generate body if not provided
            if not body:
                tickers_str = ", ".join(sorted(article.tickers)) if article.tickers else "None"
                body = (
                    f"🚨 IMMINENT NEWS ALERT\n\n"
                    f"📰 {article.title}\n\n"
                    f"🏷️ Tickers: {tickers_str}\n"
                    f"📊 Classification: {classification_result.classification.value.upper()}\n"
                    f"🎯 Confidence: {classification_result.confidence.value}\n"
                    f"💭 Reasoning: {classification_result.reasoning}\n\n"
                    f"🔗 {article.url if article.url else 'No URL'}"
                )
            
            return NotificationMessage(
                article_id=article.id,
                title=article.title,
                tickers=article.tickers,
                classification=classification_result.classification.value,
                confidence=classification_result.confidence.value,
                reasoning=classification_result.reasoning,
                body=body,
                channels=channels,
                created_at=datetime.now()
            )
        except Exception as e:
            logger.error(
                "NotificationMessageFactory: Failed to create notification message",
                error=str(e),
                article_id=article.id if article else "unknown",
                exc_info=True
            )
            return None

