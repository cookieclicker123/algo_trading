"""
Event definitions for brokerage microservice.

Infrastructure events use infrastructure-specific typed models.
"""

from .infrastructure_models import (
    InfrastructureTradeExecutedEvent,
    InfrastructureTradeFailedEvent,
    InfrastructureQuoteReceivedEvent,
    InfrastructureConnectionStatusEvent,
    InfrastructureBrokerageHealthEvent,
    InfrastructureTradeQueuedEvent
)

# TODO: Review: is this needed? do we need backward compatibility in dev phase? do we even need this file if straight from infra models?
# Re-export infrastructure event models as events (for backward compatibility and clarity)
TradeExecutedEvent = InfrastructureTradeExecutedEvent
TradeFailedEvent = InfrastructureTradeFailedEvent
QuoteReceivedEvent = InfrastructureQuoteReceivedEvent
ConnectionStatusChangedEvent = InfrastructureConnectionStatusEvent
BrokerageHealthStatusEvent = InfrastructureBrokerageHealthEvent
TradeRequestQueuedEvent = InfrastructureTradeQueuedEvent
