"""
Message processing logic for WebSocket service.

Stateless helper functions - all state is passed as parameters.
"""
import json
from typing import Dict, Any, Optional, List

from ...utils.logging_config import get_logger
from .infrastructure_models import InfrastructureArticleData

logger = get_logger(__name__)


def parse_websocket_message(message: str) -> tuple[Optional[Dict[str, Any]], bool]:
    """
    Parse WebSocket message, detecting format (JSON/XML).
    
    Stateless function - all state passed as parameters.
    
    Args:
        message: Raw WebSocket message string
        
    Returns:
        Tuple of (parsed_data, is_json)
        - parsed_data: Parsed JSON dict if JSON, None if XML/other
        - is_json: True if message is JSON, False if XML/other
    """
    try:
        data = json.loads(message)
        logger.debug("Message is JSON format")
        return data, True
    except json.JSONDecodeError:
        # Not JSON - check if it's XML/HTML
        if message.strip().startswith('<') or 'xml' in message.lower():
            logger.debug("Message is XML/HTML format")
            return None, False
        else:
            logger.debug("Unknown message format", message_preview=message[:100])
            return None, False


def extract_articles_from_json(data: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """
    Extract article data from parsed JSON message.
    
    Stateless function - all state passed as parameters.
    
    Args:
        data: Parsed JSON data dictionary
        
    Returns:
        List of article data dictionaries, or None if no articles found
    """
    if isinstance(data, dict):
        if data.get("kind") == "News/v1" and "data" in data:
            # Handle news articles from Benzinga WebSocket
            news_data = data["data"]
            if news_data.get("action") == "Created" and "content" in news_data:
                return [news_data["content"]]
        elif "news" in data:
            return data["news"]
    elif isinstance(data, list):
        # List of articles
        return data
    
    return None


def is_heartbeat_message(data: Dict[str, Any]) -> bool:
    """
    Check if message is a heartbeat/pong message.
    
    Stateless function - all state passed as parameters.
    
    Args:
        data: Parsed JSON data dictionary
        
    Returns:
        True if message is heartbeat/pong
    """
    if "heartbeat" in data:
        return True
    if "pong" in str(data).lower() or data.get("type") == "pong":
        return True
    return False


def is_error_message(data: Dict[str, Any]) -> tuple[bool, Optional[str], bool]:
    """
    Check if message is an error message.
    
    Stateless function - all state passed as parameters.
    
    Args:
        data: Parsed JSON data dictionary
        
    Returns:
        Tuple of (is_error, error_message, is_rate_limit)
    """
    if "error" in data:
        error_msg = data.get("error", "Unknown error")
        is_rate_limit = "429" in str(error_msg) or "Too Many Requests" in str(error_msg)
        return True, error_msg, is_rate_limit
    return False, None, False


def process_xml_message(message: str) -> None:
    """
    Process XML/HTML message from WebSocket.
    
    Stateless function - all state passed as parameters.
    
    Args:
        message: XML/HTML message string
    """
    try:
        logger.debug("Processing XML/HTML message from Benzinga WebSocket")
        logger.debug(f"XML message received: {len(message)} characters")
        
        # Check if this looks like news content
        if any(keyword in message.lower() for keyword in ['news', 'press', 'release', 'earnings', 'financial']):
            logger.debug("XML message appears to contain news content")
            # TODO: Parse XML to extract structured news data if needed
        else:
            logger.debug("XML message appears to be financial data (not news)")
    
    except Exception as e:
        logger.error("Error processing XML message", error=str(e))


def create_infrastructure_article_data(data: Dict[str, Any]) -> Optional[InfrastructureArticleData]:
    """
    Create InfrastructureArticleData from raw article data.
    
    Stateless function - all state passed as parameters.
    
    Args:
        data: Raw article data dictionary from WebSocket
        
    Returns:
        InfrastructureArticleData instance, or None if invalid
    """
    try:
        # Extract tickers from securities if present
        tickers = []
        if data.get("securities"):
            tickers = [stock.get("symbol", "") for stock in data.get("securities", []) if stock.get("symbol")]
        elif data.get("tickers"):
            tickers = data.get("tickers", [])
        elif data.get("symbols"):
            tickers = data.get("symbols", [])
        
        # Create typed infrastructure model
        return InfrastructureArticleData(
            benzinga_id=int(data.get("id", 0)) if data.get("id") else None,
            source_id=str(data.get("id", "")) if data.get("id") else None,
            title=data.get("title", "") or data.get("headline", ""),
            headline=data.get("headline"),
            content=data.get("content"),
            body=data.get("body"),
            teaser=data.get("teaser"),
            summary=data.get("summary"),
            author=data.get("author") or (data.get("authors", ["Benzinga"])[0] if data.get("authors") else "Benzinga"),
            published=data.get("published"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            last_updated=data.get("last_updated"),
            url=data.get("url"),
            tickers=tickers,
            symbols=data.get("symbols", []),
            securities=data.get("securities", []),
            tags=data.get("tags", []),
            categories=data.get("categories", []),
            channels=data.get("channels", []),
            images=data.get("images", []),
            raw_data=data
        )
    except Exception as e:
        logger.error("Failed to create InfrastructureArticleData", error=str(e), data=data)
        return None

