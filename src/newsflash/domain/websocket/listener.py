"""
Domain listener for WebSocket - subscribes to infrastructure events, publishes domain events.

This is the bridge between infrastructure and domain layers.
Implements protocols for type-safe event handling.
"""
from typing import Dict, Any
from datetime import datetime

from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType, InfrastructureEventType
from ...infra.websocket.infrastructure_models import ArticleReceivedInfrastructureEvent
from ...infra.websocket.event_protocols import InfrastructureArticleEventSubscriber
from ...utils.logging_config import get_logger
from ...shared.decorators import handle_errors
from ..base_listener import BaseDomainListener
from .validators import ArticleValidator
from .factories import ArticleFactory
from .events import (
    ArticleReceivedDomainEvent,
    ArticleValidationFailedDomainEvent,
    WebSocketHealthStatusDomainEvent,
    WebSocketConnectedDomainEvent,
    WebSocketDisconnectedDomainEvent,
    WebSocketErrorDomainEvent,
    WebSocketRateLimitDomainEvent
)
from .models import Article
from .event_protocols import DomainArticleEventPublisher

logger = get_logger(__name__)


class WebSocketDomainListener(
    BaseDomainListener,
    InfrastructureArticleEventSubscriber,
    DomainArticleEventPublisher
):
    """
    Listens to WebSocket infrastructure events and publishes domain events.
    
    Responsibilities:
    - Subscribe to infrastructure ArticleReceivedEvent
    - Validate and transform to domain models
    - Publish domain events for services to consume
    
    Standard Domain Layer Pattern:
    - Validators: Validate domain models (protocol contracts)
    - Factories: Create domain models from infrastructure (use mappers internally + business rules)
    - Note: No mappers needed here - only one-way flow (infra → domain), no reverse mapping required
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        validator: ArticleValidator,
        factory: ArticleFactory,
    ):
        """
        Initialize WebSocket domain listener.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            validator: Validator for Article domain models
            factory: Factory for creating Article domain models from infrastructure
        """
        super().__init__(event_bus, "WebSocketDomainListener")
        self.validator = validator
        self.factory = factory
    
    async def start(self) -> None:
        """
        Start listening to infrastructure events.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        # Subscribe to infrastructure article events
        self.event_bus.subscribe(InfrastructureEventType.ARTICLE_RECEIVED, self._handle_article_received_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.WEBSOCKET_HEALTH_STATUS, self._handle_websocket_health_status_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.WEBSOCKET_CONNECTED, self._handle_websocket_connected_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.WEBSOCKET_DISCONNECTED, self._handle_websocket_disconnected_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.WEBSOCKET_ERROR, self._handle_websocket_error_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.WEBSOCKET_RATE_LIMIT, self._handle_websocket_rate_limit_from_bus)
        logger.info("WebSocketDomainListener started - listening to infrastructure events")
    
    async def stop(self) -> None:
        """
        Stop listening to infrastructure events.
        
        Idempotent: Safe to call multiple times.
        """
        # Unsubscribe from infrastructure article events
        self.event_bus.unsubscribe(InfrastructureEventType.ARTICLE_RECEIVED, self._handle_article_received_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.WEBSOCKET_HEALTH_STATUS, self._handle_websocket_health_status_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.WEBSOCKET_CONNECTED, self._handle_websocket_connected_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.WEBSOCKET_DISCONNECTED, self._handle_websocket_disconnected_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.WEBSOCKET_ERROR, self._handle_websocket_error_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.WEBSOCKET_RATE_LIMIT, self._handle_websocket_rate_limit_from_bus)
        logger.info("WebSocketDomainListener stopped")
    
    async def handle_article_received(self, event: ArticleReceivedInfrastructureEvent) -> None:
        """
        Handle ArticleReceived infrastructure event (implements InfrastructureArticleEventSubscriber).
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        await self._handle_article_received_from_bus("ArticleReceived", event.model_dump())
    
    @handle_errors(log_context="WebSocketDomainListener: Error handling article event")
    async def _handle_article_received_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle ArticleReceived infrastructure event.
        
        Flow: Validate → Factory (Map + Business Rules) → Publish
        
        Process:
        1. Validate infrastructure event/data (reconstruct typed event - Pydantic validates)
        2. Factory creates domain model (uses mapper internally to transform, then applies business rules)
        3. Publish domain event
        """
        self.log_debug("Received infrastructure article event", event_type=event_type)
        
        # Step 1: VALIDATE infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, ArticleReceivedInfrastructureEvent
        )
        if not infra_event:
            return
        
        # Validate infrastructure model structure
        if not infra_event.article_data:
            self.log_warning("No article_data in infrastructure event", event_type=event_type)
            return
        
        # Step 2: FACTORY creates domain model (uses mapper internally + business rules)
        domain_article = self.factory.create_from_infrastructure_model(
            infra_event.article_data,
            received_at=infra_event.received_at  # Pass received_at as fallback for published timestamp
        )
        
        if not domain_article:
            source_id = infra_event.article_data.source_id or str(infra_event.article_data.benzinga_id) if infra_event.article_data.benzinga_id else "unknown"
            self.log_warning(
                "Failed to create domain article from infrastructure model",
                event_type=event_type,
                source_id=source_id,
                has_title=bool(infra_event.article_data.title),
                has_published=bool(infra_event.article_data.published),
                has_created_at=bool(infra_event.article_data.created_at)
            )
            await self._publish_validation_failed(
                infra_event.article_data.model_dump(),
                ["Failed to create domain article from infrastructure model - likely missing required fields"]
            )
            return
        
        # Step 3: PUBLISH typed domain event (factory already validated)
        await self.publish_article_received(domain_article, infra_event.received_at)
    
    @handle_errors(log_context="WebSocketDomainListener: Error publishing domain article event")
    async def publish_article_received(self, article: Article, received_at: datetime) -> None:
        """
        Publish ArticleReceived domain event (implements DomainArticleEventPublisher).
        
        Args:
            article: Typed domain Article model (validated, immutable)
            received_at: When article was received
        """
        domain_event = ArticleReceivedDomainEvent(
            article=article,  # ✅ Typed domain model
            received_at=received_at
        )
        await self.event_bus.publish(DomainEventType.ARTICLE_RECEIVED, domain_event.model_dump())
        
        logger.info(
            "WebSocketDomainListener: Published domain article event",
            article_id=article.id,
            tickers=list(article.tickers)
        )
    
    @handle_errors(log_context="WebSocketDomainListener: Error publishing validation failed event")
    async def _publish_validation_failed(
        self,
        article_data: Dict[str, Any],
        errors: list[str]
    ) -> None:
        """Publish validation failed domain event."""
        event = ArticleValidationFailedDomainEvent(
            article_data=article_data,
            validation_errors=errors,
            failed_at=datetime.now()
        )
        await self.event_bus.publish(DomainEventType.ARTICLE_VALIDATION_FAILED, event.model_dump())
    
    @handle_errors(log_context="WebSocketDomainListener: Error handling health status event")
    async def _handle_websocket_health_status_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocketHealthStatus infrastructure event and publish domain event."""
        from ...infra.websocket.events import WebSocketHealthStatusEvent
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, WebSocketHealthStatusEvent
        )
        if not infra_event:
            return
        
        domain_event = WebSocketHealthStatusDomainEvent(
            is_healthy=infra_event.healthy,
            status=infra_event.status,
            reason=infra_event.reason,
            occurred_at=infra_event.occurred_at,
            details=infra_event.details
        )
        await self.event_bus.publish(DomainEventType.WEBSOCKET_HEALTH_STATUS, domain_event.model_dump())
        
        logger.debug(
            "WebSocketDomainListener: Published domain health status event",
            is_healthy=infra_event.healthy,
            status=infra_event.status
        )
    
    @handle_errors(log_context="WebSocketDomainListener: Error handling connected event")
    async def _handle_websocket_connected_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocketConnected infrastructure event and publish domain event."""
        from ...infra.websocket.events import WebSocketConnectedEvent
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, WebSocketConnectedEvent
        )
        if not infra_event:
            return
        
        domain_event = WebSocketConnectedDomainEvent(
            connected_at=infra_event.connected_at
        )
        await self.event_bus.publish(DomainEventType.WEBSOCKET_CONNECTED, domain_event.model_dump())
        
        logger.debug("WebSocketDomainListener: Published domain connected event")
    
    @handle_errors(log_context="WebSocketDomainListener: Error handling disconnected event")
    async def _handle_websocket_disconnected_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocketDisconnected infrastructure event and publish domain event."""
        from ...infra.websocket.events import WebSocketDisconnectedEvent
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, WebSocketDisconnectedEvent
        )
        if not infra_event:
            return
        
        domain_event = WebSocketDisconnectedDomainEvent(
            disconnected_at=infra_event.disconnected_at,
            reason=infra_event.reason
        )
        await self.event_bus.publish(DomainEventType.WEBSOCKET_DISCONNECTED, domain_event.model_dump())
        
        logger.debug("WebSocketDomainListener: Published domain disconnected event")
    
    @handle_errors(log_context="WebSocketDomainListener: Error handling error event")
    async def _handle_websocket_error_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocketError infrastructure event and publish domain event."""
        from ...infra.websocket.events import WebSocketErrorEvent
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, WebSocketErrorEvent
        )
        if not infra_event:
            return
        
        domain_event = WebSocketErrorDomainEvent(
            error=infra_event.error,
            occurred_at=infra_event.occurred_at,
            is_rate_limit=infra_event.is_rate_limit
        )
        await self.event_bus.publish(DomainEventType.WEBSOCKET_ERROR, domain_event.model_dump())
        
        logger.debug("WebSocketDomainListener: Published domain error event")
    
    @handle_errors(log_context="WebSocketDomainListener: Error handling rate limit event")
    async def _handle_websocket_rate_limit_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle WebSocketRateLimit infrastructure event and publish domain event."""
        from ...infra.websocket.events import WebSocketRateLimitEvent
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, WebSocketRateLimitEvent
        )
        if not infra_event:
            return
        
        domain_event = WebSocketRateLimitDomainEvent(
            occurred_at=infra_event.occurred_at,
            message=infra_event.message
        )
        await self.event_bus.publish(DomainEventType.WEBSOCKET_RATE_LIMIT, domain_event.model_dump())
        
        logger.debug("WebSocketDomainListener: Published domain rate limit event")

