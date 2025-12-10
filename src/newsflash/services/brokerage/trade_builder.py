"""
Pure functions for building trade requests from articles.

Service layer - pure functions with typed inputs and outputs.
Uses domain models and factories for business logic.
"""
from typing import Optional
from decimal import Decimal

from ...domain.websocket.models import Article
from ...domain.brokerage.models import TradeRequest, TradeAction
from ...domain.brokerage.factories import TradeRequestFactory
from ...utils.logging_config import get_logger

logger = get_logger(__name__)


def select_ticker(article: Article) -> Optional[str]:
    """
    Select which ticker to trade from a domain Article.
    
    Business rules:
    - If article has NO tickers: return None
    - If article has 1+ tickers: return the FIRST ticker
      (usually the primary company mentioned in news)
    
    Args:
        article: Domain Article model with tickers
        
    Returns:
        Ticker symbol to trade, or None if no valid ticker
    """
    if not article.has_tickers():
        logger.debug(
            "Article has no tickers - cannot build trade request",
            article_id=article.id
        )
        return None
    
    tickers_list = list(article.tickers)
    ticker = tickers_list[0]  # First ticker is primary
    logger.info(
        "Selected ticker for trade request",
        ticker=ticker,
        all_tickers=tickers_list,
        article_id=article.id
    )
    return ticker


def build_trade_request(
    ticker: str,
    article: Article,
    amount_usd: Optional[Decimal] = None,
    leverage: Optional[Decimal] = None,
    action: TradeAction = TradeAction.BUY,
) -> Optional[TradeRequest]:
    """
    Build a domain TradeRequest from ticker and domain Article.
    
    Uses domain factory to ensure business rules are applied.
    
    Args:
        ticker: Stock ticker symbol
        article: Domain Article model
        amount_usd: Trade notional in USD (only needed if no leverage; not used when leverage is used)
        leverage: Leverage multiplier (defaults to None, max 2x)
        action: Trade action (default: BUY)
        
    Returns:
        Domain TradeRequest model, or None if invalid
    """
    # Use domain factory to create trade request with business rules
    trade_request = TradeRequestFactory.create_from_article(
        article=article,
        amount_usd=amount_usd,
        leverage=leverage,
        action=action
    )
    
    if trade_request:
        logger.debug(
            "Built trade request using domain factory",
            ticker=ticker,
            notional=str(amount_usd),
            leverage=str(leverage) if leverage else "None",
            article_id=article.id
        )
    
    return trade_request


def build_trade_request_from_article(
    article: Article,
    amount_usd: Optional[Decimal] = None,
    leverage: Optional[Decimal] = None,
    action: TradeAction = TradeAction.BUY,
) -> Optional[TradeRequest]:
    """
    Build a domain TradeRequest directly from a domain Article.
    
    NOTE: When leverage is used, amount_usd is not needed. The leverage calculation
    uses the price of 1 share as capital. Business rule: Pay for 1 share, leverage the second.
    
    Args:
        article: Domain Article model with tickers
        amount_usd: Trade notional in USD (only used if no leverage; not needed when leverage is used)
        leverage: Leverage multiplier (defaults to None, max 2x)
        action: Trade action (default: BUY)
        
    Returns:
        Domain TradeRequest model, or None if article has no tickers
    """
    ticker = select_ticker(article)
    if not ticker:
        return None
    
    return build_trade_request(ticker, article, amount_usd, leverage, action)


def validate_trade_request(trade_request: TradeRequest) -> bool:
    """
    Validate a trade request using domain validators.
    
    Args:
        trade_request: Domain TradeRequest to validate
        
    Returns:
        True if valid, False otherwise
    """
    from ...domain.brokerage.validators import TradeRequestValidator
    
    return TradeRequestValidator.is_valid_domain_trade_request(trade_request)


def create_default_trade_request(article: Article) -> Optional[TradeRequest]:
    """
    Create a trade request with default settings (2x leverage).
    
    Business rule: Pay for 1 share, leverage the second.
    No amount_usd needed - capital is always price of 1 share.
    
    Args:
        article: Domain Article model
        
    Returns:
        Domain TradeRequest model, or None if invalid
    """
    return build_trade_request_from_article(
        article=article,
        amount_usd=None,  # Not needed with leverage - capital is price of 1 share
        leverage=Decimal("2.0"),
        action=TradeAction.BUY
    )

