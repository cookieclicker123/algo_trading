"""
WebSocket microservice for Benzinga news feed.
Pure infrastructure - handles connection management and publishes events.

REFACTORED: Now uses native async `websockets` library instead of thread-based
`websocket-client`. This eliminates the thread-to-async bridge that was causing
50-200ms latency under load due to call_soon_threadsafe() queue delays.
"""
import asyncio
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
except ImportError:
    websockets = None
    ConnectionClosed = Exception
    ConnectionClosedError = Exception
    ConnectionClosedOK = Exception

from ...utils.logging_config import get_logger
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
    WebSocket microservice for Benzinga news feed (Native Async).

    This implementation uses the `websockets` library for native async operation,
    eliminating the thread-to-async bridge that caused latency issues.

    Responsibilities:
    - Manage WebSocket connection to Benzinga
    - Handle reconnection logic with exponential backoff
    - Parse incoming messages
    - Publish events to event bus (directly, no thread crossing)

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
            metrics_service: Metrics service for statistics (injected via DI)
        """
        self.token = token
        self.websocket_url = f"wss://api.benzinga.com/api/v1/news/stream"
        self.websocket: Optional[Any] = None
        self._running = False
        self._reconnect_allowed = True
        self.metrics_service = metrics_service

        # Event bus for publishing events (direct async, no thread crossing!)
        self.event_bus = event_bus

        # Operational stats (not tracked via events)
        self._operational_stats = {
            "connection_attempts": 0,
            "ping_sent_count": 0,
            "pong_received_count": 0,
            "missed_pongs": 0,
            "last_ping_sent": None,
            "last_pong_received": None,
            "last_connection_check": None,
            "connection_verified_at": None,
        }

        # Configuration
        self.ping_interval = 90.0  # Seconds between pings
        self.ping_timeout = 30.0
        self.connection_check_interval = 30.0

        # Reconnection
        self._reconnect_delay = 5.0
        self._max_reconnect_delay = 300.0  # 5 minutes max
        self._reconnect_attempts = 0

        # Health monitor
        self.health_monitor: Optional[WebSocketHealthMonitor] = None

        # Background tasks
        self._connection_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None

        # Startup filtering
        self._startup_time: Optional[datetime] = None
        from ...config import settings
        self._startup_skip_old_minutes = settings.WEBSOCKET_STARTUP_SKIP_OLD_MESSAGES_MINUTES
        self._autorestart_enabled = settings.FEED_AUTORESTART_WEBSOCKET

        # Lock for stats updates
        self._stats_lock = asyncio.Lock()

        logger.info("BenzingaWebSocketMicroservice initialized (native async)", token_prefix=token[:10] + "...")

    def start(self) -> None:
        """
        Start the WebSocket connection.

        Idempotent: Safe to call multiple times.
        Spawns the connection task in the current event loop.
        """
        if self._running and self._connection_task and not self._connection_task.done():
            logger.debug("Benzinga WebSocket microservice already started")
            return

        logger.info("Starting Benzinga WebSocket microservice (native async)")
        self._running = True
        self._reconnect_allowed = True
        self._startup_time = datetime.now()

        logger.info(
            f"WebSocket startup time recorded - will skip articles older than {self._startup_skip_old_minutes} minutes"
        )

        # Start health monitor
        if not self.health_monitor:
            self.health_monitor = WebSocketHealthMonitor(self.event_bus, self)
        self.health_monitor.start()

        # Start connection task (handles reconnection loop)
        self._connection_task = asyncio.create_task(self._connection_loop())

        logger.info(
            "WebSocket connection task started",
            autorestart_enabled=self._autorestart_enabled,
            reconnect_allowed=self._reconnect_allowed
        )

    def stop(self) -> None:
        """
        Stop the WebSocket connection.

        Idempotent: Safe to call multiple times.
        """
        logger.info("Stopping Benzinga WebSocket microservice")
        self._running = False
        self._reconnect_allowed = False

        # Stop health monitor
        if self.health_monitor:
            self.health_monitor.stop()
            self.health_monitor = None

        # Cancel background tasks
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()

        # Close WebSocket connection
        if self.websocket:
            asyncio.create_task(self._close_websocket())

        logger.info("WebSocket microservice stopped")

    async def _close_websocket(self) -> None:
        """Close the WebSocket connection gracefully."""
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error("Error closing WebSocket", error=str(e))
            self.websocket = None

    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        websocket_stats = self.metrics_service.get_websocket_stats()
        return websocket_stats.get("is_connected", False)

    def get_stats(self) -> Dict[str, Any]:
        """Get WebSocket service statistics."""
        websocket_stats = self.metrics_service.get_websocket_stats()
        return serialize_stats({
            **websocket_stats,
            **self._operational_stats,
        })

    def is_healthy(self) -> bool:
        """Check if WebSocket service is healthy."""
        websocket_stats = self.metrics_service.get_websocket_stats()
        is_connected = websocket_stats.get("is_connected", False)
        last_error = websocket_stats.get("last_error")
        return (
            is_connected and
            self._running and
            (last_error is None or "429" not in str(last_error))
        )

    async def _connection_loop(self) -> None:
        """
        Main connection loop with automatic reconnection.

        Runs until stop() is called. Handles reconnection with exponential backoff.
        """
        while self._running:
            try:
                self._operational_stats["connection_attempts"] += 1
                attempt_num = self._reconnect_attempts + 1
                logger.info("Attempting WebSocket connection", attempt=attempt_num)

                await self._connect_and_process()

                # If we exit _connect_and_process() normally, connection closed
                logger.info("WebSocket connection closed normally")

            except asyncio.CancelledError:
                logger.info("Connection loop cancelled")
                break

            except Exception as e:
                error_str = str(e)
                logger.error("WebSocket connection failed", error=error_str, attempt=self._reconnect_attempts + 1)

                # Check for rate limit
                is_rate_limit = "429" in error_str or "Too Many Requests" in error_str
                if is_rate_limit:
                    self._reconnect_allowed = False
                    await self._publish_rate_limit()
                    logger.warning("WebSocket will NOT auto-reconnect to prevent 429 rate limits")
                    break

            # Check if we should reconnect
            if not self._running:
                break

            if not self._autorestart_enabled:
                logger.info("Auto-restart disabled - not reconnecting")
                break

            if not self._reconnect_allowed:
                logger.info("Reconnection not allowed - not reconnecting")
                break

            # Exponential backoff
            self._reconnect_attempts += 1
            delay = min(self._reconnect_delay * (2 ** (self._reconnect_attempts - 1)), self._max_reconnect_delay)

            logger.info(
                "WebSocket connection closed - will reconnect",
                attempt=self._reconnect_attempts,
                delay_seconds=delay
            )

            await asyncio.sleep(delay)

        logger.info("WebSocket connection loop stopped")

    async def _connect_and_process(self) -> None:
        """Connect to WebSocket and process messages."""
        logger.info("Connecting to Benzinga WebSocket", url=self.websocket_url)

        headers = {
            'Authorization': self.token,
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Origin': 'https://api.benzinga.com',
        }

        try:
            async with websockets.connect(
                self.websocket_url,
                additional_headers=headers,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
                close_timeout=10,
                max_size=10 * 1024 * 1024,  # 10MB - handle large news bursts from Benzinga
            ) as ws:
                self.websocket = ws

                # Connection opened — reset all ping/pong state so monitor
                # doesn't see stale timestamps from a previous dead connection
                logger.info("WebSocket connection opened")
                now = datetime.now()
                self._operational_stats["connection_verified_at"] = now
                self._operational_stats["last_ping_sent"] = now
                self._operational_stats["last_pong_received"] = now
                self._operational_stats["missed_pongs"] = 0
                self._reconnect_attempts = 0
                self._reconnect_delay = 5.0
                self._startup_time = now

                # Publish connected event
                await self._publish_connected()

                # Start ping task
                self._ping_task = asyncio.create_task(self._ping_loop())

                # Start monitor task
                self._monitor_task = asyncio.create_task(self._monitor_loop())

                # Process messages
                try:
                    async for message in ws:
                        if not self._running:
                            break
                        await self._process_message(message)
                except ConnectionClosedOK:
                    logger.info("WebSocket closed normally")
                except ConnectionClosedError as e:
                    logger.warning(f"WebSocket closed with error: {e}")
                except ConnectionClosed as e:
                    logger.warning(f"WebSocket connection closed: {e}")

                # Connection closed
                self.websocket = None
                await self._publish_disconnect("Connection closed")

                # Cancel background tasks
                if self._ping_task and not self._ping_task.done():
                    self._ping_task.cancel()
                if self._monitor_task and not self._monitor_task.done():
                    self._monitor_task.cancel()

        except Exception as e:
            self.websocket = None
            error_str = str(e)
            logger.error("WebSocket connection error", error=error_str)
            await self._publish_error(error_str)
            raise

    async def _process_message(self, message: str) -> None:
        """Process incoming WebSocket message."""
        try:
            logger.info(f"Received WebSocket message ({len(message)} chars)")

            # Parse message
            data, is_json = parse_websocket_message(message)

            if is_json and data:
                if isinstance(data, dict):
                    # Check for articles
                    articles = extract_articles_from_json(data)
                    if articles:
                        await self._process_news_articles(articles)
                    # Check for heartbeat/pong
                    elif is_heartbeat_message(data):
                        if "pong" in str(data).lower() or data.get("type") == "pong":
                            self._operational_stats["last_pong_received"] = datetime.now()
                            self._operational_stats["pong_received_count"] += 1
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
                            await self._publish_rate_limit()
                        else:
                            await self._publish_error(error_msg, is_rate_limit=False)
                    else:
                        logger.warning(
                            "Unknown JSON WebSocket message format — articles not extracted",
                            top_level_keys=list(data.keys())[:10],
                            kind=data.get("kind"),
                            action=(data.get("data") or {}).get("action") if isinstance(data.get("data"), dict) else None,
                            sample=str(data)[:300],
                        )

                elif isinstance(data, list):
                    # List of articles
                    await self._process_news_articles(data)
                else:
                    logger.warning("Unexpected JSON message type", message_type=type(data).__name__)

            elif not is_json:
                # XML/HTML message
                logger.warning(
                    "WebSocket message did not parse as JSON — articles not extracted",
                    message_length=len(message),
                    preview=message[:200],
                )
                process_xml_message(message)

        except Exception as e:
            logger.error("Error processing message", error=str(e))
            await self._publish_error(str(e))

    async def _process_news_articles(self, articles_data: list) -> None:
        """Process news articles and publish events."""
        for article_data in articles_data:
            try:
                # Check if article is too old during startup period
                if self._should_skip_old_article(article_data):
                    article_id = article_data.get("id") or article_data.get("benzinga_id") or "unknown"
                    logger.info(
                        "Skipping old article during startup",
                        article_id=article_id,
                        published=article_data.get("published"),
                        created_at=article_data.get("created_at"),
                        skip_threshold_minutes=self._startup_skip_old_minutes,
                    )
                    continue

                # Create typed infrastructure model
                infra_article_data = create_infrastructure_article_data(article_data)

                if infra_article_data:
                    # Publish event directly (no thread crossing!)
                    await self._publish_article_received(infra_article_data)

                    article_id = infra_article_data.source_id or str(infra_article_data.benzinga_id) if infra_article_data.benzinga_id else "unknown"
                    logger.info("Published ArticleReceived event", article_id=article_id)
                else:
                    logger.warning(
                        "Article dropped — create_infrastructure_article_data returned None",
                        top_level_keys=list(article_data.keys())[:15] if isinstance(article_data, dict) else type(article_data).__name__,
                        sample=str(article_data)[:300],
                    )

            except Exception as e:
                logger.error("Error processing article", error=str(e), article_data=article_data)
                await self._publish_error(f"Article processing error: {str(e)}", is_rate_limit=False)

    def _should_skip_old_article(self, article_data: Dict[str, Any]) -> bool:
        """Check if article should be skipped because it's too old (during startup period)."""
        if not self._startup_time:
            return False

        startup_period_end = self._startup_time + timedelta(minutes=self._startup_skip_old_minutes)
        if datetime.now() > startup_period_end:
            return False

        article_timestamp = None

        for field in ["published", "created_at", "updated_at", "last_updated"]:
            timestamp_str = article_data.get(field)
            if timestamp_str:
                try:
                    if isinstance(timestamp_str, str):
                        if 'T' in timestamp_str:
                            article_timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        else:
                            continue
                    elif isinstance(timestamp_str, (int, float)):
                        article_timestamp = datetime.fromtimestamp(timestamp_str, tz=timezone.utc)
                    break
                except (ValueError, TypeError):
                    continue

        if not article_timestamp:
            return False

        if article_timestamp.tzinfo is None:
            article_timestamp = article_timestamp.replace(tzinfo=timezone.utc)

        startup_time_aware = self._startup_time
        if startup_time_aware.tzinfo is None:
            startup_time_aware = startup_time_aware.replace(tzinfo=timezone.utc)

        article_age = (startup_time_aware - article_timestamp).total_seconds() / 60.0

        if article_age > self._startup_skip_old_minutes:
            return True

        return False

    async def _ping_loop(self) -> None:
        """Ping loop for keepalive."""
        logger.info("Ping loop started")
        try:
            while self._running and self.websocket:
                await asyncio.sleep(self.ping_interval)

                if self.websocket and self.is_connected():
                    try:
                        await self.websocket.send(json.dumps({"action": "ping"}))
                        self._operational_stats["last_ping_sent"] = datetime.now()
                        self._operational_stats["ping_sent_count"] += 1
                        logger.info("📤 WebSocket ping sent", ping_count=self._operational_stats["ping_sent_count"])
                    except Exception as e:
                        logger.error("Error sending ping", error=str(e))
                        await self._publish_error(f"Ping error: {str(e)}", is_rate_limit=False)

        except asyncio.CancelledError:
            logger.debug("Ping loop cancelled")
        except Exception as e:
            logger.error("Error in ping loop", error=str(e))

    async def _monitor_loop(self) -> None:
        """Monitor connection health."""
        logger.info("Connection monitor started")
        try:
            while self._running:
                await asyncio.sleep(self.connection_check_interval)

                self._operational_stats["last_connection_check"] = datetime.now()

                if self._running and self._reconnect_allowed and self._autorestart_enabled:
                    stats = self.get_stats()
                    is_connected = stats.get("is_connected", False)
                    last_ping_sent = stats.get("last_ping_sent")
                    last_pong_received = stats.get("last_pong_received")
                    missed_pongs = stats.get("missed_pongs", 0)

                    # Check for recent messages
                    last_message_time = stats.get("last_message_time")
                    has_recent_messages = False
                    if last_message_time:
                        if isinstance(last_message_time, str):
                            last_message_time = datetime.fromisoformat(last_message_time.replace('Z', '+00:00'))
                        time_since_message = (datetime.now(last_message_time.tzinfo) - last_message_time).total_seconds()
                        has_recent_messages = time_since_message < 300  # 5 minutes

                    # Detect zombie connection
                    if is_connected and self.websocket:
                        if last_ping_sent:
                            if isinstance(last_ping_sent, str):
                                last_ping_sent = datetime.fromisoformat(last_ping_sent.replace('Z', '+00:00'))

                            time_since_ping = (datetime.now(last_ping_sent.tzinfo) - last_ping_sent).total_seconds()
                            zombie_threshold = self.ping_interval * 2

                            if time_since_ping > zombie_threshold:
                                if not last_pong_received or (
                                    isinstance(last_pong_received, datetime) and last_pong_received < last_ping_sent
                                ):
                                    if not has_recent_messages:
                                        logger.warning(
                                            "Connection monitor detected zombie connection - triggering reconnection",
                                            time_since_ping=time_since_ping,
                                            zombie_threshold=zombie_threshold
                                        )
                                        try:
                                            if self.websocket:
                                                await self.websocket.close()
                                        except Exception as e:
                                            logger.error("Error closing zombie connection", error=str(e))

                        if missed_pongs >= 2:
                            logger.warning(
                                "Connection monitor detected multiple missed pongs - triggering reconnection",
                                missed_pongs=missed_pongs
                            )
                            try:
                                if self.websocket:
                                    await self.websocket.close()
                            except Exception as e:
                                logger.error("Error closing zombie connection", error=str(e))

        except asyncio.CancelledError:
            logger.debug("Monitor loop cancelled")
        except Exception as e:
            logger.error("Error in connection monitor", error=str(e))

    # Event publishing methods (direct async - no thread crossing!)

    async def _publish_article_received(self, article_data: InfrastructureArticleData) -> None:
        """Publish ArticleReceived infrastructure event."""
        event = ArticleReceivedEvent(
            article_data=article_data,
            received_at=datetime.now(timezone.utc)
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

