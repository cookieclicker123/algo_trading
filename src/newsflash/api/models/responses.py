"""
Pydantic response models for API endpoints.

These models provide type-safe responses and automatic OpenAPI schema generation.
"""
from typing import List, Dict, Any, Literal
from pydantic import BaseModel, Field


class RootResponse(BaseModel):
    """Root endpoint response."""
    service: str = Field(..., description="Service name")
    status: Literal["running"] = Field(..., description="Service status")
    version: str = Field(..., description="Service version")
    sources: List[str] = Field(..., description="Available data sources")
    healthy: bool = Field(..., description="Whether services are healthy")
    
    model_config = {"json_schema_extra": {"example": {
        "service": "NewsFlash Trading System",
        "status": "running",
        "version": "2.0.0",
        "sources": ["benzinga_websocket"],
        "healthy": True
    }}}


class HealthResponse(BaseModel):
    """Health check endpoint response."""
    status: Literal["healthy"] = Field(..., description="Health status")
    sources: Dict[str, bool] = Field(..., description="Source availability status")
    available_sources: List[str] = Field(..., description="List of available sources")
    
    model_config = {"json_schema_extra": {"example": {
        "status": "healthy",
        "sources": {"benzinga_websocket": True},
        "available_sources": ["benzinga_websocket"]
    }}}


class StatsResponse(BaseModel):
    """System statistics endpoint response."""
    stats: Dict[str, Any] = Field(..., description="Service statistics")
    service_status: Literal["running"] = Field(..., description="Overall service status")
    
    model_config = {"json_schema_extra": {"example": {
        "stats": {
            "feed_manager": {},
            "storage_query_service": "Available",
            "telegram": {"enabled_1": True, "enabled_2": False}
        },
        "service_status": "running"
    }}}


class RecentArticlesResponse(BaseModel):
    """Recent articles endpoint response."""
    articles: List[Dict[str, Any]] = Field(..., description="List of recent articles")
    count: int = Field(..., description="Number of articles returned")
    hours: int = Field(..., description="Hours of history queried")
    
    model_config = {"json_schema_extra": {"example": {
        "articles": [],
        "count": 0,
        "hours": 1
    }}}


class ArchivedArticlesResponse(BaseModel):
    """Archived articles endpoint response."""
    articles: List[Dict[str, Any]] = Field(..., description="List of archived articles")
    count: int = Field(..., description="Number of articles returned")
    date: str = Field(..., description="Date queried (YYYY-MM-DD)")
    
    model_config = {"json_schema_extra": {"example": {
        "articles": [],
        "count": 0,
        "date": "2025-12-01"
    }}}


class ArchiveStatsResponse(BaseModel):
    """Archive statistics endpoint response."""
    # This is flexible since archive_stats can vary
    # We'll use Dict[str, Any] for now, can be made more specific later
    stats: Dict[str, Any] = Field(..., description="Archive statistics")
    
    model_config = {"json_schema_extra": {"example": {
        "stats": {
            "total_archived_dates": 10,
            "total_archived_files": 10
        }
    }}}


class FeedStatusResponse(BaseModel):
    """Feed control endpoint response."""
    message: str = Field(..., description="Status message")
    status: Literal["running", "started", "stopped"] = Field(..., description="Feed status")
    
    model_config = {"json_schema_extra": {"example": {
        "message": "Feeds started",
        "status": "started"
    }}}

