"""
Benzinga WebSocket service for real-time news streaming.
Direct connection to Benzinga's WebSocket API for faster news delivery.
"""
import asyncio
import json
import websockets
from typing import Dict, Any, Optional, List
from datetime import datetime
import structlog

from ..utils.logging_config import get_logger
from ..models.benzinga_models import BenzingaArticle
from ..models.base_models import NewsSource, StandardizedArticle

logger = get_logger(__name__)


class BenzingaWebSocketService:
    """
    WebSocket service for Benzinga direct news feed.
    
    Features:
    - Real-time WebSocket connection to Benzinga
    - Automatic reconnection with exponential backoff
    - Message parsing and article processing
    - Error handling and logging
    """
    
    def __init__(self, article_processor, token: str):
        """
        Initialize Benzinga WebSocket service.
        
        Args:
            article_processor: Article processor for handling new articles
            token: Benzinga API token
        """
        self.article_processor = article_processor
        self.token = token
        self.websocket_url = f"wss://api.benzinga.com/api/v1/news/stream?token={token}"
        self.websocket = None
        self.is_running = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 1  # Start with 1 second
        
        # Statistics
        self.stats = {
            "messages_received": 0,
            "articles_processed": 0,
            "connection_attempts": 0,
            "last_message_time": None,
            "last_error": None,
            "is_connected": False
        }
        
        logger.info("BenzingaWebSocketService initialized", token_prefix=token[:10] + "...")
    
    async def start(self):
        """Start the WebSocket connection and message processing."""
        logger.info("Starting Benzinga WebSocket service")
        self.is_running = True
        
        while self.is_running and self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                await self._connect_and_process()
            except Exception as e:
                logger.error("WebSocket connection failed", error=str(e))
                self.stats["last_error"] = str(e)
                
                if self.is_running:
                    self.reconnect_attempts += 1
                    delay = min(self.reconnect_delay * (2 ** self.reconnect_attempts), 60)
                    logger.info(f"Reconnecting in {delay} seconds (attempt {self.reconnect_attempts})")
                    await asyncio.sleep(delay)
                else:
                    break
        
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached, stopping WebSocket service")
    
    async def _connect_and_process(self):
        """Connect to WebSocket and process messages."""
        logger.info("Connecting to Benzinga WebSocket", url=self.websocket_url)
        self.stats["connection_attempts"] += 1
        
        async with websockets.connect(
            self.websocket_url,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=10
        ) as websocket:
            self.websocket = websocket
            self.stats["is_connected"] = True
            self.reconnect_attempts = 0  # Reset on successful connection
            self.reconnect_delay = 1  # Reset delay
            
            logger.info("Connected to Benzinga WebSocket")
            
            # Process messages
            async for message in websocket:
                if not self.is_running:
                    break
                    
                try:
                    await self._process_message(message)
                except Exception as e:
                    logger.error("Error processing WebSocket message", error=str(e))
                    self.stats["last_error"] = str(e)
    
    async def _process_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)
            self.stats["messages_received"] += 1
            self.stats["last_message_time"] = datetime.now()
            
            logger.debug("Received WebSocket message", message_type=type(data).__name__)
            
            # Handle different message types
            if isinstance(data, dict):
                if data.get("kind") == "News/v1" and "data" in data:
                    # Handle news articles from Benzinga WebSocket
                    news_data = data["data"]
                    if news_data.get("action") == "Created" and "content" in news_data:
                        await self._process_news_articles([news_data["content"]])
                elif "news" in data:
                    # Handle news articles (legacy format)
                    await self._process_news_articles(data["news"])
                elif "heartbeat" in data:
                    # Handle heartbeat/ping messages
                    logger.debug("Received heartbeat from Benzinga")
                elif "error" in data:
                    # Handle error messages
                    logger.error("Benzinga WebSocket error", error=data["error"])
                    self.stats["last_error"] = data["error"]
                else:
                    # Unknown message format
                    logger.warning("Unknown WebSocket message format", data=data)
            else:
                logger.warning("Unexpected WebSocket message type", message_type=type(data).__name__)
                
        except json.JSONDecodeError as e:
            logger.error("Failed to parse WebSocket message as JSON", error=str(e), message=message[:200])
        except Exception as e:
            logger.error("Unexpected error processing WebSocket message", error=str(e))
    
    async def _process_news_articles(self, articles_data: List[Dict[str, Any]]):
        """Process news articles from WebSocket."""
        
        for article_data in articles_data:
            try:
                # Convert to BenzingaArticle model
                article = self._convert_to_benzinga_article(article_data)
                
                if article:
                    # Convert BenzingaArticle to StandardizedArticle for processing
                    standardized_article = self._convert_to_standardized_article(article)
                    if standardized_article:
                        # Process through article processor
                        await self.article_processor.process_article(standardized_article)
                        self.stats["articles_processed"] += 1
                        
                        logger.info("Processed WebSocket article", 
                                   article_id=article.benzinga_id,
                                   title=article.title[:50] + "..." if len(article.title) > 50 else article.title)
                
            except Exception as e:
                logger.error("Error processing individual article", error=str(e), article_data=article_data)
    
    def _convert_to_benzinga_article(self, data: Dict[str, Any]) -> Optional[BenzingaArticle]:
        """Convert WebSocket data to BenzingaArticle model."""
        try:
            # Map WebSocket fields to BenzingaArticle fields
            # WebSocket data structure: {"id": 123, "created_at": "2025-10-22T20:15:00.000Z", ...}
            article = BenzingaArticle(
                benzinga_id=int(data.get("id", 0)),
                title=data.get("title", ""),
                teaser=data.get("teaser", ""),
                body=data.get("body", ""),
                published=data.get("created_at", ""),
                last_updated=data.get("updated_at", ""),
                url=data.get("url", ""),
                channels=data.get("channels", []),
                tickers=[stock.get("symbol", "") for stock in data.get("securities", []) if stock.get("symbol")],
                tags=data.get("tags", []),
                author=data.get("authors", ["Benzinga"])[0] if data.get("authors") else "Benzinga",
                images=[]  # WebSocket doesn't seem to include images
            )
            
            return article
            
        except Exception as e:
            logger.error("Failed to convert WebSocket data to BenzingaArticle", error=str(e), data=data)
            return None
    
    def _convert_to_standardized_article(self, benzinga_article: BenzingaArticle) -> Optional[StandardizedArticle]:
        """Convert BenzingaArticle to StandardizedArticle for processing."""
        try:
            return StandardizedArticle(
                source=NewsSource.BENZINGA_WEBSOCKET,
                source_id=str(benzinga_article.benzinga_id),
                title=benzinga_article.title,
                content=benzinga_article.body or benzinga_article.teaser or "No content available.",
                summary=benzinga_article.teaser or "No summary available.",
                author=benzinga_article.author,
                published=benzinga_article.published,
                updated=benzinga_article.last_updated,
                url=benzinga_article.url,
                tickers=benzinga_article.tickers,
                tags=benzinga_article.tags + benzinga_article.channels,
                raw_data=benzinga_article.dict()  # Include raw data for validation
            )
        except Exception as e:
            logger.error("Failed to convert BenzingaArticle to StandardizedArticle", error=str(e), article_id=benzinga_article.benzinga_id)
            return None
    
    async def stop(self):
        """Stop the WebSocket service."""
        logger.info("Stopping Benzinga WebSocket service")
        self.is_running = False
        
        if self.websocket:
            try:
                await self.websocket.close()
                logger.info("WebSocket connection closed")
            except Exception as e:
                logger.error("Error closing WebSocket", error=str(e))
        
        self.stats["is_connected"] = False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get WebSocket service statistics."""
        return self.stats.copy()
    
    def is_healthy(self) -> bool:
        """Check if WebSocket service is healthy."""
        return (
            self.stats["is_connected"] and 
            self.stats["last_error"] is None and
            self.reconnect_attempts < self.max_reconnect_attempts
        )


# Factory function for dependency injection
_benzinga_websocket_service_instance: Optional[BenzingaWebSocketService] = None

def get_benzinga_websocket_service(article_processor, token: str) -> BenzingaWebSocketService:
    """Get Benzinga WebSocket service instance."""
    global _benzinga_websocket_service_instance
    if _benzinga_websocket_service_instance is None:
        _benzinga_websocket_service_instance = BenzingaWebSocketService(article_processor, token)
        logger.info("Created new Benzinga WebSocket service instance")
    return _benzinga_websocket_service_instance
