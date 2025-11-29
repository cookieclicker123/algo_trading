"""
Article utility functions for common operations across services.
"""
from typing import Union

from ..models.benzinga_models import BenzingaArticle
from ..models.base_models import StandardizedArticle


def get_article_id(article: Union[BenzingaArticle, StandardizedArticle, dict]) -> str:
    """
    Extract article ID from various article types.
    
    Handles:
    - BenzingaArticle objects
    - StandardizedArticle objects  
    - Dict-like objects with article data
    
    Args:
        article: Article object or dict
        
    Returns:
        Article ID as string, or "unknown" if not found
    """
    # Handle BenzingaArticle
    if isinstance(article, BenzingaArticle):
        return str(article.benzinga_id)
    
    # Handle StandardizedArticle
    if isinstance(article, StandardizedArticle):
        return article.source_id
    
    # Handle objects with attributes (most comprehensive check)
    if hasattr(article, 'benzinga_id'):
        return str(getattr(article, 'benzinga_id'))
    if hasattr(article, 'source_id'):
        return str(getattr(article, 'source_id'))
    if hasattr(article, 'id'):
        return str(getattr(article, 'id'))
    
    # Handle dict-like objects
    if isinstance(article, dict):
        for key in ('benzinga_id', 'source_id', 'id'):
            if key in article:
                return str(article[key])
    
    return "unknown"

