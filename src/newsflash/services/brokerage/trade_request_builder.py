"""
Trade request builder - builds domain TradeRequest from domain Article.

Pure business logic - uses domain models only.
No infrastructure dependencies.
"""
from typing import Optional
from decimal import Decimal

from ...domain.websocket.models import Article
from ...domain.brokerage.models import TradeRequest, TradeAction
from ...domain.brokerage.factories import TradeRequestFactory
from ...utils.logging_config import get_logger
from ...config.settings import AUTO_TRADE_AMOUNT_USD

logger = get_logger(__name__)


class TradeRequestBuilder:
    """
    Builds domain TradeRequest objects from domain Article models.
    
    Uses domain factories for proper business logic and validation.
    
    Responsibilities:
    - Select ticker from domain Article
    - Build trade request using domain factory
    - Apply leverage settings (2x default)
    
    Does NOT:
    - Execute trades (infrastructure does that)
    - Know about Telegram/notifications
    - Know about classification (that's done before this)
    - Know about infrastructure details
    """
    
    def __init__(self, default_notional: float = AUTO_TRADE_AMOUNT_USD, default_leverage: float = 2.0):
        """
        Initialize trade request builder.
        
        Args:
            default_notional: Default trade notional in USD
            default_leverage: Default leverage multiplier (2x)
        """
        self.default_notional = Decimal(str(default_notional))
        self.default_leverage = Decimal(str(default_leverage))
        self.factory = TradeRequestFactory()
        
        logger.info(
            "TradeRequestBuilder initialized",
            default_notional=str(self.default_notional),
            default_leverage=str(self.default_leverage)
        )
    
    def select_ticker(self, article: Article) -> Optional[str]:
        """
        Select which ticker to trade from a domain Article.
        
        Rules:
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
        self,
        ticker: str,
        article: Article,
        notional: Optional[Decimal] = None,
        leverage: Optional[Decimal] = None,
    ) -> Optional[TradeRequest]:
        """
        Build a domain TradeRequest from ticker and domain Article.
        
        Uses domain factory to ensure business rules are applied.
        
        Args:
            ticker: Stock ticker symbol
            article: Domain Article model
            notional: Trade notional in USD (defaults to self.default_notional)
            leverage: Leverage multiplier (defaults to self.default_leverage, 2x)
            
        Returns:
            Domain TradeRequest model, or None if invalid
        """
        trade_notional = notional or self.default_notional
        trade_leverage = leverage or self.default_leverage
        
        # Use domain factory to create trade request with business rules
        trade_request = self.factory.create_from_article(
            article=article,
            amount_usd=trade_notional,
            leverage=trade_leverage,
            action=TradeAction.BUY
        )
        
        if trade_request:
            logger.debug(
                "Built trade request using domain factory",
                ticker=ticker,
                notional=str(trade_notional),
                leverage=str(trade_leverage),
                article_id=article.id
            )
        
        return trade_request
    
    def build_trade_request_from_article(
        self,
        article: Article,
        notional: Optional[Decimal] = None,
        leverage: Optional[Decimal] = None,
    ) -> Optional[TradeRequest]:
        """
        Build a domain TradeRequest directly from a domain Article.
        
        Args:
            article: Domain Article model with tickers
            notional: Trade notional in USD (defaults to self.default_notional)
            leverage: Leverage multiplier (defaults to self.default_leverage, 2x)
            
        Returns:
            Domain TradeRequest model, or None if article has no tickers
        """
        ticker = self.select_ticker(article)
        if not ticker:
            return None
        
        return self.build_trade_request(ticker, article, notional, leverage)

