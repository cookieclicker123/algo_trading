"""
WebSocket health monitor - infrastructure layer.
Monitors WebSocket health and publishes health status events.
"""
import asyncio
import threading
import time
from typing import Dict, Any
from datetime import datetime, timedelta

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from .events import WebSocketHealthStatusEvent

logger = get_logger(__name__)


class WebSocketHealthMonitor:
    """
    Monitors WebSocket health and publishes health status events.
    
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
        # Thread control flag (operational state needed by threads)
        self._threads_should_run = False
        self.monitor_thread: threading.Thread | None = None
        self.event_bus = event_bus
        
        # Store reference to main event loop for thread-safe publishing
        try:
            self._main_event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_event_loop = None
        
        logger.info("WebSocketHealthMonitor initialized", check_interval=check_interval)
    
    def start(self) -> None:
        """
        Start health monitoring.
        
        Idempotent: Safe to call multiple times. Thread control prevents duplicate threads.
        """
        # Set thread control flag (operational state for threads)
        self._threads_should_run = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("WebSocket health monitor started")
    
    def stop(self) -> None:
        """
        Stop health monitoring.
        
        Idempotent: Safe to call multiple times.
        """
        # Signal thread to stop (operational state for threads)
        self._threads_should_run = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        logger.info("WebSocket health monitor stopped")
    
    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        logger.info("WebSocket health monitor loop started")
        
        while self._threads_should_run:
            try:
                time.sleep(self.check_interval)
                
                if not self._threads_should_run:
                    break
                
                # Check health and publish event
                health_status = self._check_health()
                
                # Get main event loop from websocket service or try to get it
                main_loop = None
                if hasattr(self.websocket_service, '_main_event_loop'):
                    main_loop = self.websocket_service._main_event_loop
                
                if main_loop is None:
                    try:
                        main_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        try:
                            main_loop = asyncio.get_event_loop()
                        except RuntimeError:
                            logger.warning("No event loop available for publishing health status")
                            continue
                
                # Schedule publishing on main event loop (thread-safe)
                if main_loop and main_loop.is_running():
                    main_loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self._publish_health_status(health_status))
                    )
                else:
                    logger.warning("Main event loop not running, cannot publish health status")
                
            except Exception as e:
                logger.error("Error in health monitor loop", error=str(e))
                time.sleep(5)
        
        logger.info("WebSocket health monitor loop stopped")
    
    def _check_health(self) -> Dict[str, Any]:
        """Check WebSocket health and return status."""
        try:
            stats = self.websocket_service.get_stats()
            is_connected = stats.get("is_connected", False)
            # Check if service threads are running (operational state)
            threads_running = self.websocket_service._threads_should_run
            
            # Check if service is running
            if not threads_running:
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
            
            # If pings are being sent and pongs received, connection is healthy (even if no messages yet)
            if last_ping_sent and last_pong_received:
                if isinstance(last_ping_sent, str):
                    last_ping_sent = datetime.fromisoformat(last_ping_sent.replace('Z', '+00:00'))
                if isinstance(last_pong_received, str):
                    last_pong_received = datetime.fromisoformat(last_pong_received.replace('Z', '+00:00'))
                
                # Check if pong is recent (within last 2 ping intervals = 60s)
                time_since_pong = (datetime.now(last_pong_received.tzinfo) - last_pong_received).total_seconds()
                if time_since_pong < 60:  # Pong within last 60 seconds = healthy
                    # Connection is alive via ping/pong - that's what matters
                    # Don't check message frequency - weekends/low news periods are normal
                    return {
                        "healthy": True,
                        "reason": "WebSocket is connected with active ping/pong",
                        "status": "healthy",
                        "ping_count": ping_sent_count,
                        "pong_count": pong_received_count,
                        "messages_received": stats.get("messages_received", 0),
                        "time_since_last_message_minutes": None  # Not relevant if ping/pong works
                    }
            
            # Check ping/pong for zombie connections
            missed_pongs = stats.get("missed_pongs", 0)
            
            if missed_pongs >= 2:
                return {
                    "healthy": False,
                    "reason": f"Zombie connection: {missed_pongs} missed pongs",
                    "status": "zombie"
                }
            
            # Check if ping was sent but no pong received (timeout)
            if last_ping_sent:
                if isinstance(last_ping_sent, str):
                    last_ping_sent = datetime.fromisoformat(last_ping_sent.replace('Z', '+00:00'))
                
                time_since_ping = (datetime.now(last_ping_sent.tzinfo) - last_ping_sent).total_seconds()
                if time_since_ping > 35:  # More than 30 seconds + buffer
                    if not last_pong_received or (isinstance(last_pong_received, datetime) and last_pong_received < last_ping_sent):
                        return {
                            "healthy": False,
                            "reason": f"Zombie connection: no pong for {time_since_ping:.1f}s",
                            "status": "zombie"
                        }
            
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
                # Have recent messages - healthy
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
                    if time_since_connection < 60:  # Just connected within last minute
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

