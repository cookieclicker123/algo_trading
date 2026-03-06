"""
Domain listener for classification - subscribes to infrastructure events, publishes domain events.

This bridges infrastructure ↔ domain for classification operations.
"""
from typing import Dict, Any, Optional
from datetime import datetime

from newsflash.domain.classification.models import ClassificationResult

from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType, InfrastructureEventType
from ...infra.classification.infrastructure_models import (
    ClassificationRequestedInfrastructureEvent,
    ClassificationCompletedInfrastructureEvent,
    ClassificationFailedInfrastructureEvent
)
from ...infra.classification.event_protocols import InfrastructureClassificationRequestEventSubscriber
from ...utils.logging_config import get_logger
from ...shared.decorators import handle_errors
from ..base_listener import BaseDomainListener
from .validators import ClassificationRequestValidator, ClassificationResultValidator
from .mappers import ClassificationRequestMapper
from .factories import ClassificationRequestFactory, ClassificationResultFactory
from .events import (
    ClassificationRequestedDomainEvent,
    ArticleClassifiedDomainEvent,
    ClassificationFailedDomainEvent
)
from .event_protocols import DomainClassificationEventPublisher

logger = get_logger(__name__)


class ClassificationDomainListener(
    BaseDomainListener,
    InfrastructureClassificationRequestEventSubscriber,
    DomainClassificationEventPublisher
):
    """
    Listens to classification infrastructure events and publishes domain events.
    
    Also listens to domain classification requests and forwards them to infrastructure.
    
    Responsibilities:
    - Subscribe to Domain.ClassificationRequested (from use cases) → Publish ClassificationRequested (to infrastructure)
    - Subscribe to ClassificationCompleted (from infrastructure) → Publish Domain.ArticleClassified (to services)
    - Subscribe to ClassificationFailed (from infrastructure) → Publish Domain.ClassificationFailed (to services)
    
    Standard Domain Layer Pattern:
    - Validators: Validate domain models (protocol contracts)
    - Factories: Create domain models from infrastructure (use mappers internally + business rules)
    - Mappers: Transform domain → infrastructure (reverse mapping for forwarding to infra)
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        request_validator: ClassificationRequestValidator,
        result_validator: ClassificationResultValidator,
        request_factory: ClassificationRequestFactory,
        result_factory: ClassificationResultFactory,
        request_mapper: ClassificationRequestMapper,
    ):
        """
        Initialize classification domain listener.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            request_validator: Validator for ClassificationRequest domain models
            result_validator: Validator for ClassificationResult domain models
            request_factory: Factory for creating ClassificationRequest domain models
            result_factory: Factory for creating ClassificationResult domain models
            request_mapper: Mapper for classification request domain ↔ infrastructure transformation
        """
        super().__init__(event_bus, "ClassificationDomainListener")
        self.request_validator = request_validator
        self.result_validator = result_validator
        self.request_factory = request_factory
        self.result_factory = result_factory
        self.request_mapper = request_mapper
    
    async def start(self) -> None:
        """
        Start listening to events.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        # Subscribe to domain classification requests (use cases → infrastructure)
        self.event_bus.subscribe(DomainEventType.CLASSIFICATION_REQUESTED, self._handle_domain_classification_request)
        
        self.event_bus.subscribe(InfrastructureEventType.CLASSIFICATION_COMPLETED, self._handle_infra_classification_completed_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.CLASSIFICATION_FAILED, self._handle_infra_classification_failed_from_bus)
        
        logger.info("ClassificationDomainListener started - listening to domain and infrastructure events")
    
    async def stop(self) -> None:
        """
        Stop listening to events.
        
        Idempotent: Safe to call multiple times.
        """
        # Unsubscribe from domain classification requests
        self.event_bus.unsubscribe(DomainEventType.CLASSIFICATION_REQUESTED, self._handle_domain_classification_request)
        
        # Unsubscribe from infrastructure events
        self.event_bus.unsubscribe(InfrastructureEventType.CLASSIFICATION_COMPLETED, self._handle_infra_classification_completed_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.CLASSIFICATION_FAILED, self._handle_infra_classification_failed_from_bus)
        
        logger.info("ClassificationDomainListener stopped")
    
    @handle_errors(log_context="ClassificationDomainListener: Error handling domain classification request")
    async def _handle_domain_classification_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain classification request event (from use cases).
        
        Flow: Validate → Map → Publish
        
        Process:
        1. Validate domain event
        2. Validate domain model
        3. Map domain model → infrastructure format
        4. Publish infrastructure event
        """
        self.log_debug("Received domain classification request event", event_type=event_type)
        
        # Step 1: VALIDATE domain event (using base class helper)
        domain_event = self.validate_domain_event(
            event_type, event_data, ClassificationRequestedDomainEvent
        )
        if not domain_event:
            return
        
        # Extract typed domain model
        classification_request = domain_event.request
        
        # Step 2: VALIDATE domain model (protocol contract)
        if not self.request_validator.is_valid_classification_request(classification_request):
            self.log_warning(
                "Invalid domain classification request",
                event_type=event_type,
                article_id=classification_request.article_id
            )
            return
        
        # Step 3: MAP domain model → infrastructure format
        infra_request_data = self.request_mapper.to_infrastructure_model(classification_request)
        
        # Step 4: PUBLISH typed infrastructure event
        infra_event = ClassificationRequestedInfrastructureEvent(
            request_data=infra_request_data,
            requested_at=domain_event.requested_at
        )
        
        await self.publish_infrastructure_event(
            "ClassificationRequested",
            infra_event,
            log_context=f"✅ CLASSIFY DOMAIN: Published infrastructure classification request event (article_id={classification_request.article_id}, title={classification_request.article_title or ''})"
        )
    
    async def handle_classification_requested(self, event: ClassificationRequestedInfrastructureEvent) -> None:
        """
        Handle ClassificationRequested infrastructure event (implements InfrastructureClassificationRequestEventSubscriber).
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        await self._handle_domain_classification_request("ClassificationRequested", event.model_dump())
    
    @handle_errors(log_context="ClassificationDomainListener: Error handling classification completed event")
    async def _handle_infra_classification_completed_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle ClassificationCompleted infrastructure event.
        
        Flow: Validate → Factory (Map + Business Rules) → Publish
        
        Process:
        1. Validate infrastructure event (reconstruct typed event - Pydantic validates)
        2. Factory creates domain model (uses mapper internally to transform, then applies business rules)
        3. Publish domain event
        """
        logger.info(
            "🎯 CLASSIFY DOMAIN: Received infrastructure classification completed event",
            event_type=event_type
        )
        
        # Step 1: VALIDATE infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, ClassificationCompletedInfrastructureEvent
        )
        if not infra_event:
            return
        
        # Validate infrastructure model structure
        if not infra_event.request_data or not infra_event.response_data:
            self.log_warning("Missing data in infrastructure event", event_type=event_type)
            return
        
        # Step 2: FACTORY creates domain model (uses mapper internally + business rules)
        domain_result = self.result_factory.create_from_infrastructure_model(
            infra_response=infra_event.response_data,
            article_id=infra_event.request_data.article_id,
            latency_ms=infra_event.latency_ms,
            classified_at=infra_event.completed_at
        )
        
        if not domain_result:
            self.log_warning(
                "Failed to create domain classification result from infrastructure model",
                event_type=event_type,
                article_id=infra_event.request_data.article_id
            )
            await self._publish_classification_failed(
                infra_event.request_data.article_id,
                "Failed to create domain classification result from infrastructure model"
            )
            return

        # Parse published_at from ISO string if available
        published_at = None
        if infra_event.request_data.article_published_at_iso:
            try:
                published_at = datetime.fromisoformat(
                    infra_event.request_data.article_published_at_iso.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError) as e:
                logger.debug(f"Could not parse published_at: {e}")

        # Step 3: PUBLISH typed domain event (factory already validated)
        # Include tickers/title/published_at to avoid storage race condition in auto-trade
        # Include position_size for AI-based position sizing (no confluence delay)
        await self.publish_article_classified(
            domain_result,
            infra_event.completed_at,
            tickers=infra_event.request_data.article_tickers,
            title=infra_event.request_data.article_title,
            published_at=published_at,
            position_size=infra_event.response_data.position_size,
            headline_type=infra_event.response_data.headline_type,
        )
    
    @handle_errors(log_context="ClassificationDomainListener: Error handling classification failed event")
    async def _handle_infra_classification_failed_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle ClassificationFailed infrastructure event.
        
        Flow: Validate → Publish
        
        Process:
        1. Validate infrastructure event
        2. Publish domain failure event
        """
        self.log_debug("Received infrastructure classification failed event", event_type=event_type)
        
        # Step 1: VALIDATE infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, ClassificationFailedInfrastructureEvent
        )
        if not infra_event:
            return
        
        # Step 2: PUBLISH typed domain event
        await self.publish_classification_failed(
            infra_event.request_data.article_id,
            infra_event.error,
            infra_event.failed_at
        )
    
    async def publish_classification_requested(self, event: ClassificationRequestedDomainEvent) -> None:
        """
        Publish ClassificationRequested domain event (implements DomainClassificationEventPublisher).
        
        Args:
            event: Typed domain event model (validated)
        """
        # This is handled by _handle_domain_classification_request which forwards to infrastructure
        # This method is for protocol compliance, but the actual publishing is done in the handler
        await self.event_bus.publish(DomainEventType.CLASSIFICATION_REQUESTED, event.model_dump())
    
    @handle_errors(log_context="ClassificationDomainListener: Error publishing domain article classified event")
    async def publish_article_classified(
        self,
        result: "ClassificationResult",
        classified_at: datetime,
        tickers: Optional[list] = None,
        title: Optional[str] = None,
        published_at: Optional[datetime] = None,
        position_size: Optional[str] = None,
        headline_type: Optional[str] = None,
    ) -> None:
        """
        Publish ArticleClassified domain event (implements DomainClassificationEventPublisher).

        Args:
            result: Typed domain ClassificationResult model (validated, immutable)
            classified_at: When classification was completed
            tickers: Article tickers (included to avoid storage race condition)
            title: Article title (for logging)
            published_at: Article publication time (for confluence scoring)
            position_size: AI-determined position size (SMALL, MODERATE, LARGE, MAX)
            headline_type: Headline type from HeadlineTypeClassifier (for high-conviction bypass)
        """
        domain_event = ArticleClassifiedDomainEvent(
            article_id=result.article_id,
            result=result,  # ✅ Typed domain model
            classified_at=classified_at,
            tickers=tickers or [],
            title=title or "",
            published_at=published_at,
            position_size=position_size,
            headline_type=headline_type,
        )
        await self.event_bus.publish(DomainEventType.ARTICLE_CLASSIFIED, domain_event.model_dump())

        logger.info(
            "✅ CLASSIFY DOMAIN: Published domain article classified event",
            article_id=result.article_id,
            classification=result.classification.value,
            confidence=result.confidence.value,
            reasoning=result.reasoning,
            tickers=tickers,
            has_published_at=published_at is not None,
            position_size=position_size,
            headline_type=headline_type
        )
    
    @handle_errors(log_context="ClassificationDomainListener: Error publishing domain classification failed event")
    async def publish_classification_failed(
        self,
        article_id: str,
        error: str,
        failed_at: Optional[datetime] = None
    ) -> None:
        """
        Publish ClassificationFailed domain event (implements DomainClassificationEventPublisher).
        
        Args:
            article_id: Article ID that failed to classify
            error: Error message
            failed_at: Optional timestamp (defaults to now)
        """
        domain_event = ClassificationFailedDomainEvent(
            article_id=article_id,
            error=error,
            failed_at=failed_at or datetime.now()
        )
        await self.event_bus.publish(DomainEventType.CLASSIFICATION_FAILED, domain_event.model_dump())
        
        logger.info(
            "ClassificationDomainListener: Published domain classification failed event",
            article_id=article_id,
            error=error
        )

