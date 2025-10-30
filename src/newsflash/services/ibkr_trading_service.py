"""
IBKR Trading Service - Amalgamation of Market Hours and Extended Hours Tests.
Unified trading engine with market session detection.
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple, Union
from ib_insync import IB, Stock, MarketOrder, LimitOrder
import pytz
import yfinance as yf

from ..models.base_models import TradeRequest
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

class TradeResult:
    """Result of a trade execution."""
    def __init__(self, success: bool, shares: int = 0, fill_price: float = 0.0, 
                 total_cost: float = 0.0, commission: float = 0.0, error: str = "",
                 session: str = "", order_type: str = "", timing_info: Dict[str, float] = None,
                 limit_price_used: Optional[float] = None, percentage_above_below: Optional[float] = None):
        self.success = success
        self.shares = shares
        self.fill_price = fill_price
        self.total_cost = total_cost
        self.commission = commission
        self.error = error
        self.session = session  # "market_hours", "premarket", "postmarket", "closed"
        self.order_type = order_type  # "MARKET" or "LIMIT"
        self.timing_info = timing_info or {}  # Dict with timing breakdown
        self.limit_price_used = limit_price_used  # For limit orders
        self.percentage_above_below = percentage_above_below  # For extended hours limit orders

class IBKRTradingService:
    """
    Unified IBKR Trading Service combining market hours and extended hours strategies.
    Automatically detects market session and uses appropriate trading method.
    """
    
    def __init__(self, paper_trading: bool = False):
        self.paper_trading = paper_trading
        self.pending_trades: Dict[str, Dict[str, Any]] = {}
        self.trade_timeout_minutes = 30
        
        if paper_trading:
            logger.info("IBKRTradingService initialized in PAPER TRADING mode.")
        else:
            logger.info("IBKRTradingService initialized in LIVE TRADING mode.")

    def get_market_session(self) -> Tuple[str, bool]:
        """Determine current market session based on Eastern Time."""
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        premarket_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
        postmarket_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
        
        logger.info(f"🕐 Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        
        if market_open <= now_et < market_close:
            logger.info("📈 Currently in MARKET HOURS")
            return 'market_hours', False
        elif premarket_start <= now_et < market_open:
            logger.info("🌅 Currently in PREMARKET")
            return 'premarket', True
        elif market_close <= now_et < postmarket_end:
            logger.info("🌆 Currently in POSTMARKET")
            return 'postmarket', True
        else:
            logger.info("🌙 Currently MARKET CLOSED")
            return 'closed', True

    async def get_yfinance_price(self, ticker_symbol: str) -> Optional[float]:
        """Get INSTANT price from yfinance with prepost=True."""
        try:
            logger.info(f"📊 Getting INSTANT price from yfinance for {ticker_symbol}...")
            
            ticker = yf.Ticker(ticker_symbol)
            data = ticker.history(period="1d", interval="1m", prepost=True)
            
            if not data.empty:
                # Use the last available price
                current_price = data['Close'].iloc[-1]
                logger.info(f"💰 yfinance price: ${current_price}")
                return float(current_price)
            else:
                logger.error(f"❌ No data available for {ticker_symbol}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error fetching {ticker_symbol} price from yfinance: {e}")
            return None

    async def process_trade_request(self, trade_request: TradeRequest) -> TradeResult:
        """
        Process a trade request - public interface for Telegram handler.
        
        Args:
            trade_request: The trade request to execute
            
        Returns:
            TradeResult: Detailed result of the trade execution
        """
        logger.info("🚀 Processing trade request", 
                   ticker=trade_request.ticker,
                   amount=trade_request.amount_usd,
                   action=trade_request.action)
        
        return await self._execute_trade(trade_request)
    
    async def _execute_trade(self, trade_request: TradeRequest) -> TradeResult:
        """
        Execute a trade using IBKR API with market session detection.
        
        Args:
            trade_request: The trade request containing ticker, amount, etc.
            
        Returns:
            bool: True if trade was successful, False otherwise
        """
        logger.info("🚀 Starting UNIFIED trade execution", 
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
            return TradeResult(success=False, error=str(e))

    def _run_trade_in_thread(self, trade_request: TradeRequest) -> TradeResult:
        """Run the trade in a separate thread to avoid event loop conflicts."""
        import asyncio
        
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            return loop.run_until_complete(self._execute_trade_sync(trade_request))
        finally:
            loop.close()

    async def _execute_trade_sync(self, trade_request: TradeRequest) -> TradeResult:
        """
        Execute a trade using IBKR API with market session detection.
        This runs in its own event loop to avoid conflicts.
        """
        try:
            # Start total timing
            total_start_time = time.time()
            
            # Check market session
            session_start = time.time()
            session, is_extended = self.get_market_session()
            session_time = time.time() - session_start
            logger.info(f"⏱️ Market session detection: {session_time:.3f}s")
            
            # Check if we can trade
            if session == 'closed':
                logger.error("❌ Market is currently closed - no trading available")
                return TradeResult(
                    success=False, 
                    error="Market is currently closed",
                    session="closed"
                )
            
            # Create IBKR connection
            ib = IB()
            
            # Connect to IBKR Gateway
            connect_start = time.time()
            port = 4001 if self.paper_trading else 7497
            mode = "Paper Trading" if self.paper_trading else "Live Trading"
            logger.info(f"🔌 Connecting to IBKR {mode} Gateway (port {port})...")
            await ib.connectAsync('127.0.0.1', port, clientId=5)  # Use different client ID
            connect_time = time.time() - connect_start
            logger.info(f"✅ Connected to IBKR Gateway - {connect_time:.3f}s")
            
            # Create stock contract
            contract_start = time.time()
            logger.info(f"📋 Creating {trade_request.ticker} contract...")
            contract = Stock(trade_request.ticker, 'SMART', 'USD')
            contract_time = time.time() - contract_start
            logger.info(f"✅ Contract created: {contract} - {contract_time:.3f}s")
            
            # Execute based on market session
            if session == 'market_hours':
                logger.info("📈 MARKET HOURS: Using market order strategy")
                success = await self._execute_market_hours_trade(ib, contract, trade_request, total_start_time, session_time, connect_time, contract_time)
            else:
                logger.info(f"🌅 EXTENDED HOURS ({session}): Using limit order strategy")
                success = await self._execute_extended_hours_trade(ib, contract, trade_request, total_start_time, session_time, connect_time, contract_time)
            
            return success
                
        except Exception as e:
            logger.error("❌ Trade execution failed", error=str(e))
            logger.error(f"📝 Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"📝 Full traceback:\n{traceback.format_exc()}")
            return TradeResult(success=False, error=str(e))
        finally:
            # Disconnect
            if 'ib' in locals() and ib.isConnected():
                ib.disconnect()
                logger.info("🔌 Disconnected from IBKR")

    async def _execute_market_hours_trade(self, ib: IB, contract: Stock, trade_request: TradeRequest, 
                                        total_start_time: float, session_time: float, connect_time: float, contract_time: float) -> TradeResult:
        """Execute market hours trade using market order."""
        try:
            # Create market order - use action from trade_request
            action = trade_request.action.upper()
            order_create_start = time.time()
            logger.info(f"📝 Creating market order for 1 share ({action})...")
            order = MarketOrder(action, 1)
            order_create_time = time.time() - order_create_start
            logger.info(f"✅ Market order created: {order} (create: {order_create_time:.3f}s)")
            
            # Place the order
            place_start = time.time()
            logger.info("🚀 Placing market order...")
            trade = ib.placeOrder(contract, order)
            place_time = time.time() - place_start
            logger.info(f"✅ Order placed: {trade} (place: {place_time:.3f}s)")
            
            # Wait for fill
            fill_wait_start = time.time()
            logger.info("⏳ Waiting for immediate fill...")
            
            for attempt in range(10):  # 10 attempts × 0.5s = 5 seconds
                await asyncio.sleep(0.5)
                
                if trade.isDone():
                    fill_price = trade.orderStatus.avgFillPrice
                    fill_wait_time = time.time() - fill_wait_start
                    total_time = time.time() - total_start_time
                    
                    logger.info(f"🎉 ORDER FILLED! Price: ${fill_price}")
                    logger.info(f"⏱️ Fill wait time: {fill_wait_time:.3f}s")
                    logger.info(f"⏱️ TOTAL TIME: {total_time:.3f}s")
                    
                    # Performance summary
                    logger.info("📈 MARKET HOURS PERFORMANCE SUMMARY:")
                    logger.info(f"   📊 Market session detection: {session_time:.3f}s")
                    logger.info(f"   🔌 Connection: {connect_time:.3f}s")
                    logger.info(f"   📋 Contract creation: {contract_time:.3f}s")
                    logger.info(f"   📝 Order creation: {order_create_time:.3f}s")
                    logger.info(f"   🚀 Order placement: {place_time:.3f}s")
                    logger.info(f"   ⏳ Fill wait: {fill_wait_time:.3f}s")
                    logger.info(f"   ⚡ TOTAL: {total_time:.3f}s")
                    
                    return TradeResult(
                        success=True, 
                        shares=1, 
                        fill_price=fill_price, 
                        total_cost=fill_price,
                        session="market_hours",
                        order_type="MARKET",
                        timing_info={
                            "session_detection": session_time,
                            "connection": connect_time,
                            "contract_creation": contract_time,
                            "order_creation": order_create_time,
                            "order_placement": place_time,
                            "fill_wait": fill_wait_time,
                            "total_time": total_time
                        }
                    )
                
                logger.debug(f"⏳ Attempt {attempt + 1}: Status = {trade.orderStatus.status}")
            
            total_time = time.time() - total_start_time
            logger.warning("⚠️ ORDER TIMEOUT - Did not fill within 5 seconds")
            return TradeResult(
                success=False, 
                error="Order timeout - did not fill within 5 seconds",
                session="market_hours",
                order_type="MARKET",
                timing_info={
                    "session_detection": session_time,
                    "connection": connect_time,
                    "contract_creation": contract_time,
                    "order_creation": order_create_time,
                    "order_placement": place_time,
                    "total_time": total_time
                }
            )
            
        except Exception as e:
            logger.error(f"❌ Market hours trade failed: {e}")
            return TradeResult(
                success=False, 
                error=str(e),
                session="market_hours",
                order_type="MARKET"
            )

    async def _execute_extended_hours_trade(self, ib: IB, contract: Stock, trade_request: TradeRequest,
                                          total_start_time: float, session_time: float, connect_time: float, contract_time: float) -> TradeResult:
        """Execute extended hours trade using limit order with aggressive continuation."""
        # Get session info for TradeResult
        session, is_extended = self.get_market_session()
        try:
            # Get INSTANT price from yfinance
            price_start = time.time()
            current_price = await self.get_yfinance_price(contract.symbol)
            price_time = time.time() - price_start
            logger.info(f"💰 Price retrieval: {price_time:.3f}s")
            
            if not current_price:
                logger.error("❌ Could not get price from yfinance - aborting trade")
                return TradeResult(
                    success=False, 
                    error="Could not get price from yfinance",
                    session=session,
                    order_type="LIMIT"
                )
            
            logger.info(f"💰 Current {contract.symbol} price: ${current_price}")
            
            # AGGRESSIVE CONTINUATION: Adjust based on action (BUY = above price, SELL = below price)
            action = trade_request.action.upper()
            if action == "BUY":
                logger.info("🚀 Starting AGGRESSIVE CONTINUATION: 0.25% to 10% above price with 0.0001s intervals")
                base_percentage = 0.25   # Start at 0.25% above price
                max_percentage = 10.0    # Go up to 10% above price
                increment = 0.25        # Increase by 0.25% each time
            else:  # SELL
                logger.info("🚀 Starting AGGRESSIVE CONTINUATION: 0.25% to 10% below price with 0.0001s intervals")
                base_percentage = 0.25   # Start at 0.25% below price
                max_percentage = 10.0    # Go down to 10% below price
                increment = 0.25        # Decrease by 0.25% each time
            
            wait_time = 0.0001      # Wait 0.0001 seconds between attempts (INSTANT)
            current_percentage = base_percentage
            attempt_number = 1
            
            # Start trading timing
            trading_start = time.time()
            
            while current_percentage <= max_percentage:
                attempt_start = time.time()
                direction = "above" if action == "BUY" else "below"
                logger.info(f"🚀 Attempt {attempt_number}: {current_percentage}% {direction} yfinance price")
                
                # Calculate limit price using yfinance price
                calc_start = time.time()
                if action == "BUY":
                    limit_price = round(current_price * (1 + current_percentage / 100), 2)
                else:  # SELL
                    limit_price = round(current_price * (1 - current_percentage / 100), 2)
                calc_time = time.time() - calc_start
                logger.info(f"📈 Limit price: ${limit_price:.2f} ({current_percentage}% {'above' if action == 'BUY' else 'below'}) (calc: {calc_time:.3f}s)")
                
                # Create limit order (action already defined above)
                order_create_start = time.time()
                order_id = ib.client.getReqId()
                order = LimitOrder(action, 1, limit_price, orderId=order_id)
                order.outsideRth = True
                order_create_time = time.time() - order_create_start
                logger.info(f"✅ Limit order created: {order} ({action}) (create: {order_create_time:.3f}s)")
                
                # Place order
                place_start = time.time()
                logger.info("🚀 Placing limit order...")
                trade = ib.placeOrder(contract, order)
                place_time = time.time() - place_start
                logger.info(f"✅ Order placed: {trade} (place: {place_time:.3f}s)")
                
                # Wait for INSTANT fill detection
                fill_wait_start = time.time()
                logger.info("⚡ Waiting for INSTANT fill...")
                filled = False
                
                for check_attempt in range(5):  # 5 attempts × 0.1s = 0.5 seconds
                    await asyncio.sleep(0.1)
                    
                    if trade.isDone():
                        fill_wait_time = time.time() - fill_wait_start
                        fill_price = trade.orderStatus.avgFillPrice
                        total_trading_time = time.time() - trading_start
                        total_time = time.time() - total_start_time
                        
                        logger.info(f"🎉 ORDER FILLED! Price: ${fill_price}")
                        logger.info(f"✅ SUCCESS at attempt {attempt_number}: {current_percentage}% above yfinance price")
                        logger.info(f"⏱️ Fill wait time: {fill_wait_time:.3f}s")
                        logger.info(f"⏱️ Total trading time: {total_trading_time:.3f}s")
                        logger.info(f"⏱️ TOTAL TIME: {total_time:.3f}s")
                        
                        # Performance summary
                        logger.info("🚀 EXTENDED HOURS PERFORMANCE SUMMARY:")
                        logger.info(f"   📊 Market session detection: {session_time:.3f}s")
                        logger.info(f"   🔌 Connection: {connect_time:.3f}s")
                        logger.info(f"   📋 Contract creation: {contract_time:.3f}s")
                        logger.info(f"   📊 yfinance price retrieval: {price_time:.3f}s")
                        logger.info(f"   🚀 Trading (to fill): {total_trading_time:.3f}s")
                        logger.info(f"   ⚡ TOTAL: {total_time:.3f}s")
                        
                        return TradeResult(
                            success=True, 
                            shares=1, 
                            fill_price=fill_price, 
                            total_cost=fill_price,
                            session=session,
                            order_type="LIMIT",
                            timing_info={
                                "session_detection": session_time,
                                "connection": connect_time,
                                "contract_creation": contract_time,
                                "price_retrieval": price_time,
                                "trading_time": total_trading_time,
                                "total_time": total_time,
                                "attempts": attempt_number
                            },
                            limit_price_used=limit_price,
                            percentage_above_below=current_percentage
                        )
                    
                    if trade.orderStatus and trade.orderStatus.status in ['Cancelled', 'Rejected']:
                        logger.warning(f"⚠️ Order rejected at {current_percentage}%: {trade.orderStatus.status}")
                        # Get rejection reason
                        if trade.log and len(trade.log) > 0:
                            last_log = trade.log[-1]
                            if last_log.message:
                                logger.warning(f"📝 Rejection reason: {last_log.message}")
                        break
                
                attempt_time = time.time() - attempt_start
                logger.info(f"⏱️ Attempt {attempt_number} total time: {attempt_time:.3f}s")
                
                if not filled:
                    logger.info(f"⚡ No fill at {current_percentage}% - INSTANTLY trying next level")
                    # Cancel current order
                    try:
                        ib.cancelOrder(order)
                        logger.info(f"🚫 Cancelled order at {current_percentage}%")
                    except Exception as cancel_error:
                        logger.warning(f"Failed to cancel order: {cancel_error}")
                        pass
                
                # INSTANT continuation - 0.0001 seconds
                logger.info(f"⚡ INSTANT continuation: {wait_time}s before next attempt...")
                await asyncio.sleep(wait_time)
                
                # Increase percentage for next attempt
                current_percentage += increment
                attempt_number += 1
            
            direction = "above" if action == "BUY" else "below"
            total_time = time.time() - total_start_time
            logger.error(f"❌ AGGRESSIVE CONTINUATION FAILED - no fill up to 10% {direction} yfinance price")
            logger.error("🚨 This should NEVER happen - check market conditions!")
            return TradeResult(
                success=False, 
                error=f"Aggressive continuation failed - no fill up to 10% {direction} price",
                session=session,
                order_type="LIMIT",
                timing_info={
                    "session_detection": session_time,
                    "connection": connect_time,
                    "contract_creation": contract_time,
                    "price_retrieval": price_time,
                    "total_time": total_time,
                    "attempts": attempt_number
                }
            )
                
        except Exception as e:
            logger.error(f"❌ Extended hours trade failed: {e}")
            return TradeResult(
                success=False, 
                error=str(e),
                session=session,
                order_type="LIMIT"
            )
    
    async def execute_trade(self, trade_request: TradeRequest) -> TradeResult:
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
_paper_trading_service_instance: Optional[IBKRTradingService] = None

def get_ibkr_trading_service(paper_trading: bool = False) -> IBKRTradingService:
    """Get singleton instance of IBKR trading service."""
    global _ibkr_trading_service_instance, _paper_trading_service_instance
    
    if paper_trading:
        if _paper_trading_service_instance is None:
            _paper_trading_service_instance = IBKRTradingService(paper_trading=True)
            logger.info("Created new IBKR paper trading service instance")
        return _paper_trading_service_instance
    else:
        if _ibkr_trading_service_instance is None:
            _ibkr_trading_service_instance = IBKRTradingService(paper_trading=False)
            logger.info("Created new IBKR live trading service instance")
        return _ibkr_trading_service_instance