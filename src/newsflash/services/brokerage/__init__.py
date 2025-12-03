"""
Brokerage microservice - self-contained initialization.

This module initializes all brokerage-related components:
- Infrastructure service
- Domain listener (bridge)
- Services (auto-trade)
"""
from dataclasses import dataclass
from typing import Optional

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus

# Infrastructure layer
from ...infra.brokerage import IBKRBrokerageService

# Domain layer
from ...domain.brokerage.listener import BrokerageDomainListener

# Services layer
from .auto_trade import AutoTradeService

logger = get_logger(__name__)


@dataclass
class BrokerageMicroservice:
    """
    Brokerage microservice container.
    
    Holds all brokerage-related components:
    - Infrastructure service
    - Domain listener (bridge)
    - Services (auto-trade)
    """
    infra: IBKRBrokerageService
    domain_listener: BrokerageDomainListener
    auto_trade_service: Optional[AutoTradeService]  # Will be created in composition root after dependencies wired
    
    async def start(self) -> None:
        """Start all brokerage microservice components."""
        logger.info("Starting brokerage microservice...")
        
        # Start infrastructure FIRST
        logger.info("About to start IBKR Brokerage Service...")
        await self.infra.start()
        logger.info("IBKR Brokerage Service started")
        
        # Start domain listener
        await self.domain_listener.start()
        logger.info("Brokerage domain listener started")
        
        # Start services
        if self.auto_trade_service:
            await self.auto_trade_service.start()
            logger.info("AutoTradeService started")
        
        logger.info("Brokerage microservice started")
    
    async def stop(self) -> None:
        """Stop all brokerage microservice components."""
        logger.info("Stopping brokerage microservice...")
        
        # Stop services first
        if self.auto_trade_service:
            await self.auto_trade_service.stop()
        
        # Stop domain listener
        await self.domain_listener.stop()
        
        # Stop infrastructure last
        await self.infra.stop()
        
        logger.info("Brokerage microservice stopped")


async def initialize_brokerage_microservice(event_bus: AsyncEventBus) -> BrokerageMicroservice:
    """
    Initialize brokerage microservice independently.
    
    This function knows ONLY about brokerage microservice.
    It doesn't know about other microservices.
    
    Note: storage_query_service dependency will be wired in composition root.
    
    Args:
        event_bus: Event bus instance (shared dependency)
        
    Returns:
        BrokerageMicroservice: Initialized brokerage microservice
    """
    logger.info("Initializing brokerage microservice...")
    
    # Step 1: Infrastructure layer
    infra = IBKRBrokerageService(
        event_bus=event_bus,
        paper_trading=True,
        client_id=5
    )
    logger.info("Brokerage infrastructure initialized")
    
    # Step 2: Domain listener (bridge infrastructure ↔ domain)
    from ...domain.brokerage.validators import TradeRequestValidator, TradeResultValidator
    from ...domain.brokerage.factories import TradeRequestFactory, TradeResultFactory, QuoteFactory
    from ...domain.brokerage.mappers import TradeRequestMapper
    
    domain_listener = BrokerageDomainListener(
        event_bus=event_bus,
        request_validator=TradeRequestValidator(),
        result_validator=TradeResultValidator(),
        request_factory=TradeRequestFactory(),
        result_factory=TradeResultFactory(),
        quote_factory=QuoteFactory(),
        request_mapper=TradeRequestMapper(),
    )
    logger.info("Brokerage domain listener initialized")
    
    # Step 3: Services layer
    # Note: AutoTradeService will be created in composition root after storage_query_service is available
    # (cross-microservice dependency)
    
    return BrokerageMicroservice(
        infra=infra,
        domain_listener=domain_listener,
        auto_trade_service=None,  # Will be created in composition root
    )


__all__ = ["BrokerageMicroservice", "initialize_brokerage_microservice"]
