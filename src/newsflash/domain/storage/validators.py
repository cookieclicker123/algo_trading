"""
Validators for storage domain - business rule validation.
"""
from ...utils.logging_config import get_logger
from .models import StoredArticle, AuditEntry

logger = get_logger(__name__)


class StoredArticleValidator:
    """Validates StoredArticle domain models."""
    
    @staticmethod
    def is_valid_stored_article(article: StoredArticle) -> bool:
        """
        Validate that a StoredArticle meets business rules.
        
        Args:
            article: StoredArticle to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not article.article_id:
            logger.warning("StoredArticle validation failed: missing article_id")
            return False
        
        if not article.title:
            logger.warning("StoredArticle validation failed: missing title")
            return False
        
        if not article.source:
            logger.warning("StoredArticle validation failed: missing source")
            return False
        
        return True


class AuditEntryValidator:
    """Validates AuditEntry domain models."""
    
    @staticmethod
    def is_valid_audit_entry(entry: AuditEntry) -> bool:
        """
        Validate that an AuditEntry meets business rules.
        
        Args:
            entry: AuditEntry to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not entry.article_id:
            logger.warning("AuditEntry validation failed: missing article_id")
            return False
        
        if not entry.classification:
            logger.warning("AuditEntry validation failed: missing classification")
            return False
        
        if entry.classification.lower() not in ["imminent", "ignore"]:
            logger.warning("AuditEntry validation failed: invalid classification", classification=entry.classification)
            return False
        
        return True

