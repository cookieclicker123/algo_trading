"""
Factories for WebSocket domain - create domain objects with business rules.

Validates against protocols to ensure contract compliance.
"""
from typing import Dict, Any, Optional

from ...utils.logging_config import get_logger
from .models import Article
from .validators import ArticleValidator
from .mappers import ArticleMapper

logger = get_logger(__name__)


class ArticleFactory:
    """
    Factory for creating Article domain objects.
    
    Ensures business rules are applied during creation.
    """
    
    @staticmethod
    def create_from_standardized(standardized_article) -> Optional[Article]:
        """
        Create Article from StandardizedArticle (infrastructure model).
        
        Args:
            standardized_article: StandardizedArticle from infrastructure
            
        Returns:
            Article domain model, or None if invalid
        """
        try:
            # Map to domain model
            article = ArticleMapper.from_standardized_article(standardized_article)
            
            # Validate domain model
            if not ArticleValidator.is_valid_domain_article(article):
                logger.warning(
                    "Article factory: Created article failed validation",
                    article_id=article.id
                )
                return None
            
            logger.debug("Article factory: Created article", article_id=article.id)
            return article
            
        except Exception as e:
            logger.error(
                "Article factory: Error creating article",
                error=str(e),
                exc_info=True
            )
            return None
    
    @staticmethod
    def create_from_infrastructure_model(infra_article_data, received_at=None) -> Optional[Article]:
        """
        Create Article from typed infrastructure model.

        Args:
            infra_article_data: InfrastructureArticleData model (typed, already validated)
            received_at: Optional timestamp to use as fallback if published timestamp is missing

        Returns:
            Article domain model, or None if mapping failed
        """
        try:
            # Infrastructure model is already validated by Pydantic
            # Transform to domain model via mapper (Pydantic validates Article on creation)
            article = ArticleMapper.from_infrastructure_model(infra_article_data, received_at=received_at)

            if not article:
                logger.warning("Article factory: Mapping from infrastructure model failed")
                return None

            # Skip redundant validation - Pydantic already validated the Article model on creation
            # (saves ~5-10ms per article)

            logger.debug("Article factory: Created article from infrastructure model", article_id=article.id)
            return article

        except Exception as e:
            logger.error("Article factory: Error creating article from infrastructure model", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def create_from_dict(data: Dict[str, Any]) -> Optional[Article]:
        """
        Create Article from raw dictionary.
        
        Args:
            data: Raw article dictionary
            
        Returns:
            Article domain model, or None if invalid
        """
        try:
            # Validate raw data first
            if not ArticleValidator.is_valid_article_data(data):
                logger.warning("Article factory: Invalid article data provided")
                return None
            
            # Map to domain model
            article = ArticleMapper.from_dict(data)
            
            if not article:
                logger.warning("Article factory: Mapping failed")
                return None
            
            # Validate domain model
            if not ArticleValidator.is_valid_domain_article(article):
                logger.warning(
                    "Article factory: Created article failed validation",
                    article_id=article.id
                )
                return None
            
            logger.debug("Article factory: Created article from dict", article_id=article.id)
            return article
            
        except Exception as e:
            logger.error(
                "Article factory: Error creating article from dict",
                error=str(e),
                exc_info=True
            )
            return None

