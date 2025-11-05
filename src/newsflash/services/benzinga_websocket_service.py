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
        self.last_request_time = 0  # For rate limiting
        self.min_request_interval = 3.0  # 3 seconds between requests (respects Benzinga's 1 req/sec limit)
        
        # Article processing queue
        self.article_queue = queue.Queue()
        
        # Statistics
        self.stats = {
            "messages_received": 0,
            "articles_processed": 0,
            "connection_attempts": 0,
            "last_message_time": None,
            "last_error": None,
            "is_connected": False,
            # Ping/pong tracking
            "last_ping_sent": None,
            "last_pong_received": None,
            "ping_sent_count": 0,
            "pong_received_count": 0,
            "missed_pongs": 0,
            "connection_verified_at": None,
            "last_connection_check": None,
        }
        
        # Ping/pong configuration
        self.ping_interval = 30.0  # Send ping every 30 seconds
        self.ping_timeout = 30.0  # Expect pong within 30 seconds
        self.connection_check_interval = 30.0  # Verify connection every 30 seconds
        
        # Thread locks for thread safety
        self._lock = threading.Lock()
        self._ping_thread = None
        self._monitor_thread = None
        
        # Reconnection tracking
        self._reconnect_allowed = True
        self._reconnect_delay = 5.0  # Wait 5 seconds before reconnecting
        
        logger.info("BenzingaWebSocketService initialized", token_prefix=token[:10] + "...")
    
    def start(self):
        """Start the WebSocket connection and message processing."""
        logger.info("Starting Benzinga WebSocket service")
        self.is_running = True
        self._reconnect_allowed = True
        
        # Clean up any existing connections first
        self._cleanup_connections()
        
        # Start connection in a separate thread
        self.websocket_thread = threading.Thread(target=self._run_websocket_loop)
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
        
        # Start ping thread (will start after connection is confirmed)
        self._ping_thread = threading.Thread(target=self._ping_loop)
        self._ping_thread.daemon = True
        
        # Start connection monitor thread
        self._monitor_thread = threading.Thread(target=self._connection_monitor_loop)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()
        
        logger.info("WebSocket service threads started (connection, ping, monitor)")
    
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
        # Connect ONCE and stay connected (like the test script)
        # No auto-retry to prevent violating "one connection at a time" rule
        try:
            logger.info("Attempting single WebSocket connection (no auto-retry)")
            self._connect_and_process()
        except Exception as e:
            logger.error("WebSocket connection failed", error=str(e))
            self.stats["last_error"] = str(e)
            logger.warning("WebSocket will NOT auto-reconnect to prevent 429 rate limits")
    
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
                with self._lock:
                    self.stats["messages_received"] += 1
                    self.stats["last_message_time"] = datetime.now()
                
                logger.info(f"Received WebSocket message ({len(message)} chars): {message[:200]}...")
                
                # Process the message
                self._process_message(message)
                
            except Exception as e:
                logger.error("Error processing WebSocket message", error=str(e))
                with self._lock:
                    self.stats["last_error"] = str(e)
        
        def on_error(ws, error):
            """Handle WebSocket errors."""
            error_msg = str(error)
            logger.error("WebSocket error", error=error_msg)
            with self._lock:
                self.stats["last_error"] = error_msg
            
            # Log 429 errors but don't retry (to respect "one connection at a time")
            if "429" in error_msg or "Too Many Requests" in error_msg:
                logger.error("Rate limit hit (429) - connection will close and NOT retry")
                logger.info("Check for leftover connections or previous test runs")
                self._reconnect_allowed = False  # Disable reconnection for rate limit errors
        
        def on_close(ws, close_status_code, close_msg):
            """Handle WebSocket close."""
            logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
            with self._lock:
                self.stats["is_connected"] = False
                
            # Auto-reconnect if allowed and still running
            if self.is_running and self._reconnect_allowed:
                logger.info("Connection closed, will attempt reconnect in monitor loop")
        
        def on_open(ws):
            """Handle WebSocket open."""
            logger.info("Connected to Benzinga WebSocket - connection established and verified")
            with self._lock:
                self.stats["is_connected"] = True
                self.stats["connection_verified_at"] = datetime.now()
                self.stats["last_connection_check"] = datetime.now()
                self.stats["last_error"] = None  # Clear previous errors
                self.stats["missed_pongs"] = 0  # Reset missed pongs
                
            # Start ping thread after connection is confirmed
            if not self._ping_thread.is_alive():
                self._ping_thread.start()
                logger.info("Ping/pong thread started - will send pings every 30 seconds")
        
        def on_pong(ws, data):
            """Handle WebSocket pong frame."""
            logger.info("Received WebSocket pong frame from Benzinga")
            with self._lock:
                self.stats["last_pong_received"] = datetime.now()
                self.stats["pong_received_count"] += 1
                if self.stats["missed_pongs"] > 0:
                    self.stats["missed_pongs"] = 0  # Reset on successful pong
            logger.info("Pong received, connection is alive", 
                       pong_count=self.stats["pong_received_count"],
                       ping_count=self.stats["ping_sent_count"])
        
        def on_ping(ws, data):
            """Handle WebSocket ping frame from server (shouldn't happen but handle it)."""
            logger.debug("Received ping frame from server")
        
        # Create WebSocket app
        self.websocket = websocket.WebSocketApp(
            self.websocket_url,
            header=headers,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
            on_ping=on_ping,
            on_pong=on_pong
        )
        
        # Run the WebSocket connection with automatic ping frames
        # ping_interval: Send ping frame every 30 seconds
        # ping_timeout: Wait 10 seconds for pong response before timing out
        self.websocket.run_forever(
            ping_interval=int(self.ping_interval),
            ping_timeout=10
        )
    
    def _process_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            # Update stats (already updated in on_message, but keep for safety)
            with self._lock:
                if not self.stats.get("last_message_time"):
                    self.stats["last_message_time"] = datetime.now()
            
            logger.info(f"Processing WebSocket message ({len(message)} chars): {message[:200]}...")
            
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
                        # Handle heartbeat messages from server (if they send JSON heartbeats)
                        logger.debug("Received heartbeat message from Benzinga")
                        # Note: WebSocket pong frames are handled in on_pong callback, not here
                    elif "pong" in str(data).lower() or data.get("type") == "pong":
                        # Handle JSON pong message (if Benzinga sends JSON pongs)
                        with self._lock:
                            self.stats["last_pong_received"] = datetime.now()
                            self.stats["pong_received_count"] += 1
                            if self.stats["missed_pongs"] > 0:
                                self.stats["missed_pongs"] = 0
                        logger.info("Received JSON pong message from Benzinga", 
                                   pong_count=self.stats["pong_received_count"])
                    elif "ping" in str(data).lower() and data.get("type") != "ping":
                        # Server-initiated ping (unlikely, but handle it)
                        logger.debug("Server sent JSON ping message")
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
    
    def _ping_loop(self):
        """Send ping messages every 30 seconds to keep connection alive and detect zombie connections."""
        logger.info("Ping loop started - waiting for connection confirmation")
        
        # Wait for connection to be established
        while self.is_running and not self.stats.get("is_connected"):
            time.sleep(1)
        
        if not self.is_running:
            return
        
        logger.info("Connection confirmed, starting ping/pong cycle")
        
        while self.is_running:
            try:
                if not self.stats.get("is_connected"):
                    logger.debug("Connection not active, pausing ping loop")
                    time.sleep(5)
                    continue
                
                # Send ping
                self._send_ping()
                
                # Wait for ping interval, then check if pong was received
                time.sleep(self.ping_interval)
                
                # Check if we got a pong response
                with self._lock:
                    last_ping = self.stats.get("last_ping_sent")
                    last_pong = self.stats.get("last_pong_received")
                    
                    if last_ping:
                        # Check if pong was received after this ping
                        if not last_pong or last_pong < last_ping:
                            # No pong received for this ping
                            self.stats["missed_pongs"] += 1
                            logger.warning("Missed pong response", 
                                         ping_sent=last_ping.isoformat(),
                                         last_pong=last_pong.isoformat() if last_pong else None,
                                         missed_count=self.stats["missed_pongs"])
                        else:
                            # Pong received, reset missed count
                            if self.stats["missed_pongs"] > 0:
                                self.stats["missed_pongs"] = 0
                                logger.info("Pong received, resetting missed pong count")
                
            except Exception as e:
                logger.error("Error in ping loop", error=str(e))
                time.sleep(5)  # Wait a bit before retrying
    
    def _send_ping(self):
        """
        Send a ping frame to the WebSocket server.
        
        Note: The websocket-client library's run_forever(ping_interval=...) 
        automatically sends ping frames. This method tracks ping timing for our monitoring.
        """
        try:
            if not self.websocket or not self.stats.get("is_connected"):
                logger.warning("Cannot track ping - websocket not connected")
                return
            
            # Track that a ping frame should be sent (run_forever handles actual sending)
            with self._lock:
                self.stats["last_ping_sent"] = datetime.now()
                self.stats["ping_sent_count"] += 1
            
            # Log ping tracking (actual ping is sent by run_forever with ping_interval)
            logger.info("Tracking ping cycle to Benzinga WebSocket", 
                       ping_count=self.stats["ping_sent_count"],
                       last_pong=self.stats["last_pong_received"].isoformat() if self.stats["last_pong_received"] else None,
                       note="WebSocket library sends ping frames automatically")
                    
        except Exception as e:
            logger.error("Error tracking ping", error=str(e))
    
    def _connection_monitor_loop(self):
        """Monitor connection health every 30 seconds and handle reconnection."""
        logger.info("Connection monitor loop started")
        
        while self.is_running:
            try:
                time.sleep(self.connection_check_interval)
                
                if not self.is_running:
                    break
                
                # Perform connection health check
                health_check = self._check_connection_health()
                
                with self._lock:
                    self.stats["last_connection_check"] = datetime.now()
                
                # Log connection verification
                if health_check["status"] == "healthy":
                    logger.info("Connection verification: HEALTHY", 
                              details=health_check.get("details", {}))
                elif health_check["status"] == "zombie":
                    logger.warning("Connection verification: ZOMBIE DETECTED", 
                                 details=health_check.get("details", {}))
                    self._handle_zombie_connection()
                elif health_check["status"] == "disconnected":
                    logger.warning("Connection verification: DISCONNECTED", 
                                 details=health_check.get("details", {}))
                    self._handle_disconnection()
                else:
                    logger.warning("Connection verification: UNHEALTHY", 
                                 details=health_check.get("details", {}))
                    self._handle_unhealthy_connection()
                    
            except Exception as e:
                logger.error("Error in connection monitor loop", error=str(e))
                time.sleep(5)
    
    def _check_connection_health(self) -> Dict[str, Any]:
        """
        Check connection health and detect zombie connections.
        
        Returns:
            Dict with status: "healthy", "disconnected", "zombie", or "unhealthy"
        """
        with self._lock:
            is_connected = self.stats.get("is_connected", False)
            last_message_time = self.stats.get("last_message_time")
            last_ping_sent = self.stats.get("last_ping_sent")
            last_pong_received = self.stats.get("last_pong_received")
            missed_pongs = self.stats.get("missed_pongs", 0)
            
        # Check if disconnected
        if not is_connected:
            return {
                "status": "disconnected",
                "details": {
                    "reason": "Not connected",
                    "is_connected": False
                }
            }
        
        now = datetime.now()
        details = {}
        
        # Check if connection is zombie (connected but no activity)
        # Consider zombie if:
        # 1. No messages for > 30 seconds AND
        # 2. (No pong received after ping OR no ping sent recently)
        is_zombie = False
        zombie_reasons = []
        
        if last_message_time:
            time_since_message = (now - last_message_time).total_seconds()
            if time_since_message > 60:  # No messages for 60+ seconds
                zombie_reasons.append(f"No messages for {time_since_message:.1f}s")
        
        if last_ping_sent:
            time_since_ping = (now - last_ping_sent).total_seconds()
            if time_since_ping > self.ping_timeout:
                if not last_pong_received or (now - last_pong_received).total_seconds() > self.ping_timeout:
                    zombie_reasons.append(f"No pong received for {time_since_ping:.1f}s after ping")
                    is_zombie = True
        
        # Check missed pongs
        if missed_pongs >= 2:  # 2 missed pongs = 60 seconds of silence
            zombie_reasons.append(f"{missed_pongs} consecutive missed pongs")
            is_zombie = True
        
        if is_zombie:
            return {
                "status": "zombie",
                "details": {
                    "reason": "Connected but no activity (zombie)",
                    "reasons": zombie_reasons,
                    "last_message": last_message_time.isoformat() if last_message_time else None,
                    "last_ping": last_ping_sent.isoformat() if last_ping_sent else None,
                    "last_pong": last_pong_received.isoformat() if last_pong_received else None,
                    "missed_pongs": missed_pongs
                }
            }
        
        # Check if unhealthy (some issues but not zombie)
        if missed_pongs > 0:
            return {
                "status": "unhealthy",
                "details": {
                    "reason": "Some connection issues detected",
                    "missed_pongs": missed_pongs,
                    "last_pong": last_pong_received.isoformat() if last_pong_received else None
                }
            }
        
        # Connection is healthy
        return {
            "status": "healthy",
            "details": {
                "last_message": last_message_time.isoformat() if last_message_time else None,
                "last_ping": last_ping_sent.isoformat() if last_ping_sent else None,
                "last_pong": last_pong_received.isoformat() if last_pong_received else None,
                "ping_count": self.stats["ping_sent_count"],
                "pong_count": self.stats["pong_received_count"]
            }
        }
    
    def _handle_zombie_connection(self):
        """Handle zombie connection by reconnecting."""
        logger.warning("ZOMBIE CONNECTION DETECTED - reconnecting WebSocket")
        
        with self._lock:
            self.stats["missed_pongs"] += 1
        
        # Force reconnect
        self._force_reconnect(reason="zombie_connection")
    
    def _handle_disconnection(self):
        """Handle disconnection by attempting reconnect."""
        logger.warning("WebSocket disconnected - attempting reconnect")
        self._force_reconnect(reason="disconnected")
    
    def _handle_unhealthy_connection(self):
        """Handle unhealthy connection - monitor and reconnect if needed."""
        with self._lock:
            missed_pongs = self.stats.get("missed_pongs", 0)
        
        if missed_pongs >= 2:
            logger.warning("Connection unhealthy with multiple missed pongs - reconnecting")
            self._force_reconnect(reason="unhealthy")
    
    def _force_reconnect(self, reason: str = "unknown"):
        """Force reconnect the WebSocket connection."""
        if not self._reconnect_allowed:
            logger.warning("Reconnection not allowed, skipping reconnect")
            return
        
        logger.info(f"Initiating forced reconnect: {reason}")
        
        try:
            # Stop current connection
            self._cleanup_connections()
            
            # Wait a bit before reconnecting
            time.sleep(self._reconnect_delay)
            
            # Only reconnect if still running
            if self.is_running:
                logger.info("Attempting to reconnect WebSocket...")
                # Reset connection state
                with self._lock:
                    self.stats["is_connected"] = False
                    self.stats["connection_attempts"] += 1
                    self.stats["missed_pongs"] = 0
                
                # Start new connection thread
                self.websocket_thread = threading.Thread(target=self._run_websocket_loop)
                self.websocket_thread.daemon = True
                self.websocket_thread.start()
                
                logger.info("Reconnection thread started", reconnect_reason=reason)
        except Exception as e:
            logger.error("Error during forced reconnect", error=str(e), reconnect_reason=reason)
            with self._lock:
                self.stats["last_error"] = f"Reconnect error: {str(e)}"
    
    def stop(self):
        """Stop the WebSocket service."""
        logger.info("Stopping Benzinga WebSocket service")
        self.is_running = False
        self._reconnect_allowed = False
        
        # Stop monitor and ping threads
        if self._monitor_thread and self._monitor_thread.is_alive():
            logger.info("Waiting for connection monitor thread to finish...")
            self._monitor_thread.join(timeout=5)
        
        if self._ping_thread and self._ping_thread.is_alive():
            logger.info("Waiting for ping thread to finish...")
            self._ping_thread.join(timeout=5)
        
        # Clean up connections
        self._cleanup_connections()
        
        # Wait for websocket thread to finish
        if hasattr(self, 'websocket_thread') and self.websocket_thread.is_alive():
            logger.info("Waiting for WebSocket thread to finish...")
            self.websocket_thread.join(timeout=10)
        
        # Final cleanup
        self._cleanup_connections()
        with self._lock:
            self.stats["is_connected"] = False
        logger.info("Benzinga WebSocket service stopped completely")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get WebSocket service statistics."""
        stats_copy = {}
        for key, value in self.stats.items():
            # Serialize datetime objects to ISO format strings
            if isinstance(value, datetime):
                stats_copy[key] = value.isoformat()
            else:
                stats_copy[key] = value
        return stats_copy
    
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
            self.is_running
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
