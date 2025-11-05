"""
Health monitoring service for news feeds.
Checks connection status and activity every 30 seconds.
Sends Telegram alerts on disconnections or failures.
"""
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from ..utils.logging_config import get_logger
from ..services.feed_manager import FeedManager
from ..config import settings

logger = get_logger(__name__)


class FeedHealthMonitor:
    """
    Monitors health of all news feeds and sends alerts on failures.
    
    Features:
    - Periodic health checks every 30 seconds
    - Detects disconnections, inactivity, and errors
    - Sends Telegram alerts on state changes
    - Tracks previous state to avoid spam
    """
    
    def __init__(self, feed_manager: FeedManager, telegram_service):
        """
        Initialize health monitor.
        
        Args:
            feed_manager: FeedManager instance to monitor
            telegram_service: Telegram service for sending alerts
        """
        self.feed_manager = feed_manager
        self.telegram_service = telegram_service
        self.is_running = False
        self.check_interval = 30  # Check every 30 seconds
        
        # Track previous state for each feed
        self.previous_state: Dict[str, Dict[str, Any]] = {
            "benzinga_websocket": {
                "healthy": None,
                "last_alert_time": None,
                "consecutive_failures": 0
            }
        }
        
        # Thresholds for determining health
        self.inactivity_threshold_minutes = 5  # Alert if no messages for 5 minutes
        
        logger.info("FeedHealthMonitor initialized", check_interval_seconds=self.check_interval)
    
    async def start(self):
        """Start the health monitoring loop."""
        if self.is_running:
            logger.warning("Health monitor already running")
            return
        
        self.is_running = True
        logger.info("Starting feed health monitor")
        
        try:
            while self.is_running:
                await self._check_all_feeds()
                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("Health monitor cancelled")
            raise
        except Exception as e:
            logger.error("Error in health monitor", error=str(e))
            raise
        finally:
            self.is_running = False
            logger.info("Feed health monitor stopped")
    
    async def stop(self):
        """Stop the health monitoring loop."""
        self.is_running = False
        logger.info("Stopping feed health monitor")
    
    async def _check_all_feeds(self):
        """Check health of all feeds."""
        # Check WebSocket feed
        websocket_status = await self._check_websocket_feed()
        await self._process_health_check("benzinga_websocket", websocket_status)
    
    async def _check_websocket_feed(self) -> Dict[str, Any]:
        """Check health of WebSocket feed."""
        try:
            processors = self.feed_manager.processors
            
            # Check if WebSocket is configured
            from ..models.base_models import NewsSource
            if NewsSource.BENZINGA_WEBSOCKET not in processors:
                return {
                    "healthy": False,
                    "reason": "WebSocket feed not configured"
                }
            
            websocket_service = processors[NewsSource.BENZINGA_WEBSOCKET]
            
            # Check connection status
            stats = websocket_service.get_stats()
            is_connected = stats.get("is_connected", False)
            is_running = websocket_service.is_running
            
            if not is_running:
                return {
                    "healthy": False,
                    "reason": "WebSocket service is not running",
                    "stats": stats
                }
            
            if not is_connected:
                return {
                    "healthy": False,
                    "reason": "WebSocket is not connected",
                    "stats": stats,
                    "last_error": stats.get("last_error")
                }
            
            # Check for recent activity
            last_message_time = stats.get("last_message_time")
            if last_message_time:
                # Parse datetime string if it's a string
                if isinstance(last_message_time, str):
                    last_message_time = datetime.fromisoformat(last_message_time.replace('Z', '+00:00'))
                
                # Check if we've had activity recently
                time_since_last_message = datetime.now(last_message_time.tzinfo) - last_message_time
                if time_since_last_message > timedelta(minutes=self.inactivity_threshold_minutes):
                    return {
                        "healthy": False,
                        "reason": f"No WebSocket messages for {time_since_last_message.total_seconds() / 60:.1f} minutes",
                        "stats": stats,
                        "last_message_time": last_message_time.isoformat()
                    }
            else:
                # No messages received yet, but connection is active
                messages_received = stats.get("messages_received", 0)
                connection_attempts = stats.get("connection_attempts", 0)
                if messages_received == 0 and connection_attempts > 0:
                    # Connection was attempted but no messages - might be stale
                    return {
                        "healthy": False,
                        "reason": f"WebSocket connected but no messages received (attempts: {connection_attempts})",
                        "stats": stats
                    }
            
            # Check for errors
            last_error = stats.get("last_error")
            if last_error:
                return {
                    "healthy": False,
                    "reason": f"WebSocket has recent error: {last_error}",
                    "stats": stats,
                    "last_error": last_error
                }
            
            # Check ping/pong status (NEW: detect zombie connections)
            missed_pongs = stats.get("missed_pongs", 0)
            last_ping_sent = stats.get("last_ping_sent")
            last_pong_received = stats.get("last_pong_received")
            last_connection_check = stats.get("last_connection_check")
            
            # Check if ping/pong is working
            ping_pong_status = {}
            if last_ping_sent:
                if isinstance(last_ping_sent, str):
                    last_ping_sent = datetime.fromisoformat(last_ping_sent.replace('Z', '+00:00'))
                ping_pong_status["last_ping"] = last_ping_sent.isoformat()
                
            if last_pong_received:
                if isinstance(last_pong_received, str):
                    last_pong_received = datetime.fromisoformat(last_pong_received.replace('Z', '+00:00'))
                ping_pong_status["last_pong"] = last_pong_received.isoformat()
            
            if last_connection_check:
                if isinstance(last_connection_check, str):
                    last_connection_check = datetime.fromisoformat(last_connection_check.replace('Z', '+00:00'))
            
            # Check for zombie connection (connected but no pong responses)
            if missed_pongs >= 2:
                return {
                    "healthy": False,
                    "reason": f"Zombie connection detected: {missed_pongs} missed pongs",
                    "stats": stats,
                    "ping_pong_status": ping_pong_status,
                    "is_zombie": True
                }
            
            # Check if ping was sent but no pong received recently
            if last_ping_sent and isinstance(last_ping_sent, datetime):
                time_since_ping = (datetime.now(last_ping_sent.tzinfo) - last_ping_sent).total_seconds()
                if time_since_ping > 35:  # More than 30 seconds (allowing 5s buffer)
                    if not last_pong_received or (isinstance(last_pong_received, datetime) and last_pong_received < last_ping_sent):
                        return {
                            "healthy": False,
                            "reason": f"Zombie connection: no pong received for {time_since_ping:.1f}s after ping",
                            "stats": stats,
                            "ping_pong_status": ping_pong_status,
                            "is_zombie": True
                        }
            
            # Feed appears healthy
            return {
                "healthy": True,
                "reason": "WebSocket feed is connected and receiving messages with active ping/pong",
                "stats": stats,
                "last_message_time": last_message_time.isoformat() if last_message_time else None,
                "ping_pong_status": ping_pong_status,
                "ping_count": stats.get("ping_sent_count", 0),
                "pong_count": stats.get("pong_received_count", 0),
                "last_connection_check": last_connection_check.isoformat() if last_connection_check else None
            }
            
        except Exception as e:
            logger.error("Error checking WebSocket feed health", error=str(e))
            return {
                "healthy": False,
                "reason": f"Health check failed: {str(e)}",
                "error": str(e)
            }
    
    async def _process_health_check(self, feed_name: str, health_status: Dict[str, Any]):
        """Process health check result and send alerts if needed."""
        is_healthy = health_status.get("healthy", False)
        reason = health_status.get("reason", "Unknown")
        previous_state = self.previous_state[feed_name]
        was_healthy = previous_state["healthy"]
        
        # Check if state changed
        state_changed = (was_healthy is not None) and (was_healthy != is_healthy)
        
        # Track consecutive failures
        if not is_healthy:
            previous_state["consecutive_failures"] += 1
        else:
            previous_state["consecutive_failures"] = 0
        
        # Send alert if:
        # 1. State changed (healthy -> unhealthy or vice versa)
        # 2. Still unhealthy after initial alert (every 5 minutes)
        should_alert = False
        if state_changed:
            should_alert = True
            logger.warning(f"{feed_name} health state changed: {was_healthy} -> {is_healthy}", reason=reason)
        elif not is_healthy:
            # Still unhealthy - send reminder every 5 minutes
            last_alert = previous_state["last_alert_time"]
            if last_alert:
                time_since_alert = (datetime.now() - last_alert).total_seconds()
                if time_since_alert >= 300:  # 5 minutes
                    should_alert = True
            else:
                # First time unhealthy
                should_alert = True
        
        # Update previous state
        previous_state["healthy"] = is_healthy
        
        # Send alert if needed
        if should_alert:
            previous_state["last_alert_time"] = datetime.now()
            await self._send_health_alert(feed_name, health_status, was_healthy, state_changed)

            # Optional auto-restart for WebSocket
            if (feed_name == "benzinga_websocket" and not is_healthy and settings.FEED_AUTORESTART_WEBSOCKET):
                try:
                    await self._attempt_websocket_restart(health_status)
                except Exception as e:
                    logger.error("Failed to auto-restart WebSocket", error=str(e))
        
        # Log health status
        if is_healthy:
            logger.debug(f"{feed_name} is healthy", reason=reason)
        else:
            logger.warning(f"{feed_name} is unhealthy", reason=reason, consecutive_failures=previous_state["consecutive_failures"])

    async def _attempt_websocket_restart(self, health_status: Dict[str, Any]):
        """Try to gracefully restart the Benzinga WebSocket service and notify Telegram."""
        try:
            processors = self.feed_manager.processors
            from ..models.base_models import NewsSource
            if NewsSource.BENZINGA_WEBSOCKET not in processors:
                return
            websocket_service = processors[NewsSource.BENZINGA_WEBSOCKET]
            logger.info("Attempting WebSocket restart...")
            # Stop current service
            try:
                websocket_service.stop()
            except Exception:
                pass
            # Give it a moment
            await asyncio.sleep(2)
            # Start again
            websocket_service.start()
            logger.info("WebSocket restart issued")
            if self.telegram_service and (self.telegram_service.enabled_1 or self.telegram_service.enabled_2):
                await self.telegram_service._send_message_to_all_bots("🔁 Benzinga WebSocket auto-restart attempted after health alert.")
        except Exception as e:
            logger.error("Error during WebSocket auto-restart", error=str(e))
    
    async def _send_health_alert(self, feed_name: str, health_status: Dict[str, Any], 
                                 was_healthy: Optional[bool], state_changed: bool):
        """Send Telegram alert about feed health."""
        if not self.telegram_service or not (self.telegram_service.enabled_1 or self.telegram_service.enabled_2):
            logger.warning("Telegram service not available for health alerts")
            return
        
        is_healthy = health_status.get("healthy", False)
        reason = health_status.get("reason", "Unknown")
        stats = health_status.get("stats", {})
        error = health_status.get("error")
        last_error = health_status.get("last_error")
        
        # Build alert message
        emoji = "✅" if is_healthy else "⚠️"
        status_text = "HEALTHY" if is_healthy else "UNHEALTHY"
        
        if state_changed:
            if is_healthy:
                message = f"{emoji} *Feed Recovered: {feed_name.replace('_', ' ').title()}*\n\n"
                message += f"Feed is now {status_text}.\n\n"
            else:
                message = f"{emoji} *Feed Disconnected: {feed_name.replace('_', ' ').title()}*\n\n"
                message += f"Feed status: {status_text}\n\n"
        else:
            message = f"{emoji} *Feed Health Alert: {feed_name.replace('_', ' ').title()}*\n\n"
            message += f"Status: {status_text}\n\n"
        
        message += f"*Reason:* {reason}\n\n"
        
        if error or last_error:
            error_msg = error or last_error
            message += f"*Error:* `{error_msg}`\n\n"
        
        # Add relevant stats
        if stats:
            message += "*Statistics:*\n"
            for key, value in list(stats.items())[:5]:  # Limit to first 5 stats
                if value is not None:
                    message += f"• {key}: `{value}`\n"
        
        # Add timestamp
        message += f"\n_Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}_"
        
        try:
            # Send to both bots if enabled
            if self.telegram_service.enabled_1 and self.telegram_service.bot_1:
                await self.telegram_service.bot_1.send_message(
                    chat_id=self.telegram_service.config_1["chat_id"],
                    text=message,
                    parse_mode="Markdown"
                )
            
            if self.telegram_service.enabled_2 and self.telegram_service.bot_2:
                await self.telegram_service.bot_2.send_message(
                    chat_id=self.telegram_service.config_2["chat_id"],
                    text=message,
                    parse_mode="Markdown"
                )
            
            logger.info(f"Health alert sent for {feed_name}", healthy=is_healthy)
            
        except Exception as e:
            logger.error(f"Failed to send health alert for {feed_name}", error=str(e))

