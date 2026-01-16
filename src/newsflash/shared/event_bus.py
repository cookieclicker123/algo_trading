"""
Async event bus for pub/sub communication.
Shared component - used across infrastructure, domain, and services layers.
"""
import asyncio
from typing import Dict, List, Callable, Any
from collections import defaultdict
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class AsyncEventBus:
    """
    Async event bus for pub/sub communication.

    Features:
    - Async event publishing (fire-and-forget, non-blocking)
    - Multiple subscribers per event type
    - Type-safe event handling
    - Error isolation (one subscriber failure doesn't affect others)

    IMPORTANT: publish() returns immediately without waiting for subscribers.
    This prevents queueing delays when many events arrive in quick succession.
    Subscribers run concurrently in the background.
    """

    def __init__(self):
        """Initialize the event bus."""
        self._subscribers: Dict[str, List[Callable]] = defaultdict[str, list](list)
        self._lock = asyncio.Lock()
        # Track background tasks to prevent garbage collection before completion
        self._background_tasks: set = set()
        logger.info("EventBus initialized")

    async def publish(self, event_type: str, event_data: Any) -> None:
        """
        Publish an event to all subscribers (fire-and-forget).

        Returns immediately after spawning subscriber tasks. Subscribers run
        concurrently in the background. This eliminates queueing delays when
        many events arrive quickly - each event's subscribers start immediately
        rather than waiting for previous events to fully complete.

        Args:
            event_type: Type/name of the event (e.g., "ArticleReceived")
            event_data: Event payload/data
        """
        async with self._lock:
            subscribers = self._subscribers[event_type].copy()

        if not subscribers:
            logger.debug(f"No subscribers for event type: {event_type}")
            return

        logger.debug(f"Publishing event: {event_type}", subscribers=len(subscribers))

        # Fire and forget - spawn tasks and return immediately
        # Tasks are tracked in _background_tasks to prevent GC before completion
        for subscriber in subscribers:
            task = asyncio.create_task(self._safe_call_subscriber(subscriber, event_type, event_data))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
    
    async def _safe_call_subscriber(self, subscriber: Callable, event_type: str, event_data: Any) -> None:
        """Safely call a subscriber, catching and logging any errors."""
        try:
            if asyncio.iscoroutinefunction(subscriber):
                await subscriber(event_type, event_data)
            else:
                subscriber(event_type, event_data)
        except Exception as e:
            logger.error(
                f"Error in subscriber for event {event_type}",
                error=str(e),
                subscriber=str(subscriber),
                exc_info=True
            )
    
    def subscribe(self, event_type: str, handler: Callable) -> None:
        """
        Subscribe to an event type.
        
        Args:
            event_type: Type/name of the event to subscribe to
            handler: Async function or callable to handle the event
                     Signature: async def handler(event_type: str, event_data: Any) -> None
        """
        # Add synchronously - lock will be acquired when needed
        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)
            logger.info(f"Subscribed to event: {event_type}")
        else:
            logger.warning(f"Handler already subscribed to {event_type}")
    
    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """
        Unsubscribe from an event type.
        
        Args:
            event_type: Type/name of the event
            handler: Handler to remove
        """
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)
            logger.info(f"Unsubscribed from event: {event_type}")
    
    def get_subscriber_count(self, event_type: str) -> int:
        """Get the number of subscribers for an event type."""
        return len(self._subscribers.get(event_type, []))

