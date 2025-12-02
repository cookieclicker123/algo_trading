"""
FastAPI application for the news trading system.
"""
from fastapi import FastAPI
from .lifespan import lifespan
from .routes import health_router, articles_router, feeds_router


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Routes are organized into modules:
    - health: Root, health check, stats
    - articles: Article queries
    - feeds: Feed control
    """
    app = FastAPI(
        title="NewsFlash Trading System",
        description="Real-time news polling and processing system",
        version="2.0.0",
        lifespan=lifespan
    )
    
    # Include route routers
    app.include_router(health_router)
    app.include_router(articles_router)
    app.include_router(feeds_router)
    
    return app
