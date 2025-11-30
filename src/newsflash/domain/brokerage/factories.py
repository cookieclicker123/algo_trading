"""
Factories for brokerage domain - create domain objects with business rules.

Factories use mappers internally to transform infrastructure → domain,
then apply business rules and validation.
"""
from typing import Optional, Dict, Any
from decimal import Decimal
from datetime import datetime

from ...utils.logging_config import get_logger
from ...domain.websocket.models import Article
from ...infra.brokerage.infrastructure_models import (
    InfrastructureTradeRequestData,
    InfrastructureTradeExecutedEvent,
    InfrastructureQuoteReceivedEvent
)
from .models import TradeRequest, TradeResult, Quote, TradeAction, TradeInstrument
from .validators import TradeRequestValidator, TradeResultValidator, QuoteValidator
from .mappers import TradeRequestMapper, TradeResultMapper, QuoteMapper

logger = get_logger(__name__)


class TradeRequestFactory:
    """
    Factory for creating TradeRequest domain objects.
    
    Ensures business rules are applied during creation.
    """
    
    @staticmethod
    def create_from_article(
        article: Article,
        amount_usd: Decimal,
        leverage: Optional[Decimal] = None,
        action: TradeAction = TradeAction.BUY
    ) -> Optional[TradeRequest]:
        """
        Create TradeRequest from Article domain model.
        
        Business rules:
        - Article must have exactly one ticker
        - Amount must be positive
        - Leverage max 2x (validated in model)
        
        Args:
            article: Domain Article model
            amount_usd: Notional value in USD
            leverage: Optional leverage multiplier (max 2x)
            action: Trade action (default: BUY)
            
        Returns:
            Domain TradeRequest model, or None if invalid
        """
        try:
            # Business rule: Article must have tickers
            if not article.has_tickers():
                logger.warning("Cannot create trade request: article has no tickers")
                return None
            
            tickers = list(article.tickers)
            
            # Business rule: For now, we only trade articles with exactly one ticker
            if len(tickers) != 1:
                logger.warning(
                    f"Cannot create trade request: article has {len(tickers)} tickers, expected 1",
                    tickers=tickers
                )
                return None
            
            ticker = tickers[0]
            
            # Validate amount
            if amount_usd <= 0:
                logger.warning(f"Cannot create trade request: invalid amount: {amount_usd}")
                return None
            
            # Validate leverage if provided
            if leverage is not None:
                if leverage <= 0 or leverage > Decimal("2.0"):
                    logger.warning(f"Cannot create trade request: invalid leverage: {leverage}")
                    return None
            
            # Create domain TradeRequest
            trade_request = TradeRequest(
                ticker=ticker,
                action=action,
                amount_usd=amount_usd,
                leverage=leverage,
                instrument=TradeInstrument.STOCK,
                article_id=article.id,
                requested_at=datetime.now()
            )
            
            # Validate domain model
            if not TradeRequestValidator.is_valid_domain_trade_request(trade_request):
                logger.warning("Created trade request failed domain validation")
                return None
            
            logger.debug(
                "TradeRequestFactory: Created trade request",
                ticker=ticker,
                amount_usd=amount_usd,
                leverage=leverage
            )
            
            return trade_request
            
        except Exception as e:
            logger.error(
                "TradeRequestFactory: Error creating trade request from article",
                error=str(e),
                exc_info=True
            )
            return None
    
    @staticmethod
    def create_from_infrastructure_model(infra_request: InfrastructureTradeRequestData) -> Optional[TradeRequest]:
        """
        Create TradeRequest from infrastructure model using mapper + business rules.
        
        Uses mapper to transform infrastructure → domain, then applies business rules.
        
        Args:
            infra_request: Infrastructure trade request model
            
        Returns:
            Domain TradeRequest model, or None if invalid
        """
        try:
            # Use mapper to transform infrastructure → domain
            trade_request = TradeRequestMapper.from_infrastructure_model(infra_request)
            
            if not trade_request:
                logger.warning("TradeRequestFactory: Mapper failed to create trade request")
                return None
            
            # Apply additional business rules here if needed
            # (Mapper already does validation, but factories can add domain-specific logic)
            
            # Final validation (mapper already validates, but factories ensure business rules)
            if not TradeRequestValidator.is_valid_domain_trade_request(trade_request):
                logger.warning("TradeRequestFactory: Trade request failed business rule validation")
                return None
            
            logger.debug("TradeRequestFactory: Created trade request from infrastructure model", ticker=trade_request.ticker)
            return trade_request
            
        except Exception as e:
            logger.error(
                "TradeRequestFactory: Error creating trade request from infrastructure model",
                error=str(e),
                exc_info=True
            )
            return None
    
    @staticmethod
    def create_from_dict(data: Dict[str, Any]) -> Optional[TradeRequest]:
        """
        Create TradeRequest from dictionary with validation.
        
        Args:
            data: Trade request dictionary
            
        Returns:
            Domain TradeRequest model, or None if invalid
        """
        try:
            # Validate raw data first
            if not TradeRequestValidator.is_valid_trade_request_data(data):
                logger.warning("TradeRequestFactory: Invalid trade request data provided")
                return None
            
            # Create domain model
            trade_request = TradeRequest.from_dict(data)
            
            if not trade_request:
                logger.warning("TradeRequestFactory: Failed to create trade request from dict")
                return None
            
            # Validate domain model
            if not TradeRequestValidator.is_valid_domain_trade_request(trade_request):
                logger.warning("TradeRequestFactory: Created trade request failed domain validation")
                return None
            
            logger.debug("TradeRequestFactory: Created trade request from dict", ticker=trade_request.ticker)
            return trade_request
            
        except Exception as e:
            logger.error(
                "TradeRequestFactory: Error creating trade request from dict",
                error=str(e),
                exc_info=True
            )
            return None


class TradeResultFactory:
    """
    Factory for creating TradeResult domain objects.
    
    Uses mappers internally and applies business rules.
    """
    
    @staticmethod
    def create_from_infrastructure_event(infra_event: InfrastructureTradeExecutedEvent) -> Optional[TradeResult]:
        """
        Create TradeResult from infrastructure event using mapper + business rules.
        
        Uses mapper to transform infrastructure → domain, then applies business rules.
        
        Args:
            infra_event: Infrastructure trade executed event
            
        Returns:
            Domain TradeResult model, or None if invalid
        """
        try:
            # Use mapper to transform infrastructure → domain
            trade_result = TradeResultMapper.from_infrastructure_event(infra_event)
            
            if not trade_result:
                logger.warning("TradeResultFactory: Mapper failed to create trade result")
                return None
            
            # Apply additional business rules here if needed
            # (Mapper already does validation, but factories can add domain-specific logic)
            
            # Final validation (mapper already validates, but factories ensure business rules)
            if not TradeResultValidator.is_valid_domain_trade_result(trade_result):
                logger.warning("TradeResultFactory: Trade result failed business rule validation")
                return None
            
            logger.debug("TradeResultFactory: Created trade result from infrastructure event", ticker=trade_result.get_ticker())
            return trade_result
            
        except Exception as e:
            logger.error(
                "TradeResultFactory: Error creating trade result from infrastructure event",
                error=str(e),
                exc_info=True
            )
            return None
    
    @staticmethod
    def create_from_dict(data: Dict[str, Any]) -> Optional[TradeResult]:
        """
        Create TradeResult from dictionary with validation.
        
        Args:
            data: Trade result dictionary
            
        Returns:
            Domain TradeResult model, or None if invalid
        """
        try:
            # Validate raw data first
            if not TradeResultValidator.is_valid_trade_result_data(data):
                logger.warning("TradeResultFactory: Invalid trade result data provided")
                return None
            
            # Create domain model
            trade_result = TradeResult.from_dict(data)
            
            if not trade_result:
                logger.warning("TradeResultFactory: Failed to create trade result from dict")
                return None
            
            # Validate domain model
            if not TradeResultValidator.is_valid_domain_trade_result(trade_result):
                logger.warning("TradeResultFactory: Created trade result failed domain validation")
                return None
            
            return trade_result
            
        except Exception as e:
            logger.error(
                "TradeResultFactory: Error creating trade result from dict",
                error=str(e),
                exc_info=True
            )
            return None


class QuoteFactory:
    """
    Factory for creating Quote domain objects.
    
    Uses mappers internally and applies business rules.
    """
    
    @staticmethod
    def create_from_infrastructure_event(infra_event: InfrastructureQuoteReceivedEvent) -> Optional[Quote]:
        """
        Create Quote from infrastructure event using mapper + business rules.
        
        Uses mapper to transform infrastructure → domain, then applies business rules.
        
        Args:
            infra_event: Infrastructure quote received event
            
        Returns:
            Domain Quote model, or None if invalid
        """
        try:
            # Use mapper to transform infrastructure → domain
            quote = QuoteMapper.from_infrastructure_event(infra_event)
            
            if not quote:
                logger.warning("QuoteFactory: Mapper failed to create quote")
                return None
            
            # Apply additional business rules here if needed
            # (Mapper already does validation, but factories can add domain-specific logic)
            
            # Final validation (mapper already validates, but factories ensure business rules)
            if not QuoteValidator.is_valid_domain_quote(quote):
                logger.warning("QuoteFactory: Quote failed business rule validation")
                return None
            
            logger.debug("QuoteFactory: Created quote from infrastructure event", ticker=quote.ticker)
            return quote
            
        except Exception as e:
            logger.error(
                "QuoteFactory: Error creating quote from infrastructure event",
                error=str(e),
                exc_info=True
            )
            return None

