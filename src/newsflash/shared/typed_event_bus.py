"""
Typed helpers for subscribing to events on the AsyncEventBus.

These helpers keep type reconstruction (Pydantic) at the boundary so
services and use cases can work directly with typed domain events.
"""
from typing import Type, TypeVar, Awaitable, Callable

from pydantic import BaseModel

from .event_bus import AsyncEventBus

TEvent = TypeVar("TEvent", bound=BaseModel)


def subscribe_typed(
    event_bus: AsyncEventBus,
    event_type: str,
    model: Type[TEvent],
    handler: Callable[[TEvent], Awaitable[None]],
) -> Callable:
    """
    Subscribe a handler that receives a typed Pydantic event model.

    Args:
        event_bus: Event bus instance to subscribe to
        event_type: Event name (e.g. "Domain.ArticleClassified")
        model: Pydantic model class for the event
        handler: Async function taking a single typed event instance
        
    Returns:
        The wrapper function that was subscribed (for unsubscribing later)
    """
    async def _wrapper(raw_event_type: str, event_data: dict) -> None:
        event = model(**event_data)
        await handler(event)

    event_bus.subscribe(event_type, _wrapper)
    return _wrapper


