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

from ..models.base_models import TradeRequest
from ..utils.logging_config import get_logger
from ..config import settings

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
        self.ib: Optional[IB] = None  # persistent warm connection
        
        if paper_trading:
            logger.info("IBKRTradingService initialized in PAPER TRADING mode.")
        else:
            logger.info("IBKRTradingService initialized in LIVE TRADING mode.")

    async def _ensure_connected(self) -> IB:
        """Ensure a warm persistent IB connection is available."""
        if self.ib and self.ib.isConnected():
            return self.ib
        self.ib = IB()
        port = 4001 if self.paper_trading else 7497
        await self.ib.connectAsync('127.0.0.1', port, clientId=5)
        return self.ib

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

    async def get_ibkr_realtime_price(self, ib: IB, contract: Stock) -> Optional[float]:
        """Get real-time price using reqMktData (supports extended hours when venue provides it).

        Prefer last; then midpoint of bid/ask; then close. Wait briefly for tick updates.
        """
        try:
            logger.info(f"📊 Requesting IBKR real-time quote for {contract.symbol}...")
            # Ensure contract is qualified to avoid pacing on first use
            [qualified] = await ib.qualifyContractsAsync(contract)
            ticker = ib.reqMktData(qualified, "", True, False)
            # Wait a few short intervals for data to arrive
            for _ in range(10):  # up to ~500ms
                await asyncio.sleep(0.05)
                last_price = getattr(ticker, 'last', None)
                bid = getattr(ticker, 'bid', None)
                ask = getattr(ticker, 'ask', None)
                close = getattr(ticker, 'close', None)
                if last_price and last_price > 0:
                    logger.info(f"💰 IBKR last price: ${last_price}")
                    ib.cancelMktData(qualified)
                    return float(last_price)
                if bid and ask and bid > 0 and ask > 0:
                    midpoint = (bid + ask) / 2.0
                    logger.info(f"💰 IBKR midpoint price: ${midpoint} (bid ${bid}, ask ${ask})")
                    ib.cancelMktData(qualified)
                    return float(midpoint)
                if close and close > 0:
                    logger.info(f"💰 IBKR close price fallback: ${close}")
                    ib.cancelMktData(qualified)
                    return float(close)
            ib.cancelMktData(qualified)
            logger.error("❌ IBKR quote unavailable (no last/bbo/close)")
            return None
        except Exception as e:
            logger.error(f"❌ Error fetching IBKR quote for {contract.symbol}: {e}")
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
            
            # Ensure persistent connection
            connect_start = time.time()
            ib = await self._ensure_connected()
            connect_time = time.time() - connect_start
            logger.info(f"✅ Connection ready - {connect_time:.3f}s")
            
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
            # Keep persistent connection open (no disconnect)
            pass

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
            # Get real-time price from IBKR (supports extended hours)
            price_start = time.time()
            current_price = await self.get_ibkr_realtime_price(ib, contract)
            price_time = time.time() - price_start
            logger.info(f"💰 Price retrieval: {price_time:.3f}s")
            
            if not current_price:
                logger.error("❌ Could not get real-time price from IBKR - aborting trade")
                return TradeResult(
                    success=False, 
                    error="Could not get real-time price",
                    session=session,
                    order_type="LIMIT"
                )
            
            logger.info(f"💰 Current {contract.symbol} price: ${current_price}")
            
            # TIGHT LADDER based on NBBO
            action = trade_request.action.upper()
            # Pull NBBO quickly
            [qualified] = await ib.qualifyContractsAsync(contract)
            ticker = ib.reqMktData(qualified, "", True, False)
            await asyncio.sleep(0.03)
            bid = getattr(ticker, 'bid', None)
            ask = getattr(ticker, 'ask', None)
            ib.cancelMktData(qualified)

            initial_cents = settings.LADDER_INITIAL_CENTS
            early_step = settings.LADDER_STEP_CENTS
            late_step = settings.LADDER_STEP_CENTS_AFTER
            switch_after = settings.LADDER_SWITCH_ATTEMPT
            interval_early = settings.LADDER_INTERVAL_MS / 1000.0
            interval_late = settings.LADDER_INTERVAL_MS_LATE / 1000.0
            max_cents_from_start = settings.LADDER_MAX_CENTS

            if action == "BUY":
                base_price = ask if ask and ask > 0 else current_price
                base_cents = initial_cents
                step_cents = early_step
                logger.info("🚀 Starting NBBO LADDER: buy at ask + small cents", ask=ask)
            else:
                base_price = bid if bid and bid > 0 else current_price
                base_cents = -initial_cents
                step_cents = -early_step
                logger.info("🚀 Starting NBBO LADDER: sell at bid - small cents", bid=bid)

            wait_time = interval_early
            current_cents = base_cents
            attempt_number = 1
            
            # Start trading timing
            trading_start = time.time()
            
            while abs(current_cents) <= abs(max_cents_from_start):
                attempt_start = time.time()
                direction = "above" if action == "BUY" else "below"
                logger.info(f"🚀 Attempt {attempt_number}: {abs(current_cents)} cents {direction} real-time price")
                
                # Calculate limit price using real-time price
                calc_start = time.time()
                limit_price = round(base_price + (current_cents / 100.0), 2)
                calc_time = time.time() - calc_start
                logger.info(f"📈 Limit price: ${limit_price:.2f} ({abs(current_cents)}¢ {'above' if action == 'BUY' else 'below'}) (calc: {calc_time:.3f}s)")
                
                # Create limit order (action already defined above)
                order_create_start = time.time()
                order_id = ib.client.getReqId()
                order = LimitOrder(action, 1, limit_price, orderId=order_id)
                order.outsideRth = True
                order.tif = 'IOC'  # immediate-or-cancel for marketable limit behavior
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
                logger.info("⚡ Waiting for fast fill...")
                filled = False
                
                for check_attempt in range(10):
                    await asyncio.sleep(wait_time)
                    
                    if trade.isDone():
                        fill_wait_time = time.time() - fill_wait_start
                        fill_price = trade.orderStatus.avgFillPrice
                        total_trading_time = time.time() - trading_start
                        total_time = time.time() - total_start_time
                        
                        logger.info(f"🎉 ORDER FILLED! Price: ${fill_price}")
                        logger.info(f"✅ SUCCESS at attempt {attempt_number}: {abs(current_cents)} cents {direction} real-time price")
                        logger.info(f"⏱️ Fill wait time: {fill_wait_time:.3f}s")
                        logger.info(f"⏱️ Total trading time: {total_trading_time:.3f}s")
                        logger.info(f"⏱️ TOTAL TIME: {total_time:.3f}s")
                        
                        # Performance summary
                        logger.info("🚀 EXTENDED HOURS PERFORMANCE SUMMARY:")
                        logger.info(f"   📊 Market session detection: {session_time:.3f}s")
                        logger.info(f"   🔌 Connection: {connect_time:.3f}s")
                        logger.info(f"   📋 Contract creation: {contract_time:.3f}s")
                        logger.info(f"   📊 IBKR quote retrieval: {price_time:.3f}s")
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
                            percentage_above_below=None
                        )
                    
                    if trade.orderStatus and trade.orderStatus.status in ['Cancelled', 'Rejected']:
                        logger.warning(f"⚠️ Order rejected at {abs(current_cents)} cents {direction}: {trade.orderStatus.status}")
                        # Get rejection reason
                        if trade.log and len(trade.log) > 0:
                            last_log = trade.log[-1]
                            if last_log.message:
                                logger.warning(f"📝 Rejection reason: {last_log.message}")
                        break
                
                attempt_time = time.time() - attempt_start
                logger.info(f"⏱️ Attempt {attempt_number} total time: {attempt_time:.3f}s")
                
                if not filled:
                    logger.info(f"⚡ No fill at {abs(current_cents)} cents {direction} - trying next level")
                    # Cancel current order
                    try:
                        ib.cancelOrder(order)
                        logger.info(f"🚫 Cancelled order at {abs(current_cents)} cents {direction}")
                    except Exception as cancel_error:
                        logger.warning(f"Failed to cancel order: {cancel_error}")
                        pass
                
                # Adjust pacing and step after threshold
                if attempt_number == switch_after:
                    step_cents = late_step if action == 'BUY' else -late_step
                    wait_time = interval_late
                await asyncio.sleep(wait_time)
                
                # Increase percentage for next attempt
                current_cents += step_cents
                attempt_number += 1
            
            direction = "above" if action == "BUY" else "below"
            total_time = time.time() - total_start_time
            logger.error(f"❌ LADDER FAILED - no fill within ${abs(max_cents_from_start)/100:.2f} {direction} real-time price")
            return TradeResult(
                success=False, 
                error=f"Ladder failed - no fill within ${abs(max_cents_from_start)/100:.2f} {direction}",
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