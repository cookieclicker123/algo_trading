"""
Classification microservice - self-contained initialization.

This module initializes all classification-related components:
- Infrastructure service
- Domain listener (bridge)
- Use cases (if any)
"""
from dataclasses import dataclass

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus

# Infrastructure layer
from ...infra.classification import ClassificationInfrastructureService

# Domain layer
from ...domain.classification.listener import ClassificationDomainListener

logger = get_logger(__name__)


@dataclass
class ClassificationMicroservice:
    """
    Classification microservice container.
    
    Holds all classification-related components:
    - Infrastructure service
    - Domain listener (bridge)
    - Use cases (if any)
    """
    infra: ClassificationInfrastructureService
    domain_listener: ClassificationDomainListener
    # Note: ClassifyArticleUseCase doesn't exist - classification is handled automatically via domain listener
    
    async def start(self) -> None:
        """Start all classification microservice components."""
        logger.info("Starting classification microservice...")
        
        # Start infrastructure FIRST
        await self.infra.start()
        logger.info("Classification infrastructure started")
        
        # Start domain listener
        await self.domain_listener.start()
        logger.info("Classification domain listener started")
        
        logger.info("Classification microservice started")
    
    async def stop(self) -> None:
        """Stop all classification microservice components."""
        logger.info("Stopping classification microservice...")
        
        # Stop domain listener
        await self.domain_listener.stop()
        
        # Stop infrastructure last
        await self.infra.stop()
        
        logger.info("Classification microservice stopped")


async def initialize_classification_microservice(
    event_bus: AsyncEventBus,
    groq_api_key: str,
    anthropic_api_key: str,
    anthropic_model: str,
    enabled: bool,
    metrics_service,  # Required - injected via DI
) -> ClassificationMicroservice:
    """
    Initialize classification microservice independently.

    This function knows ONLY about classification microservice.
    It doesn't know about other microservices.

    Args:
        event_bus: Event bus instance (shared dependency)
        groq_api_key: Groq API key for triage (Llama 70B headline type detection)
        anthropic_api_key: Anthropic API key for sector classification (Claude Sonnet)
        anthropic_model: Anthropic model name for sector classification
        enabled: Whether classification is enabled (injected via DI)
        metrics_service: Optional metrics service (injected via DI)

    Returns:
        ClassificationMicroservice: Initialized classification microservice
    """
    logger.info("Initializing classification microservice...")

    # Step 1: Infrastructure layer
    infra = ClassificationInfrastructureService(
        event_bus=event_bus,
        groq_api_key=groq_api_key,
        anthropic_api_key=anthropic_api_key,
        anthropic_model=anthropic_model,
        metrics_service=metrics_service,  # ✅ Pass metrics service
        enabled=enabled,
    )
    logger.info("Classification infrastructure initialized")
    
    # Step 2: Domain listener (bridge infrastructure ↔ domain)
    from ...domain.classification.validators import ClassificationRequestValidator, ClassificationResultValidator
    from ...domain.classification.factories import ClassificationRequestFactory, ClassificationResultFactory
    from ...domain.classification.mappers import ClassificationRequestMapper
    
    domain_listener = ClassificationDomainListener(
        event_bus=event_bus,
        request_validator=ClassificationRequestValidator(),
        result_validator=ClassificationResultValidator(),
        request_factory=ClassificationRequestFactory(),
        result_factory=ClassificationResultFactory(),
        request_mapper=ClassificationRequestMapper(),
    )
    logger.info("Classification domain listener initialized")
    
    # Note: Classification use case doesn't exist - classification happens automatically
    # via domain listener when ClassificationRequested events are published
    
    return ClassificationMicroservice(
        infra=infra,
        domain_listener=domain_listener,
    )


# Export pure functions from request_builder
from .request_builder import (
    create_classification_request,
    validate_classification_request,
    can_classify_article,
    extract_classification_summary,
    get_article_tickers_for_classification,
)

__all__ = [
    "ClassificationMicroservice",
    "initialize_classification_microservice",
    "create_classification_request",
    "validate_classification_request",
    "can_classify_article",
    "extract_classification_summary",
    "get_article_tickers_for_classification",
]
