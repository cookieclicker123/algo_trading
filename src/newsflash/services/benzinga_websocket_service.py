"""
Benzinga WebSocket service for real-time news streaming.
Direct connection to Benzinga's WebSocket API for faster news delivery.

Updated to address Benzinga support feedback:
1. Only one active connection at a time
2. Rate limit of 1 request per second
"""
import asyncio
import json
import websocket
import threading
import time
import queue
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
        self.websocket_url = f"wss://api.benzinga.com/api/v1/news/stream"
        self.websocket = None
        self.is_running = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 1  # Start with 1 second
        self.last_request_time = 0  # For rate limiting
        self.min_request_interval = 3.0  # 3 seconds between requests
        
        # Article processing queue
        self.article_queue = queue.Queue()
        
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
    
    def start(self):
        """Start the WebSocket connection and message processing."""
        logger.info("Starting Benzinga WebSocket service")
        self.is_running = True
        
        # Clean up any existing connections first
        self._cleanup_connections()
        
        # Start connection in a separate thread
        self.websocket_thread = threading.Thread(target=self._run_websocket_loop)
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
    
    def _cleanup_connections(self):
        """Force close any existing WebSocket connections."""
        logger.info("Cleaning up any existing WebSocket connections...")
        
        if self.websocket:
            try:
                self.websocket.close()
                logger.info("Existing WebSocket connection closed")
            except Exception as e:
                logger.error("Error closing existing WebSocket", error=str(e))
        
        self.websocket = None
        self.stats["is_connected"] = False
        time.sleep(2)  # Give more time for cleanup
    
    def _run_websocket_loop(self):
        """Run the WebSocket connection loop in a separate thread."""
        while self.is_running and self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                self._connect_and_process()
            except Exception as e:
                logger.error("WebSocket connection failed", error=str(e))
                self.stats["last_error"] = str(e)
                
                if self.is_running:
                    self.reconnect_attempts += 1
                    delay = min(self.reconnect_delay * (2 ** self.reconnect_attempts), 60)
                    logger.info(f"Reconnecting in {delay} seconds (attempt {self.reconnect_attempts})")
                    time.sleep(delay)
                else:
                    break
        
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached, stopping WebSocket service")
    
    def _connect_and_process(self):
        """Connect to WebSocket and process messages."""
        logger.info("Connecting to Benzinga WebSocket", url=self.websocket_url)
        self.stats["connection_attempts"] += 1
        
        # Rate limiting: ensure we don't exceed 1 request per second
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        if time_since_last_request < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last_request
            logger.info(f"Rate limiting: sleeping for {sleep_time:.2f} seconds")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
        
        # Create WebSocket connection with headers
        headers = {
            'Authorization': self.token,
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Origin': 'https://api.benzinga.com',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
        def on_message(ws, message):
            """Handle incoming WebSocket messages."""
            try:
                self.stats["messages_received"] += 1
                self.stats["last_message_time"] = datetime.now()
                
                logger.info(f"Received WebSocket message ({len(message)} chars): {message[:200]}...")
                
                # Process the message
                self._process_message(message)
                
            except Exception as e:
                logger.error("Error processing WebSocket message", error=str(e))
                self.stats["last_error"] = str(e)
        
        def on_error(ws, error):
            """Handle WebSocket errors."""
            error_msg = str(error)
            logger.error("WebSocket error", error=error_msg)
            self.stats["last_error"] = error_msg
            
            # Handle 429 rate limit errors with exponential backoff
            if "429" in error_msg or "Too Many Requests" in error_msg:
                logger.warning("Rate limit hit (429), will back off before reconnecting")
                # Increase reconnection delay exponentially
                self.reconnect_delay = min(self.reconnect_delay * 2, 60)
                logger.info(f"Rate limit backoff: {self.reconnect_delay} seconds")
        
        def on_close(ws, close_status_code, close_msg):
            """Handle WebSocket close."""
            logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
            self.stats["is_connected"] = False
        
        def on_open(ws):
            """Handle WebSocket open."""
            logger.info("Connected to Benzinga WebSocket")
            self.stats["is_connected"] = True
            self.reconnect_attempts = 0  # Reset on successful connection
            self.reconnect_delay = 1  # Reset delay
        
        # Create WebSocket app
        self.websocket = websocket.WebSocketApp(
            self.websocket_url,
            header=headers,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        # Run the WebSocket connection
        self.websocket.run_forever()
    
    def _process_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            self.stats["messages_received"] += 1
            self.stats["last_message_time"] = datetime.now()
            
            logger.info(f"Received WebSocket message ({len(message)} chars): {message[:200]}...")
            
            # Try to parse as JSON first
            try:
                data = json.loads(message)
                logger.info("Message is JSON format")
                
                # Handle JSON message types
                if isinstance(data, dict):
                    if data.get("kind") == "News/v1" and "data" in data:
                        # Handle news articles from Benzinga WebSocket
                        news_data = data["data"]
                        if news_data.get("action") == "Created" and "content" in news_data:
                            self._process_news_articles([news_data["content"]])
                    elif "news" in data:
                        # Handle news articles (legacy format)
                        self._process_news_articles(data["news"])
                    elif "heartbeat" in data:
                        # Handle heartbeat/ping messages
                        logger.debug("Received heartbeat from Benzinga")
                    elif "error" in data:
                        # Handle error messages
                        logger.error("Benzinga WebSocket error", error=data["error"])
                        self.stats["last_error"] = data["error"]
                    else:
                        # Unknown JSON message format
                        logger.warning("Unknown JSON WebSocket message format", data=data)
                else:
                    logger.warning("Unexpected JSON WebSocket message type", message_type=type(data).__name__)
                    
            except json.JSONDecodeError:
                # Not JSON - check if it's XML/HTML
                if message.strip().startswith('<') or 'xml' in message.lower():
                    logger.info("Message is XML/HTML format - processing as news data")
                    self._process_xml_message(message)
                else:
                    logger.warning("Unknown message format", message_preview=message[:100])
                
        except Exception as e:
            logger.error("Unexpected error processing WebSocket message", error=str(e))
    
    def _process_xml_message(self, message: str):
        """Process XML/HTML message from WebSocket."""
        try:
            logger.info("Processing XML/HTML message from Benzinga WebSocket")
            
            # For now, just log that we received XML data
            # In the future, we can parse XML to extract news articles
            logger.info(f"XML message received: {len(message)} characters")
            
            # Check if this looks like news content
            if any(keyword in message.lower() for keyword in ['news', 'press', 'release', 'earnings', 'financial']):
                logger.info("XML message appears to contain news content")
                # TODO: Parse XML to extract structured news data
            else:
                logger.info("XML message appears to be financial data (not news)")
                
        except Exception as e:
            logger.error("Error processing XML message", error=str(e))
    
    def _process_news_articles(self, articles_data: List[Dict[str, Any]]):
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
                        # Queue the article for processing by the main service
                        self.article_queue.put(standardized_article)
                        logger.info("WebSocket article queued for processing", article_id=article.benzinga_id)
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
    
    def stop(self):
        """Stop the WebSocket service."""
        logger.info("Stopping Benzinga WebSocket service")
        self.is_running = False
        
        # Clean up connections
        self._cleanup_connections()
        
        # Wait for thread to finish
        if hasattr(self, 'websocket_thread') and self.websocket_thread.is_alive():
            logger.info("Waiting for WebSocket thread to finish...")
            self.websocket_thread.join(timeout=10)  # Increased timeout
        
        # Final cleanup
        self._cleanup_connections()
        self.stats["is_connected"] = False
        logger.info("Benzinga WebSocket service stopped completely")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get WebSocket service statistics."""
        return self.stats.copy()
    
    def get_queued_articles(self) -> List[StandardizedArticle]:
        """Get all queued articles for processing."""
        articles = []
        while not self.article_queue.empty():
            try:
                article = self.article_queue.get_nowait()
                articles.append(article)
            except queue.Empty:
                break
        return articles
    
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
