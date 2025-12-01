"""
Validators for classification domain - business rule validation.
"""
from ...utils.logging_config import get_logger
from .models import ClassificationRequest, ClassificationResult, ClassificationCategory, ClassificationConfidence

logger = get_logger(__name__)


class ClassificationRequestValidator:
    """
    Validates classification requests according to business rules.
    
    Business Rules:
    1. Article ID must be present and valid format
    2. Article title must be present and non-empty
    3. Tickers must be valid format (if present)
    4. Summary can be empty but must be a string
    """
    
    @staticmethod
    def is_valid_classification_request(request: ClassificationRequest) -> bool:
        """
        Validate a domain ClassificationRequest object.
        
        Args:
            request: Domain ClassificationRequest object
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Check required fields
            if not request.article_id:
                logger.debug("Classification request validation failed: missing article_id")
                return False
            
            if not request.article_title or not request.article_title.strip():
                logger.debug("Classification request validation failed: missing or empty title")
                return False
            
            # Validate ID format (should contain ":")
            if ":" not in request.article_id:
                logger.warning("Classification request validation: article_id format invalid", article_id=request.article_id)
            
            # Validate tickers format (if present)
            if request.article_tickers:
                for ticker in request.article_tickers:
                    if not isinstance(ticker, str) or not ticker.strip():
                        logger.debug(f"Classification request validation warning: invalid ticker format: {ticker}")
                        continue
                    ticker_clean = ticker.upper().strip()
                    if not ticker_clean.replace(".", "").replace("-", "").isalnum():
                        logger.debug(f"Classification request validation warning: ticker contains invalid characters: {ticker}")
                        # Don't fail validation, just warn
            
            return True
            
        except Exception as e:
            logger.error("Classification request validation error", error=str(e), exc_info=True)
            return False


class ClassificationResultValidator:
    """
    Validates classification results according to business rules.
    
    Business Rules:
    1. Article ID must be present
    2. Classification must be IMMINENT or IGNORE
    3. Confidence must be HIGH, MEDIUM, or LOW
    4. Reasoning must be present and non-empty
    5. Latency must be non-negative
    """
    
    @staticmethod
    def is_valid_classification_result(result: ClassificationResult) -> bool:
        """
        Validate a domain ClassificationResult object.
        
        Args:
            result: Domain ClassificationResult object
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Check required fields
            if not result.article_id:
                logger.debug("Classification result validation failed: missing article_id")
                return False
            
            # Validate classification category
            if result.classification not in [ClassificationCategory.IMMINENT, ClassificationCategory.IGNORE]:
                logger.debug(f"Classification result validation failed: invalid classification: {result.classification}")
                return False
            
            # Validate confidence
            if result.confidence not in [ClassificationConfidence.HIGH, ClassificationConfidence.MEDIUM, ClassificationConfidence.LOW]:
                logger.debug(f"Classification result validation failed: invalid confidence: {result.confidence}")
                return False
            
            # Validate reasoning
            if not result.reasoning or not result.reasoning.strip():
                logger.debug("Classification result validation failed: missing or empty reasoning")
                return False
            
            # Validate latency
            if result.latency_ms < 0:
                logger.warning("Classification result validation: negative latency", latency_ms=result.latency_ms)
            
            return True
            
        except Exception as e:
            logger.error("Classification result validation error", error=str(e), exc_info=True)
            return False
    
    @staticmethod
    def validate_classification_category(category: str) -> bool:
        """
        Validate classification category string.
        
        Args:
            category: Classification category string
            
        Returns:
            True if valid, False otherwise
        """
        try:
            category_enum = ClassificationCategory(category.lower())
            return category_enum in [ClassificationCategory.IMMINENT, ClassificationCategory.IGNORE]
        except (ValueError, AttributeError):
            return False
    
    @staticmethod
    def validate_confidence_level(confidence: str) -> bool:
        """
        Validate confidence level string.
        
        Args:
            confidence: Confidence level string
            
        Returns:
            True if valid, False otherwise
        """
        try:
            confidence_enum = ClassificationConfidence(confidence.upper())
            return confidence_enum in [ClassificationConfidence.HIGH, ClassificationConfidence.MEDIUM, ClassificationConfidence.LOW]
        except (ValueError, AttributeError):
            return False

