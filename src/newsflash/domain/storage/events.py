"""
Domain events for storage - business events published by domain layer.

These use domain models directly - fully typed, not Dict[str, Any].
"""
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

from .models import StoredArticle, AuditEntry


class ArticleStorageRequestedDomainEvent(BaseModel):
    """
    Domain event - article storage requested.
    
    Uses domain StoredArticle model directly - fully typed, validated.
    """
    article: StoredArticle = Field(..., description="Domain StoredArticle model (validated, immutable)")
    requested_at: datetime = Field(..., description="When storage was requested")
    source: str = Field(default="domain.storage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class ArticleStoredDomainEvent(BaseModel):
    """
    Domain event - article has been stored.
    
    Published when article is successfully persisted.
    """
    article_id: str = Field(..., description="Article ID that was stored")
    stored_at: datetime = Field(..., description="When article was stored")
    file_path: str = Field(..., description="Path to stored file")
    is_archived: bool = Field(default=False, description="Whether article was archived")
    source: str = Field(default="domain.storage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class ArticleStorageFailedDomainEvent(BaseModel):
    """
    Domain event - article storage failed.
    
    Published when article cannot be stored.
    """
    article_id: str = Field(..., description="Article ID that failed to store")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When storage failed")
    source: str = Field(default="domain.storage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class AuditLogRequestedDomainEvent(BaseModel):
    """
    Domain event - audit log storage requested.
    
    Uses domain AuditEntry model directly - fully typed, validated.
    """
    entry: AuditEntry = Field(..., description="Domain AuditEntry model (validated, immutable)")
    requested_at: datetime = Field(..., description="When audit log was requested")
    source: str = Field(default="domain.storage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class AuditLoggedDomainEvent(BaseModel):
    """
    Domain event - audit entry has been logged.
    
    Published when audit entry is successfully persisted.
    """
    article_id: str = Field(..., description="Article ID that was logged")
    logged_at: datetime = Field(..., description="When audit entry was logged")
    file_path: str = Field(..., description="Path to stored audit file")
    source: str = Field(default="domain.storage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class AuditLogStorageFailedDomainEvent(BaseModel):
    """
    Domain event - audit log storage failed.
    
    Published when audit entry cannot be stored.
    """
    article_id: str = Field(..., description="Article ID that failed to log")
    error: str = Field(..., description="Error message")
    failed_at: datetime = Field(..., description="When storage failed")
    source: str = Field(default="domain.storage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class ArticleFetchRequestedDomainEvent(BaseModel):
    """
    Domain event - article fetch requested.
    
    Published when an article needs to be fetched from storage.
    """
    article_id: str = Field(..., description="Article ID to fetch")
    requested_at: datetime = Field(..., description="When fetch was requested")
    source: str = Field(default="domain.storage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable


class ArticleFetchedDomainEvent(BaseModel):
    """
    Domain event - article has been fetched.
    
    Published when article is successfully fetched from storage.
    """
    article_id: str = Field(..., description="Article ID that was fetched")
    article: Optional[StoredArticle] = Field(None, description="Domain StoredArticle model if found, None if not found")
    fetched_at: datetime = Field(..., description="When article was fetched")
    source: str = Field(default="domain.storage", description="Event source")
    
    model_config = {"frozen": True}  # Immutable

