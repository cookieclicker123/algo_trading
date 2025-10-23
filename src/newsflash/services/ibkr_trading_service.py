"""
Interactive Brokers trading service for automated trade execution.
Connects to real IBKR account for live trading.
"""
import asyncio
import pandas as pd
import threading
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


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
    
    def _run_ibkr_in_thread(self, func, *args, **kwargs):
        """
        Run IBKR operations in a separate thread with its own event loop.
        
        Args:
            func: The IBKR function to run
            *args: Arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            The result of the function call
        """
        result = [None]
        exception = [None]
        
        def run_in_thread():
            try:
                # Create a new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Run the function in the new event loop
                if asyncio.iscoroutinefunction(func):
                    result[0] = loop.run_until_complete(func(*args, **kwargs))
                else:
                    result[0] = func(*args, **kwargs)
                
                # Clean up the event loop
                loop.close()
                
            except Exception as e:
                exception[0] = e
        
        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join(timeout=60)  # 60 second timeout for order execution
        
        if thread.is_alive():
            logger.error("❌ IBKR operation timed out after 60 seconds")
            return None
        
        if exception[0]:
            raise exception[0]
        
        return result[0]
    
    def _is_extended_hours(self) -> bool:
        import pytz
        
        # Get current ET time
        et = pytz.timezone('US/Eastern')
        now_et = datetime.now(et)
        
        # Regular trading hours: 9:30 AM - 4:00 PM ET
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        
        # Extended hours: 4:00 AM - 9:30 AM ET (premarket) or 4:00 PM - 8:00 PM ET (after-hours)
        extended_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
        extended_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
        
        # Check if we're in extended hours (premarket or after-hours)
        is_premarket = now_et >= extended_start and now_et < market_open
        is_afterhours = now_et > market_close and now_et < extended_end
        is_extended = is_premarket or is_afterhours
        
        # Check if market is completely closed (between post-market and premarket)
        is_market_closed = now_et >= extended_end or now_et < extended_start
        
        logger.debug("Extended hours check", 
                    current_time=now_et.strftime("%H:%M:%S ET"),
                    is_extended_hours=is_extended,
                    is_premarket=is_premarket,
                    is_afterhours=is_afterhours,
                    is_market_closed=is_market_closed,
                    market_open=market_open.strftime("%H:%M:%S ET"),
                    market_close=market_close.strftime("%H:%M:%S ET"),
                    extended_start=extended_start.strftime("%H:%M:%S ET"),
                    extended_end=extended_end.strftime("%H:%M:%S ET"))
        
        return is_extended

    async def _place_progressive_limit_order(
        self, contract, shares: int, current_price: float, ticker: str
    ) -> bool:
        """
        Place progressive limit orders during extended hours.
        Tries increasingly higher limits: 0.25%, 0.5%, 1%, 1.5%, 2%
        """
        from ib_insync import LimitOrder
        
        # Progressive limit percentages
        limit_percentages = [0.0025, 0.005, 0.01, 0.015, 0.02]  # 0.25%, 0.5%, 1%, 1.5%, 2%
        
        for i, percentage in enumerate(limit_percentages):
            limit_price = current_price * (1 + percentage)
            
            logger.info(f"Attempting LIMIT order #{i+1} (extended hours)",
                       ticker=ticker,
                       shares=shares,
                       current_price=current_price,
                       limit_price=limit_price,
                       limit_percentage=f"{percentage*100:.2f}%",
                       outside_rth=True)
            
            # Create limit order
            order = LimitOrder('BUY', shares, limit_price)
            order.outsideRth = True  # Enable "Fill outside RTH"
            
            # Place the order
            trade = self._run_ibkr_in_thread(self.ib.placeOrder, contract, order)
            
            # Check immediately if order is filled (no timeout - instant attempts)
            await asyncio.sleep(0.1)  # Brief moment for order to be processed
            
            # Check for immediate fill or rejection
            if trade.isDone():
                # Order completed immediately
                pass
            elif trade.orderStatus and trade.orderStatus.status in ['Cancelled', 'Rejected']:
                logger.warning(f"Limit order #{i+1} immediately cancelled/rejected", 
                             ticker=ticker,
                             limit_percentage=f"{percentage*100:.2f}%",
                             status=trade.orderStatus.status)
                # Move to next attempt immediately
            else:
                # Order is pending - cancel immediately and try next
                logger.info(f"Limit order #{i+1} pending - cancelling for next attempt",
                           ticker=ticker,
                           limit_percentage=f"{percentage*100:.2f}%")
                self.ib.cancelOrder(order)
                await asyncio.sleep(0.05)  # Minimal pause before next attempt
            
            # Check if order was filled
            if trade.isDone() and trade.orderStatus.status == 'Filled':
                fill_price = float(trade.orderStatus.avgFillPrice)
                actual_slippage = ((fill_price - current_price) / current_price) * 100
                
                logger.info("✅ Progressive LIMIT order filled successfully",
                           ticker=ticker,
                           shares=shares,
                           attempt=f"{i+1}/{len(limit_percentages)}",
                           limit_percentage=f"{percentage*100:.2f}%",
                           current_price=current_price,
                           limit_price=limit_price,
                           fill_price=fill_price,
                           actual_slippage=f"{actual_slippage:.2f}%",
                           total_value=shares * fill_price)
                
                return True
            else:
                logger.warning(f"Limit order #{i+1} not filled, trying next",
                             ticker=ticker,
                             limit_percentage=f"{percentage*100:.2f}%",
                             status=trade.orderStatus.status if trade.orderStatus else "No status")
                
                # Order not filled - already cancelled above, move to next attempt immediately
        
        # All attempts failed
        logger.error("❌ All progressive limit orders failed",
                   ticker=ticker,
                   attempts=len(limit_percentages),
                   current_price=current_price)
        return False

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
            logger.error("❌ CRITICAL ERROR in process_trade_request", 
                        ticker=trade_request.ticker,
                        error=str(e),
                        error_type=type(e).__name__,
                        traceback=str(e.__traceback__) if hasattr(e, '__traceback__') else "No traceback")
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
            logger.info("🚀 Starting trade execution", 
                       ticker=trade_request.ticker,
                       amount=trade_request.amount_usd,
                       action=trade_request.action)
            
            if not self.ib:
                logger.error("❌ IBKR connection not available - ib_insync not initialized")
                return False
            
            logger.info("✅ IBKR connection available, proceeding with trade")
            
            # Check if already connected, if not connect
            if not self.ib.isConnected():
                logger.info("🔌 Connecting to IBKR Gateway (thread-based)")
                try:
                    # Use thread-based async connection with proper event loop
                    self._run_ibkr_in_thread(self.ib.connectAsync, '127.0.0.1', 7497, clientId=1)
                    logger.info("✅ Successfully connected to IBKR Gateway")
                except Exception as connect_error:
                    logger.error("❌ FAILED TO CONNECT TO IBKR GATEWAY", 
                               error=str(connect_error),
                               error_type=type(connect_error).__name__,
                               gateway_host="127.0.0.1",
                               gateway_port=7497,
                               client_id=1)
                    return False
            else:
                logger.info("✅ Already connected to IBKR Gateway")
            
            from ib_insync import Stock, MarketOrder, LimitOrder
            
            # Create stock contract
            logger.info("📋 Creating stock contract", ticker=trade_request.ticker)
            contract = Stock(trade_request.ticker, 'SMART', 'USD')
            logger.info("✅ Stock contract created", contract=str(contract))
            
            # Get current price
            logger.info("💰 Requesting market data for price")
            try:
                ticker_info = self._run_ibkr_in_thread(self.ib.reqMktData, contract)
                logger.info("✅ Market data request sent", ticker=trade_request.ticker)
            except Exception as mkt_data_error:
                logger.error("❌ FAILED TO REQUEST MARKET DATA", 
                           ticker=trade_request.ticker,
                           error=str(mkt_data_error),
                           error_type=type(mkt_data_error).__name__)
                return False
        
            # Wait longer for market data to arrive (up to 10 seconds)
            logger.info("⏳ Waiting for market data to arrive...")
            import time
            for attempt in range(20):  # 20 attempts × 0.5s = 10 seconds max
                time.sleep(0.5)
                
                # Check if we have valid market data
                if hasattr(contract, 'last') and not pd.isna(contract.last) and contract.last > 0:
                    logger.info("✅ Market data received", 
                               ticker=trade_request.ticker,
                               last_price=contract.last,
                               bid_price=getattr(contract, 'bid', 'N/A'),
                               ask_price=getattr(contract, 'ask', 'N/A'))
                    break
                elif attempt == 19:  # Last attempt
                    logger.warning("⚠️ No market data after 10 seconds, proceeding with fallback", 
                                 ticker=trade_request.ticker)
                    break
                else:
                    logger.debug("⏳ Still waiting for market data...", 
                                attempt=attempt + 1,
                                ticker=trade_request.ticker)
            
            # Calculate shares based on available price data
            if hasattr(contract, 'last') and not pd.isna(contract.last) and contract.last > 0:
                current_price = contract.last
                shares = int(trade_request.amount_usd / current_price)
                if shares < 1:
                    shares = 1
                logger.info("✅ Using market price for calculation", 
                          ticker=trade_request.ticker,
                          price=current_price,
                          amount=trade_request.amount_usd,
                          shares=shares)
            else:
                # Fallback to 1 share if no valid price
                shares = 1
                current_price = None
                logger.warning("⚠️ No valid market data, using 1 share fallback", 
                             ticker=trade_request.ticker)
            
            # Determine order type based on trading hours
            import pytz
            et = pytz.timezone('US/Eastern')
            now_et = datetime.now(et)
            
            # Check market status
            market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
            extended_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
            extended_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
            
            is_premarket = now_et >= extended_start and now_et < market_open
            is_afterhours = now_et > market_close and now_et < extended_end
            is_extended_hours = is_premarket or is_afterhours
            is_market_closed = now_et >= extended_end or now_et < extended_start
            
            logger.info("🕐 Trading hours check", 
                       is_extended_hours=is_extended_hours,
                       is_market_closed=is_market_closed,
                       current_time=datetime.now().strftime("%H:%M:%S"),
                       current_et_time=now_et.strftime("%H:%M:%S ET"))
            
            if is_market_closed:
                logger.error("❌ MARKET CLOSED - Cannot place orders", 
                           current_et_time=now_et.strftime("%H:%M:%S ET"),
                           next_premarket=extended_start.strftime("%H:%M:%S ET"),
                           next_regular=market_open.strftime("%H:%M:%S ET"))
                return False
            elif is_extended_hours:
                # Extended hours: Use progressive limit pricing to ensure fills
                success = await self._place_progressive_limit_order(
                    contract, shares, current_price if current_price else 0, trade_request.ticker
                )
                if success:
    # Keep connection alive
                    return True
                else:
                    logger.error("All progressive limit orders failed", ticker=trade_request.ticker)
    # Keep connection alive
                    return False
            else:
                # Regular hours: Use market order
                logger.info("🕐 REGULAR HOURS DETECTED - Using MARKET order")
                order = MarketOrder('BUY', shares)
                
                logger.info("📈 Placing IBKR MARKET order (regular hours)",
                           ticker=trade_request.ticker,
                           shares=shares,
                           price=current_price if current_price else "N/A",
                           total_value=shares * (current_price if current_price else 0))
            
            # Place the order
            logger.info("🎯 Placing order with IBKR...")
            try:
                trade = self._run_ibkr_in_thread(self.ib.placeOrder, contract, order)
                logger.info("✅ Order placed successfully", 
                           order_id=getattr(trade, 'orderId', 'unknown'),
                           ticker=trade_request.ticker,
                           shares=shares,
                           order_type=type(order).__name__)
            except Exception as order_error:
                logger.error("❌ FAILED TO PLACE ORDER", 
                           ticker=trade_request.ticker,
                           shares=shares,
                           order_type=type(order).__name__,
                           error=str(order_error),
                           error_type=type(order_error).__name__)
# Keep connection alive
                return False
            
            # Wait for order to be filled (with timeout)
            logger.info("⏳ Waiting for order to be filled...", ticker=trade_request.ticker)
            
            # Wait up to 30 seconds for order to be filled
            timeout = 30
            start_time = datetime.now()
            
            while (datetime.now() - start_time).seconds < timeout:
                elapsed = (datetime.now() - start_time).seconds
                
                # Log status every 5 seconds
                if elapsed % 5 == 0:
                    logger.info("⏳ Order status check", 
                               ticker=trade_request.ticker,
                               elapsed_seconds=elapsed,
                               status=trade.orderStatus.status if trade.orderStatus else "No status",
                               is_done=trade.isDone())
                
                if trade.isDone():
                    logger.info("✅ Order completed", 
                               ticker=trade_request.ticker,
                               elapsed_seconds=elapsed,
                               final_status=trade.orderStatus.status if trade.orderStatus else "No status")
                    break
                
                # Check for errors
                if trade.orderStatus and trade.orderStatus.status in ['Cancelled', 'Rejected']:
                    logger.error("❌ ORDER CANCELLED OR REJECTED", 
                               ticker=trade_request.ticker,
                               status=trade.orderStatus.status,
                               elapsed_seconds=elapsed,
                               order_id=getattr(trade, 'orderId', 'unknown'))
    # Keep connection alive
                    return False
                
                # Wait a bit before checking again (synchronous)
                time.sleep(1)
            
            # Check if order was filled
            if trade.isDone() and trade.orderStatus and trade.orderStatus.status == 'Filled':
                order_type = "LIMIT (extended hours)" if is_extended_hours else "MARKET (regular hours)"
                logger.info("✅ TRADE EXECUTED SUCCESSFULLY",
                           ticker=trade_request.ticker,
                           shares=shares,
                           order_type=order_type,
                           fill_price=trade.orderStatus.avgFillPrice,
                           total_value=shares * float(trade.orderStatus.avgFillPrice))
                
                # Keep connection alive for next trade
                return True
            else:
                # Order didn't fill - provide detailed failure info
                final_status = trade.orderStatus.status if trade.orderStatus else "No status"
                is_done = trade.isDone()
                
                logger.error("❌ TRADE NOT FILLED",
                           ticker=trade_request.ticker,
                           shares=shares,
                           order_type="LIMIT (extended hours)" if is_extended_hours else "MARKET (regular hours)",
                           final_status=final_status,
                           is_done=is_done,
                           timeout_reached=True,
                           elapsed_seconds=(datetime.now() - start_time).seconds)
                
                # Keep connection alive for next trade
                return False
                
        except Exception as e:
            logger.error("❌ CRITICAL ERROR in _execute_trade", 
                        ticker=trade_request.ticker,
                        error=str(e),
                        error_type=type(e).__name__,
                        error_args=getattr(e, 'args', None))
            
            # Try to disconnect if connected
            try:
                if self.ib and self.ib.isConnected():
                    logger.info("🔌 Keeping IBKR connection alive after error")
            except Exception as disconnect_error:
                logger.warning("Failed to check connection status after error", error=str(disconnect_error))
            
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
                # "trade" - try to use most recent pending trade, or ask for ticker
                return self._handle_default_trade(user_chat_id)
            elif len(parts) == 2:
                # "trade AAPL" - trade specific ticker
                ticker = parts[1].upper()
                return self._create_general_trade_request(ticker)
            else:
                # Invalid format
                return None
        
        # Check for ignore commands
        elif message_text in ['ignore', 'no', 'skip', 'pass']:
            return None
        
        # Invalid response
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
            amount_usd=100.0,  # Default amount
            action="BUY",
            article_id=f"general_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
    
    def _create_trade_from_pending(self, article_id: str, trade_data: dict) -> TradeRequest:
        """Create trade request from pending trade data."""
        tickers = trade_data['tickers']
        default_ticker = tickers[0] if tickers else 'AAPL'  # Fallback
        
        return TradeRequest(
            ticker=default_ticker,
            amount_usd=100.0,
            action="BUY",
            article_id=article_id
        )
    
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
