"""
Pure functions for building classification requests from articles.

Service layer - pure functions with typed inputs and outputs.
Uses domain factories for business logic.
"""
from typing import Optional

from ...utils.logging_config import get_logger
from ...domain.websocket.models import Article
from ...domain.classification.models import ClassificationRequest
from ...domain.classification.factories import ClassificationRequestFactory

logger = get_logger(__name__)


def create_classification_request(article: Article) -> Optional[ClassificationRequest]:
    """
    Create a classification request from a domain article.
    
    Uses domain factory to ensure business rules are applied.
    
    Args:
        article: Domain Article model
        
    Returns:
        ClassificationRequest domain model, or None if invalid
    """
    return ClassificationRequestFactory.create_from_article(article)


def validate_classification_request(request: ClassificationRequest) -> bool:
    """
    Validate a classification request.
    
    Args:
        request: ClassificationRequest to validate
        
    Returns:
        True if valid, False otherwise
    """
    from ...domain.classification.validators import ClassificationRequestValidator
    
    return ClassificationRequestValidator.is_valid_classification_request(request)


def can_classify_article(article: Article) -> bool:
    """
    Check if an article can be classified.
    
    Business rules:
    - Article must have a title
    
    Args:
        article: Domain Article model
        
    Returns:
        True if article can be classified, False otherwise
    """
    if not article.title or not article.title.strip():
        logger.debug("Article cannot be classified: missing title", article_id=article.id)
        return False
    
    return True


def extract_classification_summary(article: Article) -> str:
    """
    Extract summary text from article for classification.
    
    Args:
        article: Domain Article model
        
    Returns:
        Summary text (prefers summary, falls back to content)
    """
    return article.summary or article.content or ""


def get_article_tickers_for_classification(article: Article) -> frozenset[str]:
    """
    Get article tickers as a frozen set for classification.
    
    Args:
        article: Domain Article model
        
    Returns:
        Frozen set of ticker symbols
    """
    return article.tickers if article.tickers else frozenset[str]()
