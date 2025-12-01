"""
Classification audit service - subscribes to domain classification events and logs to audit trail.

Pure event subscription - subscribes to domain events only.
"""
from datetime import datetime
from typing import Optional, Dict, Any

from ...utils.logging_config import get_logger
from ...shared.event_bus import get_event_bus
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...services.classification_audit_trail import ClassificationAuditTrail
from ...models.classification_models import ClassificationResult as LegacyClassificationResult
from ...models.base_models import StandardizedArticle

logger = get_logger(__name__)


class ClassificationAuditService:
    """
    Classification audit service that subscribes to domain classification events.
    
    Responsibilities:
    - Subscribes to Domain.ArticleClassified events
    - Subscribes to Domain.ArticleReceived events (to cache article data)
    - Logs IMMINENT classifications to audit trail
    - Updates audit trail with metadata asynchronously
    
    Does NOT:
    - Know about infrastructure details
    - Know about Groq API
    - Process articles (use case layer does that)
    - Classify articles (classification microservice does that)
    """
    
    def __init__(self, audit_trail: Optional[ClassificationAuditTrail] = None):
        """
        Initialize classification audit service.
        
        Args:
            audit_trail: Optional ClassificationAuditTrail instance (creates default if None)
        """
        self.event_bus = get_event_bus()
        self.audit_trail = audit_trail or ClassificationAuditTrail()
        self.is_running = False
        
        # Cache article data by article_id (for audit trail logging)
        # Maps article_id -> StandardizedArticle (temporary until audit trail accepts domain models)
        self._article_cache: Dict[str, StandardizedArticle] = {}
        
        # Subscribe to domain events
        self.event_bus.subscribe("Domain.ArticleReceived", self._handle_article_received)
        self.event_bus.subscribe("Domain.ArticleClassified", self._handle_article_classified)
        logger.info("ClassificationAuditService subscribed to Domain.ArticleReceived and Domain.ArticleClassified events")
    
    async def start(self) -> None:
        """Start the audit service (already subscribed in __init__)."""
        if self.is_running:
            logger.warning("ClassificationAuditService already running")
            return
        
        self.is_running = True
        logger.info("ClassificationAuditService started - listening for domain events")
    
    async def stop(self) -> None:
        """Stop the audit service."""
        if not self.is_running:
            return
        
        self.is_running = False
        self.event_bus.unsubscribe("Domain.ArticleReceived", self._handle_article_received)
        self.event_bus.unsubscribe("Domain.ArticleClassified", self._handle_article_classified)
        logger.info("ClassificationAuditService stopped")
    
    async def _handle_article_received(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle Domain.ArticleReceived event - cache article data for audit trail.
        
        We cache the article so we can use it when logging to audit trail.
        """
        try:
            # Reconstruct typed domain event
            domain_event = ArticleReceivedDomainEvent(**event_data)
            domain_article = domain_event.article
            
            # Convert domain Article to StandardizedArticle for audit trail compatibility
            # TODO: Refactor audit trail to accept domain models instead
            standardized_article = self._convert_domain_article_to_standardized(domain_article)
            
            if standardized_article:
                # Cache article by ID for audit trail logging
                self._article_cache[domain_article.id] = standardized_article
                logger.debug(
                    "ClassificationAuditService: Cached article for audit trail",
                    article_id=domain_article.id
                )
            
        except Exception as e:
            logger.error(
                "ClassificationAuditService: Error handling Domain.ArticleReceived event",
                error=str(e),
                event_type=event_type,
                exc_info=True
            )
    
    async def _handle_article_classified(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle Domain.ArticleClassified event - receives typed domain ClassificationResult.
        
        Logs IMMINENT classifications to audit trail.
        """
        try:
            # Reconstruct typed domain event
            domain_event = ArticleClassifiedDomainEvent(**event_data)
            
            # Extract typed domain ClassificationResult model
            classification_result = domain_event.result
            
            # Only log IMMINENT classifications
            if not classification_result.is_imminent():
                logger.debug(
                    "ClassificationAuditService: Skipping audit trail for non-IMMINENT classification",
                    article_id=classification_result.article_id,
                    classification=classification_result.classification.value
                )
                return
            
            # Get article from cache
            standardized_article = self._article_cache.get(classification_result.article_id)
            
            if not standardized_article:
                logger.warning(
                    "ClassificationAuditService: Article not found in cache for audit trail",
                    article_id=classification_result.article_id
                )
                return
            
            # Convert domain ClassificationResult to legacy ClassificationResult for audit trail
            # TODO: Refactor audit trail to accept domain models instead
            legacy_classification = LegacyClassificationResult(
                classification=classification_result.classification.value,
                confidence=classification_result.confidence.value,
                reasoning=classification_result.reasoning
            )
            
            # Log to audit trail
            logger.info(
                "ClassificationAuditService: Logging IMMINENT classification to audit trail",
                article_id=classification_result.article_id,
                classification=classification_result.classification.value,
                confidence=classification_result.confidence.value
            )
            
            # Calculate timing
            news_received_at = datetime.now()  # TODO: Get from article received event
            classified_at = classification_result.classified_at
            
            article_id = self.audit_trail.log_imminent_classification(
                article=standardized_article,
                classification=legacy_classification,
                news_received_at=news_received_at,
                classified_at=classified_at,
                metadata={}  # Will be updated asynchronously if needed
            )
            
            logger.info(
                "ClassificationAuditService: Logged to audit trail",
                article_id=article_id,
                classification=classification_result.classification.value
            )
            
        except Exception as e:
            logger.error(
                "ClassificationAuditService: Error handling Domain.ArticleClassified event",
                error=str(e),
                event_type=event_type,
                exc_info=True
            )
    
    def _convert_domain_article_to_standardized(self, domain_article) -> Optional[StandardizedArticle]:
        """
        Convert domain Article to StandardizedArticle for audit trail compatibility.
        
        TODO: Refactor audit trail to accept domain models instead.
        """
        try:
            from ...domain.websocket.models import Article
            from ...models.base_models import NewsSource
            
            if not isinstance(domain_article, Article):
                logger.error("ClassificationAuditService: domain_article is not a domain Article model")
                return None
            
            # Map source
            source_map = {
                "benzinga": NewsSource.BENZINGA_WEBSOCKET
            }
            source = source_map.get(domain_article.source.value, NewsSource.BENZINGA_WEBSOCKET)
            
            return StandardizedArticle(
                source=source,
                source_id=domain_article.source_id,
                title=domain_article.title,
                content=domain_article.content,
                summary=domain_article.summary,
                author=domain_article.author,
                published=domain_article.published_at,
                updated=domain_article.updated_at,
                url=domain_article.url,
                tickers=list(domain_article.tickers) if domain_article.tickers else [],
                tags=list(domain_article.tags) if domain_article.tags else [],
                categories=list(domain_article.categories) if domain_article.categories else [],
                images=[],
                raw_data={}
            )
        except Exception as e:
            logger.error(
                "ClassificationAuditService: Error converting domain Article to StandardizedArticle",
                error=str(e),
                exc_info=True
            )
            return None
    
    def get_stats(self) -> dict:
        """Get audit service statistics."""
        return {
            "is_running": self.is_running,
            "has_audit_trail": self.audit_trail is not None,
            "cached_articles": len(self._article_cache)
        }

