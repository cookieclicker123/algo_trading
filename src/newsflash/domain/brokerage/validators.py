"""
Validators for brokerage domain - business rule validation.
"""
from typing import Dict, Any
from decimal import Decimal

from ...utils.logging_config import get_logger
from .models import TradeRequest, TradeResult, Quote, TradeAction

logger = get_logger(__name__)


class TradeRequestValidator:
    """
    Validates trade requests according to business rules.
    
    Business Rules:
    1. Ticker must be valid format (1-5 chars, alphanumeric)
    2. Amount must be positive
    3. Leverage max 2x
    4. Action must be BUY or SELL
    5. Shares (if specified) must be positive
    6. Amount and shares must be consistent (can't specify both contradicting values)
    """
    
    @staticmethod
    def is_valid_trade_request_data(data: Dict[str, Any]) -> bool:
        """
        Validate raw trade request data before creating domain model.
        
        Args:
            data: Raw trade request dictionary
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Required fields
            if not data.get("ticker"):
                logger.debug("Trade request validation failed: missing ticker")
                return False
            
            ticker = data.get("ticker", "").upper().strip()
            if not ticker or len(ticker) > 5:
                logger.debug(f"Trade request validation failed: invalid ticker format: {ticker}")
                return False
            
            if not ticker.replace(".", "").replace("-", "").isalnum():
                logger.debug(f"Trade request validation failed: ticker contains invalid characters: {ticker}")
                return False
            
            # Validate amount (only required if no leverage AND no explicit shares)
            leverage = data.get("leverage")
            amount = data.get("amount_usd")
            shares = data.get("shares")
            
            if leverage is None or leverage <= 1.0:
                # No leverage: need either amount_usd or explicit shares
                if shares is None:
                    # No explicit shares: amount_usd is required
                    if amount is None:
                        logger.debug("Trade request validation failed: missing amount_usd (required when no leverage and no explicit shares)")
                        return False
                    
                    try:
                        amount_decimal = Decimal(str(amount))
                        if amount_decimal <= 0:
                            logger.debug(f"Trade request validation failed: amount must be positive: {amount}")
                            return False
                    except (ValueError, TypeError):
                        logger.debug(f"Trade request validation failed: invalid amount format: {amount}")
                        return False
            # With leverage: amount_usd is optional (we use price of 1 share as capital)
            
            # Validate action
            action = data.get("action", "BUY").upper()
            if action not in ["BUY", "SELL"]:
                logger.debug(f"Trade request validation failed: invalid action: {action}")
                return False
            
            # Validate leverage (if specified) - already extracted above
            if leverage is not None:
                try:
                    leverage_decimal = Decimal(str(leverage))
                    if leverage_decimal <= 0:
                        logger.debug(f"Trade request validation failed: leverage must be positive: {leverage}")
                        return False
                    if leverage_decimal > Decimal("2.0"):
                        logger.debug(f"Trade request validation failed: leverage exceeds max 2x: {leverage}")
                        return False
                except (ValueError, TypeError):
                    logger.debug(f"Trade request validation failed: invalid leverage format: {leverage}")
                    return False
            
            # Validate shares (if specified)
            shares = data.get("shares")
            if shares is not None:
                if not isinstance(shares, (int, float)) or shares <= 0:
                    logger.debug(f"Trade request validation failed: shares must be positive number: {shares}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error("Trade request validation error", error=str(e), exc_info=True)
            return False
    
    @staticmethod
    def is_valid_domain_trade_request(trade_request: TradeRequest) -> bool:
        """
        Validate a domain TradeRequest object.
        
        Args:
            trade_request: Domain TradeRequest object
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Validate ticker
            if not trade_request.ticker or len(trade_request.ticker) > 5:
                return False
            
            # Validate amount (only required if no leverage AND no explicit shares)
            if trade_request.leverage is None or trade_request.leverage <= 1.0:
                # No leverage: need either amount_usd or explicit shares
                if trade_request.shares is None:
                    # No explicit shares: amount_usd is required
                    if not trade_request.amount_usd or trade_request.amount_usd <= 0:
                        return False
            # With leverage: amount_usd is optional (we use price of 1 share as capital)
            
            # Validate action
            if trade_request.action not in [TradeAction.BUY, TradeAction.SELL]:
                return False
            
            # Validate leverage
            if trade_request.leverage is not None:
                if trade_request.leverage <= 0 or trade_request.leverage > Decimal("2.0"):
                    return False
            
            # Validate shares
            if trade_request.shares is not None and trade_request.shares <= 0:
                return False
            
            # Business rule: If both amount and shares specified, they should be consistent
            # (But we don't reject, just warn - this is handled during execution)
            
            return True
            
        except Exception as e:
            logger.error("Domain trade request validation error", error=str(e), exc_info=True)
            return False


class TradeResultValidator:
    """Validates trade execution results."""
    
    @staticmethod
    def is_valid_trade_result_data(data: Dict[str, Any]) -> bool:
        """Validate raw trade result data."""
        try:
            # Must have trade_request
            if "trade_request" not in data:
                return False
            
            # Must have success flag
            if "success" not in data:
                return False
            
            # Must have status
            if "status" not in data:
                return False
            
            # If successful, must have execution details
            if data.get("success"):
                if "shares" not in data or data.get("shares") is None:
                    return False
                if "fill_price" not in data or data.get("fill_price") is None:
                    return False
            
            return True
            
        except Exception as e:
            logger.error("Trade result validation error", error=str(e), exc_info=True)
            return False
    
    @staticmethod
    def is_valid_domain_trade_result(trade_result: TradeResult) -> bool:
        """Validate domain TradeResult object."""
        try:
            # Validate trade_request exists
            if not trade_result.trade_request:
                return False
            
            # Validate status matches success
            if trade_result.success and trade_result.status != "executed":
                logger.warning("Trade result success=True but status != executed")
            
            # If successful, must have execution details
            if trade_result.success:
                if not trade_result.shares or trade_result.shares <= 0:
                    return False
                if not trade_result.fill_price or trade_result.fill_price <= 0:
                    return False
            
            return True
            
        except Exception as e:
            logger.error("Domain trade result validation error", error=str(e), exc_info=True)
            return False


class QuoteValidator:
    """Validates market quotes."""
    
    @staticmethod
    def is_valid_quote_data(data: Dict[str, Any]) -> bool:
        """Validate raw quote data."""
        try:
            if not data.get("ticker"):
                return False
            
            bid = data.get("bid")
            ask = data.get("ask")
            
            if bid is None or ask is None:
                return False
            
            try:
                bid_decimal = Decimal(str(bid))
                ask_decimal = Decimal(str(ask))
                
                if bid_decimal <= 0 or ask_decimal <= 0:
                    return False
                
                if ask_decimal < bid_decimal:  # Ask must be >= bid (locked market where ask=bid is valid)
                    logger.warning("Quote validation: ask < bid, invalid quote")
                    return False
                
            except (ValueError, TypeError):
                return False
            
            return True
            
        except Exception as e:
            logger.error("Quote validation error", error=str(e), exc_info=True)
            return False
    
    @staticmethod
    def is_valid_domain_quote(quote: Quote) -> bool:
        """Validate domain Quote object."""
        try:
            if not quote.ticker:
                return False
            
            if quote.bid <= 0 or quote.ask <= 0:
                return False
            
            if quote.ask < quote.bid:  # Locked market where ask=bid is valid
                return False
            
            return True
            
        except Exception as e:
            logger.error("Domain quote validation error", error=str(e), exc_info=True)
            return False

