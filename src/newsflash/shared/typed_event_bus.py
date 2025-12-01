"""
Typed helpers for subscribing to events on the AsyncEventBus.

These helpers keep type reconstruction (Pydantic) at the boundary so
services and use cases can work directly with typed domain events.
"""
from typing import Type, TypeVar, Awaitable, Callable

from pydantic import BaseModel

from .event_bus import get_event_bus

TEvent = TypeVar("TEvent", bound=BaseModel)


def subscribe_typed(
    event_type: str,
    model: Type[TEvent],
    handler: Callable[[TEvent], Awaitable[None]],
) -> None:
    """
    Subscribe a handler that receives a typed Pydantic event model.

    Args:
        event_type: Event name (e.g. "Domain.ArticleClassified")
        model: Pydantic model class for the event
        handler: Async function taking a single typed event instance
    """
    event_bus = get_event_bus()

    async def _wrapper(raw_event_type: str, event_data: dict) -> None:
        event = model(**event_data)
        await handler(event)

    event_bus.subscribe(event_type, _wrapper)


