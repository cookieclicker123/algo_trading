"""
Infrastructure-specific models for storage operations.

These are infrastructure's own typed models - NOT domain models.
Infrastructure owns these completely.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Dict, Any, Optional


class ArticleStorageRequestData(BaseModel):
    """
    Infrastructure article storage request data model.
    
    Infrastructure's own representation - can change without affecting domain.
    """
    article_id: str = Field(..., description="Unique article identifier")
    article_data: Dict[str, Any] = Field(..., description="Serialized article data (dict)")
    stored_at: datetime = Field(..., description="When storage was requested")
    source: str = Field(..., description="Article source (e.g., 'benzinga')")
    published_at: datetime = Field(..., description="Article publication timestamp")


class ArticleStoredInfrastructureEvent(BaseModel):
    """
    Infrastructure event - article stored successfully.
    
    Published after article is persisted to file.
    """
    request_data: ArticleStorageRequestData = Field(..., description="Original request data")
    file_path: str = Field(..., description="Path to stored file (rolling window or archive)")
    stored_at: datetime = Field(..., description="When article was actually stored")
    is_archived: bool = Field(default=False, description="Whether article was archived (vs rolling window)")
    source: str = Field(default="storage_infrastructure", description="Event source")
    
    model_config = {"frozen": False}


class ArticleStorageFailedInfrastructureEvent(BaseModel):
    """Infrastructure event - article storage failed."""
    request_data: ArticleStorageRequestData = Field(..., description="Original request data")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When storage failed")
    source: str = Field(default="storage_infrastructure", description="Event source")
    
    model_config = {"frozen": False}


class AuditLogStorageRequestData(BaseModel):
    """
    Infrastructure audit log storage request data model.
    
    Infrastructure's own representation - can change without affecting domain.
    """
    article_id: str = Field(..., description="Article ID for audit entry")
    audit_data: Dict[str, Any] = Field(..., description="Serialized audit entry data (dict)")
    logged_at: datetime = Field(..., description="When audit log was requested")
    entry_type: str = Field(default="classification", description="Type of audit entry (classification, trade, etc.)")


class AuditLoggedInfrastructureEvent(BaseModel):
    """
    Infrastructure event - audit log stored successfully.
    
    Published after audit entry is persisted to file.
    """
    request_data: AuditLogStorageRequestData = Field(..., description="Original request data")
    file_path: str = Field(..., description="Path to stored audit file")
    logged_at: datetime = Field(..., description="When audit entry was actually stored")
    source: str = Field(default="storage_infrastructure", description="Event source")
    
    model_config = {"frozen": False}


class AuditLogStorageFailedInfrastructureEvent(BaseModel):
    """Infrastructure event - audit log storage failed."""
    request_data: AuditLogStorageRequestData = Field(..., description="Original request data")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When storage failed")
    source: str = Field(default="storage_infrastructure", description="Event source")
    
    model_config = {"frozen": False}


class ArticleFetchRequestData(BaseModel):
    """Infrastructure request to fetch an article by ID."""
    article_id: str = Field(..., description="Article ID to fetch")
    requested_at: datetime = Field(..., description="When fetch was requested")


class ArticleFetchedInfrastructureEvent(BaseModel):
    """Infrastructure event - article fetched successfully."""
    request_data: ArticleFetchRequestData = Field(..., description="Original request data")
    article_data: Optional[Dict[str, Any]] = Field(None, description="Article data if found, None if not found")
    fetched_at: datetime = Field(..., description="When article was fetched")
    source: str = Field(default="storage_infrastructure", description="Event source")
    
    model_config = {"frozen": False}

