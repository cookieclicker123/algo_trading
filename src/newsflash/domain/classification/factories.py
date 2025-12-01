"""
Factories for classification domain - create domain objects with business rules.

Factories use mappers internally to transform infrastructure → domain,
then apply business rules and validation.
"""
from typing import Optional
from datetime import datetime

from ...utils.logging_config import get_logger
from ...domain.websocket.models import Article
from ...infra.classification.infrastructure_models import (
    InfrastructureClassificationRequestData,
    InfrastructureClassificationResponseData
)
from .models import (
    ClassificationRequest,
    ClassificationResult,
    ClassificationCategory,
    ClassificationConfidence
)
from .validators import ClassificationRequestValidator, ClassificationResultValidator
from .mappers import ClassificationRequestMapper, ClassificationResultMapper

logger = get_logger(__name__)


class ClassificationRequestFactory:
    """
    Factory for creating ClassificationRequest domain objects.
    
    Ensures business rules are applied during creation.
    """
    
    @staticmethod
    def create_from_article(article: Article) -> Optional[ClassificationRequest]:
        """
        Create ClassificationRequest from Article domain model.
        
        Business rules:
        - Article must have a title
        - Tickers are optional but will be included if present
        
        Args:
            article: Domain Article model
            
        Returns:
            Domain ClassificationRequest model, or None if invalid
        """
        try:
            # Business rule: Article must have a title
            if not article.title or not article.title.strip():
                logger.warning("Cannot create classification request: article has no title", article_id=article.id)
                return None
            
            # Create classification request
            request = ClassificationRequest(
                article_id=article.id,
                article_title=article.title,
                article_tickers=article.tickers if article.tickers else frozenset(),
                article_summary=article.summary or article.content or "",
                requested_at=datetime.now()
            )
            
            # Validate domain model
            if not ClassificationRequestValidator.is_valid_classification_request(request):
                logger.warning("Created classification request failed domain validation", article_id=article.id)
                return None
            
            logger.debug("ClassificationRequestFactory: Created classification request", article_id=article.id)
            return request
            
        except Exception as e:
            logger.error(
                "ClassificationRequestFactory: Error creating classification request",
                error=str(e),
                article_id=article.id if hasattr(article, 'id') else 'unknown',
                exc_info=True
            )
            return None
    
    @staticmethod
    def create_from_infrastructure_model(infra_request: InfrastructureClassificationRequestData) -> Optional[ClassificationRequest]:
        """
        Create ClassificationRequest from typed infrastructure model.
        
        Args:
            infra_request: InfrastructureClassificationRequestData model (typed)
            
        Returns:
            ClassificationRequest domain model, or None if invalid
        """
        try:
            # Infrastructure model is already validated by Pydantic
            # Transform to domain model via mapper
            request = ClassificationRequestMapper.from_infrastructure_model(infra_request)
            
            if not request:
                logger.warning("ClassificationRequestFactory: Mapping from infrastructure model failed")
                return None
            
            # Validate domain model
            if not ClassificationRequestValidator.is_valid_classification_request(request):
                logger.warning("ClassificationRequestFactory: Created request failed domain validation")
                return None
            
            logger.debug("ClassificationRequestFactory: Created classification request from infrastructure model")
            return request
            
        except Exception as e:
            logger.error("ClassificationRequestFactory: Error creating request from infrastructure model", error=str(e), exc_info=True)
            return None


class ClassificationResultFactory:
    """
    Factory for creating ClassificationResult domain objects.
    
    Ensures business rules are applied during creation.
    """
    
    @staticmethod
    def create_from_infrastructure_model(
        infra_response: InfrastructureClassificationResponseData,
        article_id: str,
        latency_ms: float,
        classified_at: Optional[datetime] = None
    ) -> Optional[ClassificationResult]:
        """
        Create ClassificationResult from typed infrastructure response model.
        
        Args:
            infra_response: InfrastructureClassificationResponseData model (typed)
            article_id: Article ID that was classified
            latency_ms: Classification latency in milliseconds
            classified_at: Optional timestamp (defaults to now)
            
        Returns:
            ClassificationResult domain model, or None if invalid
        """
        try:
            # Infrastructure model is already validated by Pydantic
            # Transform to domain model via mapper
            result = ClassificationResultMapper.from_infrastructure_model(
                infra_response=infra_response,
                article_id=article_id,
                latency_ms=latency_ms,
                classified_at=classified_at
            )
            
            if not result:
                logger.warning("ClassificationResultFactory: Mapping from infrastructure model failed", article_id=article_id)
                return None
            
            # Validate domain model
            if not ClassificationResultValidator.is_valid_classification_result(result):
                logger.warning("ClassificationResultFactory: Created result failed domain validation", article_id=article_id)
                return None
            
            logger.debug("ClassificationResultFactory: Created classification result from infrastructure model", article_id=article_id)
            return result
            
        except Exception as e:
            logger.error(
                "ClassificationResultFactory: Error creating result from infrastructure model",
                error=str(e),
                article_id=article_id,
                exc_info=True
            )
            return None

