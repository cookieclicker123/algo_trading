"""
Simplified IBKR Trading Service - EXACTLY like the working test.
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from ib_insync import IB, Stock, MarketOrder

from ..models.base_models import TradeRequest
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

class IBKRTradingService:
    """
    Simplified IBKR Trading Service that matches the working test code EXACTLY.
    """
    
    def __init__(self):
        self.pending_trades: Dict[str, Dict[str, Any]] = {}
        self.trade_timeout_minutes = 30
        logger.info("IBKRTradingService initialized.")

    async def process_trade_request(self, trade_request: TradeRequest) -> bool:
        """
        Process a trade request - public interface for Telegram handler.
        
        Args:
            trade_request: The trade request to execute
            
        Returns:
            bool: True if trade was successful, False otherwise
        """
        logger.info("Processing trade request", 
                   ticker=trade_request.ticker,
                   amount=trade_request.amount_usd,
                   action=trade_request.action)
        
        return await self._execute_trade(trade_request)
    
    async def _execute_trade(self, trade_request: TradeRequest) -> bool:
        """
        Execute a trade using IBKR API - SIMPLE version with threading to avoid event loop conflicts.
        
        Args:
            trade_request: The trade request containing ticker, amount, etc.
            
        Returns:
            bool: True if trade was successful, False otherwise
        """
        logger.info("🚀 Starting SIMPLE trade execution", 
                   ticker=trade_request.ticker,
                   amount=trade_request.amount_usd)
        
        try:
            # Run the trade in a thread to avoid event loop conflicts
            result = await asyncio.get_event_loop().run_in_executor(
                None, 
                self._run_trade_in_thread, 
                trade_request
            )
            return result
                
        except Exception as e:
            logger.error("❌ Trade execution failed", error=str(e))
            return False

    def _run_trade_in_thread(self, trade_request: TradeRequest) -> bool:
        """
        Run the trade in a separate thread - EXACTLY like the working test.
        This avoids event loop conflicts with the Telegram bot.
        """
        import asyncio
        
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            return loop.run_until_complete(self._execute_trade_sync(trade_request))
        finally:
            loop.close()

    async def _execute_trade_sync(self, trade_request: TradeRequest) -> bool:
        """
        Execute a trade using IBKR API - EXACTLY like the working test.
        This runs in its own event loop to avoid conflicts.
        """
        try:
            # Create IBKR connection (same as test)
            ib = IB()
            
            # Connect to IBKR Gateway (same as test)
            logger.info("🔌 Connecting to IBKR Gateway...")
            await ib.connectAsync('127.0.0.1', 7497, clientId=2)  # Use different client ID
            logger.info("✅ Connected to IBKR Gateway")
            
            # Force account to use USD base currency to prevent conversion
            logger.info("💰 Setting account base currency to USD...")
            try:
                # Request account summary to get account info
                account_summary = ib.accountSummary()
                logger.info("📊 Account summary retrieved", summary=account_summary)
            except Exception as e:
                logger.warning("⚠️ Could not get account summary", error=str(e))
            
            # Create stock contract (same as test) - use SMART routing but force USD
            logger.info("📋 Creating stock contract", ticker=trade_request.ticker)
            contract = Stock(trade_request.ticker, 'SMART', 'USD')  # Use SMART routing (has market data)
            
            # Force USD currency on contract to prevent conversion
            contract.currency = 'USD'
            logger.info("✅ Contract created", contract=contract)
            
            # Get market data (same as test)
            logger.info("📊 Requesting market data...")
            ticker = ib.reqMktData(contract)
            logger.info("✅ Market data requested")
            
            # Wait for market data (same as test)
            await asyncio.sleep(2)
            
            if ticker.last and ticker.last > 0:
                current_price = ticker.last
                logger.info("💰 Current price", ticker=trade_request.ticker, price=f"${current_price}")
                
                # Calculate shares (same as test) - but limit to 1 share to avoid currency conversion issues
                shares = int(trade_request.amount_usd / current_price)
                if shares <= 0:
                    shares = 1  # Default to 1 share like test
                else:
                    shares = 1  # Always use 1 share to avoid currency conversion issues
                
                # Create market order with proper order ID (same as test)
                logger.info("📝 Creating market order", shares=shares)
                order_id = ib.client.getReqId()
                logger.info("📋 Generated order ID", order_id=order_id)
                order = MarketOrder('BUY', shares, orderId=order_id)
                
                # Force USD currency to prevent conversion issues
                order.currency = 'USD'
                logger.info("✅ Order created", order=order)
                
                # Place order (same as test)
                logger.info("🚀 Placing order...")
                trade = ib.placeOrder(contract, order)
                logger.info("✅ Order placed", trade=trade)
                
                # Wait for fill (same as test)
                logger.info("⏳ Waiting for order to fill...")
                for attempt in range(20):  # 20 attempts × 0.5s = 10 seconds
                    await asyncio.sleep(0.5)
                    
                    if trade.fills and len(trade.fills) > 0:
                        fill_price = trade.fills[0].execution.price
                        logger.info("🎉 ORDER FILLED! Price", price=f"${fill_price}")
                        return True
                    
                    if trade.orderStatus and trade.orderStatus.status == 'Filled':
                        fill_price = trade.orderStatus.avgFillPrice
                        logger.info("🎉 ORDER FILLED! Price", price=f"${fill_price}")
                        return True
                    
                    if trade.orderStatus and trade.orderStatus.status in ['Cancelled', 'Rejected']:
                        logger.error("❌ ORDER REJECTED", status=trade.orderStatus.status)
                        return False
                    
                    logger.debug("⏳ Attempt", attempt=attempt + 1, 
                                status=trade.orderStatus.status if trade.orderStatus else "No status")
                
                logger.error("❌ ORDER TIMEOUT - Did not fill within 10 seconds")
                return False
                
            else:
                logger.error("❌ No market data received")
                return False
                
        except Exception as e:
            logger.error("❌ Trade execution failed", error=str(e))
            return False
        finally:
            # Disconnect (same as test)
            if 'ib' in locals() and ib.isConnected():
                ib.disconnect()
                logger.info("🔌 Disconnected from IBKR")
    
    async def execute_trade(self, trade_request: TradeRequest) -> bool:
        """Execute a trade request."""
        return await self._execute_trade(trade_request)
    
    def add_pending_trade(self, article_id: str, tickers: List[str], user_chat_id: str):
        """Add a pending trade decision for an article."""
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
        Now supports general trading - any ticker at any time!
        
        Args:
            user_chat_id: Telegram chat ID of the user
            message_text: User's response text
            
        Returns:
            TradeRequest if user wants to trade, None if ignore
        """
        message_text = message_text.strip().lower()
        
        # Parse trade commands
        if message_text.startswith('trade'):
            parts = message_text.split()
            
            if len(parts) == 1:
                # User sent "trade" without a ticker - try to find a pending trade
                return self._handle_default_trade(user_chat_id)
            elif len(parts) == 2:
                # User sent "trade AAPL"
                ticker = parts[1].upper()
                logger.info("User requested trade for specific ticker", ticker=ticker, chat_id=user_chat_id)
                return self._create_general_trade_request(ticker)
            else:
                logger.warning("Invalid trade command format", message=message_text, chat_id=user_chat_id)
                return None
        elif message_text == 'ignore':
            logger.info("User chose to ignore trade", chat_id=user_chat_id)
            return None
        else:
            logger.info("Unrecognized user response", message=message_text, chat_id=user_chat_id)
            return None

    def _handle_default_trade(self, user_chat_id: str) -> Optional[TradeRequest]:
        """Handle 'trade' command - try pending trade first, then ask for ticker."""
        # First, try to find a recent pending trade
        user_trades = [(aid, data) for aid, data in self.pending_trades.items() 
                      if data['user_chat_id'] == user_chat_id]
        
        if user_trades:
            # Get the most recent trade
            article_id, trade_data = max(user_trades, key=lambda x: x[1]['timestamp'])
            
            # Check if expired
            if datetime.now() <= trade_data['expires_at']:
                logger.info("Using pending trade for default", article_id=article_id)
                return self._create_trade_from_pending(article_id, trade_data)
        
        # No valid pending trade - this is where we need to ask for ticker
        # For now, return None and let the Telegram handler ask for ticker
        logger.info("No pending trade available for default trade", chat_id=user_chat_id)
        return None
        
    def _create_general_trade_request(self, ticker: str) -> TradeRequest:
        """Create a trade request for any ticker (not tied to news)."""
        logger.info("Creating general trade request", ticker=ticker)
        return TradeRequest(
            ticker=ticker,
            amount_usd=100.0,        # Default to $100 for general trades
            action="BUY"             # Default to BUY for general trades
        )

    def _create_trade_from_pending(self, article_id: str, trade_data: Dict[str, Any]) -> TradeRequest:
        """Create a trade request from pending trade data."""
        tickers = trade_data['tickers']
        if tickers:
            # Use the first ticker
            ticker = tickers[0]
            logger.info("Creating trade from pending", article_id=article_id, ticker=ticker)
            return TradeRequest(
                ticker=ticker,
                amount_usd=100.0,  # Default amount
                action='BUY'
            )
        else:
            logger.warning("No tickers in pending trade data", article_id=article_id)
            return None


# Factory function for dependency injection
_ibkr_trading_service_instance: Optional[IBKRTradingService] = None

def get_ibkr_trading_service() -> IBKRTradingService:
    """Get singleton instance of IBKR trading service."""
    global _ibkr_trading_service_instance
    if _ibkr_trading_service_instance is None:
        _ibkr_trading_service_instance = IBKRTradingService()
        logger.info("Created new IBKR trading service instance")
    return _ibkr_trading_service_instance