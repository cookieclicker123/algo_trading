"""
IBKR Trading Service - unified trade execution with persistent connection management.
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple, Union, TYPE_CHECKING

import pytz
from ib_insync import IB, Stock, MarketOrder, LimitOrder

from ..models.base_models import TradeRequest
from ..utils.logging_config import get_logger
from ..config import settings

if TYPE_CHECKING:
    from .position_tracker import PositionTracker


logger = get_logger(__name__)


class TradeResult:
    """Result of a trade execution."""

    def __init__(
        self,
        success: bool,
        shares: int = 0,
        fill_price: float = 0.0,
        total_cost: float = 0.0,
        commission: float = 0.0,
        error: str = "",
        session: str = "",
        order_type: str = "",
        timing_info: Optional[Dict[str, float]] = None,
        limit_price_used: Optional[float] = None,
        percentage_above_below: Optional[float] = None,
    ):
        self.success = success
        self.shares = shares
        self.fill_price = fill_price
        self.total_cost = total_cost
        self.commission = commission
        self.error = error
        self.session = session
        self.order_type = order_type
        self.timing_info = timing_info or {}
        self.limit_price_used = limit_price_used
        self.percentage_above_below = percentage_above_below


class IBKRTradingService:
    """Unified IBKR trading service with resilient connection management."""
    
    def __init__(self, paper_trading: bool = False):
        self.paper_trading = paper_trading
        self.pending_trades: Dict[str, Dict[str, Any]] = {}
        self.trade_timeout_minutes = 30

        # Connection state
        self.ib: Optional[IB] = None
        self._main_event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._connection_lock: Optional[asyncio.Lock] = None
        self.gateway_api_client_connected: bool = False

        # Background tasks
        self._connection_verification_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._daily_restart_watchdog_task: Optional[asyncio.Task] = None

        self.keep_alive_interval = 60
        self._reconnect_backoff_seconds = 5
        self.enabled = True  # for compatibility with existing stats reporting

        # Optional Telegram notifier injected by the service container
        self.telegram_service = None
        self.position_tracker: Optional["PositionTracker"] = None
        
        if paper_trading:
            logger.info("IBKRTradingService initialized in PAPER TRADING mode.")
        else:
            logger.info("IBKRTradingService initialized in LIVE TRADING mode.")

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Connect to IB Gateway and start background monitoring tasks."""

        logger.info("🚀 Starting IBKR Trading Service - connecting to Gateway...")

        if self._main_event_loop is None:
            try:
                self._main_event_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._main_event_loop = asyncio.get_event_loop()

        await self._ensure_connected()
        logger.info("✅ IBKR Trading Service started - Gateway connected")

        self._start_connection_verification()
        self._start_keepalive()
        self._start_daily_restart_watchdog()

    async def stop(self) -> None:
        """Stop background tasks and disconnect from IB Gateway."""

        logger.info("🛑 Stopping IBKR Trading Service")

        tasks = [
            self._connection_verification_task,
            self._keepalive_task,
            self._daily_restart_watchdog_task,
        ]

        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._connection_verification_task = None
        self._keepalive_task = None
        self._daily_restart_watchdog_task = None

        if self.ib:
            try:
                self.ib.disconnect()
            except Exception:
                pass
            finally:
                self.ib = None
                self.gateway_api_client_connected = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    async def _ensure_connected(self, timeout_seconds: Optional[float] = None) -> IB:
        """Ensure a warm persistent IB connection is available."""

        if self._connection_lock is None:
            self._connection_lock = asyncio.Lock()

        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds

        def remaining_time() -> Optional[float]:
            if deadline is None:
                return None
            return deadline - time.monotonic()

        async with self._connection_lock:
            if self.ib and self.gateway_api_client_connected:
                try:
                    if self.ib.isConnected():
                        # Light ping to confirm the API client is responsive
                        self.ib.accountValues()
                        return self.ib
                except Exception:
                    logger.warning("⚠️ Existing IB connection became unresponsive – reconnecting")
                    self.gateway_api_client_connected = False

            remaining = remaining_time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Connection timeout reached before attempting IB reconnect")

            return await self._connect_with_confirmation(remaining)

    async def _connect_with_confirmation(self, timeout_seconds: Optional[float] = None) -> IB:
        """Connect to IB Gateway and confirm the API client is responsive."""

        if self.ib:
            try:
                self.ib.disconnect()
            except Exception:
                pass
            self.ib = None

        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds

        def remaining_time() -> Optional[float]:
            if deadline is None:
                return None
            return deadline - time.monotonic()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()

        self._main_event_loop = loop
        asyncio.set_event_loop(loop)

        self.ib = IB()
        self.ib.disconnectedEvent += self._on_disconnect
        self.ib.errorEvent += self._on_ib_error

        port = 4001 if self.paper_trading else 7497
        logger.info(f"🔌 Connecting to IB Gateway (port {port}, clientId 5)...")

        remaining = remaining_time()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Connection timeout reached before contacting IB Gateway")

        connect_future = self.ib.connectAsync("127.0.0.1", port, clientId=5)
        try:
            if remaining is None:
                await connect_future
            else:
                await asyncio.wait_for(connect_future, timeout=max(remaining, 0))
        except asyncio.TimeoutError:
            raise TimeoutError("IB Gateway connection attempt timed out") from None

        try:
            remaining = remaining_time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Connection timeout reached before verification")
            accounts = self.ib.accountValues()
            logger.info(
                f"✅ Gateway API client verified via accountValues() ({len(accounts) if accounts else 0} accounts)"
            )
            self.gateway_api_client_connected = True
            try:
                # Ensure we are subscribed to live (not frozen/delayed) data
                self.ib.reqMarketDataType(1)
            except Exception as exc:
                logger.warning("⚠️ Failed to request real-time market data type", error=str(exc))

            self._notify_telegram("✅ IB Gateway connected and verified")
        except Exception as exc:
            logger.error(f"❌ Connection verification failed: {exc}")
            self.gateway_api_client_connected = False
            raise

        return self.ib

    def _on_disconnect(self) -> None:
        """Handle Gateway-initiated disconnects."""

        logger.warning("⚠️ Gateway disconnected API client - scheduling reconnect")
        self.gateway_api_client_connected = False
        self._notify_telegram("⚠️ IB Gateway disconnected (daily restart or network). Reconnecting...")

        if self._main_event_loop:
            self._main_event_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._reconnect_after_disconnect())
            )

    async def _reconnect_after_disconnect(self) -> None:
        """Reconnect to IB Gateway after a disconnect."""

        await asyncio.sleep(1)
        attempts = 0
        while not self.gateway_api_client_connected:
            attempts += 1
            try:
                await self._connect_with_confirmation()
                logger.info("🔄 Reconnected to IB Gateway", attempts=attempts)
            except Exception as exc:
                logger.error(f"❌ Reconnect attempt failed: {exc}", attempts=attempts)
                await asyncio.sleep(self._reconnect_backoff_seconds)

    def _start_connection_verification(self) -> None:
        if self._main_event_loop is None:
            return
        if self._connection_verification_task and not self._connection_verification_task.done():
            return
        self._connection_verification_task = self._main_event_loop.create_task(self._verify_connection())

    async def _verify_connection(self) -> None:
        """Periodically verify that the Gateway API client responds."""

        try:
            while True:
                await asyncio.sleep(15)
                if not self.ib:
                    continue

                try:
                    if not self.ib.isConnected():
                        logger.warning("⚠️ ib.isConnected() returned False - triggering reconnection")
                        self.gateway_api_client_connected = False
                        await self._reconnect_after_disconnect()
                        continue

                    self.ib.accountValues()
                    if not self.gateway_api_client_connected:
                        self.gateway_api_client_connected = True
                        self._notify_telegram("✅ IB Gateway reconnected and verified")
                except Exception as exc:
                    logger.warning(f"⚠️ Gateway API client verification failed: {exc}")
                    self.gateway_api_client_connected = False
                    await self._reconnect_after_disconnect()
        except asyncio.CancelledError:
            logger.info("Connection verification task cancelled")

    def _start_keepalive(self) -> None:
        if self._main_event_loop is None:
            return
        if self._keepalive_task and not self._keepalive_task.done():
            return
        self._keepalive_task = self._main_event_loop.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Send lightweight keepalive pings to avoid idle disconnects."""

        try:
            while True:
                await asyncio.sleep(self.keep_alive_interval)
                try:
                    ib = await self._ensure_connected()
                    ib.accountValues()
                    logger.debug("🔁 Keepalive ping successful")
                except Exception as exc:
                    logger.warning(f"⚠️ Keepalive ping failed: {exc}")
                    self.gateway_api_client_connected = False
                    await self._reconnect_after_disconnect()
        except asyncio.CancelledError:
            logger.info("Keepalive task cancelled")

    def _start_daily_restart_watchdog(self) -> None:
        if self._main_event_loop is None:
            return
        if self._daily_restart_watchdog_task and not self._daily_restart_watchdog_task.done():
            return
        self._daily_restart_watchdog_task = self._main_event_loop.create_task(
            self._daily_restart_watchdog_loop()
        )

    async def _daily_restart_watchdog_loop(self) -> None:
        """Watch for the Gateway's daily restart window and reconnect quickly."""

        try:
            while True:
                await asyncio.sleep(60 * 30)  # check every 30 minutes
                if not self.ib:
                    continue

                try:
                    est = pytz.timezone("US/Eastern")
                    now_et = datetime.now(est)
                    if now_et.hour == 2:  # typical IBKR paper restart window
                        logger.info("🔁 Performing proactive reconnect during daily restart window")
                        await self._connect_with_confirmation()
                except Exception as exc:
                    logger.warning(f"⚠️ Daily restart watchdog encountered an error: {exc}")
        except asyncio.CancelledError:
            logger.info("Daily restart watchdog task cancelled")

    def _notify_telegram(self, message: str) -> None:
        if not self.telegram_service:
            return
        send_method = getattr(self.telegram_service, "_send_message_to_all_bots", None)
        if not send_method:
            return
        if not self._main_event_loop:
            return
        self._main_event_loop.call_soon_threadsafe(
            lambda: asyncio.create_task(send_method(message))
        )

    def _on_ib_error(self, req_id: int, error_code: int, error_message: str, misc: str):
        # High-frequency error 2104/2106 spam is already handled by IB; only log non-informational codes
        if error_code in {2104, 2106, 2107, 2157, 2158}:
            logger.debug(
                "IBKR informational message",
                req_id=req_id,
                error_code=error_code,
                message=error_message,
                misc=misc,
            )
            return
        logger.warning(
            "IBKR error event",
            req_id=req_id,
            error_code=error_code,
            message=error_message,
            misc=misc,
        )

    # ------------------------------------------------------------------
    # Trade processing
    # ------------------------------------------------------------------
    async def process_trade_request(
        self,
        trade_request: TradeRequest,
        timeout_seconds: Optional[float] = None,
    ) -> TradeResult:
        """Process a trade request - public interface for Telegram handler."""

        logger.info(
            "🚀 Processing trade request",
                   ticker=trade_request.ticker,
                   amount=trade_request.amount_usd,
            action=trade_request.action,
            shares=trade_request.shares,
        )
        result = await self._execute_trade(trade_request, timeout_seconds)

        if result.success and trade_request.action.upper() == "SELL" and self.position_tracker:
            if trade_request.close_all_positions:
                self.position_tracker.remove_position(trade_request.ticker)
            elif trade_request.position_article_id:
                self.position_tracker.remove_position(
                    trade_request.ticker, trade_request.position_article_id
                )

        return result

    async def _execute_trade(
        self,
        trade_request: TradeRequest,
        timeout_seconds: Optional[float] = None,
    ) -> TradeResult:
        """Execute a trade using IBKR API with market session detection."""

        total_start_time = time.time()
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds

        def remaining_time() -> Optional[float]:
            if deadline is None:
                return None
            return deadline - time.monotonic()

        session = "unknown"
        order_type_hint = "LIMIT"

        try:
            session_start = time.time()
            session, _ = self.get_market_session()
            session_time = time.time() - session_start
            logger.info(f"⏱️ Market session detection: {session_time:.3f}s")
            
            if session == "closed":
                logger.error("❌ Market is currently closed - no trading available")
                return TradeResult(success=False, error="Market is currently closed", session="closed")

            if session == "market_hours":
                order_type_hint = "MARKET"

            remaining = remaining_time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timeout reached before connecting to IB Gateway")

            connect_start = time.time()
            ib = await self._ensure_connected(remaining)
            connect_time = time.time() - connect_start
            logger.info(f"✅ Connection ready - {connect_time:.3f}s")
            
            remaining = remaining_time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timeout reached before order preparation")

            contract_start = time.time()
            contract = Stock(trade_request.ticker, "SMART", "USD")
            contract_time = time.time() - contract_start
            logger.info(f"✅ Contract created: {contract} - {contract_time:.3f}s")
            
            if session == "market_hours":
                logger.info("📈 MARKET HOURS: Using market order strategy")
                return await self._execute_market_hours_trade(
                    ib,
                    contract,
                    trade_request,
                    total_start_time,
                    session_time,
                    connect_time,
                    contract_time,
                    deadline,
                )

            logger.info("🌙 EXTENDED HOURS: Using ladder limit order strategy")
            return await self._execute_extended_hours_trade(
                ib,
                contract,
                trade_request,
                total_start_time,
                session_time,
                connect_time,
                contract_time,
                deadline,
            )
        except TimeoutError as exc:
            logger.error(
                "❌ Trade execution timed out",
                error=str(exc),
                session=session,
                ticker=trade_request.ticker,
            )
            return TradeResult(success=False, error=str(exc), session=session, order_type=order_type_hint)
        except asyncio.TimeoutError as exc:
            logger.error("❌ Trade execution timed out", error=str(exc))
            return TradeResult(success=False, error="Trade attempt timed out")
        except Exception as exc:
            logger.error("❌ Trade execution failed", error=str(exc))
            logger.error(f"📝 Exception type: {type(exc).__name__}")
            import traceback

            logger.error(f"📝 Full traceback:\n{traceback.format_exc()}")
            return TradeResult(success=False, error=str(exc))

    def get_market_session(self) -> Tuple[str, bool]:
        """Determine current market session based on Eastern Time."""

        et_tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(et_tz)

        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        premarket_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
        postmarket_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)

        logger.info(f"🕐 Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")

        if market_open <= now_et < market_close:
            logger.info("📈 Currently in MARKET HOURS")
            return "market_hours", False
        if premarket_start <= now_et < market_open:
            logger.info("🌅 Currently in PREMARKET")
            return "premarket", True
        if market_close <= now_et < postmarket_end:
            logger.info("🌆 Currently in POSTMARKET")
            return "postmarket", True
        logger.info("🌙 Currently MARKET CLOSED")
        return "closed", True

    async def get_ibkr_realtime_price(
        self,
        ib: IB,
        contract: Stock,
        timeout_deadline: Optional[float] = None,
    ) -> Optional[float]:
        """Get real-time price using reqMktData."""

        try:
            def time_left() -> Optional[float]:
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()

            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before qualifying contract")

            logger.info(f"📊 Requesting IBKR real-time quote for {contract.symbol}...")
            try:
                ib.reqMarketDataType(1)
                logger.debug("Requested market data type", market_data_type=ib.marketDataType())
            except Exception as exc:
                logger.warning("⚠️ Unable to request real-time market data type while fetching quote", error=str(exc))
            qualify_coro = ib.qualifyContractsAsync(contract)
            if remaining is None:
                qualified_list = await qualify_coro
            else:
                qualified_list = await asyncio.wait_for(qualify_coro, timeout=max(remaining, 0))

            if not qualified_list:
                logger.error("❌ IBKR returned empty qualification list")
                return None

            [qualified] = qualified_list
            logger.debug("Qualified contract", contract=qualified)
            ticker = ib.reqMktData(qualified, "", True, False)
            last_snapshot: Dict[str, Optional[float]] = {
                "last": None,
                "bid": None,
                "ask": None,
                "close": None,
            }
            for iteration in range(10):
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    break
                sleep_interval = 0.05 if remaining is None else min(0.05, max(remaining, 0))
                if sleep_interval > 0:
                    await asyncio.sleep(sleep_interval)
                last_price = getattr(ticker, "last", None)
                bid = getattr(ticker, "bid", None)
                ask = getattr(ticker, "ask", None)
                close = getattr(ticker, "close", None)
                last_snapshot = {
                    "last": last_price,
                    "bid": bid,
                    "ask": ask,
                    "close": close,
                    "iteration": iteration,
                }
                if last_price and last_price > 0:
                    ib.cancelMktData(qualified)
                    return float(last_price)
                if bid and ask and bid > 0 and ask > 0:
                    ib.cancelMktData(qualified)
                    return float((bid + ask) / 2.0)
                if close and close > 0:
                    ib.cancelMktData(qualified)
                    return float(close)
            ib.cancelMktData(qualified)
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                logger.error(
                    "⏱️ Timeout waiting for IBKR quote",
                    ticker=contract.symbol,
                    snapshot=last_snapshot,
                    market_data_type=ib.marketDataType() if hasattr(ib, "marketDataType") else None,
                )
                raise TimeoutError(
                    "Timeout waiting for IBKR quote (no last/bid/ask received); check real-time market data subscription"
                )
            logger.error(
                "❌ IBKR quote unavailable (no last/bbo/close)",
                ticker=contract.symbol,
                snapshot=last_snapshot,
                market_data_type=ib.marketDataType() if hasattr(ib, "marketDataType") else None,
            )
            return None
        except TimeoutError:
            raise
        except Exception as exc:
            logger.error(f"❌ Error fetching IBKR quote for {contract.symbol}: {exc}")
            return None

    # (The remainder of the file keeps the existing trade execution logic and user-response helpers.)

    async def _execute_market_hours_trade(
        self,
        ib: IB,
        contract: Stock,
        trade_request: TradeRequest,
        total_start_time: float,
        session_time: float,
        connect_time: float,
        contract_time: float,
        timeout_deadline: Optional[float] = None,
    ) -> TradeResult:
        try:
            def time_left() -> Optional[float]:
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()

            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before order creation")

            action = trade_request.action.upper()
            quantity = trade_request.shares or 1
            order_create_start = time.time()
            order = MarketOrder(action, quantity)
            order_create_time = time.time() - order_create_start
            logger.info(f"✅ Market order created: {order} (create: {order_create_time:.3f}s)")
            
            place_start = time.time()
            trade = ib.placeOrder(contract, order)
            place_time = time.time() - place_start
            logger.info(f"✅ Order placed: {trade} (place: {place_time:.3f}s)")
            
            fill_wait_start = time.time()
            for attempt in range(10):
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    try:
                        ib.cancelOrder(order)
                    except Exception:
                        pass
                    raise TimeoutError("Trade timed out before order fill")

                sleep_interval = 0.5 if remaining is None else min(0.5, max(remaining, 0))
                if sleep_interval > 0:
                    await asyncio.sleep(sleep_interval)
                if trade.isDone():
                    fill_price = trade.orderStatus.avgFillPrice or 0.0
                    filled_shares = int(trade.orderStatus.filled or quantity)
                    fill_wait_time = time.time() - fill_wait_start
                    total_time = time.time() - total_start_time
                    logger.info(
                        f"🎉 ORDER FILLED! Price: ${fill_price} for {filled_shares} share(s)"
                    )
                    return TradeResult(
                        success=True, 
                        shares=filled_shares,
                        fill_price=fill_price, 
                        total_cost=fill_price * filled_shares,
                        session="market_hours",
                        order_type="MARKET",
                        timing_info={
                            "session_detection": session_time,
                            "connection": connect_time,
                            "contract_creation": contract_time,
                            "order_creation": order_create_time,
                            "order_placement": place_time,
                            "fill_wait": fill_wait_time,
                            "total_time": total_time,
                        },
                    )
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
                    "total_time": total_time,
                },
            )
        except TimeoutError:
            raise
        except Exception as exc:
            logger.error(f"❌ Market hours trade failed: {exc}")
            return TradeResult(success=False, error=str(exc), session="market_hours", order_type="MARKET")

    async def _execute_extended_hours_trade(
        self,
        ib: IB,
        contract: Stock,
        trade_request: TradeRequest,
        total_start_time: float,
        session_time: float,
        connect_time: float,
        contract_time: float,
        timeout_deadline: Optional[float] = None,
    ) -> TradeResult:
        session, _ = self.get_market_session()
        try:
            def time_left() -> Optional[float]:
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()

            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before price retrieval")

            try:
                ib.reqMarketDataType(1)
            except Exception as exc:
                logger.warning("⚠️ Unable to set market data type to real-time before quote retrieval", error=str(exc))

            price_start = time.time()
            current_price = await self.get_ibkr_realtime_price(ib, contract, timeout_deadline)
            price_time = time.time() - price_start
            logger.info(f"💰 Price retrieval: {price_time:.3f}s")
            
            if not current_price:
                logger.error(
                    "❌ Could not get real-time price from IBKR - aborting trade",
                    ticker=contract.symbol,
                    session=session,
                )
                return TradeResult(
                    success=False, 
                    error="Could not get real-time price",
                    session=session,
                    order_type="LIMIT",
                )
            
            action = trade_request.action.upper()
            quantity = trade_request.shares or 1
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before preparing ladder" )

            qualify_coro = ib.qualifyContractsAsync(contract)
            if remaining is None:
                qualified_list = await qualify_coro
            else:
                qualified_list = await asyncio.wait_for(qualify_coro, timeout=max(remaining, 0))

            if not qualified_list:
                logger.error("❌ IBKR returned empty qualification list for ladder")
                return TradeResult(success=False, error="Could not qualify contract", session=session, order_type="LIMIT")

            [qualified] = qualified_list
            ticker = ib.reqMktData(qualified, "", True, False)
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                ib.cancelMktData(qualified)
                raise TimeoutError("Trade timed out before receiving ladder snapshot")
            sleep_interval = 0.03 if remaining is None else min(0.03, max(remaining, 0))
            if sleep_interval > 0:
                await asyncio.sleep(sleep_interval)
            bid = getattr(ticker, "bid", None)
            ask = getattr(ticker, "ask", None)
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
            else:
                base_price = bid if bid and bid > 0 else current_price
                base_cents = -initial_cents
                step_cents = -early_step

            wait_time = interval_early
            current_cents = base_cents
            attempt_number = 1
            trading_start = time.time()
            
            while abs(current_cents) <= abs(max_cents_from_start):
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("Trade timed out before ladder could fill")

                limit_price = round(base_price + (current_cents / 100.0), 2)
                order = LimitOrder(action, quantity, limit_price)
                order.outsideRth = True
                order.tif = "IOC"
                
                trade = ib.placeOrder(contract, order)
                fill_wait_start = time.time()

                for _ in range(10):
                    remaining = time_left()
                    if remaining is not None and remaining <= 0:
                        break
                    sleep_interval = wait_time if remaining is None else min(wait_time, max(remaining, 0))
                    if sleep_interval > 0:
                        await asyncio.sleep(sleep_interval)
                    if trade.isDone():
                        fill_wait_time = time.time() - fill_wait_start
                        fill_price = trade.orderStatus.avgFillPrice or limit_price
                        filled_shares = int(trade.orderStatus.filled or quantity)
                        total_trading_time = time.time() - trading_start
                        total_time = time.time() - total_start_time
                        logger.info(
                            f"🎉 ORDER FILLED after {attempt_number} attempt(s)! Price: ${fill_price}"
                        )
                        return TradeResult(
                            success=True, 
                            shares=filled_shares,
                            fill_price=fill_price, 
                            total_cost=fill_price * filled_shares,
                            session=session,
                            order_type="LIMIT",
                            timing_info={
                                "session_detection": session_time,
                                "connection": connect_time,
                                "contract_creation": contract_time,
                                "price_retrieval": price_time,
                                "trading_time": total_trading_time,
                                "total_time": total_time,
                                "attempts": attempt_number,
                            },
                            limit_price_used=limit_price,
                        )

                    if trade.orderStatus and trade.orderStatus.status in ["Cancelled", "Rejected"]:
                        break
                
                    try:
                        ib.cancelOrder(order)
                    except Exception:
                        pass
                
                if attempt_number == switch_after:
                    step_cents = late_step if action == "BUY" else -late_step
                    wait_time = interval_late
                
                current_cents += step_cents
                attempt_number += 1
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("Trade timed out during ladder progression")
                sleep_interval = wait_time if remaining is None else min(wait_time, max(remaining, 0))
                if sleep_interval > 0:
                    await asyncio.sleep(sleep_interval)

            total_time = time.time() - total_start_time
            logger.error(
                f"❌ LADDER FAILED - no fill within ${abs(max_cents_from_start) / 100:.2f} {'above' if action == 'BUY' else 'below'}"
            )
            return TradeResult(
                success=False, 
                error="Ladder failed - no fill within configured range",
                session=session,
                order_type="LIMIT",
                timing_info={
                    "session_detection": session_time,
                    "connection": connect_time,
                    "contract_creation": contract_time,
                    "price_retrieval": price_time,
                    "total_time": total_time,
                    "attempts": attempt_number,
                },
            )
        except TimeoutError:
            raise
        except Exception as exc:
            logger.error(f"❌ Extended hours trade failed: {exc}")
            return TradeResult(success=False, error=str(exc), session=session, order_type="LIMIT")

    async def execute_trade(
        self, trade_request: TradeRequest, timeout_seconds: Optional[float] = None
    ) -> TradeResult:
        return await self._execute_trade(trade_request, timeout_seconds)

    async def probe_market_data(
        self,
        tickers: List[str],
        timeout_seconds: float = 5.0,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Attempt to fetch live market data for the provided tickers.

        Returns a mapping of ticker -> diagnostic info containing whether
        qualifying succeeded, if any price fields were received, and the
        elapsed times for qualification and first quote.
        """

        diagnostics: Dict[str, Dict[str, Any]] = {}
        ib = await self._ensure_connected(timeout_seconds)
        try:
            ib.reqMarketDataType(1)
        except Exception as exc:
            logger.warning("⚠️ Unable to request real-time market data type during probe", error=str(exc))

        for ticker in tickers:
            diag: Dict[str, Any] = {
                "qualified": False,
                "qualification_time_ms": None,
                "quote_time_ms": None,
                "had_price": False,
                "last": None,
                "bid": None,
                "ask": None,
                "close": None,
            }
            start = time.time()
            try:
                qualified = await ib.qualifyContractsAsync(Stock(ticker, "SMART", "USD"))
                qualification_elapsed = (time.time() - start) * 1000
                diag["qualification_time_ms"] = round(qualification_elapsed, 2)
                if not qualified:
                    diagnostics[ticker] = diag
                    continue
                diag["qualified"] = True
                contract = qualified[0]
                ticker_obj = ib.reqMktData(contract, "", True, False)
                try:
                    # Poll for up to timeout_seconds seconds for any price field
                    poll_start = time.time()
                    while time.time() - poll_start < timeout_seconds:
                        await asyncio.sleep(0.1)
                        last = getattr(ticker_obj, "last", None)
                        bid = getattr(ticker_obj, "bid", None)
                        ask = getattr(ticker_obj, "ask", None)
                        close = getattr(ticker_obj, "close", None)
                        if any(value and value > 0 for value in (last, bid, ask, close)):
                            diag["had_price"] = True
                            diag["quote_time_ms"] = round((time.time() - poll_start) * 1000, 2)
                            diag["last"] = last
                            diag["bid"] = bid
                            diag["ask"] = ask
                            diag["close"] = close
                            break
                    else:
                        diag["quote_time_ms"] = round(timeout_seconds * 1000, 2)
                finally:
                    try:
                        ib.cancelMktData(contract)
                    except Exception:
                        pass
            except Exception as exc:
                diag["error"] = str(exc)
            diagnostics[ticker] = diag

        return diagnostics

    # ------------------------------------------------------------------
    # User response helpers (unchanged from original implementation)
    # ------------------------------------------------------------------
    def add_pending_trade(self, article_id: str, tickers: List[str], user_chat_id: str):
        self.pending_trades[article_id] = {
            "tickers": tickers,
            "user_chat_id": user_chat_id,
            "timestamp": datetime.now(),
            "expires_at": datetime.now() + timedelta(minutes=self.trade_timeout_minutes),
        }
        logger.info(
            "Added pending trade decision",
                   article_id=article_id,
                   tickers=tickers,
            expires_in_minutes=self.trade_timeout_minutes,
        )

    def process_user_response(self, user_chat_id: str, message_text: str) -> Optional[TradeRequest]:
        message_text = message_text.strip().lower()
        
        if message_text.startswith("close"):
            parts = message_text.split()
            if len(parts) != 2:
                logger.warning("Invalid close command format", message=message_text, chat_id=user_chat_id)
                return None

            ticker = parts[1].upper()
            total_shares = None
            if self.position_tracker:
                total_shares = self.position_tracker.get_total_shares(ticker)
                if total_shares == 0:
                    logger.warning("Requested close for ticker with no tracked position", ticker=ticker, chat_id=user_chat_id)
            shares = total_shares or 1
            logger.info("User requested manual close", ticker=ticker, shares=shares, chat_id=user_chat_id)
            return TradeRequest(
                ticker=ticker,
                amount_usd=shares * 100.0,
                action="SELL",
                shares=shares,
                close_all_positions=True,
            )

        if message_text.startswith("trade"):
            parts = message_text.split()
            if len(parts) == 1:
                return self._handle_default_trade(user_chat_id)
            if len(parts) == 2:
                ticker = parts[1].upper()
                logger.info("User requested trade for specific ticker", ticker=ticker, chat_id=user_chat_id)
                return self._create_general_trade_request(ticker)
                logger.warning("Invalid trade command format", message=message_text, chat_id=user_chat_id)
                return None

        if message_text == "ignore":
            logger.info("User chose to ignore trade", chat_id=user_chat_id)
            return None

            logger.info("Unrecognized user response", message=message_text, chat_id=user_chat_id)
            return None

    def _handle_default_trade(self, user_chat_id: str) -> Optional[TradeRequest]:
        user_trades = [
            (aid, data)
            for aid, data in self.pending_trades.items()
            if data["user_chat_id"] == user_chat_id
        ]
        
        if user_trades:
            article_id, trade_data = max(user_trades, key=lambda x: x[1]["timestamp"])
            if datetime.now() <= trade_data["expires_at"]:
                logger.info("Using pending trade for default", article_id=article_id)
                return self._create_trade_from_pending(article_id, trade_data)
        
        logger.info("No pending trade available for default trade", chat_id=user_chat_id)
        return None
        
    def _create_general_trade_request(self, ticker: str) -> TradeRequest:
        logger.info("Creating general trade request", ticker=ticker)
        return TradeRequest(ticker=ticker, amount_usd=100.0, action="BUY", shares=1)

    def _create_trade_from_pending(self, article_id: str, trade_data: Dict[str, Any]) -> Optional[TradeRequest]:
        tickers = trade_data["tickers"]
        if tickers:
            ticker = tickers[0]
            logger.info("Creating trade from pending", article_id=article_id, ticker=ticker)
            return TradeRequest(
                ticker=ticker,
                amount_usd=100.0,
                action="BUY",
                shares=1,
                position_article_id=article_id,
            )
            logger.warning("No tickers in pending trade data", article_id=article_id)
            return None


# Factory function for dependency injection
_ibkr_trading_service_instance: Optional[IBKRTradingService] = None
_paper_trading_service_instance: Optional[IBKRTradingService] = None


def get_ibkr_trading_service(paper_trading: bool = False) -> IBKRTradingService:
    global _ibkr_trading_service_instance, _paper_trading_service_instance
    
    if paper_trading:
        if _paper_trading_service_instance is None:
            _paper_trading_service_instance = IBKRTradingService(paper_trading=True)
            logger.info("Created new IBKR paper trading service instance")
        return _paper_trading_service_instance

        if _ibkr_trading_service_instance is None:
            _ibkr_trading_service_instance = IBKRTradingService(paper_trading=False)
            logger.info("Created new IBKR live trading service instance")
        return _ibkr_trading_service_instance