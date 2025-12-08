"""
Helper functions for building infrastructure events from shared models.

These convert shared/infrastructure models to typed infrastructure event models.
"""
from ...models.base_models import TradeRequest
from .infrastructure_models import InfrastructureTradeRequestData


def build_infrastructure_trade_request_data(trade_request: TradeRequest) -> InfrastructureTradeRequestData:
    """
    Convert shared TradeRequest model to InfrastructureTradeRequestData.
    
    Args:
        trade_request: Shared TradeRequest model
        
    Returns:
        Typed InfrastructureTradeRequestData model
    """
    return InfrastructureTradeRequestData(
        ticker=trade_request.ticker,
        amount_usd=trade_request.amount_usd,
        action=trade_request.action,
        shares=trade_request.shares,
        leverage=trade_request.leverage,
        instrument=trade_request.instrument.value if hasattr(trade_request.instrument, 'value') else str(trade_request.instrument),
        article_id=getattr(trade_request, 'article_id', None)
    )

