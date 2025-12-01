"""
Health monitoring service - subscribes to health events.
NO direct infrastructure access - pure event subscription.
"""
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType

logger = get_logger(__name__)


class FeedHealthMonitor:
    """
    Monitors health of news feeds via domain events.
    
    Responsibilities:
    - Subscribes to domain health status events (from domain layer)
    - Sends Telegram alerts on state changes
    - Tracks previous state to avoid spam
    
    Does NOT:
    - Access infrastructure directly
    - Subscribe to infrastructure events (domain listener bridges those)
    - Poll stats
    - Know about WebSocket implementation details
    """
    
    def __init__(self, event_bus: AsyncEventBus, telegram_service):
        """
        Initialize health monitor.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            telegram_service: Telegram service for sending alerts
        """
        self.telegram_service = telegram_service
        self.is_running = False
        
        # Event bus for subscribing to events
        self.event_bus = event_bus
        
        # Track previous state for each feed
        self.previous_state: Dict[str, Dict[str, Any]] = {
            "benzinga_websocket": {
                "healthy": None,
                "last_alert_time": None,
                "consecutive_failures": 0
            }
        }
        
        # Subscribe to WebSocket events
        self._subscribe_to_websocket_events()
        
        # Subscribe to brokerage connection events
        self._subscribe_to_brokerage_events()
        
        logger.info("FeedHealthMonitor initialized")
    
    def _subscribe_to_websocket_events(self):
        """Subscribe to domain WebSocket events for real-time health monitoring."""
        # Subscribe to domain health status events (not infrastructure events)
        self.event_bus.subscribe(DomainEventType.WEBSOCKET_HEALTH_STATUS, self._handle_websocket_health_status)
        self.event_bus.subscribe(DomainEventType.WEBSOCKET_ERROR, self._handle_websocket_error)
        self.event_bus.subscribe(DomainEventType.WEBSOCKET_RATE_LIMIT, self._handle_websocket_rate_limit)
        self.event_bus.subscribe(DomainEventType.WEBSOCKET_DISCONNECTED, self._handle_websocket_disconnected)
        self.event_bus.subscribe(DomainEventType.WEBSOCKET_CONNECTED, self._handle_websocket_connected)
        
        logger.info("Subscribed to domain WebSocket events for health monitoring")
    
    def _subscribe_to_brokerage_events(self):
        """Subscribe to domain brokerage connection events for Telegram notifications."""
        self.event_bus.subscribe(DomainEventType.BROKERAGE_CONNECTION_STATUS, self._handle_brokerage_connection_status)
        self.event_bus.subscribe(DomainEventType.BROKERAGE_HEALTH_STATUS, self._handle_brokerage_health_status)
        logger.info("Subscribed to domain brokerage connection and health events")
    
    async def _handle_brokerage_connection_status(self, event_type: str, event_data: dict) -> None:
        """Handle Domain.BrokerageConnectionStatus event."""
        try:
            # Reconstruct typed domain event
            from ...domain.brokerage.events import BrokerageConnectionStatusDomainEvent
            domain_event = BrokerageConnectionStatusDomainEvent(**event_data)
            
            # Send Telegram notification
            if self.telegram_service and (self.telegram_service.enabled_1 or self.telegram_service.enabled_2):
                emoji = "✅" if domain_event.is_connected else "❌"
                mode = "Paper Trading" if domain_event.paper_trading else "Live Trading"
                status = "connected and verified" if domain_event.is_connected else "disconnected"
                
                message = f"{emoji} IB Gateway {status}\n\n"
                message += f"Mode: {mode}\n"
                if domain_event.reason:
                    message += f"Reason: {domain_event.reason}\n"
                
                try:
                    # Send to all enabled bots
                    await self.telegram_service._send_message_to_all_bots(message)
                except Exception as e:
                    logger.error("Failed to send brokerage connection status to Telegram", error=str(e))
            
            logger.info(
                "Domain brokerage connection status changed",
                is_connected=domain_event.is_connected,
                paper_trading=domain_event.paper_trading,
                reason=domain_event.reason
            )
        
        except Exception as e:
            logger.error("Error handling domain BrokerageConnectionStatus event", error=str(e), exc_info=True)
    
    async def _handle_brokerage_health_status(self, event_type: str, event_data: dict) -> None:
        """Handle Domain.BrokerageHealthStatus event."""
        try:
            # Reconstruct typed domain event
            from ...domain.brokerage.events import BrokerageHealthStatusDomainEvent
            domain_event = BrokerageHealthStatusDomainEvent(**event_data)
            
            logger.debug(
                "Domain brokerage health status event received",
                is_healthy=domain_event.is_healthy,
                is_connected=domain_event.is_connected,
                reason=domain_event.reason
            )
            
            # Process health check for brokerage
            health_status = {
                "healthy": domain_event.is_healthy,
                "reason": domain_event.reason,
                "is_connected": domain_event.is_connected,
                "is_critical": domain_event.is_critical,
                "stats": domain_event.stats
            }
            
            # Track brokerage state (add if not exists)
            if "brokerage" not in self.previous_state:
                self.previous_state["brokerage"] = {
                    "healthy": None,
                    "last_alert_time": None,
                    "consecutive_failures": 0
                }
            
            await self._process_health_check("brokerage", health_status)
        
        except Exception as e:
            logger.error("Error handling domain BrokerageHealthStatus event", error=str(e), exc_info=True)
    
    async def _handle_websocket_error(self, event_type: str, event_data: dict) -> None:
        """Handle Domain.WebSocketError event."""
        try:
            # Reconstruct typed domain event
            from ...domain.websocket.events import WebSocketErrorDomainEvent
            domain_event = WebSocketErrorDomainEvent(**event_data)
            
            logger.warning("Domain WebSocket error event received", error=domain_event.error, is_rate_limit=domain_event.is_rate_limit)
            
            # Update health state
            if not domain_event.is_rate_limit:
                # Non-rate-limit errors - mark as unhealthy
                self.previous_state["benzinga_websocket"]["healthy"] = False
                
                # Send alert if needed
                health_status = {
                    "healthy": False,
                    "reason": f"WebSocket error: {domain_event.error}",
                    "error": domain_event.error
                }
                await self._process_health_check("benzinga_websocket", health_status)
        
        except Exception as e:
            logger.error("Error handling domain WebSocketError event", error=str(e), exc_info=True)
    
    async def _handle_websocket_rate_limit(self, event_type: str, event_data: dict) -> None:
        """Handle Domain.WebSocketRateLimit event - critical event."""
        try:
            # Reconstruct typed domain event
            from ...domain.websocket.events import WebSocketRateLimitDomainEvent
            domain_event = WebSocketRateLimitDomainEvent(**event_data)
            
            logger.error("Domain WebSocket rate limit event received", message=domain_event.message, occurred_at=domain_event.occurred_at)
            
            # Mark as unhealthy
            self.previous_state["benzinga_websocket"]["healthy"] = False
            
            # Send critical alert
            health_status = {
                "healthy": False,
                "reason": f"RATE LIMIT HIT: {domain_event.message}",
                "error": "429 Rate Limit",
                "is_critical": True
            }
            await self._process_health_check("benzinga_websocket", health_status)
            
            # Also send Telegram alert immediately
            if self.telegram_service and (self.telegram_service.enabled_1 or self.telegram_service.enabled_2):
                alert_msg = f"🚨 *CRITICAL: WebSocket Rate Limit Hit*\n\n{domain_event.message}\n\nConnection will not auto-reconnect."
                try:
                    await self.telegram_service._send_message_to_all_bots(alert_msg)
                except Exception as e:
                    logger.error("Failed to send rate limit alert to Telegram", error=str(e))
        
        except Exception as e:
            logger.error("Error handling domain WebSocketRateLimit event", error=str(e), exc_info=True)
    
    async def _handle_websocket_disconnected(self, event_type: str, event_data: dict) -> None:
        """Handle Domain.WebSocketDisconnected event."""
        try:
            # Reconstruct typed domain event
            from ...domain.websocket.events import WebSocketDisconnectedDomainEvent
            domain_event = WebSocketDisconnectedDomainEvent(**event_data)
            
            logger.warning("Domain WebSocket disconnected event received", reason=domain_event.reason, disconnected_at=domain_event.disconnected_at)
            
            # Mark as unhealthy
            self.previous_state["benzinga_websocket"]["healthy"] = False
            
            # Send alert
            health_status = {
                "healthy": False,
                "reason": f"WebSocket disconnected: {domain_event.reason or 'Unknown'}",
                "disconnected_at": domain_event.disconnected_at
            }
            await self._process_health_check("benzinga_websocket", health_status)
        
        except Exception as e:
            logger.error("Error handling domain WebSocketDisconnected event", error=str(e), exc_info=True)
    
    async def _handle_websocket_connected(self, event_type: str, event_data: dict) -> None:
        """Handle Domain.WebSocketConnected event."""
        try:
            # Reconstruct typed domain event
            from ...domain.websocket.events import WebSocketConnectedDomainEvent
            domain_event = WebSocketConnectedDomainEvent(**event_data)
            
            logger.info("Domain WebSocket connected event received", connected_at=domain_event.connected_at)
            
            # Mark as healthy
            self.previous_state["benzinga_websocket"]["healthy"] = True
            self.previous_state["benzinga_websocket"]["consecutive_failures"] = 0
        
        except Exception as e:
            logger.error("Error handling domain WebSocketConnected event", error=str(e), exc_info=True)
    
    async def start(self):
        """Start the health monitor (just waits - all monitoring via events)."""
        if self.is_running:
            logger.warning("Health monitor already running")
            return
        
        self.is_running = True
        logger.info("Starting feed health monitor (event-driven)")
        
        try:
            # Health monitor just waits - all health checks come via events
            while self.is_running:
                await asyncio.sleep(1)
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
        
        # Unsubscribe from domain events
        self.event_bus.unsubscribe(DomainEventType.WEBSOCKET_HEALTH_STATUS, self._handle_websocket_health_status)
        self.event_bus.unsubscribe(DomainEventType.WEBSOCKET_ERROR, self._handle_websocket_error)
        self.event_bus.unsubscribe(DomainEventType.WEBSOCKET_RATE_LIMIT, self._handle_websocket_rate_limit)
        self.event_bus.unsubscribe(DomainEventType.WEBSOCKET_DISCONNECTED, self._handle_websocket_disconnected)
        self.event_bus.unsubscribe(DomainEventType.WEBSOCKET_CONNECTED, self._handle_websocket_connected)
        self.event_bus.unsubscribe(DomainEventType.BROKERAGE_CONNECTION_STATUS, self._handle_brokerage_connection_status)
        self.event_bus.unsubscribe(DomainEventType.BROKERAGE_HEALTH_STATUS, self._handle_brokerage_health_status)
        
        logger.info("Stopping feed health monitor")
    
    async def _handle_websocket_health_status(self, event_type: str, event_data: dict) -> None:
        """Handle Domain.WebSocketHealthStatus event."""
        try:
            # Reconstruct typed domain event
            from ...domain.websocket.events import WebSocketHealthStatusDomainEvent
            domain_event = WebSocketHealthStatusDomainEvent(**event_data)
            
            # Process health check
            health_status = {
                "healthy": domain_event.is_healthy,
                "reason": domain_event.reason,
                "status": domain_event.status,
                **domain_event.details
            }
            await self._process_health_check("benzinga_websocket", health_status)
        
        except Exception as e:
            logger.error("Error handling domain WebSocketHealthStatus event", error=str(e), exc_info=True)
    
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

            # If needed, publish a restart event that infrastructure can subscribe to
        
        # Log health status
        if is_healthy:
            logger.info(f"{feed_name} is healthy", reason=reason)
        else:
            logger.warning(f"{feed_name} is unhealthy", reason=reason, consecutive_failures=previous_state["consecutive_failures"])

    
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

