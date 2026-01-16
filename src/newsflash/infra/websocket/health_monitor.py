"""
WebSocket health monitor - infrastructure layer.
Monitors WebSocket health and publishes health status events.

REFACTORED: Now uses native async instead of threading.
"""
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from .events import WebSocketHealthStatusEvent

logger = get_logger(__name__)


class WebSocketHealthMonitor:
    """
    Monitors WebSocket health and publishes health status events (Native Async).

    This is infrastructure - it directly accesses WebSocket state
    and publishes health events that services can subscribe to.
    """

    def __init__(self, event_bus: AsyncEventBus, websocket_service, check_interval: float = 30.0, inactivity_threshold_minutes: int = 5):
        """
        Initialize health monitor.

        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            websocket_service: WebSocket microservice to monitor
            check_interval: How often to check health (seconds)
            inactivity_threshold_minutes: Alert if no messages for this many minutes
        """
        self.websocket_service = websocket_service
        self.check_interval = check_interval
        self.inactivity_threshold_minutes = inactivity_threshold_minutes
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self.event_bus = event_bus

        logger.info("WebSocketHealthMonitor initialized (native async)", check_interval=check_interval)

    def start(self) -> None:
        """
        Start health monitoring.

        Idempotent: Safe to call multiple times.
        """
        if self._running and self._monitor_task and not self._monitor_task.done():
            logger.debug("WebSocket health monitor already started")
            return

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("WebSocket health monitor started")

    def stop(self) -> None:
        """
        Stop health monitoring.

        Idempotent: Safe to call multiple times.
        """
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        logger.info("WebSocket health monitor stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop (async)."""
        logger.info("WebSocket health monitor loop started")

        try:
            while self._running:
                await asyncio.sleep(self.check_interval)

                if not self._running:
                    break

                # Check health and publish event directly (no thread crossing!)
                health_status = self._check_health()
                await self._publish_health_status(health_status)

        except asyncio.CancelledError:
            logger.debug("Health monitor loop cancelled")
        except Exception as e:
            logger.error("Error in health monitor loop", error=str(e))

        logger.info("WebSocket health monitor loop stopped")

    def _check_health(self) -> Dict[str, Any]:
        """Check WebSocket health and return status."""
        try:
            stats = self.websocket_service.get_stats()
            is_connected = stats.get("is_connected", False)
            # Check if service is running (use _running, consistent with new async pattern)
            service_running = getattr(self.websocket_service, '_running', False)

            # Check if service is running
            if not service_running:
                return {
                    "healthy": False,
                    "reason": "WebSocket service is not running",
                    "status": "disconnected"
                }

            # Check if connected
            if not is_connected:
                return {
                    "healthy": False,
                    "reason": "WebSocket is not connected",
                    "status": "disconnected",
                    "last_error": stats.get("last_error")
                }

            # Check ping/pong status FIRST - if ping/pong is working, connection is alive
            last_ping_sent = stats.get("last_ping_sent")
            last_pong_received = stats.get("last_pong_received")
            ping_sent_count = stats.get("ping_sent_count", 0)
            pong_received_count = stats.get("pong_received_count", 0)

            # If pings are being sent and pongs received, connection is healthy
            if last_ping_sent and last_pong_received:
                if isinstance(last_ping_sent, str):
                    last_ping_sent = datetime.fromisoformat(last_ping_sent.replace('Z', '+00:00'))
                if isinstance(last_pong_received, str):
                    last_pong_received = datetime.fromisoformat(last_pong_received.replace('Z', '+00:00'))

                # Check if pong is recent (within last 2 ping intervals)
                time_since_pong = (datetime.now(last_pong_received.tzinfo) - last_pong_received).total_seconds()
                if time_since_pong < 180:  # Pong within last 180 seconds
                    return {
                        "healthy": True,
                        "reason": "WebSocket is connected with active ping/pong",
                        "status": "healthy",
                        "ping_count": ping_sent_count,
                        "pong_count": pong_received_count,
                        "messages_received": stats.get("messages_received", 0),
                        "time_since_last_message_minutes": None
                    }

            # Check ping/pong for zombie connections
            missed_pongs = stats.get("missed_pongs", 0)

            if missed_pongs >= 2:
                return {
                    "healthy": False,
                    "reason": f"Zombie connection: {missed_pongs} missed pongs",
                    "status": "zombie"
                }

            # Check if ping was sent but no pong received
            messages_received = stats.get("messages_received", 0)
            last_message_time = stats.get("last_message_time")

            if last_ping_sent:
                if isinstance(last_ping_sent, str):
                    last_ping_sent = datetime.fromisoformat(last_ping_sent.replace('Z', '+00:00'))

                time_since_ping = (datetime.now(last_ping_sent.tzinfo) - last_ping_sent).total_seconds()
                ping_interval = getattr(self.websocket_service, 'ping_interval', 90.0)
                zombie_threshold = ping_interval * 2

                # Check if messages are recent
                has_recent_messages = False
                if last_message_time:
                    if isinstance(last_message_time, str):
                        last_message_time = datetime.fromisoformat(last_message_time.replace('Z', '+00:00'))
                    time_since_message = (datetime.now(last_message_time.tzinfo) - last_message_time).total_seconds()
                    has_recent_messages = time_since_message < 300  # 5 minutes

                if time_since_ping > zombie_threshold:
                    if not last_pong_received or (isinstance(last_pong_received, datetime) and last_pong_received < last_ping_sent):
                        if not has_recent_messages:
                            return {
                                "healthy": False,
                                "reason": f"Zombie connection: no pong for {time_since_ping:.1f}s and no recent messages",
                                "status": "zombie"
                            }
                        else:
                            logger.debug(
                                "No pong received but messages are flowing - connection is alive",
                                time_since_ping=time_since_ping,
                                time_since_last_message=time_since_message
                            )

            # If no ping/pong yet, check messages
            last_message_time = stats.get("last_message_time")
            if last_message_time:
                if isinstance(last_message_time, str):
                    last_message_time = datetime.fromisoformat(last_message_time.replace('Z', '+00:00'))

                time_since_last_message = datetime.now(last_message_time.tzinfo) - last_message_time
                if time_since_last_message > timedelta(minutes=self.inactivity_threshold_minutes):
                    return {
                        "healthy": False,
                        "reason": f"No messages for {time_since_last_message.total_seconds() / 60:.1f} minutes",
                        "status": "inactive",
                        "last_message_time": last_message_time.isoformat()
                    }
                return {
                    "healthy": True,
                    "reason": "WebSocket is connected and receiving messages",
                    "status": "healthy",
                    "messages_received": stats.get("messages_received", 0),
                    "last_message_time": last_message_time.isoformat()
                }
            else:
                # No messages yet - if recently connected, might be OK
                connection_verified_at = stats.get("connection_verified_at")
                if connection_verified_at:
                    if isinstance(connection_verified_at, str):
                        connection_verified_at = datetime.fromisoformat(connection_verified_at.replace('Z', '+00:00'))
                    time_since_connection = (datetime.now(connection_verified_at.tzinfo) - connection_verified_at).total_seconds()
                    if time_since_connection < 60:
                        return {
                            "healthy": True,
                            "reason": "WebSocket recently connected, waiting for messages",
                            "status": "healthy",
                            "connected_seconds_ago": time_since_connection
                        }

            # Default: connected but no activity yet
            return {
                "healthy": True,
                "reason": "WebSocket is connected (waiting for activity)",
                "status": "healthy",
                "messages_received": stats.get("messages_received", 0)
            }

        except Exception as e:
            logger.error("Error checking WebSocket health", error=str(e))
            return {
                "healthy": False,
                "reason": f"Health check failed: {str(e)}",
                "status": "error",
                "error": str(e)
            }

    async def _publish_health_status(self, health_status: Dict[str, Any]) -> None:
        """Publish health status event."""
        try:
            is_healthy = health_status.get("healthy", False)
            status = health_status.get("status", "unknown")
            reason = health_status.get("reason", "")

            # Log health status
            if is_healthy:
                logger.info(
                    "WebSocket health check: HEALTHY",
                    status=status,
                    reason=reason,
                    messages_received=health_status.get("messages_received", 0)
                )
            else:
                logger.warning(
                    "WebSocket health check: UNHEALTHY",
                    status=status,
                    reason=reason
                )

            event = WebSocketHealthStatusEvent(
                healthy=is_healthy,
                status=status,
                reason=reason,
                occurred_at=datetime.now(),
                details=health_status
            )
            await self.event_bus.publish("WebSocketHealthStatus", event.model_dump())
        except Exception as e:
            logger.error("Error publishing health status event", error=str(e))
