"""
Alpaca Brokerage Service - main orchestrator for brokerage infrastructure.
Pure infrastructure - coordinates connection, quotes, executors, and queue.
"""
import asyncio
import time
from typing import Optional, Dict, Any
from datetime import datetime
from copy import deepcopy

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest
from .infrastructure_models import InfrastructureTradeExecutionRequestEvent
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import InfrastructureEventType
from .connection_manager import AlpacaConnectionManager
from .quote_fetcher import AlpacaQuoteFetcher
from .trade_executor_market_hours import AlpacaMarketHoursTradeExecutor
from .trade_executor_extended_hours import AlpacaExtendedHoursTradeExecutor
from .queue_manager import TradeQueueManager
from .events import BrokerageHealthStatusEvent
from ...utils.brokerage.session_detector import get_market_session
from ...utils.service_utils import serialize_stats
from ..notification.fast_trade_notifier import FastTradeNotifier

logger = get_logger(__name__)


class BrokerageService:
    """
    Main Alpaca brokerage service orchestrator.
    
    Responsibilities:
    - Manage connection lifecycle
    - Route trades to appropriate executors
    - Queue closed-market trades
    - Coordinate quote fetching
    - Publish health status events
    
    Does NOT:
    - Know about business logic
    - Send Telegram notifications
    - Know about AI classification
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        metrics_service,  # Required - injected via DI
        paper_trading: bool = True,
        fast_notifier: Optional[FastTradeNotifier] = None,
    ):
        """
        Initialize brokerage service.

        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            paper_trading: Whether to use paper trading
            metrics_service: Metrics service for statistics (injected via DI)
            fast_notifier: Optional fast trade notifier for immediate Telegram notifications
        """
        self.paper_trading = paper_trading

        # Core components - inject event_bus into all sub-components
        self.connection_manager = AlpacaConnectionManager(
            event_bus=event_bus,
            paper_trading=paper_trading,
            metrics_service=metrics_service
        )

        # Quote fetcher needs market data client and optional WebSocket stream manager
        self.quote_fetcher = AlpacaQuoteFetcher(
            event_bus=event_bus,
            market_data_client=self.connection_manager.market_data_client,
            stream_manager=self.connection_manager.stream_manager  # Optional - backward compatible
        )

        # Trade executors (with optional fast notifier for immediate Telegram)
        self.market_hours_executor = AlpacaMarketHoursTradeExecutor(
            event_bus=event_bus,
            quote_fetcher=self.quote_fetcher,
            trading_client=self.connection_manager.trading_client,
            fast_notifier=fast_notifier,
        )

        self.extended_hours_executor = AlpacaExtendedHoursTradeExecutor(
            event_bus=event_bus,
            quote_fetcher=self.quote_fetcher,
            trading_client=self.connection_manager.trading_client,
            fast_notifier=fast_notifier,
        )

        self.queue_manager = TradeQueueManager(event_bus=event_bus)

        # Event bus
        self.event_bus = event_bus

        # Shadow paper trading executors (for parallel paper trades when in live mode)
        self.paper_shadow_extended_executor = None
        self.paper_shadow_market_executor = None
        paper_shadow_client = self.connection_manager.get_paper_shadow_client()
        if paper_shadow_client:
            # Create shadow executors that trade on paper account
            # Note: No fast_notifier for shadow - we don't want double notifications
            self.paper_shadow_extended_executor = AlpacaExtendedHoursTradeExecutor(
                event_bus=event_bus,
                quote_fetcher=self.quote_fetcher,
                trading_client=paper_shadow_client,
                fast_notifier=None,  # No notifications for shadow trades
            )
            self.paper_shadow_market_executor = AlpacaMarketHoursTradeExecutor(
                event_bus=event_bus,
                quote_fetcher=self.quote_fetcher,
                trading_client=paper_shadow_client,
                fast_notifier=None,  # No notifications for shadow trades
            )
            logger.info("✅ Shadow paper executors initialized for parallel paper trading")

        mode = "Paper Trading" if paper_trading else "Live Trading"
        logger.info(f"BrokerageService initialized for {mode}", paper_trading=paper_trading)

    @property
    def stream_manager(self):
        """Expose WebSocket stream manager for real-time quote subscriptions."""
        return self.connection_manager.stream_manager if self.connection_manager else None

    async def start(self) -> None:
        """
        Start the brokerage service.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        logger.info("🚀 Starting Brokerage Service")
        
        # Subscribe to trade execution requests from domain listener
        # Event bus automatically prevents duplicate subscriptions
        self.event_bus.subscribe(InfrastructureEventType.TRADE_EXECUTION_REQUESTED, self._handle_trade_execution_request)
        logger.info("Subscribed to TradeExecutionRequested events")
        
        # Start connection manager (will connect automatically, idempotent)
        await self.connection_manager.start()
        
        logger.info("✅ Brokerage Service started")
    
    async def _handle_trade_execution_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Handle trade execution request from domain listener.

        Receives typed InfrastructureTradeExecutionRequestEvent and executes trade.
        """
        try:
            # Reconstruct typed infrastructure event
            infra_event = InfrastructureTradeExecutionRequestEvent(**event_data)

            # Convert InfrastructureTradeRequestData to TradeRequest (shared model for now)
            from ...models.base_models import TradeInstrument
            trade_request = TradeRequest(
                ticker=infra_event.trade_request.ticker,
                amount_usd=infra_event.trade_request.amount_usd,
                action=infra_event.trade_request.action,
                shares=infra_event.trade_request.shares,
                leverage=infra_event.trade_request.leverage,
                instrument=TradeInstrument.STOCK,  # Stocks only
                article_id=infra_event.article_id or infra_event.trade_request.article_id,  # Preserve article_id
            )

            logger.info(
                "Received trade execution request from domain",
                ticker=trade_request.ticker,
                amount_usd=trade_request.amount_usd,
                metadata=infra_event.metadata
            )

            # Execute trade (pass metadata for exit notifications)
            result = await self.execute_trade(trade_request, timeout_seconds=30.0, metadata=infra_event.metadata)

            logger.info(
                "Trade execution completed",
                ticker=trade_request.ticker,
                success=result.get("success")
            )
            
        except Exception as e:
            logger.error(
                "Error handling trade execution request",
                error=str(e),
                event_data=event_data,
                exc_info=True
            )
    
    async def stop(self) -> None:
        """
        Stop the brokerage service.
        
        Idempotent: Safe to call multiple times. Connection manager stop is idempotent.
        """
        logger.info("🛑 Stopping Brokerage Service")
        
        # Stop connection manager (idempotent)
        await self.connection_manager.stop()
        
        # Unsubscribe from events (safe even if not subscribed)
        self.event_bus.unsubscribe(InfrastructureEventType.TRADE_EXECUTION_REQUESTED, self._handle_trade_execution_request)
        
        logger.info("✅ Brokerage Service stopped")
    
    def is_connected(self) -> bool:
        """Check if brokerage is connected."""
        return self.connection_manager.is_connected
    
    async def execute_trade(
        self,
        trade_request: TradeRequest,
        timeout_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a trade request.

        Args:
            trade_request: Trade request to execute
            timeout_seconds: Optional timeout in seconds
            metadata: Optional metadata (exit_reason, tier, etc.) for notifications

        Returns:
            Trade result dictionary
        """
        total_start_time = time.time()
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        
        def remaining_time() -> Optional[float]:
            if deadline is None:
                return None
            return deadline - time.monotonic()
        
        try:
            # Detect market session
            session_start = time.time()
            session, is_extended = get_market_session()
            session_time = time.time() - session_start
            logger.info(f"⏱️ Market session detection: {session_time:.3f}s", session=session)
            
            # Handle closed market - queue the trade
            if session == "closed":
                logger.info("🔒 Market is closed - queuing trade for next premarket")
                try:
                    self.queue_manager.queue_trade(trade_request)
                    return {
                        "success": False,
                        "error": "Market is currently closed - trade queued for next premarket",
                        "session": "closed",
                        "order_type": None,
                        "instrument": "stock",
                        "queued": True,
                    }
                except Exception as exc:
                    logger.error(f"Failed to queue trade: {exc}")
                    return {
                        "success": False,
                        "error": f"Market is closed and queueing failed: {str(exc)}",
                        "session": "closed",
                        "order_type": None,
                        "instrument": "stock",
                        "queued": False,
                    }
            
            # Ensure connection (simple for REST API)
            remaining = remaining_time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timeout reached before connecting")
            
            connect_start = time.time()
            await self.connection_manager.ensure_connected(remaining)
            connect_time = time.time() - connect_start
            logger.info(f"✅ Connection ready - {connect_time:.3f}s")
            
            # Prepare timing info (no contract creation needed for Alpaca!)
            timing_info = {
                "session_detection": session_time,
                "connection": connect_time,
            }
            
            # Route to appropriate executor
            if session == "market_hours":
                logger.info("📈 MARKET HOURS: Using market order strategy")
                result = await self.market_hours_executor.execute(
                    trade_request,
                    timing_info,
                    deadline,
                    metadata=metadata,
                )
                # Fire shadow paper trade (if enabled and live trade succeeded)
                if result.get("success") and self.paper_shadow_market_executor:
                    asyncio.create_task(self._execute_shadow_paper_trade(
                        trade_request, session, timing_info, metadata, is_market_hours=True
                    ))
                return result

            # Extended hours (premarket or postmarket)
            logger.info("🌙 EXTENDED HOURS: Using limit order strategy", session=session)
            result = await self.extended_hours_executor.execute(
                trade_request,
                session,
                timing_info,
                deadline,
                metadata=metadata,
            )
            # Fire shadow paper trade (if enabled and live trade succeeded)
            if result.get("success") and self.paper_shadow_extended_executor:
                asyncio.create_task(self._execute_shadow_paper_trade(
                    trade_request, session, timing_info, metadata, is_market_hours=False
                ))
            return result
        
        except TimeoutError as exc:
            logger.error(f"⏱️ Trade execution timed out: {exc}")
            return {
                "success": False,
                "error": f"Trade execution timed out: {str(exc)}",
                "session": session if 'session' in locals() else "unknown",
                "order_type": None,
                "instrument": "stock",
            }
        
        except Exception as exc:
            logger.error(f"❌ Trade execution failed: {exc}", exc_info=True)
            return {
                "success": False,
                "error": str(exc),
                "session": session if 'session' in locals() else "unknown",
                "order_type": None,
                "instrument": "stock",
            }

    async def _execute_shadow_paper_trade(
        self,
        trade_request: TradeRequest,
        session: str,
        timing_info: Dict[str, float],
        metadata: Optional[Dict[str, Any]],
        is_market_hours: bool,
    ) -> None:
        """
        Execute a shadow paper trade (fire-and-forget).

        Shadow trades mirror live trades to paper account at 1:1 size.
        This allows comparing live vs paper performance.

        Args:
            trade_request: Original trade request
            session: Trading session
            timing_info: Timing info from original trade
            metadata: Metadata from original trade
            is_market_hours: Whether this is market hours trading
        """
        try:
            # Create shadow trade request mirroring live size 1:1
            PAPER_MULTIPLIER = 1  # Live is at full production size — paper mirrors 1:1 (was 50x when live was scaled down)
            shadow_request = deepcopy(trade_request)
            if shadow_request.amount_usd:
                shadow_request.amount_usd = shadow_request.amount_usd * PAPER_MULTIPLIER

            logger.info(
                "📋 SHADOW PAPER TRADE: Mirroring to paper account",
                ticker=shadow_request.ticker,
                action=shadow_request.action,
                live_amount=trade_request.amount_usd,
                paper_amount=shadow_request.amount_usd,
                multiplier=f"{PAPER_MULTIPLIER}x"
            )

            # Execute on paper (no deadline - fire and forget)
            if is_market_hours and self.paper_shadow_market_executor:
                result = await self.paper_shadow_market_executor.execute(
                    shadow_request,
                    timing_info,
                    timeout_deadline=None,
                    metadata=metadata,
                )
            elif self.paper_shadow_extended_executor:
                result = await self.paper_shadow_extended_executor.execute(
                    shadow_request,
                    session,
                    timing_info,
                    timeout_deadline=None,
                    metadata=metadata,
                )
            else:
                logger.warning("No shadow executor available")
                return

            if result.get("success"):
                logger.info(
                    "✅ SHADOW PAPER TRADE FILLED",
                    ticker=shadow_request.ticker,
                    fill_price=result.get("fill_price"),
                    shares=result.get("shares"),
                    paper_amount=shadow_request.amount_usd
                )
            else:
                logger.warning(
                    "⚠️ SHADOW PAPER TRADE FAILED (non-critical)",
                    ticker=shadow_request.ticker,
                    error=result.get("error")
                )

        except Exception as e:
            # Shadow trades are non-critical - don't let failures affect live trading
            logger.warning(
                "⚠️ Shadow paper trade error (non-critical)",
                ticker=trade_request.ticker,
                error=str(e)
            )

    async def get_realtime_price(
        self,
        ticker: str,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[float]:
        """
        Get real-time price for a ticker.
        
        Args:
            ticker: Stock ticker symbol
            timeout_seconds: Optional timeout in seconds
            
        Returns:
            Real-time price or None if unavailable
        """
        try:
            # Ensure connection
            await self.connection_manager.ensure_connected(timeout_seconds)
            
            # Get price (no contract needed for Alpaca!)
            return await self.quote_fetcher.get_realtime_price(ticker)
        
        except Exception as exc:
            logger.error(f"Failed to get real-time price for {ticker}", error=str(exc))
            return None
    
    def get_market_session(self) -> tuple[str, bool]:
        """
        Get current market session.
        
        Returns:
            Tuple of (session_name, is_extended_hours)
        """
        return get_market_session()
    
    def get_last_quote_snapshot(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get the most recent quote snapshot for a ticker.
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            Quote snapshot dictionary or None
        """
        # Quote fetcher doesn't cache snapshots
        # Could add caching if needed, but for now return None
        return None
    
    def get_queued_trades(self) -> list[Dict[str, Any]]:
        """Get all queued trades."""
        return self.queue_manager.get_queued_trades()
    
    async def get_positions(self) -> list[Dict[str, Any]]:
        """
        Get all open positions from Alpaca.
        
        Returns:
            List of position dictionaries with symbol, qty, market_value, etc.
        """
        try:
            await self.connection_manager.ensure_connected()
            positions = self.connection_manager.trading_client.get_all_positions()
            
            result = []
            for pos in positions:
                result.append({
                    "symbol": pos.symbol,
                    "qty": float(pos.qty),
                    "market_value": float(pos.market_value) if pos.market_value else 0.0,
                    "unrealized_pl": float(pos.unrealized_pl) if pos.unrealized_pl else 0.0,
                    "avg_entry_price": float(pos.avg_entry_price) if pos.avg_entry_price else 0.0,
                })
            
            return result
        except Exception as exc:
            logger.error(f"Failed to get positions: {exc}", error=str(exc))
            return []
    
    async def manual_exit_position(
        self,
        ticker: str,
        exit_percentage: float = 1.0,
        entry_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Manually exit a position (or portion of it).
        
        Args:
            ticker: Ticker symbol to exit
            exit_percentage: Percentage of position to exit (0.0 to 1.0, default 1.0 = 100%)
            entry_price: Optional entry price for P&L calculation (if None, will try to get from position)
            
        Returns:
            Trade result dictionary with success, shares, fill_price, etc.
        """
        try:
            # Get current positions
            positions = await self.get_positions()
            position = next((p for p in positions if p["symbol"].upper() == ticker.upper()), None)
            
            if not position:
                return {
                    "success": False,
                    "error": f"No open position found for {ticker}",
                    "ticker": ticker
                }
            
            # Calculate shares to sell
            total_shares = position["qty"]
            shares_to_sell = int(total_shares * exit_percentage)
            
            if shares_to_sell <= 0:
                return {
                    "success": False,
                    "error": f"Invalid exit percentage: {exit_percentage} (would sell 0 shares)",
                    "ticker": ticker,
                    "total_shares": total_shares
                }
            
            # Use entry price from position if not provided
            if entry_price is None:
                entry_price = position.get("avg_entry_price", 0.0)
            
            # Create SELL trade request
            from ...domain.brokerage.models import TradeRequest, TradeAction, TradeInstrument
            trade_request = TradeRequest(
                ticker=ticker.upper(),
                action=TradeAction.SELL,
                shares=shares_to_sell,
                amount_usd=None,
                leverage=None,
                article_id=None,  # Manual exit, no article
                instrument=TradeInstrument.STOCK
            )
            
            # Execute the exit trade using the same smart exit system
            result = await self.execute_trade(
                trade_request,
                metadata={"exit_reason": "manual_exit"}
            )
            
            # Add position info to result
            result["position_info"] = {
                "total_shares": total_shares,
                "shares_sold": shares_to_sell,
                "shares_remaining": total_shares - shares_to_sell,
                "exit_percentage": exit_percentage,
                "entry_price": entry_price
            }
            
            return result
            
        except Exception as exc:
            logger.error(f"Failed to manually exit position {ticker}: {exc}", error=str(exc), exc_info=True)
            return {
                "success": False,
                "error": f"Failed to exit position: {str(exc)}",
                "ticker": ticker
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get brokerage service statistics."""
        stats = {
            "is_connected": self.is_connected(),
            "paper_trading": self.paper_trading,
            "queued_trades_count": len(self.queue_manager.get_queued_trades()),
        }
        return serialize_stats(stats)
    
    def is_healthy(self) -> bool:
        """Check if brokerage service is healthy."""
        return self.connection_manager.is_connected
    
    async def publish_health_status(self) -> None:
        """Publish health status event."""
        is_healthy = self.is_healthy()
        stats = self.get_stats()
        
        event = BrokerageHealthStatusEvent(
            is_healthy=is_healthy,
            reason="Service is healthy" if is_healthy else "Service is unhealthy",
            is_connected=self.is_connected(),
            occurred_at=datetime.now(),
            stats=stats,
            is_critical=not is_healthy and not self.is_connected(),
        )
        
        await self.event_bus.publish(InfrastructureEventType.BROKERAGE_HEALTH_STATUS, event.model_dump())
        logger.debug("Published BrokerageHealthStatus event", is_healthy=is_healthy)
