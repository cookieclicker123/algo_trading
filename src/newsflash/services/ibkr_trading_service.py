"""
IBKR Trading Service - unified trade execution with persistent connection management.
"""

import asyncio
import math
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple, Union, TYPE_CHECKING

import pytz
from ib_insync import IB, Stock, MarketOrder, LimitOrder, Option

from ..models.base_models import TradeRequest, TradeInstrument, OptionContractParams
from ..utils.logging_config import get_logger
from ..config import settings

if TYPE_CHECKING:
    from .position_tracker import PositionTracker


logger = get_logger(__name__)

OPTION_EXCHANGE_DENYLIST = {
    "BATS",
    "BATSCBOE",
}
OPTION_EXCHANGE_FALLBACK = "SMART"
OPTION_NBBO_SNAPSHOT_DELAY = 0.15
OPTION_EXPIRY_OFFSET = 2
OPTION_SELL_LADDER_STEPS = 3
OPTION_SELL_STEP_SECONDS = 0.35


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
        instrument: str = TradeInstrument.STOCK.value,
        instrument_details: Optional[Dict[str, Any]] = None,
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
        self.instrument = instrument
        self.instrument_details = instrument_details or {}


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
        self._quote_snapshots: Dict[str, Dict[str, Any]] = {}
        
        if paper_trading:
            logger.info("IBKRTradingService initialized in PAPER TRADING mode.")
        else:
            logger.info("IBKRTradingService initialized in LIVE TRADING mode.")

    def _record_quote_snapshot(self, symbol: str, snapshot: Dict[str, Any]) -> None:
        """Store the most recent NBBO snapshot for a symbol."""
        try:
            clean_snapshot = {
                key: float(value) if isinstance(value, (int, float)) and value is not None else value
                for key, value in snapshot.items()
            }
        except Exception:
            clean_snapshot = snapshot
        self._quote_snapshots[symbol] = clean_snapshot

    def get_last_quote_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return the most recently recorded NBBO snapshot for the symbol."""
        return self._quote_snapshots.get(symbol)

    @staticmethod
    def _to_float(value: Optional[Any]) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _round_to_increment(value: float, increment: float) -> float:
        if increment <= 0:
            return round(value, 4)
        return round(round(value / increment) * increment, 4)

    @staticmethod
    def _infer_option_tick(ask: Optional[float], spread: Optional[float]) -> float:
        if spread is not None and spread > 0:
            if spread <= 0.03:
                return 0.01
            if spread <= 0.1:
                return 0.05
        if ask is not None and ask < 1.0:
            return 0.01
        return 0.05

    @classmethod
    def _build_option_sell_price_ladder(cls, nbbo: Optional[Dict[str, Any]]) -> List[float]:
        if not nbbo or not isinstance(nbbo, dict):
            return []

        ask = cls._to_float(nbbo.get("ask"))
        bid = cls._to_float(nbbo.get("bid"))
        spread = cls._to_float(nbbo.get("spread"))

        if ask is None or ask <= 0:
            return []

        tick = cls._infer_option_tick(ask, spread)
        if bid is not None and bid >= ask:
            bid = ask - tick

        min_price = max(tick, (bid + tick) if bid is not None else tick)

        ladder: List[float] = []
        for step in range(OPTION_SELL_LADDER_STEPS):
            raw_price = ask - (step * tick)
            if raw_price < min_price:
                if ladder:
                    break
                raw_price = min_price
            rounded = cls._round_to_increment(raw_price, tick)
            if bid is not None and rounded <= bid:
                if ladder:
                    break
                rounded = cls._round_to_increment(max(bid + tick, bid + tick / 2), tick)
            if rounded <= 0:
                continue
            if ladder and abs(ladder[-1] - rounded) < max(tick / 2, 0.005):
                continue
            if rounded > ask:
                rounded = ask
            ladder.append(round(rounded, 4))

        return ladder

    @staticmethod
    def _describe_ladder_attempt(price: float, status: str, duration: float) -> Dict[str, Any]:
        return {
            "price": round(price, 4),
            "status": status,
            "duration": round(duration, 4),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    async def _attempt_option_sell_ladder(
        self,
        ib: IB,
        contract: Option,
        quantity: int,
        contract_details: Dict[str, Any],
        session_time: float,
        connect_time: float,
        contract_time: float,
        total_start_time: float,
        time_left,
    ) -> Optional[TradeResult]:
        nbbo = contract_details.get("option_nbbo") or {}
        ladder_prices = self._build_option_sell_price_ladder(nbbo)
        if not ladder_prices:
            return None

        ladder_attempts: List[Dict[str, Any]] = []
        multiplier = float(contract_details.get("multiplier", "100"))
        fill_wait_start = time.time()
        total_attempts = 0

        for price in ladder_prices:
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before placing option ladder order")

            order = LimitOrder("SELL", quantity, price)
            order_start = time.time()
            trade = ib.placeOrder(contract, order)
            total_attempts += 1

            while True:
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    try:
                        ib.cancelOrder(order)
                    except Exception:
                        pass
                    raise TimeoutError("Trade timed out before ladder order fill")

                if trade.isDone():
                    duration = time.time() - order_start
                    fill_price = trade.orderStatus.avgFillPrice or price
                    filled_contracts = int(trade.orderStatus.filled or quantity)
                    fill_wait_time = time.time() - fill_wait_start
                    total_time = time.time() - total_start_time
                    fill_venue = self._extract_fill_venue(trade)
                    total_cost = fill_price * multiplier * filled_contracts
                    ladder_attempts.append(
                        self._describe_ladder_attempt(price, "filled", duration)
                    )
                    contract_details["sell_price_ladder"] = ladder_attempts
                    logger.info(
                        "🎯 OPTION LADDER FILLED",
                        fill_price=fill_price,
                        contracts=filled_contracts,
                        attempts=total_attempts,
                    )
                    return TradeResult(
                        success=True,
                        shares=filled_contracts,
                        fill_price=fill_price,
                        total_cost=total_cost,
                        session="market_hours",
                        order_type="LIMIT",
                        timing_info={
                            "session_detection": session_time,
                            "connection": connect_time,
                            "contract_preparation": contract_time,
                            "order_creation": 0.0,
                            "order_placement": time.time() - order_start,
                            "fill_wait": fill_wait_time,
                            "total_time": total_time,
                            "attempts": total_attempts,
                            "ladder_attempts": len(ladder_attempts),
                        },
                        instrument=TradeInstrument.OPTION.value,
                        instrument_details={
                            **contract_details,
                            "fill_venue": fill_venue,
                            "execution_mode": "ladder_limit",
                        },
                    )

                elapsed = time.time() - order_start
                if elapsed >= OPTION_SELL_STEP_SECONDS:
                    try:
                        ib.cancelOrder(order)
                    except Exception:
                        pass
                    ladder_attempts.append(
                        self._describe_ladder_attempt(price, "cancelled", elapsed)
                    )
                    await asyncio.sleep(0.05)
                    break

                await asyncio.sleep(0.08)

        contract_details["sell_price_ladder"] = ladder_attempts
        contract_details["ladder_exhausted"] = True
        return None

    @staticmethod
    def _get_market_data_type_value(ib: IB) -> Optional[int]:
        """Safely retrieve the current market data type from the IB client."""
        try:
            attr = getattr(ib, "marketDataType", None)
            if callable(attr):
                return attr()
            return attr
        except Exception:
            return None

    @staticmethod
    def _build_nbbo_info(
        bid: Optional[float],
        ask: Optional[float],
        *,
        spread: Optional[float] = None,
        source: str = "ladder",
        fallback: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Compose NBBO telemetry from bid/ask with optional fallback data."""
        nbbo: Dict[str, Any] = {}
        def _valid(value: Optional[float]) -> bool:
            return value is not None and isinstance(value, (int, float)) and value > 0

        if _valid(bid):
            nbbo["bid"] = float(bid)  # type: ignore[arg-type]
        if _valid(ask):
            nbbo["ask"] = float(ask)  # type: ignore[arg-type]

        if "bid" in nbbo and "ask" in nbbo:
            nbbo["mid"] = round((nbbo["bid"] + nbbo["ask"]) / 2.0, 4)
            nbbo["spread"] = round(nbbo["ask"] - nbbo["bid"], 4)
        else:
            if fallback:
                for key in ("bid", "ask", "mid", "spread"):
                    if key not in nbbo and fallback.get(key) is not None:
                        nbbo[key] = fallback.get(key)
        if spread is not None:
            nbbo["spread"] = float(spread)
        if "spread" not in nbbo and "bid" in nbbo and "ask" in nbbo:
            nbbo["spread"] = round(nbbo["ask"] - nbbo["bid"], 4)
        nbbo["source"] = source
        if fallback and not nbbo:
            return fallback
        if fallback:
            for key, value in fallback.items():
                nbbo.setdefault(key, value)
        return nbbo or None

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
        # Daily restart watchdog retired; rely on gateway's configured restart.

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
        """Legacy no-op; daily watchdog removed in favor of single scheduled restart."""
        return

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
            instrument=str(getattr(trade_request, "instrument", TradeInstrument.STOCK)),
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

            instrument_value = getattr(trade_request, "instrument", TradeInstrument.STOCK)
            if isinstance(instrument_value, str):
                try:
                    instrument = TradeInstrument(instrument_value)
                except ValueError:
                    logger.warning("Unknown instrument on trade request, defaulting to stock", instrument=instrument_value)
                    instrument = TradeInstrument.STOCK
            else:
                instrument = instrument_value

            if instrument == TradeInstrument.OPTION and session != "market_hours":
                logger.error("❌ Option trading only supported during regular market hours")
                return TradeResult(
                    success=False,
                    error="Options trading only supported during market hours",
                    session=session,
                    order_type="MARKET",
                    instrument=instrument.value,
                )

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
            if instrument == TradeInstrument.OPTION:
                contract, contract_details, contract_time = await self._prepare_option_contract(
                    ib, trade_request, deadline
                )
                if contract is None:
                    return TradeResult(
                        success=False,
                        error="Unable to prepare option contract",
                        session=session,
                        order_type="MARKET",
                        instrument=instrument.value,
                    )
                logger.info(f"✅ Option contract prepared: {contract} - {contract_time:.3f}s")
                return await self._execute_option_trade(
                    ib,
                    contract,
                    trade_request,
                    contract_details,
                    total_start_time,
                    session_time,
                    connect_time,
                    contract_time,
                    deadline,
                )

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
            instrument_value = getattr(trade_request, "instrument", TradeInstrument.STOCK)
            if isinstance(instrument_value, str):
                try:
                    instrument_value = TradeInstrument(instrument_value)
                except ValueError:
                    instrument_value = TradeInstrument.STOCK
            return TradeResult(
                success=False,
                error=str(exc),
                session=session,
                order_type=order_type_hint,
                instrument=instrument_value.value,
            )
        except asyncio.TimeoutError as exc:
            logger.error("❌ Trade execution timed out", error=str(exc))
            instrument_value = getattr(trade_request, "instrument", TradeInstrument.STOCK)
            if isinstance(instrument_value, str):
                try:
                    instrument_value = TradeInstrument(instrument_value)
                except ValueError:
                    instrument_value = TradeInstrument.STOCK
            return TradeResult(success=False, error="Trade attempt timed out", instrument=instrument_value.value)
        except Exception as exc:
            logger.error("❌ Trade execution failed", error=str(exc))
            logger.error(f"📝 Exception type: {type(exc).__name__}")
            import traceback

            logger.error(f"📝 Full traceback:\n{traceback.format_exc()}")
            instrument_value = getattr(trade_request, "instrument", TradeInstrument.STOCK)
            if isinstance(instrument_value, str):
                try:
                    instrument_value = TradeInstrument(instrument_value)
                except ValueError:
                    instrument_value = TradeInstrument.STOCK
            return TradeResult(success=False, error=str(exc), instrument=instrument_value.value)

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
                logger.debug(
                    "Requested market data type",
                    market_data_type=self._get_market_data_type_value(ib),
                )
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
            last_snapshot: Dict[str, Any] = {}
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
                snapshot = {
                    "last": float(last_price) if last_price else None,
                    "bid": float(bid) if bid else None,
                    "ask": float(ask) if ask else None,
                    "close": float(close) if close else None,
                    "iteration": iteration,
                }
                if snapshot.get("bid") is not None and snapshot.get("ask") is not None:
                    snapshot["mid"] = round((snapshot["bid"] + snapshot["ask"]) / 2.0, 4)
                    snapshot["spread"] = round(snapshot["ask"] - snapshot["bid"], 4)
                last_snapshot = snapshot
                if last_price and last_price > 0:
                    snapshot["price_used"] = float(last_price)
                    snapshot["price_source"] = "last"
                    self._record_quote_snapshot(contract.symbol, snapshot)
                    ib.cancelMktData(qualified)
                    return float(last_price)
                if bid and ask and bid > 0 and ask > 0:
                    mid_price = (bid + ask) / 2.0
                    snapshot["price_used"] = float(mid_price)
                    snapshot["price_source"] = "mid"
                    self._record_quote_snapshot(contract.symbol, snapshot)
                    ib.cancelMktData(qualified)
                    return float(mid_price)
                if close and close > 0:
                    snapshot["price_used"] = float(close)
                    snapshot["price_source"] = "close"
                    self._record_quote_snapshot(contract.symbol, snapshot)
                    ib.cancelMktData(qualified)
                    return float(close)
            ib.cancelMktData(qualified)
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                last_snapshot.setdefault("price_used", None)
                last_snapshot.setdefault("price_source", "timeout")
                self._record_quote_snapshot(contract.symbol, last_snapshot)
                logger.error(
                    "⏱️ Timeout waiting for IBKR quote",
                    ticker=contract.symbol,
                    snapshot=last_snapshot,
                    market_data_type=self._get_market_data_type_value(ib),
                )
                raise TimeoutError(
                    "Timeout waiting for IBKR quote (no last/bid/ask received); check real-time market data subscription"
                )
            last_snapshot.setdefault("price_used", None)
            last_snapshot.setdefault("price_source", "unavailable")
            self._record_quote_snapshot(contract.symbol, last_snapshot)
            logger.error(
                "❌ IBKR quote unavailable (no last/bbo/close)",
                ticker=contract.symbol,
                snapshot=last_snapshot,
                market_data_type=self._get_market_data_type_value(ib),
            )
            return None
        except TimeoutError:
            raise
        except Exception as exc:
            snapshot = {"price_used": None, "price_source": "error", "error": str(exc)}
            self._record_quote_snapshot(contract.symbol, snapshot)
            logger.error(f"❌ Error fetching IBKR quote for {contract.symbol}: {exc}")
            return None

    async def _prepare_option_contract(
        self,
        ib: IB,
        trade_request: TradeRequest,
        timeout_deadline: Optional[float],
    ) -> Tuple[Optional[Option], Optional[Dict[str, Any]], float]:
        """
        Select and qualify an at-the-money call option contract for the given ticker.
        """

        start = time.time()

        def time_left() -> Optional[float]:
            if timeout_deadline is None:
                return None
            return timeout_deadline - time.monotonic()

        remaining = time_left()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Trade timed out before preparing option contract")

        option_params = getattr(trade_request, "option_contract", None)
        if option_params:
            if isinstance(option_params, dict):
                option_params = OptionContractParams(**option_params)
            option_contract = Option(
                option_params.symbol,
                option_params.last_trade_date_or_contract_month,
                option_params.strike,
                option_params.right,
                option_params.exchange,
                option_params.currency,
            )
            option_contract.multiplier = option_params.multiplier
            if option_params.trading_class:
                option_contract.tradingClass = option_params.trading_class
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before qualifying option contract")
            qualify_option_coro = ib.qualifyContractsAsync(option_contract)
            if remaining is None:
                qualified_option = await qualify_option_coro
            else:
                qualified_option = await asyncio.wait_for(qualify_option_coro, timeout=max(remaining, 0))
            if not qualified_option:
                logger.error("❌ Unable to qualify provided option contract", contract=option_contract)
                return None, None, time.time() - start
            [qualified_option_contract] = qualified_option
            details = {
                "symbol": option_params.symbol,
                "expiry": option_params.last_trade_date_or_contract_month,
                "strike": option_params.strike,
                "right": option_params.right,
                "multiplier": option_params.multiplier,
                "trading_class": option_params.trading_class,
                "exchange": option_params.exchange,
                "currency": option_params.currency,
                "con_id": qualified_option_contract.conId,
            }
            return qualified_option_contract, details, time.time() - start

        stock_contract = Stock(trade_request.ticker, "SMART", "USD")

        qualify_coro = ib.qualifyContractsAsync(stock_contract)
        if remaining is None:
            qualified_stock = await qualify_coro
        else:
            qualified_stock = await asyncio.wait_for(qualify_coro, timeout=max(remaining, 0))

        if not qualified_stock:
            logger.error("❌ Unable to qualify underlying stock contract for option selection")
            return None, None, time.time() - start

        [qualified_underlying] = qualified_stock

        remaining = time_left()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Trade timed out before retrieving underlying price")

        underlying_price = await self.get_ibkr_realtime_price(ib, stock_contract, timeout_deadline)
        if underlying_price is None:
            logger.error("❌ Unable to fetch underlying price for option selection", ticker=trade_request.ticker)
            return None, None, time.time() - start

        remaining = time_left()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Trade timed out before retrieving option parameters")

        try:
            params_list = await (
                ib.reqSecDefOptParamsAsync(
                    trade_request.ticker, "", "STK", qualified_underlying.conId
                )
                if remaining is None
                else asyncio.wait_for(
                    ib.reqSecDefOptParamsAsync(
                        trade_request.ticker, "", "STK", qualified_underlying.conId
                    ),
                    timeout=max(remaining, 0),
                )
            )
        except asyncio.TimeoutError:
            raise TimeoutError("Timeout while retrieving option parameters") from None

        if not params_list:
            logger.error("❌ IBKR returned empty option parameters", ticker=trade_request.ticker)
            return None, None, time.time() - start

        today = datetime.now(pytz.UTC).date()
        chains_to_consider = [
            chain
            for chain in params_list
            if (getattr(chain, "exchange", "") or "").upper() not in OPTION_EXCHANGE_DENYLIST
        ] or params_list

        best_chain = None
        best_expiry = None
        best_strike = None
        best_delta = None
        best_offset_index: Optional[int] = None

        for chain in chains_to_consider:
            if not getattr(chain, "expirations", None) or not getattr(chain, "strikes", None):
                continue
            expirations = sorted(
                (datetime.strptime(exp, "%Y%m%d").date() for exp in chain.expirations),
                key=lambda d: (d < today, d),
            )
            future_expirations = [exp for exp in expirations if exp >= today]
            candidates = future_expirations or expirations
            if not candidates:
                continue
            offset_index = min(OPTION_EXPIRY_OFFSET, len(candidates) - 1)
            selected_expiry = candidates[offset_index]

            strikes: List[float] = []
            for s in chain.strikes:
                try:
                    strikes.append(float(s))
                except (TypeError, ValueError):
                    continue
            if not strikes:
                continue

            strike_value = min(strikes, key=lambda s: abs(s - underlying_price))
            delta = abs(strike_value - underlying_price)

            if (
                best_expiry is None
                or selected_expiry < best_expiry
                or (
                    selected_expiry == best_expiry
                    and (best_delta is None or delta < best_delta)
                )
            ):
                best_chain = chain
                best_expiry = selected_expiry
                best_strike = float(strike_value)
                best_delta = delta
                best_offset_index = offset_index

        if not (best_chain and best_expiry and best_strike):
            logger.error("❌ Could not determine suitable option chain", ticker=trade_request.ticker)
            return None, None, time.time() - start

        expiry_str = best_expiry.strftime("%Y%m%d")
        exchange_choice = getattr(best_chain, "exchange", None)
        exchange_upper = (exchange_choice or "").upper()
        if not exchange_choice or exchange_upper in OPTION_EXCHANGE_DENYLIST:
            exchange_choice = OPTION_EXCHANGE_FALLBACK

        option_contract = Option(
            trade_request.ticker,
            expiry_str,
            best_strike,
            "C",
            exchange_choice or "SMART",
            getattr(best_chain, "currency", None) or "USD",
        )
        if best_chain.tradingClass:
            option_contract.tradingClass = best_chain.tradingClass
        if getattr(best_chain, "multiplier", None):
            option_contract.multiplier = best_chain.multiplier

        remaining = time_left()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Trade timed out before qualifying option contract")

        qualify_option_coro = ib.qualifyContractsAsync(option_contract)
        if remaining is None:
            qualified_option = await qualify_option_coro
        else:
            qualified_option = await asyncio.wait_for(qualify_option_coro, timeout=max(remaining, 0))

        if not qualified_option:
            logger.error("❌ Unable to qualify selected option contract", contract=option_contract)
            return None, None, time.time() - start

        [qualified_option_contract] = qualified_option
        contract_details = {
            "symbol": trade_request.ticker,
            "expiry": expiry_str,
            "strike": best_strike,
            "right": "C",
            "multiplier": getattr(qualified_option_contract, "multiplier", getattr(option_contract, "multiplier", "100")),
            "trading_class": getattr(qualified_option_contract, "tradingClass", getattr(option_contract, "tradingClass", None)),
            "exchange": qualified_option_contract.exchange or option_contract.exchange,
            "currency": getattr(qualified_option_contract, "currency", None) or getattr(option_contract, "currency", "USD"),
            "con_id": qualified_option_contract.conId,
            "underlying_price": underlying_price,
            "last_trade_date_or_contract_month": expiry_str,
            "underlying_nbbo": self.get_last_quote_snapshot(trade_request.ticker),
            "expiry_offset_index": best_offset_index,
        }

        return qualified_option_contract, contract_details, time.time() - start

    async def _execute_option_trade(
        self,
        ib: IB,
        contract: Option,
        trade_request: TradeRequest,
        contract_details: Dict[str, Any],
        total_start_time: float,
        session_time: float,
        connect_time: float,
        contract_time: float,
        timeout_deadline: Optional[float] = None,
    ) -> TradeResult:
        """Execute a market order for an option contract during regular trading hours."""

        def time_left() -> Optional[float]:
            if timeout_deadline is None:
                return None
            return timeout_deadline - time.monotonic()

        remaining = time_left()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Trade timed out before option order creation")

        quantity = trade_request.shares or 1
        action = trade_request.action.upper()

        option_nbbo = contract_details.get("option_nbbo")
        try:
            remaining = time_left()
            if remaining is None or remaining > 0:
                market_data_ticker = ib.reqMktData(contract, "", False, False)
                snapshot_delay = OPTION_NBBO_SNAPSHOT_DELAY if remaining is None else min(
                    OPTION_NBBO_SNAPSHOT_DELAY, max(remaining, 0)
                )
                if snapshot_delay > 0:
                    await asyncio.sleep(snapshot_delay)
                option_nbbo = self._build_nbbo_info(
                    getattr(market_data_ticker, "bid", None),
                    getattr(market_data_ticker, "ask", None),
                    source="option_snapshot",
                    fallback=option_nbbo,
                ) or option_nbbo
        except Exception as exc:
            logger.warning("⚠️ Unable to capture option NBBO snapshot", error=str(exc))
        finally:
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass

        if option_nbbo:
            contract_details["option_nbbo"] = option_nbbo

        if action == "SELL":
            ladder_result = await self._attempt_option_sell_ladder(
                ib,
                contract,
                quantity,
                contract_details,
                session_time,
                connect_time,
                contract_time,
                total_start_time,
                time_left,
            )
            if ladder_result is not None:
                return ladder_result

        order_create_start = time.time()
        order = MarketOrder(action, quantity)
        order_create_time = time.time() - order_create_start
        logger.info(f"✅ Option market order created: {order} (create: {order_create_time:.3f}s)")

        place_start = time.time()
        trade = ib.placeOrder(contract, order)
        place_time = time.time() - place_start
        logger.info(f"✅ Option order placed: {trade} (place: {place_time:.3f}s)")

        fill_wait_start = time.time()
        attempts = 0
        while True:
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                try:
                    ib.cancelOrder(order)
                except Exception:
                    pass
                raise TimeoutError("Trade timed out before option order fill")

            sleep_interval = 0.25 if remaining is None else min(0.25, max(remaining, 0))
            if sleep_interval > 0:
                await asyncio.sleep(sleep_interval)
            attempts += 1
            if trade.isDone():
                fill_price = trade.orderStatus.avgFillPrice or 0.0
                filled_contracts = int(trade.orderStatus.filled or quantity)
                fill_wait_time = time.time() - fill_wait_start
                total_time = time.time() - total_start_time
                multiplier = float(contract_details.get("multiplier", "100"))
                total_cost = fill_price * multiplier * filled_contracts
                fill_venue = self._extract_fill_venue(trade)
                logger.info(
                    "🎉 OPTION ORDER FILLED",
                    fill_price=fill_price,
                    contracts=filled_contracts,
                    multiplier=multiplier,
                )
                return TradeResult(
                    success=True,
                    shares=filled_contracts,
                    fill_price=fill_price,
                    total_cost=total_cost,
                    session="market_hours",
                    order_type="MARKET",
                    timing_info={
                        "session_detection": session_time,
                        "connection": connect_time,
                        "contract_preparation": contract_time,
                        "order_creation": order_create_time,
                        "order_placement": place_time,
                        "fill_wait": fill_wait_time,
                        "total_time": total_time,
                        "attempts": attempts,
                    },
                    instrument=TradeInstrument.OPTION.value,
                    instrument_details={**contract_details, "fill_venue": fill_venue},
                )

            if remaining is None and attempts >= 120:
                try:
                    ib.cancelOrder(order)
                except Exception:
                    pass
                break

        total_time = time.time() - total_start_time
        logger.warning("⚠️ OPTION ORDER TIMEOUT - Did not fill within allotted time")
        return TradeResult(
            success=False,
            error="Option order timeout - did not fill within expected time",
            session="market_hours",
            order_type="MARKET",
            timing_info={
                "session_detection": session_time,
                "connection": connect_time,
                "contract_preparation": contract_time,
                "order_creation": order_create_time,
                "order_placement": place_time,
                "total_time": total_time,
                "attempts": attempts,
            },
            instrument=TradeInstrument.OPTION.value,
            instrument_details={**contract_details, "fill_venue": None},
        )

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
            quantity = trade_request.shares
            instrument = getattr(trade_request, "instrument", TradeInstrument.STOCK)
            if isinstance(instrument, str):
                instrument = TradeInstrument(instrument)

            quote_snapshot = None
            if quantity is None:
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("Trade timed out before price retrieval for quantity sizing")
                price_start = time.time()
                current_price = await self.get_ibkr_realtime_price(ib, contract, timeout_deadline)
                price_time = time.time() - price_start
                logger.info(f"💰 Market hours price retrieval for sizing: {price_time:.3f}s")
                quote_snapshot = self.get_last_quote_snapshot(contract.symbol)
                if not current_price:
                    logger.error("❌ Unable to determine market price for quantity sizing", ticker=contract.symbol)
                    return TradeResult(
                        success=False,
                        error="Could not retrieve price to size order",
                        session="market_hours",
                        order_type="MARKET",
                        instrument=instrument.value,
                        instrument_details={
                            "leverage": getattr(trade_request, "leverage", None),
                            "target_notional": trade_request.amount_usd,
                            "nbbo": quote_snapshot,
                        },
                    )
                target_notional = max(trade_request.amount_usd, current_price)
                quantity = max(1, int(target_notional // current_price))
                logger.info(
                    "Calculated share quantity for market-hours trade",
                    quantity=quantity,
                    target_notional=target_notional,
                    price=current_price,
                )
            else:
                logger.debug("Using explicit quantity for market-hours trade", quantity=quantity)
                quote_snapshot = self.get_last_quote_snapshot(contract.symbol)

            order_create_start = time.time()
            order = MarketOrder(action, quantity)
            order_create_time = time.time() - order_create_start
            logger.info(f"✅ Market order created: {order} (create: {order_create_time:.3f}s)")
            
            place_start = time.time()
            trade = ib.placeOrder(contract, order)
            place_time = time.time() - place_start
            logger.info(f"✅ Order placed: {trade} (place: {place_time:.3f}s)")
            
            fill_wait_start = time.time()
            attempts = 0
            while True:
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
                attempts += 1
                if trade.isDone():
                    fill_price = trade.orderStatus.avgFillPrice or 0.0
                    filled_shares = int(trade.orderStatus.filled or quantity)
                    fill_venue = self._extract_fill_venue(trade)
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
                            "attempts": attempts,
                        },
                        instrument=instrument.value,
                        instrument_details={
                            "leverage": getattr(trade_request, "leverage", None),
                            "target_notional": trade_request.amount_usd,
                            "fill_venue": fill_venue,
                            "nbbo": quote_snapshot,
                        },
                    )
                if remaining is None and attempts >= 120:
                    try:
                        ib.cancelOrder(order)
                    except Exception:
                        pass
                    break
            total_time = time.time() - total_start_time
            logger.warning("⚠️ ORDER TIMEOUT - Did not fill before timeout")
            return TradeResult(
                success=False, 
                error="Order timeout - did not fill before timeout",
                session="market_hours",
                order_type="MARKET",
                timing_info={
                    "session_detection": session_time,
                    "connection": connect_time,
                    "contract_creation": contract_time,
                    "order_creation": order_create_time,
                    "order_placement": place_time,
                    "total_time": total_time,
                "attempts": attempts,
                },
                instrument=instrument.value,
                instrument_details={
                    "leverage": getattr(trade_request, "leverage", None),
                    "target_notional": trade_request.amount_usd,
                    "nbbo": quote_snapshot,
                },
            )
        except TimeoutError:
            raise
        except Exception as exc:
            logger.error(f"❌ Market hours trade failed: {exc}")
            instrument = getattr(trade_request, "instrument", TradeInstrument.STOCK)
            if isinstance(instrument, str):
                try:
                    instrument = TradeInstrument(instrument)
                except ValueError:
                    instrument = TradeInstrument.STOCK
            return TradeResult(
                success=False, 
                error=str(exc),
                session="market_hours",
                order_type="MARKET",
                instrument=instrument.value,
                instrument_details={
                    "leverage": getattr(trade_request, "leverage", None),
                    "target_notional": trade_request.amount_usd,
                    "nbbo": quote_snapshot,
                },
            )

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
        projected_notional: Optional[float] = None
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
            quote_snapshot = self.get_last_quote_snapshot(contract.symbol) or {}
            nbbo_info: Optional[Dict[str, Any]] = quote_snapshot if quote_snapshot else None
            
            price_fallback_used = False
            if not current_price:
                fallback_price = None
                if trade_request.shares and trade_request.amount_usd:
                    fallback_price = trade_request.amount_usd / max(trade_request.shares, 1)
                if fallback_price and fallback_price > 0:
                    logger.warning(
                        "⚠️ Falling back to estimated price for extended-hours trade",
                        ticker=contract.symbol,
                        fallback_price=fallback_price,
                    )
                    current_price = fallback_price
                    price_fallback_used = True
                else:
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
                    instrument=TradeInstrument.STOCK.value,
                    instrument_details={
                        "leverage": getattr(trade_request, "leverage", None),
                        "target_notional": trade_request.amount_usd,
                        "requested_notional": trade_request.amount_usd,
                        "projected_notional": 0.0,
                        "effective_notional": 0.0,
                            "nbbo": quote_snapshot,
                    },
                )
            
            action = trade_request.action.upper()
            quantity = trade_request.shares
            leverage = getattr(trade_request, "leverage", None) or 1.0
            if quantity is None:
                base_notional = trade_request.amount_usd or current_price
                target_notional = max(base_notional * leverage, current_price)
                raw_quantity = target_notional / current_price
                quantity = max(1, int(math.ceil(raw_quantity - 1e-9)))
                logger.info(
                    "Calculated share quantity for extended-hours trade",
                    quantity=quantity,
                    requested_notional=base_notional,
                    leverage=leverage,
                    target_notional=target_notional,
                    price=current_price,
                    raw_quantity=raw_quantity,
                )
            projected_notional = quantity * current_price
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
                return TradeResult(
                    success=False,
                    error="Could not qualify contract",
                    session=session,
                    order_type="LIMIT",
                    instrument=TradeInstrument.STOCK.value,
                    instrument_details={
                        "leverage": getattr(trade_request, "leverage", None),
                        "target_notional": trade_request.amount_usd,
                        "requested_notional": trade_request.amount_usd,
                        "projected_notional": projected_notional,
                        "effective_notional": 0.0,
                        "nbbo": quote_snapshot,
                    },
                )

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
            nbbo_info = self._build_nbbo_info(
                bid if bid and bid > 0 else None,
                ask if ask and ask > 0 else None,
                source="ladder_snapshot",
                fallback=quote_snapshot,
            ) or nbbo_info

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
                        fill_venue = self._extract_fill_venue(trade)
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
                            instrument=TradeInstrument.STOCK.value,
                            instrument_details={
                                "leverage": getattr(trade_request, "leverage", None),
                                "target_notional": trade_request.amount_usd,
                                "requested_notional": trade_request.amount_usd,
                                "projected_notional": projected_notional,
                                "effective_notional": fill_price * filled_shares,
                                "nbbo": nbbo_info,
                                "fill_venue": fill_venue,
                                "used_price_fallback": price_fallback_used,
                            },
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
                instrument=TradeInstrument.STOCK.value,
                instrument_details={
                    "leverage": getattr(trade_request, "leverage", None),
                    "target_notional": trade_request.amount_usd,
                    "requested_notional": trade_request.amount_usd,
                    "projected_notional": projected_notional,
                    "effective_notional": 0.0,
                    "nbbo": nbbo_info,
                    "fill_venue": None,
                    "used_price_fallback": price_fallback_used,
                },
            )
        except TimeoutError:
            raise
        except Exception as exc:
            logger.error(f"❌ Extended hours trade failed: {exc}")
            return TradeResult(
                success=False, 
                error=str(exc),
                session=session,
                order_type="LIMIT",
                instrument=TradeInstrument.STOCK.value,
                instrument_details={
                    "leverage": getattr(trade_request, "leverage", None),
                    "target_notional": trade_request.amount_usd,
                    "requested_notional": trade_request.amount_usd,
                    "projected_notional": projected_notional if projected_notional is not None else 0.0,
                    "effective_notional": 0.0,
                    "nbbo": nbbo_info,
                },
            )

    async def execute_trade(
        self, trade_request: TradeRequest, timeout_seconds: Optional[float] = None
    ) -> TradeResult:
        return await self._execute_trade(trade_request, timeout_seconds)

    @staticmethod
    def _extract_fill_venue(trade) -> Optional[str]:
        try:
            fills = getattr(trade, "fills", None)
            if not fills:
                last_liquidity = getattr(trade.orderStatus, "lastLiquidity", None)
                return str(last_liquidity) if last_liquidity else None
            venues = {
                getattr(fill.execution, "exchange", "")
                for fill in fills
                if getattr(fill, "execution", None)
            }
            venues = {venue for venue in venues if venue}
            if not venues:
                last_liquidity = getattr(trade.orderStatus, "lastLiquidity", None)
                return str(last_liquidity) if last_liquidity else None
            return ",".join(sorted(venues))
        except Exception:
            return None

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