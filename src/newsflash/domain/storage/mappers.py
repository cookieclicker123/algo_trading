"""
Mappers for storage domain - transform infrastructure models to domain models.
"""
from datetime import datetime

from ...utils.logging_config import get_logger
from ...infra.storage.infrastructure_models import (
    ArticleStorageRequestData,
    AuditLogStorageRequestData,
)
from .models import StoredArticle, AuditEntry

logger = get_logger(__name__)


class ArticleStorageMapper:
    """
    Maps domain StoredArticle ↔ infrastructure storage format.
    """
    
    @staticmethod
    def from_domain_article(domain_article: StoredArticle) -> dict:
        """
        Transform domain StoredArticle → dict for storage.
        
        Args:
            domain_article: Domain StoredArticle model
            
        Returns:
            Dictionary representation for storage
        """
        return {
            "article_id": domain_article.article_id,
            "source": domain_article.source,
            "source_id": domain_article.source_id,
            "title": domain_article.title,
            "content": domain_article.content,
            "summary": domain_article.summary,
            "author": domain_article.author,
            "published_at": domain_article.published_at.isoformat(),
            "updated_at": domain_article.updated_at.isoformat() if domain_article.updated_at else None,
            "url": domain_article.url,
            "tickers": list(domain_article.tickers),
            "tags": list(domain_article.tags),
            "categories": list(domain_article.categories),
        }
    
    @staticmethod
    def to_infrastructure_request(article_data: dict, article_id: str) -> ArticleStorageRequestData:
        """
        Transform article data → infrastructure storage request.
        
        Args:
            article_data: Article data dictionary
            article_id: Article ID
            
        Returns:
            Infrastructure storage request data
        """
        return ArticleStorageRequestData(
            article_id=article_id,
            article_data=article_data,
            stored_at=datetime.now(),
            source=article_data.get("source", "unknown"),
            published_at=datetime.fromisoformat(article_data["published_at"]) if "published_at" in article_data else datetime.now()
        )


class AuditLogMapper:
    """
    Maps domain AuditEntry ↔ infrastructure storage format.
    """
    
    @staticmethod
    def from_domain_audit_entry(audit_entry: AuditEntry) -> dict:
        """
        Transform domain AuditEntry → dict for storage.
        
        Args:
            audit_entry: Domain AuditEntry model
            
        Returns:
            Dictionary representation for storage
        """
        return {
            "article_id": audit_entry.article_id,
            "article_title": audit_entry.article_title,
            "article_tickers": list(audit_entry.article_tickers),
            "article_published": audit_entry.article_published.isoformat() if audit_entry.article_published else None,
            "classification": audit_entry.classification,
            "confidence": audit_entry.confidence,
            "reasoning": audit_entry.reasoning,
            "source": audit_entry.source,
            "news_received_at": audit_entry.news_received_at.isoformat(),
            "classified_at": audit_entry.classified_at.isoformat(),
            "logged_at": audit_entry.logged_at.isoformat(),
            "metadata": audit_entry.metadata,
            "trade_details": audit_entry.trade_details,
            "timing_stats": audit_entry.timing_stats,
            "price_history": audit_entry.price_history,
        }
    
    @staticmethod
    def to_infrastructure_request(audit_data: dict, article_id: str) -> AuditLogStorageRequestData:
        """
        Transform audit data → infrastructure storage request.
        
        Args:
            audit_data: Audit data dictionary
            article_id: Article ID
            
        Returns:
            Infrastructure storage request data
        """
        return AuditLogStorageRequestData(
            article_id=article_id,
            audit_data=audit_data,
            logged_at=datetime.now(),
            entry_type="classification"
        )

