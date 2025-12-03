"""
Pure functions for formatting notification messages.

Service layer - pure functions with typed inputs and outputs.
"""
from typing import Optional, Dict, Any, Union

from ...utils.logging_config import get_logger
from ...models.benzinga_models import BenzingaArticle
from ...models.base_models import StandardizedArticle
from ...models.classification_models import ClassificationResult, NewsClassification

logger = get_logger(__name__)


def format_message_data(
    article: Union[BenzingaArticle, StandardizedArticle],
    classification: Optional[ClassificationResult] = None,
) -> Dict[str, Any]:
    """
    Format article and classification into message data structure.
    
    Args:
        article: The article to format
        classification: The classification result
        
    Returns:
        Message data dictionary (empty if invalid)
    """
    # Get classification emoji and label
    # Only IMMINENT articles should reach here
    if classification:
        if classification.classification == NewsClassification.IMMINENT:
            emoji = "🚨"
            label = "IMMINENT"
            confidence = classification.confidence
        else:
            # IGNORE classification - should never reach here, but log and allow if it does
            logger.warning("Non-IMMINENT classification sent to Telegram", 
                         classification=classification.classification.value)
            return {}  # Return empty dict to prevent sending IGNORE articles
    else:
        # No classification provided - should not happen, but log it
        logger.error("Article sent to Telegram without classification - this is a bug!")
        return {}  # Return empty dict to prevent sending
    
    # Extract tickers
    if isinstance(article, BenzingaArticle):
        tickers = article.tickers
        title = article.title
        url = article.url or "No URL available"
        source = "Benzinga (REST)"
    else:  # StandardizedArticle
        tickers = article.tickers
        title = article.title
        url = article.url or "No URL available"
        # Format source nicely: "benzinga_websocket" -> "Benzinga WebSocket"
        source_map = {
            "benzinga": "Benzinga (REST)",
            "benzinga_websocket": "Benzinga WebSocket"
        }
        source = source_map.get(article.source.value, article.source.value.replace('_', ' ').title())
    
    # Format tickers with "Company Symbol:" prefix
    ticker_display = f"Company Symbol: '{', '.join(tickers)}'" if tickers else "Company Symbol: 'N/A'"
    
    # Format publication timestamp (already in UTC from API)
    published_gmt = article.published.strftime("%Y-%m-%d %H:%M:%S UTC")
    
    fundamental_data = None
    
    # Build message data
    message_data = {
        "emoji": emoji,
        "classification": label,
        "confidence": confidence,
        "tickers": ticker_display,
        "headline": title,
        "url": url,
        "source": source,
        "published_gmt": published_gmt,
        "fundamental_data": fundamental_data
    }
    
    return message_data


def format_telegram_message(message_data: Dict[str, Any]) -> str:
    """
    Format message data into Telegram message string.
    
    Args:
        message_data: The message data dictionary
        
    Returns:
        Formatted message string
    """
    if not message_data:
        return ""
        
    header = f"{message_data['emoji']} {message_data['classification']} | {message_data['confidence']} CONFIDENCE"
    
    message_parts = [
        header,
        message_data["tickers"],
        message_data["headline"],
        f"🔗 {message_data['url']}",
        f"📡 Source: {message_data['source']}",
        f"🕐 Published: {message_data.get('published_gmt', 'Unknown')} GMT",
    ]
    
    # Add fundamental data if available
    fundamental_data = message_data.get('fundamental_data')
    if fundamental_data:
        message_parts.extend([
            "",
            "📊 FUNDAMENTAL DATA:",
            f"💰 Price: {fundamental_data['price_volume']['current_price']} ({fundamental_data['price_volume']['price_change_10min']})",
            f"💵 Earnings: {fundamental_data['earnings']['current_earnings']} ({fundamental_data['earnings']['earnings_growth']})",
            f"📈 Revenue: {fundamental_data['revenue']['current_revenue']} ({fundamental_data['revenue']['revenue_growth']})",
            f"📊 Margins: Gross {fundamental_data['margins']['gross_margin']}, Net {fundamental_data['margins']['net_margin']}",
            f"📊 Volume: {fundamental_data['price_volume']['current_volume']} ({fundamental_data['price_volume']['volume_change_10min']})"
        ])
    
    return "\n".join(message_parts)


def format_trading_options(tickers: list[str]) -> str:
    """
    Format trading options for IMMINENT news.
    
    Args:
        tickers: List of ticker symbols
        
    Returns:
        Formatted trading options string
    """
    if not tickers:
        return ""
    
    ticker_list = ", ".join(tickers) if len(tickers) > 1 else tickers[0]
    
    trading_options = (
        f"\n\n🎯 TRADING OPTIONS:\n"
        f"📊 Tickers: {ticker_list}\n"
        f"💰 Amount: $100 per trade\n"
        f"⏰ Reply within 30 minutes\n\n"
        f"Reply with:\n"
        f"• 'trade' - Trade default ticker\n"
        f"• 'trade {tickers[0]}' - Trade specific ticker\n"
        f"• 'ignore' - Ignore this news\n"
        f"• No reply = ignore"
    )
    
    return trading_options


def format_source_display(source_value: str) -> str:
    """
    Format source value for display.
    
    Args:
        source_value: Source value (e.g., "benzinga_websocket")
        
    Returns:
        Formatted source string (e.g., "Benzinga WebSocket")
    """
    source_map = {
        "benzinga": "Benzinga (REST)",
        "benzinga_websocket": "Benzinga WebSocket"
    }
    return source_map.get(source_value, source_value.replace('_', ' ').title())


def format_ticker_display(tickers: list[str]) -> str:
    """
    Format tickers for display with prefix.
    
    Args:
        tickers: List of ticker symbols
        
    Returns:
        Formatted ticker string (e.g., "Company Symbol: 'AAPL'")
    """
    if tickers:
        return f"Company Symbol: '{', '.join(tickers)}'"
    return "Company Symbol: 'N/A'"

