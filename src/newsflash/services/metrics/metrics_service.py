"""
Metrics service - aggregates statistics from events.

STATELESS PRINCIPLE:
- Services publish events (no mutation)
- MetricsService subscribes to events and aggregates
- Statistics are derived from events, not mutated in services

NOTE: No locks needed - Python's GIL makes dict operations atomic,
and all handlers run in the same async event loop (no threading).
"""
from datetime import datetime
from typing import Dict, Any, Final

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType, InfrastructureEventType

logger = get_logger(__name__)


class MetricsService:
    """
    Service that aggregates statistics from events.
    
    STATELESS DESIGN:
    - Services don't mutate stats dictionaries
    - Services publish events
    - This service subscribes and aggregates
    - Statistics are derived, not mutated
    
    Responsibilities:
    - Subscribe to relevant domain/infrastructure events
    - Aggregate statistics from events
    - Provide get_stats() methods for querying
    """
    
    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize metrics service.
        
        Args:
            event_bus: Event bus instance for subscribing to events
        """
        self.event_bus: Final[AsyncEventBus] = event_bus

        # Statistics aggregated from events
        # Classification metrics
        self._classification_stats = {
            "classifications_requested": 0,
            "classifications_completed": 0,
            "classifications_failed": 0,
            "last_classification_time": None,
        }
        
        # Notification metrics
        self._notification_stats = {
            "notifications_requested": 0,
            "notifications_sent": 0,
            "notifications_failed": 0,
            "last_notification_time": None,
        }
        
        # WebSocket metrics
        self._websocket_stats = {
            "messages_received": 0,
            "articles_received": 0,
            "connection_attempts": 0,
            "ping_sent_count": 0,
            "pong_received_count": 0,
            "missed_pongs": 0,
            "last_message_time": None,
            "last_ping_sent": None,
            "last_pong_received": None,
            "connection_verified_at": None,
            "last_connection_check": None,
            "is_connected": False,
            "last_error": None,
        }
        
        # Brokerage connection metrics
        self._brokerage_connection_stats = {
            "connection_attempts": 0,
            "reconnect_attempts": 0,  # Tracked via reconnect events (if published) or operational
            "last_connection_time": None,
            "last_disconnection_time": None,
            "last_keepalive_time": None,  # Operational metric (not from events)
            "is_connected": False,
        }
        
        logger.info("MetricsService initialized - will subscribe to events on start()")
    
    async def start(self) -> None:
        """Start metrics service - subscribe to events."""
        # Subscribe to classification events
        self.event_bus.subscribe(
            InfrastructureEventType.CLASSIFICATION_REQUESTED,
            self._handle_classification_requested
        )
        self.event_bus.subscribe(
            InfrastructureEventType.CLASSIFICATION_COMPLETED,
            self._handle_classification_completed
        )
        self.event_bus.subscribe(
            InfrastructureEventType.CLASSIFICATION_FAILED,
            self._handle_classification_failed
        )
        
        # Subscribe to notification events
        self.event_bus.subscribe(
            InfrastructureEventType.NOTIFICATION_SEND_REQUESTED,
            self._handle_notification_requested
        )
        self.event_bus.subscribe(
            InfrastructureEventType.NOTIFICATION_SENT,
            self._handle_notification_sent
        )
        self.event_bus.subscribe(
            InfrastructureEventType.NOTIFICATION_FAILED,
            self._handle_notification_failed
        )
        
        # Subscribe to WebSocket events
        self.event_bus.subscribe(
            InfrastructureEventType.ARTICLE_RECEIVED,
            self._handle_websocket_article_received
        )
        self.event_bus.subscribe(
            InfrastructureEventType.WEBSOCKET_CONNECTED,
            self._handle_websocket_connected
        )
        self.event_bus.subscribe(
            InfrastructureEventType.WEBSOCKET_DISCONNECTED,
            self._handle_websocket_disconnected
        )
        self.event_bus.subscribe(
            InfrastructureEventType.WEBSOCKET_ERROR,
            self._handle_websocket_error
        )
        
        # Subscribe to brokerage connection events
        self.event_bus.subscribe(
            InfrastructureEventType.CONNECTION_STATUS_CHANGED,
            self._handle_connection_status_changed
        )
        
        logger.info("MetricsService started - subscribed to events")
    
    async def stop(self) -> None:
        """Stop metrics service - unsubscribe from events."""
        self.event_bus.unsubscribe(
            InfrastructureEventType.CLASSIFICATION_REQUESTED,
            self._handle_classification_requested
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.CLASSIFICATION_COMPLETED,
            self._handle_classification_completed
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.CLASSIFICATION_FAILED,
            self._handle_classification_failed
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.NOTIFICATION_SEND_REQUESTED,
            self._handle_notification_requested
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.NOTIFICATION_SENT,
            self._handle_notification_sent
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.NOTIFICATION_FAILED,
            self._handle_notification_failed
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.ARTICLE_RECEIVED,
            self._handle_websocket_article_received
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.WEBSOCKET_CONNECTED,
            self._handle_websocket_connected
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.WEBSOCKET_DISCONNECTED,
            self._handle_websocket_disconnected
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.WEBSOCKET_ERROR,
            self._handle_websocket_error
        )
        self.event_bus.unsubscribe(
            InfrastructureEventType.CONNECTION_STATUS_CHANGED,
            self._handle_connection_status_changed
        )
        
        logger.info("MetricsService stopped")
    
    # Event handlers - aggregate statistics from events
    
    async def _handle_classification_requested(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle classification requested event."""
        self._classification_stats["classifications_requested"] += 1

    async def _handle_classification_completed(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle classification completed event."""
        self._classification_stats["classifications_completed"] += 1
        self._classification_stats["last_classification_time"] = datetime.now().isoformat()

    async def _handle_classification_failed(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle classification failed event."""
        self._classification_stats["classifications_failed"] += 1

    async def _handle_notification_requested(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle notification requested event."""
        self._notification_stats["notifications_requested"] += 1
        self._notification_stats["last_notification_time"] = datetime.now().isoformat()

    async def _handle_notification_sent(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle notification sent event."""
        self._notification_stats["notifications_sent"] += 1

    async def _handle_notification_failed(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle notification failed event."""
        self._notification_stats["notifications_failed"] += 1

    async def _handle_websocket_article_received(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocket article received event."""
        self._websocket_stats["messages_received"] += 1
        self._websocket_stats["articles_received"] += 1
        self._websocket_stats["last_message_time"] = datetime.now().isoformat()

    async def _handle_websocket_connected(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocket connected event."""
        self._websocket_stats["is_connected"] = True
        self._websocket_stats["connection_verified_at"] = datetime.now().isoformat()

    async def _handle_websocket_disconnected(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocket disconnected event."""
        self._websocket_stats["is_connected"] = False

    async def _handle_websocket_error(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocket error event."""
        error_msg = event_data.get("error", "Unknown error")
        self._websocket_stats["last_error"] = str(error_msg)

    async def _handle_connection_status_changed(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle brokerage connection status changed event."""
        is_connected = event_data.get("is_connected", False)
        self._brokerage_connection_stats["is_connected"] = is_connected
        if is_connected:
            self._brokerage_connection_stats["last_connection_time"] = datetime.now().isoformat()
            self._brokerage_connection_stats["connection_attempts"] += 1
        else:
            self._brokerage_connection_stats["last_disconnection_time"] = datetime.now().isoformat()
    
    # Public API for querying statistics
    
    def get_classification_stats(self, model: str, enabled: bool, has_api_key: bool) -> Dict[str, Any]:
        """
        Get classification statistics.

        Args:
            model: Classification model name
            enabled: Whether classification is enabled
            has_api_key: Whether API key is configured

        Returns:
            Dictionary with classification statistics
        """
        return {
            **self._classification_stats,
            "model": model,
            "is_enabled": enabled,
            "has_api_key": has_api_key,
        }

    def get_notification_stats(self, enabled: bool) -> Dict[str, Any]:
        """
        Get notification statistics.

        Args:
            enabled: Whether notifications are enabled

        Returns:
            Dictionary with notification statistics
        """
        return {
            **self._notification_stats,
            "is_enabled": enabled,
        }

    def get_websocket_stats(self) -> Dict[str, Any]:
        """
        Get WebSocket statistics.

        Returns:
            Dictionary with WebSocket statistics
        """
        return self._websocket_stats.copy()

    def get_brokerage_connection_stats(self) -> Dict[str, Any]:
        """
        Get brokerage connection statistics.

        Returns:
            Dictionary with connection statistics
        """
        return self._brokerage_connection_stats.copy()

