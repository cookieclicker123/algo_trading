"""
WebSocket microservice for Benzinga news feed.
Pure infrastructure - handles connection management and publishes events.
"""
import asyncio
import json
import websocket
import threading
import time
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone

from ...utils.logging_config import get_logger
from ...models.benzinga_models import BenzingaArticle
from .infrastructure_models import InfrastructureArticleData
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import InfrastructureEventType
from .events import (
    ArticleReceivedEvent, 
    WebSocketConnectedEvent, 
    WebSocketDisconnectedEvent
)
from .health_monitor import WebSocketHealthMonitor
from .message_handler import (
    parse_websocket_message,
    extract_articles_from_json,
    is_heartbeat_message,
    is_error_message,
    process_xml_message,
    create_infrastructure_article_data,
)
from ...utils.service_utils import serialize_stats

logger = get_logger(__name__)


class BenzingaWebSocketMicroservice:
    """
    WebSocket microservice for Benzinga news feed.
    
    Responsibilities:
    - Manage WebSocket connection to Benzinga
    - Handle reconnection logic
    - Parse incoming messages
    - Publish events to event bus
    
    Does NOT:
    - Process articles (publishes events instead)
    - Call services directly
    - Know about business logic
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        token: str,
        metrics_service,  # Required - injected via DI
    ):
        """
        Initialize WebSocket microservice.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            token: Benzinga API token
            metrics_service: Optional metrics service for statistics (injected via DI)
        """
        self.token = token
        self.websocket_url = f"wss://api.benzinga.com/api/v1/news/stream"
        self.websocket = None
        # Thread control flag (operational state needed by threads)
        # Lifecycle is tracked by LifecycleManager, this is for thread coordination
        self._threads_should_run = False
        self.last_request_time = 0
        self.min_request_interval = 3.0
        self.metrics_service = metrics_service  # ✅ Injected metrics service
        
        # Event bus for publishing events
        self.event_bus = event_bus
        
        # ✅ Reduced stats - only operational stats not tracked via events
        # Business stats (articles_received, messages_received, is_connected) come from MetricsService
        self._operational_stats = {
            "connection_attempts": 0,  # Not published as event yet
            "ping_sent_count": 0,  # Operational metric
            "pong_received_count": 0,  # Operational metric
            "missed_pongs": 0,  # Operational metric
            "last_ping_sent": None,  # Operational metric
            "last_pong_received": None,  # Operational metric
            "last_connection_check": None,  # Operational metric
            "connection_verified_at": None,  # Operational metric
        }
        
        # Configuration
        self.ping_interval = 30.0
        self.ping_timeout = 30.0
        self.connection_check_interval = 30.0
        
        # Thread management
        self._lock = threading.Lock()
        self._ping_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self.websocket_thread: Optional[threading.Thread] = None
        
        # Main event loop reference for thread-safe publishing
        self._main_event_loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Reconnection
        self._reconnect_allowed = True
        self._reconnect_delay = 5.0
        
        # Health monitor (infrastructure layer)
        self.health_monitor: Optional[WebSocketHealthMonitor] = None
        
        # Startup filtering - track when service started to skip old messages
        self._startup_time: Optional[datetime] = None
        from ...config import settings
        self._startup_skip_old_minutes = settings.WEBSOCKET_STARTUP_SKIP_OLD_MESSAGES_MINUTES
        
        logger.info("BenzingaWebSocketMicroservice initialized", token_prefix=token[:10] + "...")
    
    def _publish_event_threadsafe(self, coro) -> None:
        """Publish an async event from a thread, scheduling it on the main event loop."""
        if self._main_event_loop and self._main_event_loop.is_running():
            self._main_event_loop.call_soon_threadsafe(lambda: asyncio.create_task(coro))
        else:
            # Fallback: try to get current loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(coro))
                else:
                    logger.warning("Event loop not running, cannot publish event")
            except RuntimeError:
                logger.warning("No event loop available, cannot publish event")
    
    def start(self) -> None:
        """
        Start the WebSocket connection.
        
        Idempotent: Safe to call multiple times. Thread control flag prevents duplicate threads.
        """
        logger.info("Starting Benzinga WebSocket microservice")
        # Set thread control flag (operational state for threads)
        self._threads_should_run = True
        self._reconnect_allowed = True
        
        # Record startup time for filtering old messages
        self._startup_time = datetime.now()
        logger.info(
            f"WebSocket startup time recorded - will skip articles older than {self._startup_skip_old_minutes} minutes during startup"
        )
        
        # Store reference to main event loop for thread-safe publishing
        try:
            self._main_event_loop = asyncio.get_running_loop()
        except RuntimeError:
            # Try to get existing loop
            try:
                self._main_event_loop = asyncio.get_event_loop()
            except RuntimeError:
                logger.warning("No event loop available, event publishing may fail")
                self._main_event_loop = None
        
        self._cleanup_connections()
        
        # Store main event loop for thread-safe publishing
        try:
            self._main_event_loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                self._main_event_loop = asyncio.get_event_loop()
            except RuntimeError:
                logger.warning("No event loop available for WebSocket event publishing")
                self._main_event_loop = None
        
        # Start connection thread
        self.websocket_thread = threading.Thread(target=self._run_websocket_loop)
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
        
        # Start ping thread
        self._ping_thread = threading.Thread(target=self._ping_loop)
        self._ping_thread.daemon = True
        
        # Start monitor thread
        self._monitor_thread = threading.Thread(target=self._connection_monitor_loop)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()
        
        # Start health monitor
        self.health_monitor = WebSocketHealthMonitor(self.event_bus, self)
        self.health_monitor.start()
        
        logger.info("WebSocket microservice threads started")
    
    def stop(self) -> None:
        """
        Stop the WebSocket connection.
        
        Idempotent: Safe to call multiple times.
        """
        logger.info("Stopping Benzinga WebSocket microservice")
        # Signal threads to stop (operational state for threads)
        self._threads_should_run = False
        self._reconnect_allowed = False
        
        # Stop health monitor
        if self.health_monitor:
            self.health_monitor.stop()
            self.health_monitor = None
        
        # Stop threads
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        
        if self._ping_thread and self._ping_thread.is_alive():
            self._ping_thread.join(timeout=5)
        
        # Clean up connection
        self._cleanup_connections()
        
        # Wait for websocket thread
        if self.websocket_thread and self.websocket_thread.is_alive():
            self.websocket_thread.join(timeout=10)
        
        # ✅ No stats mutation - MetricsService tracks via WEBSOCKET_DISCONNECTED event
        
        logger.info("WebSocket microservice stopped")
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        with self._lock:
            websocket_stats = self.metrics_service.get_websocket_stats()
            return websocket_stats.get("is_connected", False)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get WebSocket service statistics."""
        # Merge MetricsService stats (from events) with operational stats
        websocket_stats = self.metrics_service.get_websocket_stats()
        return serialize_stats({
            **websocket_stats,
            **self._operational_stats,
        })
    
    def is_healthy(self) -> bool:
        """Check if WebSocket service is healthy."""
        with self._lock:
            websocket_stats = self.metrics_service.get_websocket_stats()
            is_connected = websocket_stats.get("is_connected", False)
            last_error = websocket_stats.get("last_error")
            return (
                is_connected and
                self._threads_should_run and
                (last_error is None or "429" not in str(last_error))
            )
    
    def _cleanup_connections(self) -> None:
        """Clean up existing connections."""
        logger.info("Cleaning up WebSocket connections...")
        
        if self.websocket:
            try:
                self.websocket.close()
            except Exception as e:
                logger.error("Error closing WebSocket", error=str(e))
        
        self.websocket = None
        # ✅ No stats mutation - MetricsService tracks via WEBSOCKET_DISCONNECTED event
        
        time.sleep(2)
    
    def _run_websocket_loop(self) -> None:
        """Run WebSocket connection loop."""
        try:
            logger.info("Attempting WebSocket connection")
            self._connect_and_process()
        except Exception as e:
            logger.error("WebSocket connection failed", error=str(e))
            # ✅ No stats mutation - MetricsService tracks via WEBSOCKET_ERROR event
            # Check if it's a rate limit error
            error_str = str(e)
            is_rate_limit = ("429" in error_str) or ("Too Many Requests" in error_str)
            if is_rate_limit:
                self._reconnect_allowed = False
                self._publish_event_threadsafe(self._publish_rate_limit())
            else:
                self._publish_event_threadsafe(self._publish_error(f"Connection loop error: {error_str}", is_rate_limit=False))
            
            logger.warning("WebSocket will NOT auto-reconnect to prevent 429 rate limits")
    
    def _connect_and_process(self) -> None:
        """Connect to WebSocket and process messages."""
        logger.info("Connecting to Benzinga WebSocket", url=self.websocket_url)
        
        with self._lock:
            self._operational_stats["connection_attempts"] += 1
        
        # Rate limiting
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            logger.info(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
        
        # Create connection
        headers = {
            'Authorization': self.token,
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Origin': 'https://api.benzinga.com',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
        def on_message(ws, message):
            """Handle incoming messages."""
            try:
                # ✅ No stats mutation - MetricsService subscribes to ARTICLE_RECEIVED event
                # (messages_received and last_message_time tracked via events)
                
                logger.info(f"Received WebSocket message ({len(message)} chars)")
                self._process_message(message)
                
            except Exception as e:
                logger.error("Error processing message", error=str(e))
                # ✅ No stats mutation - MetricsService tracks via WEBSOCKET_ERROR event
                # Publish error event
                self._publish_event_threadsafe(self._publish_error(str(e)))
        
        def on_error(ws, error):
            """Handle errors."""
            error_msg = str(error)
            logger.error("WebSocket error", error=error_msg)
            
            # ✅ No stats mutation - MetricsService tracks via WEBSOCKET_ERROR event
            
            is_rate_limit = "429" in error_msg or "Too Many Requests" in error_msg
            if is_rate_limit:
                logger.error("Rate limit hit (429) - connection will close")
                self._reconnect_allowed = False
                self._publish_event_threadsafe(self._publish_rate_limit())
            else:
                self._publish_event_threadsafe(self._publish_error(error_msg, is_rate_limit=False))
        
        def on_close(ws, close_status_code, close_msg):
            """Handle close."""
            logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
            
            # ✅ No stats mutation - MetricsService tracks via WEBSOCKET_DISCONNECTED event
            
            # Publish disconnect event
            self._publish_event_threadsafe(self._publish_disconnect(close_msg))
        
        def on_open(ws):
            """Handle open."""
            logger.info("WebSocket connection opened")
            
            with self._lock:
                self._operational_stats["connection_verified_at"] = datetime.now()
            # ✅ is_connected tracked via WEBSOCKET_CONNECTED event (MetricsService)
            
            # Start ping thread after connection is open
            if self._ping_thread and not self._ping_thread.is_alive():
                self._ping_thread.start()
            
            # Publish connect event
            self._publish_event_threadsafe(self._publish_connected())
        
        def on_pong(ws, data):
            """Handle WebSocket pong frame."""
            logger.info("📥 WebSocket pong received")
            with self._lock:
                self._operational_stats["last_pong_received"] = datetime.now()
                self._operational_stats["pong_received_count"] += 1
                if self._operational_stats.get("missed_pongs", 0) > 0:
                    self._operational_stats["missed_pongs"] = 0
        
        def on_ping(ws, data):
            """Handle WebSocket ping frame from server."""
            logger.debug("Received ping frame from server")
        
        # Create WebSocket connection
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
        
        # Run forever with ping interval
        self.websocket.run_forever(
            ping_interval=int(self.ping_interval),
            ping_timeout=10
        )
    
    def _process_message(self, message: str) -> None:
        """Process incoming WebSocket message."""
        try:
            # Parse message (using stateless helper)
            data, is_json = parse_websocket_message(message)
            
            if is_json and data:
                # Handle JSON message types
                if isinstance(data, dict):
                    # Check for articles
                    articles = extract_articles_from_json(data)
                    if articles:
                        self._process_news_articles(articles)
                    # Check for heartbeat/pong
                    elif is_heartbeat_message(data):
                        if "pong" in str(data).lower() or data.get("type") == "pong":
                            # Handle JSON pong message
                            with self._lock:
                                self._operational_stats["last_pong_received"] = datetime.now()
                                self._operational_stats["pong_received_count"] += 1
                                if self._operational_stats.get("missed_pongs", 0) > 0:
                                    self._operational_stats["missed_pongs"] = 0
                            logger.info("📥 WebSocket JSON pong received")
                        else:
                            logger.debug("Received heartbeat message from Benzinga")
                    # Check for errors
                    elif is_error_message(data)[0]:
                        is_error, error_msg, is_rate_limit = is_error_message(data)
                        logger.error("Benzinga WebSocket error", error=error_msg)
                        
                        if is_rate_limit:
                            self._reconnect_allowed = False
                            self._publish_event_threadsafe(self._publish_rate_limit())
                        else:
                            self._publish_event_threadsafe(self._publish_error(error_msg, is_rate_limit=False))
                    else:
                        # Unknown JSON message format
                        logger.debug("Unknown JSON WebSocket message format", data=data)
                elif isinstance(data, list):
                    # List of articles
                    self._process_news_articles(data)
                else:
                    logger.debug("Unexpected JSON message type", message_type=type(data).__name__)
            elif not is_json:
                # XML/HTML message (using stateless helper)
                process_xml_message(message)
        
        except Exception as e:
            logger.error("Error processing message", error=str(e))
            self._publish_event_threadsafe(self._publish_error(str(e)))
    
    def _process_xml_message(self, message: str) -> None:
        """Process XML/HTML message from WebSocket."""
        # Delegate to stateless helper
        process_xml_message(message)
        # Note: Error handling is done in the helper, but we can publish error events here if needed
    
    def _process_news_articles(self, articles_data: list) -> None:
        """Process news articles and publish events."""
        for article_data in articles_data:
            try:
                # Check if article is too old during startup period
                if self._should_skip_old_article(article_data):
                    article_id = article_data.get("id") or article_data.get("benzinga_id") or "unknown"
                    logger.debug(
                        f"Skipping old article during startup: {article_id}",
                        skip_threshold_minutes=self._startup_skip_old_minutes
                    )
                    continue
                
                # Create typed infrastructure model (using stateless helper)
                infra_article_data = create_infrastructure_article_data(article_data)
                
                if infra_article_data:
                    # Publish typed infrastructure event
                    self._publish_event_threadsafe(self._publish_article_received(infra_article_data))
                    
                    # ✅ No stats mutation - MetricsService subscribes to ARTICLE_RECEIVED event
                    
                    article_id = infra_article_data.source_id or str(infra_article_data.benzinga_id) if infra_article_data.benzinga_id else "unknown"
                    logger.info("Published ArticleReceived event", article_id=article_id)
            
            except Exception as e:
                logger.error("Error processing article", error=str(e), article_data=article_data)
                # Publish error event for article processing failures
                self._publish_event_threadsafe(self._publish_error(f"Article processing error: {str(e)}", is_rate_limit=False))
    
    def _should_skip_old_article(self, article_data: Dict[str, Any]) -> bool:
        """
        Check if article should be skipped because it's too old (during startup period).
        
        Args:
            article_data: Raw article data dictionary
            
        Returns:
            True if article should be skipped, False otherwise
        """
        # If startup time not set, don't skip (shouldn't happen, but be safe)
        if not self._startup_time:
            return False
        
        # Check if we're still in startup period (first 5 minutes after startup)
        startup_period_end = self._startup_time + timedelta(minutes=5)
        if datetime.now() > startup_period_end:
            # Startup period expired, process all articles
            return False
        
        # Extract article timestamp
        article_timestamp = None
        
        # Try different timestamp fields
        for field in ["published", "created_at", "updated_at", "last_updated"]:
            timestamp_str = article_data.get(field)
            if timestamp_str:
                try:
                    # Parse ISO format timestamp
                    if isinstance(timestamp_str, str):
                        # Handle various formats
                        if 'T' in timestamp_str:
                            # ISO format
                            article_timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        else:
                            # Try other formats if needed
                            continue
                    elif isinstance(timestamp_str, (int, float)):
                        # Unix timestamp - explicitly convert as UTC
                        # fromtimestamp() without tz interprets as local time, which is incorrect
                        article_timestamp = datetime.fromtimestamp(timestamp_str, tz=timezone.utc)
                    break
                except (ValueError, TypeError):
                    continue
        
        # If no timestamp found, don't skip (process it to be safe)
        if not article_timestamp:
            return False
        
        # Make article_timestamp timezone-aware if needed (for ISO strings that might not have timezone)
        if article_timestamp.tzinfo is None:
            # Assume UTC if no timezone (shouldn't happen for Unix timestamps after fix above)
            article_timestamp = article_timestamp.replace(tzinfo=timezone.utc)
        
        # Make startup_time timezone-aware for comparison
        startup_time_aware = self._startup_time
        if startup_time_aware.tzinfo is None:
            startup_time_aware = startup_time_aware.replace(tzinfo=timezone.utc)
        
        # Calculate age of article
        article_age = (startup_time_aware - article_timestamp).total_seconds() / 60.0  # age in minutes
        
        # Skip if article is older than threshold
        if article_age > self._startup_skip_old_minutes:
            return True
        
        return False
    
    def _create_infrastructure_article_data(self, data: Dict[str, Any]) -> Optional[InfrastructureArticleData]:
        """Create typed InfrastructureArticleData from raw WebSocket data."""
        # Delegate to stateless helper
        return create_infrastructure_article_data(data)
    
    async def _publish_article_received(self, article_data: InfrastructureArticleData) -> None:
        """Publish ArticleReceived infrastructure event with typed model."""
        event = ArticleReceivedEvent(
            article_data=article_data,  # ✅ Typed infrastructure model
            received_at=datetime.now()
        )
        await self.event_bus.publish(InfrastructureEventType.ARTICLE_RECEIVED, event.model_dump())
    
    async def _publish_connected(self) -> None:
        """Publish WebSocketConnected event."""
        event = WebSocketConnectedEvent(connected_at=datetime.now())
        await self.event_bus.publish("WebSocketConnected", event.model_dump())
    
    async def _publish_disconnect(self, reason: Optional[str] = None) -> None:
        """Publish WebSocketDisconnected event."""
        event = WebSocketDisconnectedEvent(
            disconnected_at=datetime.now(),
            reason=reason
        )
        await self.event_bus.publish("WebSocketDisconnected", event.model_dump())
    
    async def _publish_error(self, error: str, is_rate_limit: bool = False) -> None:
        """Publish WebSocketError event."""
        from .events import WebSocketErrorEvent
        event = WebSocketErrorEvent(
            error=error,
            occurred_at=datetime.now(),
            is_rate_limit=is_rate_limit
        )
        await self.event_bus.publish("WebSocketError", event.model_dump())
    
    async def _publish_rate_limit(self) -> None:
        """Publish WebSocketRateLimit event."""
        from .events import WebSocketRateLimitEvent
        event = WebSocketRateLimitEvent(occurred_at=datetime.now())
        await self.event_bus.publish("WebSocketRateLimit", event.model_dump())
    
    def _convert_to_benzinga_article(self, data: Dict[str, Any]) -> Optional[BenzingaArticle]:
        """Convert raw data to BenzingaArticle model."""
        try:
            # Map WebSocket fields to BenzingaArticle fields
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
                images=[]
            )
            return article
        except Exception as e:
            logger.error("Failed to convert to BenzingaArticle", error=str(e), data=data)
            # Note: Conversion errors are logged but don't publish events (not connection-level errors)
            return None
    
    def _ping_loop(self) -> None:
        """Ping loop for keepalive."""
        # Keep existing ping loop logic
        logger.info("Ping loop started")
        while self._threads_should_run:
            try:
                time.sleep(self.ping_interval)
                # Check connection status from MetricsService
                websocket_stats = self.metrics_service.get_websocket_stats()
                is_connected = websocket_stats.get("is_connected", False)
                
                if self.websocket and is_connected:
                    try:
                        self.websocket.send(json.dumps({"action": "ping"}))
                        with self._lock:
                            self._operational_stats["last_ping_sent"] = datetime.now()
                            self._operational_stats["ping_sent_count"] += 1
                            ping_count = self._operational_stats["ping_sent_count"]
                        logger.info("📤 WebSocket ping sent", ping_count=ping_count)
                    except Exception as e:
                        logger.error("Error sending ping", error=str(e))
                        # Publish error for ping failures
                        self._publish_event_threadsafe(self._publish_error(f"Ping error: {str(e)}", is_rate_limit=False))
            except Exception as e:
                logger.error("Error in ping loop", error=str(e))
                self._publish_event_threadsafe(self._publish_error(f"Ping loop error: {str(e)}", is_rate_limit=False))
                break
    
    def _connection_monitor_loop(self) -> None:
        """Monitor connection health."""
        logger.info("Connection monitor started")
        while self._threads_should_run:
            try:
                time.sleep(self.connection_check_interval)
                with self._lock:
                    self._operational_stats["last_connection_check"] = datetime.now()
            except Exception as e:
                logger.error("Error in connection monitor", error=str(e))
                self._publish_event_threadsafe(self._publish_error(f"Connection monitor error: {str(e)}", is_rate_limit=False))
                break

