"""
Timezone utilities for converting publication timestamps.
"""
from datetime import datetime
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


def convert_to_gmt(article_datetime: datetime) -> str:
    """
    Convert article publication time to GMT while preserving original format.
    
    Args:
        article_datetime: The publication datetime from the article
        
    Returns:
        GMT timestamp string in ISO format for backtesting
    """
    try:
        # If the datetime is naive (no timezone), assume it's US Eastern Time
        if article_datetime.tzinfo is None:
            # Assume US Eastern Time (EST/EDT)
            from zoneinfo import ZoneInfo
            us_eastern = ZoneInfo("US/Eastern")
            localized_dt = article_datetime.replace(tzinfo=us_eastern)
        else:
            localized_dt = article_datetime
        
        # Convert to GMT/UTC
        gmt_dt = localized_dt.astimezone(ZoneInfo("GMT"))
        
        # Return in ISO format for precise backtesting
        return gmt_dt.isoformat()
        
    except Exception as e:
        logger.error("Failed to convert timezone", error=str(e), original_datetime=article_datetime)
        # Fallback to original datetime as string
        return article_datetime.isoformat()


def get_published_timestamp(article) -> str:
    """
    Extract and convert publication timestamp from article.
    
    Args:
        article: BenzingaArticle or StandardizedArticle
        
    Returns:
        GMT timestamp string
    """
    try:
        # Get published timestamp from article
        if hasattr(article, 'published'):
            return convert_to_gmt(article.published)
        else:
            logger.error("Article missing published timestamp", article_type=type(article).__name__)
            return datetime.now().isoformat()
    except Exception as e:
        logger.error("Failed to extract publication timestamp", error=str(e))
        return datetime.now().isoformat()
