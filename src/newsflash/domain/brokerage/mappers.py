"""
Mappers for brokerage domain - transform infrastructure models to domain models.
"""
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime

from ...utils.logging_config import get_logger
from ...infra.brokerage.infrastructure_models import (
    InfrastructureTradeRequestData,
    InfrastructureTradeExecutedEvent,
    InfrastructureQuoteReceivedEvent
)
from .models import TradeRequest, TradeResult, Quote, TradeStatus, MarketSession
from .validators import TradeRequestValidator, TradeResultValidator, QuoteValidator

logger = get_logger(__name__)


class TradeRequestMapper:
    """
    Maps infrastructure trade request format ↔ domain TradeRequest.
    """
    
    @staticmethod
    def from_infrastructure_model(infra_request: InfrastructureTradeRequestData) -> Optional[TradeRequest]:
        """
        Transform typed InfrastructureTradeRequestData → typed domain TradeRequest.
        
        Args:
            infra_request: Typed infrastructure trade request model
            
        Returns:
            Typed domain TradeRequest model, or None if invalid
        """
        try:
            # Convert infrastructure model to domain format
            # Infrastructure uses strings, domain uses enums - convert them
            from .models import TradeAction, TradeInstrument
            
            domain_data = {
                "ticker": infra_request.ticker,
                "action": TradeAction(infra_request.action),  # Convert string → enum
                "amount_usd": infra_request.amount_usd,
                "shares": infra_request.shares,
                "leverage": infra_request.leverage,
                "instrument": TradeInstrument(infra_request.instrument),  # Convert string → enum
                "article_id": infra_request.article_id,
            }
            
            # Validate first
            if not TradeRequestValidator.is_valid_trade_request_data(domain_data):
                logger.warning("Invalid trade request data in mapper")
                return None
            
            # Create domain model
            trade_request = TradeRequest.from_dict(domain_data)
            
            # Validate domain model
            if not TradeRequestValidator.is_valid_domain_trade_request(trade_request):
                logger.warning("Created trade request failed domain validation")
                return None
            
            return trade_request
            
        except Exception as e:
            logger.error("Error mapping trade request from infrastructure model", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def to_infrastructure_model(domain_request: TradeRequest) -> InfrastructureTradeRequestData:
        """
        Transform typed domain TradeRequest → typed infrastructure TradeRequestData.
        
        Args:
            domain_request: Typed domain TradeRequest model
            
        Returns:
            Typed InfrastructureTradeRequestData model
        """
        return InfrastructureTradeRequestData(
            ticker=domain_request.ticker,
            amount_usd=float(domain_request.amount_usd) if domain_request.amount_usd else None,
            action=domain_request.action.value,
            shares=domain_request.shares,
            leverage=float(domain_request.leverage) if domain_request.leverage else None,
            instrument=domain_request.instrument.value,
            article_id=domain_request.article_id,
        )
    
    @staticmethod
    def to_infra_format(domain_request: TradeRequest) -> Dict[str, Any]:
        """
        Transform domain TradeRequest → infrastructure format.
        
        Infrastructure expects:
        - ticker: str
        - amount_usd: float
        - action: str ("BUY"/"SELL")
        - shares: Optional[int]
        - leverage: Optional[float]
        - instrument: str ("stock")
        
        Args:
            domain_request: Domain TradeRequest model
            
        Returns:
            Dictionary in infrastructure format
        """
        return {
            "ticker": domain_request.ticker,
            "amount_usd": float(domain_request.amount_usd),
            "action": domain_request.action.value,
            "shares": domain_request.shares,
            "leverage": float(domain_request.leverage) if domain_request.leverage else None,
            "instrument": domain_request.instrument.value,
            "article_id": domain_request.article_id,
        }
    
    @staticmethod
    def from_infra_dict(data: Dict[str, Any]) -> Optional[TradeRequest]:
        """
        Transform infrastructure format → domain TradeRequest.
        
        Args:
            data: Infrastructure trade request dictionary
            
        Returns:
            Domain TradeRequest model, or None if invalid
        """
        try:
            # Infrastructure format might have slightly different structure
            # Map to domain format
            domain_data = {
                "ticker": data.get("ticker", ""),
                "action": data.get("action", "BUY"),
                "amount_usd": data.get("amount_usd"),  # Can be None with leverage
                "shares": data.get("shares"),
                "leverage": data.get("leverage"),
                "instrument": data.get("instrument", "stock"),
                "article_id": data.get("article_id"),
            }
            
            # Validate first
            if not TradeRequestValidator.is_valid_trade_request_data(domain_data):
                logger.warning("Invalid trade request data in mapper")
                return None
            
            # Create domain model
            trade_request = TradeRequest.from_dict(domain_data)
            
            # Validate domain model
            if not TradeRequestValidator.is_valid_domain_trade_request(trade_request):
                logger.warning("Created trade request failed domain validation")
                return None
            
            return trade_request
            
        except Exception as e:
            logger.error("Error mapping trade request from infrastructure format", error=str(e), exc_info=True)
            return None


class TradeResultMapper:
    """
    Maps infrastructure trade result format ↔ domain TradeResult.
    """
    
    @staticmethod
    def from_infrastructure_event(infra_event: InfrastructureTradeExecutedEvent) -> Optional[TradeResult]:
        """
        Transform typed InfrastructureTradeExecutedEvent → typed domain TradeResult.
        
        Args:
            infra_event: Typed infrastructure event model
            
        Returns:
            Typed domain TradeResult model, or None if invalid
        """
        try:
            # Infrastructure event is already typed and validated by Pydantic
            # Map session string → domain enum
            session = TradeResultMapper._map_session(infra_event.session)
            
            # Map success → status enum
            if infra_event.success:
                status = TradeStatus.EXECUTED
            else:
                status = TradeStatus.FAILED
            
            # Store spread_info in trade_request dict as metadata for notifications
            trade_request_dict = infra_event.trade_request.model_dump()
            if infra_event.spread_info:
                trade_request_dict["_spread_info"] = infra_event.spread_info  # Store as metadata
            
            # Build domain TradeResult
            trade_result = TradeResult(
                trade_request=trade_request_dict,  # Convert to dict for storage (includes spread_info metadata)
                success=infra_event.success,
                status=status,
                shares=infra_event.shares,
                fill_price=Decimal(str(infra_event.fill_price)) if infra_event.fill_price else None,
                total_cost=Decimal(str(infra_event.total_cost)) if infra_event.total_cost else None,
                commission=Decimal(str(infra_event.commission)) if infra_event.commission else None,
                session=session,
                executed_at=infra_event.executed_at,
                error=None  # Success case
            )
            
            # Validate domain model
            if not TradeResultValidator.is_valid_domain_trade_result(trade_result):
                logger.warning("TradeResultMapper: Mapped result failed domain validation")
                return None
            
            return trade_result
            
        except Exception as e:
            logger.error("TradeResultMapper: Error mapping from infrastructure event", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def from_infra_event(event_data: Dict[str, Any]) -> Optional[TradeResult]:
        """
        Transform infrastructure TradeExecutedEvent or TradeFailedEvent → domain TradeResult.
        
        Infrastructure event format:
        - trade_request: Dict[str, Any]
        - success: bool
        - shares: int (if successful)
        - fill_price: float (if successful)
        - session: str
        - executed_at: datetime
        - error: str (if failed)
        
        Args:
            event_data: Infrastructure event data dictionary
            
        Returns:
            Domain TradeResult model, or None if invalid
        """
        try:
            # Map session string → domain enum
            session_str = event_data.get("session", "market")
            session = TradeResultMapper._map_session(session_str)
            
            # Map success → status enum
            success = event_data.get("success", False)
            if success:
                status = TradeStatus.EXECUTED
            else:
                # Check if it was queued
                if "queued" in event_data.get("error", "").lower() or event_data.get("status") == "queued":
                    status = TradeStatus.QUEUED
                else:
                    status = TradeStatus.FAILED
            
            # Build domain TradeResult
            trade_result = TradeResult(
                trade_request=event_data.get("trade_request", {}),
                success=success,
                status=status,
                shares=event_data.get("shares"),
                fill_price=Decimal(str(event_data.get("fill_price", 0))) if event_data.get("fill_price") else None,
                total_cost=Decimal(str(event_data.get("total_cost", 0))) if event_data.get("total_cost") else None,
                commission=Decimal(str(event_data.get("commission", 0))) if event_data.get("commission") else None,
                session=session,
                executed_at=event_data.get("executed_at") or event_data.get("failed_at") or datetime.now(),
                error=event_data.get("error")
            )
            
            # Validate domain model
            if not TradeResultValidator.is_valid_domain_trade_result(trade_result):
                logger.warning("Created trade result failed domain validation")
                return None
            
            return trade_result
            
        except Exception as e:
            logger.error("Error mapping trade result from infrastructure event", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def _map_session(session_str: str) -> MarketSession:
        """Map infrastructure session string → domain MarketSession enum."""
        session_lower = session_str.lower()
        
        if session_lower in ["premarket", "pre"]:
            return MarketSession.PREMARKET
        elif session_lower in ["market", "regular"]:
            return MarketSession.MARKET
        elif session_lower in ["postmarket", "post", "afterhours", "after hours"]:
            return MarketSession.POSTMARKET
        elif session_lower in ["closed"]:
            return MarketSession.CLOSED
        else:
            logger.warning(f"Unknown session string '{session_str}', defaulting to MARKET")
            return MarketSession.MARKET


class QuoteMapper:
    """
    Maps infrastructure quote format ↔ domain Quote.
    """
    
    @staticmethod
    def from_infrastructure_event(infra_event: InfrastructureQuoteReceivedEvent) -> Optional[Quote]:
        """
        Transform typed InfrastructureQuoteReceivedEvent → typed domain Quote.
        
        Args:
            infra_event: Typed infrastructure event model (validated)
            
        Returns:
            Typed domain Quote model, or None if invalid
        """
        try:
            # Infrastructure event is already typed and validated by Pydantic
            nbbo = infra_event.nbbo
            symbol = infra_event.symbol
            
            if nbbo.bid is None or nbbo.ask is None:
                logger.warning("Quote missing bid or ask", symbol=symbol)
                return None
            
            quote_data = {
                "ticker": symbol,
                "bid": Decimal(str(nbbo.bid)),
                "ask": Decimal(str(nbbo.ask)),
                "last": Decimal(str(nbbo.last)) if nbbo.last else None,
                "volume": nbbo.volume,
                "received_at": infra_event.received_at
            }
            
            # Validate first
            if not QuoteValidator.is_valid_quote_data(quote_data):
                logger.warning("Invalid quote data in mapper")
                return None
            
            # Create domain model
            quote = Quote.from_dict(quote_data)
            
            # Validate domain model
            if not QuoteValidator.is_valid_domain_quote(quote):
                logger.warning("Created quote failed domain validation")
                return None
            
            return quote
            
        except Exception as e:
            logger.error("Error mapping quote from infrastructure event", error=str(e), exc_info=True)
            return None
    
    @staticmethod
    def from_infra_event(event_data: Dict[str, Any]) -> Optional[Quote]:
        """
        Legacy method: Transform infrastructure QuoteReceivedEvent dict → domain Quote.
        
        Args:
            event_data: Infrastructure event data dictionary
            
        Returns:
            Domain Quote model, or None if invalid
        """
        try:
            # Reconstruct typed infrastructure event
            infra_event = InfrastructureQuoteReceivedEvent(**event_data)
            return QuoteMapper.from_infrastructure_event(infra_event)
        except Exception as e:
            logger.error("Error mapping quote from event dict", error=str(e), exc_info=True)
            return None

