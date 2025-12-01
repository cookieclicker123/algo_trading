"""
Factories for storage domain - create domain models from various sources.
"""
from typing import Optional
from datetime import datetime

from ...utils.logging_config import get_logger
from .models import StoredArticle, AuditEntry
from ...domain.websocket.models import Article as DomainArticle
from ...domain.classification.models import ClassificationResult

logger = get_logger(__name__)


class StoredArticleFactory:
    """
    Factory for creating StoredArticle domain models.
    """
    
    @staticmethod
    def create_from_domain_article(domain_article: DomainArticle) -> Optional[StoredArticle]:
        """
        Create StoredArticle from domain Article.
        
        Args:
            domain_article: Domain Article model
            
        Returns:
            StoredArticle domain model, or None if invalid
        """
        try:
            return StoredArticle(
                article_id=domain_article.id,
                source=domain_article.source.value,
                source_id=domain_article.source_id,
                title=domain_article.title,
                content=domain_article.content,
                summary=domain_article.summary,
                author=domain_article.author,
                published_at=domain_article.published_at,
                updated_at=domain_article.updated_at,
                url=domain_article.url,
                tickers=domain_article.tickers,
                tags=domain_article.tags,
                categories=domain_article.categories,
                stored_at=datetime.now()
            )
        except Exception as e:
            logger.error("Error creating StoredArticle from domain Article", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def create_from_dict(article_data: dict) -> Optional[StoredArticle]:
        """
        Create StoredArticle from dictionary (e.g., from storage).
        
        Args:
            article_data: Article data dictionary
            
        Returns:
            StoredArticle domain model, or None if invalid
        """
        try:
            return StoredArticle(
                article_id=article_data.get("article_id") or article_data.get("id", ""),
                source=article_data.get("source", "unknown"),
                source_id=article_data.get("source_id", ""),
                title=article_data.get("title", ""),
                content=article_data.get("content"),
                summary=article_data.get("summary"),
                author=article_data.get("author"),
                published_at=datetime.fromisoformat(article_data["published_at"]) if "published_at" in article_data else datetime.now(),
                updated_at=datetime.fromisoformat(article_data["updated_at"]) if article_data.get("updated_at") else None,
                url=article_data.get("url"),
                tickers=frozenset(article_data.get("tickers", [])),
                tags=frozenset(article_data.get("tags", [])),
                categories=frozenset(article_data.get("categories", [])),
                stored_at=datetime.fromisoformat(article_data["stored_at"]) if article_data.get("stored_at") else datetime.now()
            )
        except Exception as e:
            logger.error("Error creating StoredArticle from dict", error=str(e), exc_info=True)
            return None


class AuditEntryFactory:
    """
    Factory for creating AuditEntry domain models.
    """
    
    @staticmethod
    def create_from_classification(
        article: DomainArticle,
        classification_result: ClassificationResult,
        news_received_at: datetime,
        metadata: Optional[dict] = None
    ) -> Optional[AuditEntry]:
        """
        Create AuditEntry from classification result.
        
        Args:
            article: Domain Article model
            classification_result: Domain ClassificationResult model
            news_received_at: When news was received
            metadata: Optional metadata dictionary
            
        Returns:
            AuditEntry domain model, or None if invalid
        """
        try:
            return AuditEntry(
                article_id=article.id,
                article_title=article.title,
                article_tickers=article.tickers,
                article_published=article.published_at,
                classification=classification_result.classification.value,
                confidence=classification_result.confidence.value,
                reasoning=classification_result.reasoning,
                source=article.source.value,
                news_received_at=news_received_at,
                classified_at=classification_result.classified_at,
                logged_at=datetime.now(),
                metadata=metadata or {},
                trade_details={},
                timing_stats={},
                price_history={}
            )
        except Exception as e:
            logger.error("Error creating AuditEntry from classification", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def create_from_dict(audit_data: dict) -> Optional[AuditEntry]:
        """
        Create AuditEntry from dictionary (e.g., from storage).
        
        Args:
            audit_data: Audit data dictionary
            
        Returns:
            AuditEntry domain model, or None if invalid
        """
        try:
            return AuditEntry(
                article_id=audit_data.get("article_id", ""),
                article_title=audit_data.get("article_title", ""),
                article_tickers=frozenset(audit_data.get("article_tickers", [])),
                article_published=datetime.fromisoformat(audit_data["article_published"]) if audit_data.get("article_published") else None,
                classification=audit_data.get("classification", ""),
                confidence=audit_data.get("confidence", ""),
                reasoning=audit_data.get("reasoning", ""),
                source=audit_data.get("source", ""),
                news_received_at=datetime.fromisoformat(audit_data["news_received_at"]) if audit_data.get("news_received_at") else datetime.now(),
                classified_at=datetime.fromisoformat(audit_data["classified_at"]) if audit_data.get("classified_at") else datetime.now(),
                logged_at=datetime.fromisoformat(audit_data["logged_at"]) if audit_data.get("logged_at") else datetime.now(),
                metadata=audit_data.get("metadata", {}),
                trade_details=audit_data.get("trade_details", {}),
                timing_stats=audit_data.get("timing_stats", {}),
                price_history=audit_data.get("price_history", {})
            )
        except Exception as e:
            logger.error("Error creating AuditEntry from dict", error=str(e), exc_info=True)
            return None

