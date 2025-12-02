"""
API routes module.

Exports all route routers for inclusion in the FastAPI app.
"""
from .health import router as health_router
from .articles import router as articles_router
from .feeds import router as feeds_router

__all__ = ["health_router", "articles_router", "feeds_router"]

