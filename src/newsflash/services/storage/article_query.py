"""
Pure functions for article query operations.

Service layer - pure functions with typed inputs and outputs.
"""
from typing import List, Dict, Any

from ...utils.logging_config import get_logger
from ...domain.storage.factories import StoredArticleFactory
from ...domain.storage.models import StoredArticle

logger = get_logger(__name__)


def convert_article_dicts_to_models(article_dicts: List[Dict[str, Any]]) -> List[StoredArticle]:
    """
    Convert repository article dictionaries to typed domain models.
    
    Args:
        article_dicts: List of article dictionaries from repository
        
    Returns:
        List of StoredArticle domain models
    """
    factory = StoredArticleFactory()
    stored_articles = []
    
    for article_dict in article_dicts:
        stored_article = factory.create_from_dict(article_dict)
        if stored_article:
            stored_articles.append(stored_article)
        else:
            logger.warning(
                "Failed to convert article dict to StoredArticle",
                article_id=article_dict.get("article_id") or article_dict.get("id")
            )
    
    return stored_articles


def convert_stored_article_to_domain_article(stored_article: StoredArticle):
    """
    Convert StoredArticle domain model to Article domain model.
    
    Args:
        stored_article: StoredArticle domain model
        
    Returns:
        Domain Article model
    """
    from ...domain.websocket.models import Article, ArticleSource
    
    return Article(
        id=stored_article.article_id,
        source=ArticleSource(stored_article.source),
        source_id=stored_article.source_id,
        title=stored_article.title,
        content=stored_article.content,
        summary=stored_article.summary,
        author=stored_article.author,
        published_at=stored_article.published_at,
        updated_at=stored_article.updated_at,
        url=stored_article.url,
        tickers=stored_article.tickers,
        tags=stored_article.tags,
        categories=stored_article.categories
    )


async def query_recent_articles(
    article_repository,
    hours: int,
    factory: StoredArticleFactory
) -> List[StoredArticle]:
    """
    Get articles from the last N hours.
    
    Args:
        article_repository: Article repository instance
        hours: Number of hours to look back
        factory: StoredArticleFactory instance
        
    Returns:
        List of StoredArticle domain models
    """
    article_dicts = await article_repository.get_recent_articles(hours)
    return convert_article_dicts_to_models(article_dicts)


async def query_archived_articles(
    article_repository,
    date: str,
    factory: StoredArticleFactory
) -> List[StoredArticle]:
    """
    Get archived articles for a specific date.
    
    Args:
        article_repository: Article repository instance
        date: Date in YYYY-MM-DD format
        factory: StoredArticleFactory instance
        
    Returns:
        List of StoredArticle domain models
    """
    article_dicts = await article_repository.get_archived_articles(date)
    return convert_article_dicts_to_models(article_dicts)


def create_empty_archive_stats():
    """
    Create empty archive statistics.
    
    Returns:
        ArchiveStatistics domain model with default values
    """
    from ...domain.storage.models import ArchiveStatistics
    
    return ArchiveStatistics(
        total_archived_dates=0,
        total_archived_files=0,
        archive_directory=None
    )

