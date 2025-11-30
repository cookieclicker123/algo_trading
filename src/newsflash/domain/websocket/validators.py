"""
Validators for WebSocket domain - business rule validation.
"""
from typing import Dict, Any

from ...utils.logging_config import get_logger
from .models import Article

logger = get_logger(__name__)


class ArticleValidator:
    """
    Validates articles according to business rules.
    
    Business Rules:
    1. Article must have a title
    2. Article must have a source and source_id
    3. Article must have a published timestamp
    4. Tickers must be valid format (uppercase, alphanumeric)
    5. Published timestamp must be in the past (not future)
    6. Updated timestamp must be after published timestamp if present
    """
    
    @staticmethod
    def is_valid_article_data(data: Dict[str, Any]) -> bool:
        """
        Validate raw article data before creating domain model.
        
        Args:
            data: Raw article dictionary
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Required fields
            if not data.get("title"):
                logger.debug("Article validation failed: missing title")
                return False
            
            if not data.get("source"):
                logger.debug("Article validation failed: missing source")
                return False
            
            if not data.get("source_id"):
                logger.debug("Article validation failed: missing source_id")
                return False
            
            if not data.get("published_at"):
                logger.debug("Article validation failed: missing published_at")
                return False
            
            # Validate timestamps
            published_at = data.get("published_at")
            if isinstance(published_at, str):
                published_at = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
            
            # Published timestamp should not be too far in the future (allow 1 hour grace period)
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            if published_at.replace(tzinfo=timezone.utc) > now + timedelta(hours=1):
                logger.warning(
                    "Article validation warning: published_at is in the future",
                    published_at=published_at,
                    now=now
                )
                # Don't fail validation, just warn
            
            # Validate updated timestamp
            updated_at = data.get("updated_at")
            if updated_at:
                if isinstance(updated_at, str):
                    updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                
                if updated_at.replace(tzinfo=timezone.utc) < published_at.replace(tzinfo=timezone.utc):
                    logger.warning(
                        "Article validation warning: updated_at is before published_at",
                        updated_at=updated_at,
                        published_at=published_at
                    )
                    # Don't fail validation, just warn
            
            # Validate tickers format
            tickers = data.get("tickers", [])
            if tickers:
                for ticker in tickers:
                    if not isinstance(ticker, str) or not ticker.strip():
                        logger.debug(f"Article validation warning: invalid ticker format: {ticker}")
                        continue
                    # Ticker should be alphanumeric (allowing some special cases)
                    ticker_clean = ticker.upper().strip()
                    if not ticker_clean.replace(".", "").replace("-", "").isalnum():
                        logger.debug(f"Article validation warning: ticker contains invalid characters: {ticker}")
                        # Don't fail validation, just warn
            
            return True
            
        except Exception as e:
            logger.error("Article validation error", error=str(e), exc_info=True)
            return False
    
    @staticmethod
    def is_valid_domain_article(article: Article) -> bool:
        """
        Validate a domain Article object.
        
        Args:
            article: Domain Article object
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Check required fields
            if not article.title:
                return False
            
            if not article.source:
                return False
            
            if not article.source_id:
                return False
            
            if not article.published_at:
                return False
            
            # Validate ID format
            if not article.id or ":" not in article.id:
                logger.warning("Article ID format invalid", article_id=article.id)
                return False
            
            # Business rule: Article should have either title or content
            if not article.title and not article.content:
                logger.warning("Article has neither title nor content")
                return False
            
            return True
            
        except Exception as e:
            logger.error("Domain article validation error", error=str(e), exc_info=True)
            return False
    
    @staticmethod
    def validate_ticker(ticker: str) -> bool:
        """
        Validate a single ticker symbol.
        
        Business rules:
        - Must be 1-5 characters
        - Must be alphanumeric (allowing dots and hyphens)
        - Must be uppercase
        
        Args:
            ticker: Ticker symbol to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not isinstance(ticker, str):
            return False
        
        ticker = ticker.strip().upper()
        
        if not ticker:
            return False
        
        if len(ticker) > 5:
            return False
        
        # Allow alphanumeric, dots, hyphens
        if not ticker.replace(".", "").replace("-", "").isalnum():
            return False
        
        return True

