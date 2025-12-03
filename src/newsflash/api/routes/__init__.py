"""
API routes module.

Exports all route routers for inclusion in the FastAPI app.
"""
from .health import router as health_router
from .storage import articles_router
from .websocket import feeds_router

__all__ = ["health_router", "articles_router", "feeds_router"]

