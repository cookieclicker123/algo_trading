"""
Mappers for WebSocket domain - transform infrastructure models to domain models.
"""
from typing import Dict, Any, Optional
from datetime import datetime

from ...models.base_models import StandardizedArticle
from ...infra.websocket.infrastructure_models import InfrastructureArticleData
from ...utils.logging_config import get_logger
from .models import Article, ArticleSource
from .validators import ArticleValidator

logger = get_logger(__name__)


class ArticleMapper:
    """
    Maps infrastructure article models to domain Article models.
    
    Responsibilities:
    - Transform StandardizedArticle (infra model) → Article (domain model)
    - Transform raw dictionaries → Article (domain model)
    - Handle source mapping (benzinga_websocket → benzinga)
    """
    
    @staticmethod
    def from_standardized_article(standardized: StandardizedArticle) -> Article:
        """
        Map StandardizedArticle (infrastructure model) to Article (domain model).
        
        Args:
            standardized: StandardizedArticle from infrastructure layer
            
        Returns:
            Article domain model
        """
        # Map source
        source = ArticleMapper._map_source(standardized.source.value)
        
        # Build article ID
        article_id = f"{source.value}:{standardized.source_id}"
        
        # Create domain model
        return Article(
            id=article_id,
            source=source,
            source_id=standardized.source_id,
            title=standardized.title,
            content=standardized.content,
            summary=standardized.summary,
            author=standardized.author,
            published_at=standardized.published,
            updated_at=standardized.updated,
            url=standardized.url,
            tickers=frozenset(standardized.tickers) if standardized.tickers else frozenset(),
            tags=frozenset(standardized.tags) if standardized.tags else frozenset(),
            categories=frozenset(standardized.categories) if standardized.categories else frozenset(),
        )
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> Optional[Article]:
        """
        Map raw dictionary to Article domain model.
        
        Args:
            data: Raw article dictionary
            
        Returns:
            Article domain model, or None if invalid
        """
        try:
            # Validate first
            if not ArticleValidator.is_valid_article_data(data):
                logger.warning("Cannot map invalid article data", data_keys=list(data.keys()))
                return None
            
            # Map source
            source_str = data.get("source", "")
            source = ArticleMapper._map_source(source_str)
            
            # Extract source_id
            source_id = data.get("source_id", "")
            article_id = f"{source.value}:{source_id}"
            
            # Parse timestamps
            published_at = data.get("published_at")
            if isinstance(published_at, str):
                published_at = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
            
            updated_at = data.get("updated_at")
            if updated_at:
                if isinstance(updated_at, str):
                    updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            else:
                updated_at = None
            
            # Create domain model
            article = Article(
                id=article_id,
                source=source,
                source_id=source_id,
                title=data.get("title", ""),
                content=data.get("content"),
                summary=data.get("summary"),
                author=data.get("author"),
                published_at=published_at,
                updated_at=updated_at,
                url=data.get("url"),
                tickers=frozenset(data.get("tickers", [])),
                tags=frozenset(data.get("tags", [])),
                categories=frozenset(data.get("categories", [])),
            )
            
            # Validate domain model
            if not ArticleValidator.is_valid_domain_article(article):
                logger.warning("Mapped article failed domain validation", article_id=article.id)
                return None
            
            return article
            
        except Exception as e:
            logger.error("Error mapping article from dict", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def from_infrastructure_model(infra_data: InfrastructureArticleData, received_at: Optional[datetime] = None) -> Optional[Article]:
        """
        Map typed InfrastructureArticleData → typed Article domain model.
        
        Args:
            infra_data: Typed infrastructure article data model
            received_at: Optional timestamp to use as fallback if published timestamp is missing
            
        Returns:
            Article domain model, or None if invalid
        """
        try:
            # Infrastructure model is already typed and validated by Pydantic
            # Extract fields from typed model
            source_id = infra_data.source_id or str(infra_data.benzinga_id) if infra_data.benzinga_id else None
            if not source_id:
                logger.warning("ArticleMapper: No source_id in infrastructure model")
                return None
            
            # Map source
            source = ArticleMapper._map_source("benzinga")  # Benzinga WebSocket
            
            # Build article ID
            article_id = f"{source.value}:{source_id}"
            
            # Map title (can be headline or title)
            title = infra_data.title or infra_data.headline or ""
            if not title:
                logger.warning("ArticleMapper: No title in infrastructure model")
                return None
            
            # Map content (can be body or content)
            content = infra_data.content or infra_data.body
            
            # Map timestamps
            published_str = infra_data.published or infra_data.created_at
            if published_str:
                if isinstance(published_str, str):
                    try:
                        published_at = datetime.fromisoformat(published_str.replace('Z', '+00:00'))
                    except Exception as e:
                        logger.warning(
                            "ArticleMapper: Failed to parse published timestamp, using current time",
                            timestamp=published_str,
                            error=str(e)
                        )
                        from datetime import timezone
                        published_at = datetime.now(timezone.utc)
                else:
                    published_at = published_str
            else:
                # Fallback: Use received_at if provided, otherwise current time
                if received_at:
                    published_at = received_at
                    logger.debug("ArticleMapper: No published timestamp, using received_at as fallback")
                else:
                    logger.debug("ArticleMapper: No published timestamp, using current time as fallback")
                    from datetime import timezone
                    published_at = datetime.now(timezone.utc)
            
            updated_str = infra_data.updated_at or infra_data.last_updated
            updated_at = None
            if updated_str:
                if isinstance(updated_str, str):
                    updated_at = datetime.fromisoformat(updated_str.replace('Z', '+00:00'))
                else:
                    updated_at = updated_str
            
            # Map tickers (can be from tickers, symbols, or securities)
            tickers = list(infra_data.tickers) if infra_data.tickers else []
            if not tickers and infra_data.symbols:
                tickers = list(infra_data.symbols)
            if not tickers and infra_data.securities:
                tickers = [sec.get("symbol", "") for sec in infra_data.securities if sec.get("symbol")]
            
            # Create domain model
            article = Article(
                id=article_id,
                source=source,
                source_id=source_id,
                title=title,
                content=content,
                summary=infra_data.summary or infra_data.teaser,
                author=infra_data.author,
                published_at=published_at,
                updated_at=updated_at,
                url=infra_data.url,
                tickers=frozenset(tickers),
                tags=frozenset(infra_data.tags),
                categories=frozenset(infra_data.categories),
            )
            
            # Validate domain model
            if not ArticleValidator.is_valid_domain_article(article):
                logger.warning("ArticleMapper: Mapped article failed domain validation")
                return None
            
            return article
            
        except Exception as e:
            logger.error("ArticleMapper: Error mapping from infrastructure model", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def _map_source(source: str) -> ArticleSource:
        """
        Map infrastructure source string to domain ArticleSource enum.
        
        Args:
            source: Source string from infrastructure
            
        Returns:
            ArticleSource enum value
        """
        source_lower = source.lower()
        
        # Map common source names
        if "benzinga" in source_lower:
            return ArticleSource.BENZINGA
        
        # Default mapping - if unknown, try to parse as enum
        try:
            return ArticleSource(source_lower)
        except ValueError:
            logger.warning(f"Unknown source '{source}', defaulting to BENZINGA")
            return ArticleSource.BENZINGA
    
    @staticmethod
    def to_dict(article: Article) -> Dict[str, Any]:
        """
        Convert domain Article to dictionary (for serialization/events).
        
        Args:
            article: Domain Article model
            
        Returns:
            Dictionary representation
        """
        return article.to_dict()

