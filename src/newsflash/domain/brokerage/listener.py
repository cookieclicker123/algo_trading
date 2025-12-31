"""
Domain listener for brokerage - subscribes to infrastructure events, publishes domain events.

This bridges infrastructure ↔ domain for trading operations.
"""
from typing import Dict, Any, Optional, List
from datetime import datetime

from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType, InfrastructureEventType
from ...infra.brokerage.infrastructure_models import (
    InfrastructureTradeExecutionRequestEvent,
    InfrastructureTradeExecutedEvent,
    InfrastructureTradeFailedEvent,
    InfrastructureQuoteReceivedEvent,
    InfrastructureTradeQueuedEvent,
    InfrastructureConnectionStatusEvent,
    InfrastructureBrokerageHealthEvent
)
from ...infra.brokerage.event_protocols import (
    InfrastructureTradeExecutionRequestEventSubscriber,
    InfrastructureTradeExecutedEventSubscriber
)
from ...utils.logging_config import get_logger
from ...shared.decorators import handle_errors
from ..base_listener import BaseDomainListener
from .validators import TradeRequestValidator, TradeResultValidator
from .mappers import TradeRequestMapper
from .factories import TradeRequestFactory, TradeResultFactory, QuoteFactory
from .models import TradeResult, TradeRequest, Quote
from .events import (
    TradeRequestDomainEvent,
    TradeExecutedDomainEvent,
    TradeFailedDomainEvent,
    TradeQueuedDomainEvent,
    QuoteReceivedDomainEvent,
    BrokerageConnectionStatusDomainEvent,
    BrokerageHealthStatusDomainEvent
)
from .event_protocols import DomainTradeEventPublisher

logger = get_logger(__name__)


class BrokerageDomainListener(
    BaseDomainListener,
    InfrastructureTradeExecutionRequestEventSubscriber,
    InfrastructureTradeExecutedEventSubscriber,
    DomainTradeEventPublisher
):
    """
    Listens to brokerage infrastructure events and publishes domain events.
    
    Also listens to domain trade requests and forwards them to infrastructure.
    
    Responsibilities:
    - Subscribe to Domain.TradeRequested (from use cases) → Publish TradeExecutionRequested (to infrastructure)
    - Subscribe to TradeExecuted (from infrastructure) → Publish Domain.TradeExecuted (to services)
    - Subscribe to TradeFailed (from infrastructure) → Publish Domain.TradeFailed (to services)
    - Subscribe to TradeRequestQueued (from infrastructure) → Publish Domain.TradeQueued (to services)
    - Subscribe to QuoteReceived (from infrastructure) → Publish Domain.QuoteReceived (to services)
    
    Standard Domain Layer Pattern:
    - Validators: Validate domain models (protocol contracts)
    - Factories: Create domain models from infrastructure (use mappers internally + business rules)
    - Mappers: Transform domain → infrastructure (reverse mapping for forwarding to infra)
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        request_validator: TradeRequestValidator,
        result_validator: TradeResultValidator,
        request_factory: TradeRequestFactory,
        result_factory: TradeResultFactory,
        quote_factory: QuoteFactory,
        request_mapper: TradeRequestMapper,
    ):
        """
        Initialize brokerage domain listener.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            request_validator: Validator for TradeRequest domain models
            result_validator: Validator for TradeResult domain models
            request_factory: Factory for creating TradeRequest domain models
            result_factory: Factory for creating TradeResult domain models
            quote_factory: Factory for creating Quote domain models
            request_mapper: Mapper for trade request domain ↔ infrastructure transformation
        """
        super().__init__(event_bus, "BrokerageDomainListener")
        self.request_validator = request_validator
        self.result_validator = result_validator
        self.request_factory = request_factory
        self.result_factory = result_factory
        self.quote_factory = quote_factory
        self.request_mapper = request_mapper
    
    async def start(self) -> None:
        """
        Start listening to events.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        # Subscribe to domain trade requests (use cases → infrastructure)
        self.event_bus.subscribe(DomainEventType.TRADE_REQUESTED, self._handle_domain_trade_request)
        
        self.event_bus.subscribe(InfrastructureEventType.TRADE_EXECUTED, self._handle_infra_trade_executed_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.TRADE_FAILED, self._handle_infra_trade_failed)
        self.event_bus.subscribe(InfrastructureEventType.TRADE_REQUEST_QUEUED, self._handle_infra_trade_queued)
        
        self.event_bus.subscribe(InfrastructureEventType.QUOTE_RECEIVED, self._handle_infra_quote_received)
        
        self.event_bus.subscribe(InfrastructureEventType.CONNECTION_STATUS_CHANGED, self._handle_connection_status_from_bus)
        self.event_bus.subscribe(InfrastructureEventType.BROKERAGE_HEALTH_STATUS, self._handle_brokerage_health_from_bus)
        
        logger.info("BrokerageDomainListener started - listening to domain and infrastructure events")
    
    async def stop(self) -> None:
        """
        Stop listening to events.
        
        Idempotent: Safe to call multiple times.
        """
        # Unsubscribe from domain trade requests
        self.event_bus.unsubscribe(DomainEventType.TRADE_REQUESTED, self._handle_domain_trade_request)
        
        # Unsubscribe from infrastructure events
        self.event_bus.unsubscribe(InfrastructureEventType.TRADE_EXECUTED, self._handle_infra_trade_executed_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.TRADE_FAILED, self._handle_infra_trade_failed)
        self.event_bus.unsubscribe(InfrastructureEventType.TRADE_REQUEST_QUEUED, self._handle_infra_trade_queued)
        self.event_bus.unsubscribe(InfrastructureEventType.QUOTE_RECEIVED, self._handle_infra_quote_received)
        self.event_bus.unsubscribe(InfrastructureEventType.CONNECTION_STATUS_CHANGED, self._handle_connection_status_from_bus)
        self.event_bus.unsubscribe(InfrastructureEventType.BROKERAGE_HEALTH_STATUS, self._handle_brokerage_health_from_bus)
        
        logger.info("BrokerageDomainListener stopped")
    
    @handle_errors(log_context="BrokerageDomainListener: Error handling domain trade request")
    async def _handle_domain_trade_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle domain trade request event (from use cases).
        
        Flow: Validate → Map → Publish
        
        Process:
        1. Validate domain event
        2. Validate domain model
        3. Map domain model → infrastructure format
        4. Publish infrastructure event
        """
        self.log_debug("Received domain trade request event", event_type=event_type)
        
        # Step 1: VALIDATE domain event (using base class helper)
        domain_event = self.validate_domain_event(
            event_type, event_data, TradeRequestDomainEvent
        )
        if not domain_event:
            return
        
        # Extract typed domain model
        trade_request = domain_event.trade_request
        
        # Step 2: VALIDATE domain model (protocol contract)
        if not self.request_validator.is_valid_domain_trade_request(trade_request):
            self.log_warning("Invalid domain trade request", event_type=event_type)
            return
        
        # Step 3: MAP domain model → infrastructure format
        infra_request_data = self.request_mapper.to_infrastructure_model(trade_request)
        
        # Step 4: PUBLISH typed infrastructure event
        infra_event = InfrastructureTradeExecutionRequestEvent(
            trade_request=infra_request_data,
            article_id=domain_event.article_id,
            requested_at=domain_event.requested_at
        )
        
        await self.publish_infrastructure_event(
            "TradeExecutionRequested",
            infra_event,
            log_context=f"Published infrastructure trade execution request (ticker={trade_request.ticker}, amount_usd={trade_request.amount_usd})"
        )
    
    async def handle_trade_executed(self, event: InfrastructureTradeExecutedEvent) -> None:
        """
        Handle TradeExecuted infrastructure event (implements InfrastructureTradeExecutedEventSubscriber).
        
        Args:
            event: Typed infrastructure event model (validated)
        """
        await self._handle_infra_trade_executed_from_bus("TradeExecuted", event.model_dump())
    
    @handle_errors(log_context="BrokerageDomainListener: Error handling infrastructure trade executed")
    async def _handle_infra_trade_executed_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle infrastructure trade executed event (from event bus).
        
        Flow: Validate → Factory (Map + Business Rules) → Publish
        """
        self.log_debug("Received infrastructure trade executed event", event_type=event_type)
        
        # Step 1: VALIDATE infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, InfrastructureTradeExecutedEvent
        )
        if not infra_event:
            return
        
        # Step 2: FACTORY creates domain model (uses mapper internally + business rules)
        trade_result = self.result_factory.create_from_infrastructure_event(infra_event)
        
        if not trade_result:
            self.log_warning(
                "Failed to create domain trade result from infrastructure event",
                event_type=event_type
            )
            return
        
        # Step 4: PUBLISH typed domain event via protocol method
        await self.publish_trade_executed(trade_result, infra_event.executed_at)
    
    @handle_errors(log_context="BrokerageDomainListener: Error publishing domain trade executed event")
    async def publish_trade_executed(self, trade_result: TradeResult, executed_at: datetime) -> None:
        """
        Publish TradeExecuted domain event (implements DomainTradeEventPublisher).
        
        This is a CRITICAL point in the notification workflow:
        - This event triggers NotifyTradeExecutedUseCase (trade execution notification)
        - This event triggers NotifyImminentArticleUseCase (article headline notification)
        - If this isn't published, NO notifications will be sent
        
        Args:
            trade_result: Typed domain TradeResult model (validated, immutable)
            executed_at: When trade was executed
        """
        domain_event = TradeExecutedDomainEvent(
            trade_result=trade_result,
            executed_at=executed_at
        )
        
        # CRITICAL: Publish Domain.TradeExecuted event
        # Subscribers: NotifyTradeExecutedUseCase, NotifyImminentArticleUseCase, NotifyExitTradeUseCase
        await self.event_bus.publish(DomainEventType.TRADE_EXECUTED, domain_event.model_dump())
        
        logger.info(
            "✅ BrokerageDomainListener: Published domain trade executed event",
            ticker=trade_result.get_ticker(),
            success=trade_result.success,
            shares=trade_result.shares,
            article_id=trade_result.trade_request.get("article_id"),
            subscribers=self.event_bus.get_subscriber_count(DomainEventType.TRADE_EXECUTED)
        )
    
    @handle_errors(log_context="BrokerageDomainListener: Error publishing domain trade failed event")
    async def publish_trade_failed(
        self, 
        trade_request: TradeRequest, 
        error: str, 
        failed_at: datetime,
        ladder_attempts: Optional[int] = None,
        ladder_attempts_detail: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        Publish TradeFailed domain event (implements DomainTradeEventPublisher).
        
        Args:
            trade_request: Typed domain TradeRequest model
            error: Error message
            failed_at: When trade failed
            ladder_attempts: Optional number of ladder attempts (for extended hours)
            ladder_attempts_detail: Optional detailed ladder attempts list
        """
        domain_event = TradeFailedDomainEvent(
            trade_request=trade_request,
            error=error,
            failed_at=failed_at,
            ladder_attempts=ladder_attempts,
            ladder_attempts_detail=ladder_attempts_detail
        )
        await self.event_bus.publish(DomainEventType.TRADE_FAILED, domain_event.model_dump())
        
        logger.info(
            "BrokerageDomainListener: Published domain trade failed event",
            ticker=trade_request.ticker,
            error=error,
            ladder_attempts=ladder_attempts
        )
    
    @handle_errors(log_context="BrokerageDomainListener: Error publishing domain trade queued event")
    async def publish_trade_queued(self, trade_request: TradeRequest, queued_at: datetime, target_premarket: datetime) -> None:
        """
        Publish TradeQueued domain event (implements DomainTradeEventPublisher).
        
        Args:
            trade_request: Typed domain TradeRequest model
            queued_at: When trade was queued
            target_premarket: Target premarket time for execution
        """
        domain_event = TradeQueuedDomainEvent(
            trade_request=trade_request,
            queued_at=queued_at,
            target_premarket=target_premarket
        )
        await self.event_bus.publish(DomainEventType.TRADE_QUEUED, domain_event.model_dump())
        
        logger.info(
            "BrokerageDomainListener: Published domain trade queued event",
            ticker=trade_request.ticker,
            target_premarket=target_premarket.isoformat()
        )
    
    @handle_errors(log_context="BrokerageDomainListener: Error publishing domain quote received event")
    async def publish_quote_received(self, quote: Quote, received_at: datetime) -> None:
        """
        Publish QuoteReceived domain event (implements DomainTradeEventPublisher).
        
        Args:
            quote: Typed domain Quote model (validated, immutable)
            received_at: When quote was received
        """
        domain_event = QuoteReceivedDomainEvent(
            quote=quote,
            received_at=received_at
        )
        await self.event_bus.publish(DomainEventType.QUOTE_RECEIVED, domain_event.model_dump())
        
        logger.debug(
            "BrokerageDomainListener: Published domain quote received event",
            ticker=quote.ticker
        )
    
    @handle_errors(log_context="BrokerageDomainListener: Error handling infrastructure trade failed")
    async def _handle_infra_trade_failed(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle infrastructure trade failed event.
        
        Flow: Validate → Factory (Map + Business Rules) → Publish
        """
        self.log_debug("Received infrastructure trade failed event", event_type=event_type)
        
        # Step 1: VALIDATE infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, InfrastructureTradeFailedEvent
        )
        if not infra_event:
            return
        
        # Step 2: FACTORY creates domain TradeRequest (uses mapper internally + business rules)
        domain_trade_request = self.request_factory.create_from_infrastructure_model(infra_event.trade_request)
        
        if not domain_trade_request:
            self.log_warning(
                "Failed to create domain trade request from infrastructure model",
                event_type=event_type
            )
            return
        
        # Step 3: PUBLISH domain event via protocol method (with ladder attempts if available)
        await self.publish_trade_failed(
            domain_trade_request, 
            infra_event.error, 
            infra_event.failed_at,
            ladder_attempts=infra_event.ladder_attempts,
            ladder_attempts_detail=infra_event.ladder_attempts_detail
        )
    
    @handle_errors(log_context="BrokerageDomainListener: Error handling infrastructure trade queued")
    async def _handle_infra_trade_queued(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle infrastructure trade queued event.
        
        Flow: Validate → Factory (Map + Business Rules) → Publish
        """
        self.log_debug("Received infrastructure trade queued event", event_type=event_type)
        
        # Step 1: VALIDATE infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, InfrastructureTradeQueuedEvent
        )
        if not infra_event:
            return
        
        # Step 2: FACTORY creates domain TradeRequest (uses mapper internally + business rules)
        domain_trade_request = self.request_factory.create_from_infrastructure_model(infra_event.trade_request)
        
        if not domain_trade_request:
            self.log_warning(
                "Failed to create domain trade request from infrastructure model",
                event_type=event_type
            )
            return
        
        # Step 4: PUBLISH domain event via protocol method
        await self.publish_trade_queued(domain_trade_request, infra_event.queued_at, infra_event.target_premarket)
    
    @handle_errors(log_context="BrokerageDomainListener: Error handling infrastructure quote received")
    async def _handle_infra_quote_received(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle infrastructure quote received event.
        
        Flow: Validate → Factory (Map + Business Rules) → Publish
        """
        self.log_debug("Received infrastructure quote received event", event_type=event_type)
        
        # Step 1: VALIDATE infrastructure event (using base class helper)
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, InfrastructureQuoteReceivedEvent
        )
        if not infra_event:
            return
        
        # Step 2: FACTORY creates domain Quote (uses mapper internally + business rules)
        quote = self.quote_factory.create_from_infrastructure_event(infra_event)
        
        if not quote:
            self.log_warning(
                "Failed to create domain quote from infrastructure event",
                event_type=event_type
            )
            return
        
        # Step 4: PUBLISH domain event via protocol method
        await self.publish_quote_received(quote, infra_event.received_at)
    
    @handle_errors(log_context="BrokerageDomainListener: Error handling connection status event")
    async def _handle_connection_status_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle ConnectionStatusChanged infrastructure event and publish domain event."""
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, InfrastructureConnectionStatusEvent
        )
        if not infra_event:
            return
        
        domain_event = BrokerageConnectionStatusDomainEvent(
            is_connected=infra_event.is_connected,
            paper_trading=infra_event.paper_trading,
            changed_at=infra_event.changed_at,
            reason=infra_event.reason
        )
        await self.event_bus.publish(DomainEventType.BROKERAGE_CONNECTION_STATUS, domain_event.model_dump())
        
        logger.debug(
            "BrokerageDomainListener: Published domain connection status event",
            is_connected=infra_event.is_connected
        )
    
    @handle_errors(log_context="BrokerageDomainListener: Error handling health status event")
    async def _handle_brokerage_health_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle BrokerageHealthStatus infrastructure event and publish domain event."""
        infra_event = self.validate_infrastructure_event(
            event_type, event_data, InfrastructureBrokerageHealthEvent
        )
        if not infra_event:
            return
        
        domain_event = BrokerageHealthStatusDomainEvent(
            is_healthy=infra_event.is_healthy,
            is_connected=infra_event.is_connected,
            reason=infra_event.reason,
            occurred_at=infra_event.occurred_at,
            is_critical=infra_event.is_critical,
            stats=infra_event.stats
        )
        await self.event_bus.publish(DomainEventType.BROKERAGE_HEALTH_STATUS, domain_event.model_dump())
        
        logger.debug(
            "BrokerageDomainListener: Published domain health status event",
            is_healthy=infra_event.is_healthy
        )
