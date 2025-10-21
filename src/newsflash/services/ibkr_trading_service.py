"""
Interactive Brokers trading service for automated trade execution.
Connects to real IBKR account for live trading.
"""
import asyncio
import structlog
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass

logger = structlog.get_logger(__name__)


@dataclass
class TradeRequest:
    """Represents a trade request from Telegram."""
    ticker: str
    amount_usd: float = 100.0
    action: str = "BUY"  # Always buy for news trading
    article_id: str = ""
    user_chat_id: str = ""
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class IBKRTradingService:
    """
    Service for executing trades through Interactive Brokers.
    Connects to real IBKR account for live trading.
    """
    
    def __init__(self, enabled: bool = True):
        """
        Initialize IBKR trading service.
        
        Args:
            enabled: Whether trading is enabled (set to False for testing)
        """
        self.enabled = enabled
        self.pending_trades: Dict[str, TradeRequest] = {}
        self.trade_timeout_minutes = 30  # Timeout for trade decisions
        
        if not enabled:
            logger.info("IBKR trading service disabled (test mode)")
            return
            
        try:
            # Initialize IBKR connection
            self._initialize_ibkr_connection()
            logger.info("IBKR trading service initialized")
        except Exception as e:
            logger.error("Failed to initialize IBKR trading service", error=str(e))
            self.enabled = False
    
    def _initialize_ibkr_connection(self):
        """Initialize connection to IBKR TWS/Gateway."""
        try:
            from ib_insync import IB
            self.ib = IB()
            logger.info("IBKR connection initialized - ready to connect to localhost:7497")
        except ImportError:
            logger.error("ib_insync not available - install with: pip install ib-insync")
            self.ib = None
    
    async def process_trade_request(self, trade_request: TradeRequest) -> bool:
        """
        Process a trade request and execute the trade.
        
        Args:
            trade_request: The trade request to process
            
        Returns:
            True if trade was executed successfully
        """
        if not self.enabled:
            logger.info("Trading disabled - would execute trade", 
                       ticker=trade_request.ticker,
                       amount=trade_request.amount_usd)
            return True
        
        try:
            logger.info("Executing trade", 
                       ticker=trade_request.ticker,
                       amount=trade_request.amount_usd,
                       action=trade_request.action)
            
            # Execute the actual trade through IBKR
            success = await self._execute_trade(trade_request)
            
            if success:
                logger.info("Trade executed successfully",
                           ticker=trade_request.ticker,
                           amount=trade_request.amount_usd)
            else:
                logger.error("Trade execution failed",
                           ticker=trade_request.ticker,
                           amount=trade_request.amount_usd)
            
            return success
            
        except Exception as e:
            logger.error("Failed to process trade request", 
                        ticker=trade_request.ticker,
                        error=str(e))
            return False
    
    async def _execute_trade(self, trade_request: TradeRequest) -> bool:
        """
        Execute the actual trade through IBKR API.
        
        Args:
            trade_request: The trade request to execute
            
        Returns:
            True if trade was successful
        """
        try:
            if not self.ib:
                logger.error("IBKR connection not available")
                return False
            
            from ib_insync import Stock, MarketOrder
            
            logger.info("Connecting to IBKR Gateway", 
                       ticker=trade_request.ticker,
                       amount=trade_request.amount_usd)
            
            # Connect to IBKR Gateway
            await self.ib.connectAsync('127.0.0.1', 7497, clientId=1)
            
            # Create stock contract
            contract = Stock(trade_request.ticker, 'SMART', 'USD')
            
            # Get current price
            ticker_info = self.ib.reqMktData(contract)
            await asyncio.sleep(2)  # Wait for price data
            
            if not ticker_info.last or ticker_info.last <= 0:
                logger.error("Could not get valid price for ticker", 
                           ticker=trade_request.ticker,
                           price=ticker_info.last)
                self.ib.disconnect()
                return False
            
            # Calculate shares based on $100 amount
            shares = int(trade_request.amount_usd / ticker_info.last)
            
            if shares <= 0:
                logger.error("Invalid share count calculated", 
                           shares=shares,
                           price=ticker_info.last,
                           amount=trade_request.amount_usd)
                self.ib.disconnect()
                return False
            
            # Create market order
            order = MarketOrder('BUY', shares)
            
            logger.info("Placing IBKR order",
                       ticker=trade_request.ticker,
                       shares=shares,
                       price=ticker_info.last,
                       total_value=shares * ticker_info.last)
            
            # Place the order
            trade = self.ib.placeOrder(contract, order)
            
            # Wait for order to be filled
            await trade
            
            # Check if order was filled
            if trade.isDone() and trade.orderStatus.status == 'Filled':
                logger.info("Trade executed successfully",
                           ticker=trade_request.ticker,
                           shares=shares,
                           fill_price=trade.orderStatus.avgFillPrice,
                           total_value=shares * float(trade.orderStatus.avgFillPrice))
                
                self.ib.disconnect()
                return True
            else:
                logger.error("Trade not filled",
                           ticker=trade_request.ticker,
                           status=trade.orderStatus.status)
                self.ib.disconnect()
                return False
                
        except Exception as e:
            logger.error("IBKR trade execution error", 
                        ticker=trade_request.ticker,
                        error=str(e))
            if self.ib and self.ib.isConnected():
                self.ib.disconnect()
            return False
    
    def add_pending_trade(self, article_id: str, tickers: List[str], user_chat_id: str):
        """
        Add a pending trade decision for an article.
        
        Args:
            article_id: Unique identifier for the article
            tickers: List of tickers associated with the article
            user_chat_id: Telegram chat ID of the user
        """
        self.pending_trades[article_id] = {
            'tickers': tickers,
            'user_chat_id': user_chat_id,
            'timestamp': datetime.now(),
            'expires_at': datetime.now() + timedelta(minutes=self.trade_timeout_minutes)
        }
        
        logger.info("Added pending trade decision",
                   article_id=article_id,
                   tickers=tickers,
                   expires_in_minutes=self.trade_timeout_minutes)
    
    def process_user_response(self, user_chat_id: str, message_text: str) -> Optional[TradeRequest]:
        """
        Process user response to a trade decision.
        
        Args:
            user_chat_id: Telegram chat ID of the user
            message_text: User's response text
            
        Returns:
            TradeRequest if user wants to trade, None if ignore
        """
        # Find the most recent pending trade for this user
        user_trades = [(aid, data) for aid, data in self.pending_trades.items() 
                      if data['user_chat_id'] == user_chat_id]
        
        if not user_trades:
            logger.warning("No pending trades found for user", chat_id=user_chat_id)
            return None
        
        # Get the most recent trade
        article_id, trade_data = max(user_trades, key=lambda x: x[1]['timestamp'])
        
        # Check if expired
        if datetime.now() > trade_data['expires_at']:
            logger.info("Trade decision expired", article_id=article_id)
            del self.pending_trades[article_id]
            return None
        
        # Parse user response
        message_text = message_text.strip().lower()
        
        if message_text == "ignore":
            logger.info("User chose to ignore trade", article_id=article_id)
            del self.pending_trades[article_id]
            return None
        
        elif message_text.startswith("trade"):
            # Extract ticker if specified
            parts = message_text.split()
            if len(parts) > 1:
                specified_ticker = parts[1].upper()
                if specified_ticker in trade_data['tickers']:
                    ticker = specified_ticker
                else:
                    logger.warning("Specified ticker not in article", 
                                 specified=specified_ticker,
                                 available=trade_data['tickers'])
                    ticker = trade_data['tickers'][0]  # Default to first ticker
            else:
                ticker = trade_data['tickers'][0]  # Default to first ticker
            
            # Create trade request
            trade_request = TradeRequest(
                ticker=ticker,
                amount_usd=100.0,
                article_id=article_id,
                user_chat_id=user_chat_id
            )
            
            # Remove from pending trades
            del self.pending_trades[article_id]
            
            logger.info("User chose to trade", 
                       article_id=article_id,
                       ticker=ticker)
            
            return trade_request
        
        else:
            logger.warning("Invalid user response", 
                          message=message_text,
                          chat_id=user_chat_id)
            return None
    
    def cleanup_expired_trades(self):
        """Remove expired trade decisions."""
        now = datetime.now()
        expired = [aid for aid, data in self.pending_trades.items() 
                  if now > data['expires_at']]
        
        for article_id in expired:
            logger.info("Cleaning up expired trade decision", article_id=article_id)
            del self.pending_trades[article_id]


def get_ibkr_trading_service() -> IBKRTradingService:
    """Get IBKR trading service instance."""
    # Set enabled=True for live trading
    return IBKRTradingService(enabled=True)  # Live trading enabled
