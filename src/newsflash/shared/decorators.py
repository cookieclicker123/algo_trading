"""
Shared decorators for consistent patterns across the codebase.

These decorators eliminate code duplication and ensure consistent behavior.
"""
from functools import wraps
from typing import Callable, Optional, Any
import asyncio

from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def handle_errors(
    log_context: Optional[str] = None,
    publish_error_event: bool = False,
    error_event_publisher: Optional[Callable] = None
) -> Callable:
    """
    Decorator for consistent error handling across the codebase.
    
    Eliminates duplicated try/except/log patterns (100+ instances).
    
    Args:
        log_context: Optional context string for logging (e.g., "BrokerageDomainListener: Error handling trade request")
        publish_error_event: Whether to publish an error event on failure
        error_event_publisher: Optional async function to publish error event (receives exception and context)
    
    Usage:
        @handle_errors(log_context="MyService: Error processing request")
        async def my_method(self, ...):
            # No try/except needed - decorator handles it
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                context = log_context or f"Error in {func.__name__}"
                logger.error(
                    context,
                    error=str(e),
                    exc_info=True
                )
                
                if publish_error_event and error_event_publisher:
                    try:
                        await error_event_publisher(e, context, *args, **kwargs)
                    except Exception as pub_error:
                        logger.error(
                            f"Failed to publish error event for {context}",
                            error=str(pub_error)
                        )
                
                # Re-raise to maintain existing behavior
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                context = log_context or f"Error in {func.__name__}"
                logger.error(
                    context,
                    error=str(e),
                    exc_info=True
                )
                
                if publish_error_event and error_event_publisher:
                    # For sync functions, we can't await, so log warning
                    logger.warning(
                        f"Cannot publish error event from sync function {func.__name__}",
                        error=str(e)
                    )
                
                raise
        
        # Return appropriate wrapper based on whether function is async
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

