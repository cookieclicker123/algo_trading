"""
Logging configuration for the news trading system.
Gracefully degrades if structlog is not available.
"""
import logging
import sys
from typing import Any

try:
    import structlog  # type: ignore
    _HAS_STRUCTLOG = True
except Exception:
    structlog = None  # type: ignore
    _HAS_STRUCTLOG = False


def setup_logging(log_level: str = "INFO") -> None:
    """
    Set up structured logging for the news trading system.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )
    
    if _HAS_STRUCTLOG:
        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.processors.JSONRenderer(),
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )


def get_logger(name: str) -> Any:
    """Get a configured logger instance (structlog if available, else stdlib logger)."""
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)
