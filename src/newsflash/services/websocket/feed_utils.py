"""
Pure functions for feed management operations.

Service layer - pure functions with typed inputs and outputs.
"""
from typing import Dict, Any

from ...utils.logging_config import get_logger
from ...domain.websocket.events import ArticleReceivedDomainEvent

logger = get_logger(__name__)


def extract_article_from_event(event_data: Dict[str, Any]) -> ArticleReceivedDomainEvent:
    """
    Extract and reconstruct ArticleReceivedDomainEvent from event data.
    
    Args:
        event_data: Event data dictionary
        
    Returns:
        ArticleReceivedDomainEvent domain event
    """
    return ArticleReceivedDomainEvent(**event_data)


def log_article_reception(article_id: str, tickers: list, total_count: int) -> None:
    """
    Log article reception with feed statistics.
    
    Args:
        article_id: Article ID
        tickers: List of tickers
        total_count: Total articles received count
    """
    logger.info(
        "FeedManager: Article received from domain",
        article_id=article_id,
        tickers=tickers,
        total_received=total_count
    )


def create_feed_stats(is_running: bool, articles_received: int) -> Dict[str, Any]:
    """
    Create feed statistics dictionary.
    
    Args:
        is_running: Whether feed manager is running
        articles_received: Total articles received count
        
    Returns:
        Statistics dictionary
    """
    return {
        "is_running": is_running,
        "articles_received": articles_received
    }

