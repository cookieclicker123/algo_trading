"""
Mappers for classification domain - transform infrastructure models to domain models.
"""
from typing import Optional
from datetime import datetime

from ...utils.logging_config import get_logger
from ...infra.classification.infrastructure_models import (
    InfrastructureClassificationRequestData,
    InfrastructureClassificationResponseData,
)
from .models import (
    ClassificationRequest,
    ClassificationResult,
    ClassificationCategory,
    ClassificationConfidence
)
from .validators import ClassificationRequestValidator, ClassificationResultValidator

logger = get_logger(__name__)


class ClassificationRequestMapper:
    """
    Maps infrastructure classification request format ↔ domain ClassificationRequest.
    """
    
    @staticmethod
    def from_infrastructure_model(infra_request: InfrastructureClassificationRequestData) -> Optional[ClassificationRequest]:
        """
        Transform typed InfrastructureClassificationRequestData → typed domain ClassificationRequest.
        
        Args:
            infra_request: Typed infrastructure classification request model
            
        Returns:
            Typed domain ClassificationRequest model, or None if invalid
        """
        try:
            # Infrastructure model is already validated by Pydantic
            # Transform to domain format
            domain_request = ClassificationRequest(
                article_id=infra_request.article_id,
                article_title=infra_request.article_title,
                article_tickers=frozenset(infra_request.article_tickers) if infra_request.article_tickers else frozenset(),
                article_summary=infra_request.article_summary or "",
                article_published_at=datetime.fromisoformat(infra_request.article_published_at_iso) if infra_request.article_published_at_iso else None,
                article_received_at=datetime.fromisoformat(infra_request.article_received_at_iso) if infra_request.article_received_at_iso else None,
                requested_at=datetime.now()  # Use current time as requested_at
            )
            
            # Validate domain model
            if not ClassificationRequestValidator.is_valid_classification_request(domain_request):
                logger.warning("Mapped classification request failed domain validation")
                return None
            
            return domain_request
            
        except Exception as e:
            logger.error("Error mapping classification request from infrastructure model", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def to_infrastructure_model(domain_request: ClassificationRequest) -> InfrastructureClassificationRequestData:
        """
        Transform typed domain ClassificationRequest → typed infrastructure ClassificationRequestData.

        Args:
            domain_request: Typed domain ClassificationRequest model

        Returns:
            Typed InfrastructureClassificationRequestData model
        """
        # Sort tickers to ensure the PRIMARY ticker (subject of the headline) comes first.
        # Priority: 1) appears in headline (earliest position) 2) common stock (no suffix) 3) shorter 4) alphabetical
        # This fixes multi-ticker articles (e.g., SGN/SMCI/AIB) picking wrong sector.
        import re
        headline_upper = (domain_request.article_title or "").upper()

        def ticker_sort_key(ticker: str) -> tuple:
            has_suffix = ticker.endswith(('W', 'WS', '.U', '.UN', 'R', '.WS'))
            # Check if ticker symbol appears as a word in the headline
            match = re.search(r'\b' + re.escape(ticker.upper()) + r'\b', headline_upper)
            headline_pos = match.start() if match else 9999
            appears = 0 if match else 1  # 0 = appears (sort first), 1 = doesn't
            return (has_suffix, appears, headline_pos, len(ticker), ticker)

        sorted_tickers = sorted(domain_request.article_tickers, key=ticker_sort_key)

        return InfrastructureClassificationRequestData(
            article_id=domain_request.article_id,
            article_title=domain_request.article_title,
            article_tickers=sorted_tickers,
            article_summary=domain_request.article_summary,
            article_published_at_iso=domain_request.article_published_at.isoformat() if domain_request.article_published_at else None,
            article_received_at_iso=domain_request.article_received_at.isoformat() if domain_request.article_received_at else None
        )


class ClassificationResultMapper:
    """
    Maps infrastructure classification response format ↔ domain ClassificationResult.
    """
    
    @staticmethod
    def from_infrastructure_model(
        infra_response: InfrastructureClassificationResponseData,
        article_id: str,
        latency_ms: float,
        classified_at: Optional[datetime] = None
    ) -> Optional[ClassificationResult]:
        """
        Transform typed InfrastructureClassificationResponseData → typed domain ClassificationResult.
        
        Args:
            infra_response: Typed infrastructure classification response model
            article_id: Article ID that was classified
            latency_ms: Classification latency in milliseconds
            classified_at: Optional timestamp (defaults to now)
            
        Returns:
            Typed domain ClassificationResult model, or None if invalid
        """
        try:
            # Normalize classification to lowercase and convert to enum
            classification_str = infra_response.classification.lower()
            try:
                classification = ClassificationCategory(classification_str)
            except ValueError:
                logger.warning(f"Invalid classification category from infrastructure: {classification_str}")
                return None
            
            # Normalize confidence to uppercase and convert to enum
            confidence_str = infra_response.confidence.upper()
            try:
                confidence = ClassificationConfidence(confidence_str)
            except ValueError:
                logger.warning(f"Invalid confidence level from infrastructure: {confidence_str}")
                return None
            
            # Create domain model
            domain_result = ClassificationResult(
                article_id=article_id,
                classification=classification,
                confidence=confidence,
                reasoning=infra_response.reasoning,
                classified_at=classified_at or datetime.now(),
                latency_ms=latency_ms
            )
            
            # Validate domain model
            if not ClassificationResultValidator.is_valid_classification_result(domain_result):
                logger.warning("Mapped classification result failed domain validation")
                return None
            
            return domain_result
            
        except Exception as e:
            logger.error("Error mapping classification result from infrastructure model", error=str(e), exc_info=True)
            return None

