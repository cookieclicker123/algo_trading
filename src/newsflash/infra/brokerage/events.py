"""
Event definitions for brokerage microservice.

Infrastructure events use infrastructure-specific typed models.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any

from .infrastructure_models import (
    InfrastructureTradeRequestData,
    InfrastructureTradeExecutedEvent,
    InfrastructureTradeFailedEvent,
    InfrastructureQuoteReceivedEvent,
    InfrastructureConnectionStatusEvent,
    InfrastructureBrokerageHealthEvent,
    InfrastructureTradeQueuedEvent
)

# Re-export infrastructure event models as events (for backward compatibility and clarity)
TradeExecutedEvent = InfrastructureTradeExecutedEvent
TradeFailedEvent = InfrastructureTradeFailedEvent
QuoteReceivedEvent = InfrastructureQuoteReceivedEvent
ConnectionStatusChangedEvent = InfrastructureConnectionStatusEvent
BrokerageHealthStatusEvent = InfrastructureBrokerageHealthEvent
TradeRequestQueuedEvent = InfrastructureTradeQueuedEvent
