"""
Exit trade use case - schedules exit trades after entry trades execute.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Final, Dict, Optional
from decimal import Decimal

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.brokerage.events import TradeExecutedDomainEvent
from ...domain.brokerage.models import TradeRequest, TradeAction, TradeResult
from ...config import settings

logger = get_logger(__name__)


class ExitTradeUseCase:
    """
    Use case for scheduling exit trades after entry trades execute.

    Responsibilities:
    - Subscribe to Domain.TradeExecuted events
    - Filter for successful BUY trades (entries)
    - Schedule exit SELL trade after AUTO_TRADE_EXIT_DELAY_MINUTES (default 10 min)
    - Support /hold command to extend exit time for meteoric winners
    - 30-minute failsafe for held positions
    - Publish Domain.TradeRequested event for exit

    This ensures positions are automatically closed after the configured delay.
    """

    # Failsafe timeout for held positions (30 minutes)
    HOLD_FAILSAFE_MINUTES: int = 30

    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize exit trade use case.

        Args:
            event_bus: Event bus instance for publishing/subscribing to events
        """
        self.event_bus: Final[AsyncEventBus] = event_bus
        self.exit_delay_minutes: Final[int] = settings.AUTO_TRADE_EXIT_DELAY_MINUTES
        self._scheduled_exits: Dict[str, asyncio.Task] = {}

        # Track held tickers (user requested extended hold via /hold command)
        # Maps ticker -> failsafe task
        self._held_tickers: Dict[str, asyncio.Task] = {}

        # Store trade info for hold_ticker to use when creating failsafe
        # Maps ticker -> (trade_result, trade_request)
        self._trade_info: Dict[str, tuple] = {}

        # Subscribe to typed Domain.TradeExecuted events
        # Store wrapper for unsubscribe
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent,
            self._handle_trade_executed,
        )

        logger.info(
            "ExitTradeUseCase initialized - subscribes to Domain.TradeExecuted events",
            exit_delay_minutes=self.exit_delay_minutes,
            hold_failsafe_minutes=self.HOLD_FAILSAFE_MINUTES
        )
    
    async def start(self) -> None:
        """
        Start the use case (already subscribed in __init__).
        
        NOTE: Scheduled exits are stored in memory and will be lost on restart.
        For production, consider persisting scheduled exits or recovering from Alpaca positions on startup.
        """
        logger.info("ExitTradeUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case and cancel all scheduled exits."""
        # Cancel all scheduled exit tasks
        for ticker, task in self._scheduled_exits.items():
            if not task.done():
                task.cancel()
                logger.info(f"Cancelled scheduled exit for {ticker}")
        
        self._scheduled_exits.clear()
        
        self.event_bus.unsubscribe(DomainEventType.TRADE_EXECUTED, self._trade_executed_wrapper)
        logger.info("ExitTradeUseCase stopped")
    
    def cancel_scheduled_exit(self, ticker: str) -> bool:
        """
        Cancel scheduled exit for a ticker (called when manual exit happens).

        Args:
            ticker: Ticker symbol to cancel exit for

        Returns:
            True if exit was cancelled, False if no exit was scheduled
        """
        if ticker in self._scheduled_exits:
            task = self._scheduled_exits[ticker]
            if not task.done():
                task.cancel()
                del self._scheduled_exits[ticker]
                logger.info(
                    "ExitTradeUseCase: Cancelled scheduled exit due to manual exit",
                    ticker=ticker
                )
                return True
            else:
                # Task already completed, remove from dict
                del self._scheduled_exits[ticker]

        # Also cancel held ticker failsafe if exists
        if ticker in self._held_tickers:
            task = self._held_tickers[ticker]
            if not task.done():
                task.cancel()
            del self._held_tickers[ticker]

        return False

    def hold_ticker(self, ticker: str) -> bool:
        """
        Hold a ticker - cancel scheduled 10-min exit and start 30-min failsafe.

        Called when user sends /hold TICKER command for meteoric winners.

        Args:
            ticker: Ticker symbol to hold

        Returns:
            True if hold was activated, False if no scheduled exit exists
        """
        ticker = ticker.upper()

        # Cancel the scheduled 10-minute exit
        if ticker in self._scheduled_exits:
            task = self._scheduled_exits[ticker]
            if not task.done():
                task.cancel()
                del self._scheduled_exits[ticker]
                logger.info(
                    "🔒 HOLD: Cancelled 10-min auto-exit for ticker",
                    ticker=ticker,
                    failsafe_minutes=self.HOLD_FAILSAFE_MINUTES
                )

                # Start 30-minute failsafe using stored trade info
                if ticker in self._trade_info:
                    trade_result, trade_request = self._trade_info[ticker]
                    failsafe_task = asyncio.create_task(
                        self._execute_exit_after_delay(
                            trade_result,
                            trade_request,
                            delay_minutes=self.HOLD_FAILSAFE_MINUTES,
                            reason="hold_failsafe"
                        )
                    )
                    self._held_tickers[ticker] = failsafe_task
                    logger.info(
                        "🔒 HOLD: Started 30-min failsafe for ticker",
                        ticker=ticker,
                        failsafe_minutes=self.HOLD_FAILSAFE_MINUTES
                    )
                else:
                    logger.warning(
                        "🔒 HOLD: No trade info stored for failsafe",
                        ticker=ticker
                    )

                return True
            else:
                del self._scheduled_exits[ticker]

        # If already held, just log
        if ticker in self._held_tickers:
            logger.info(
                "🔒 HOLD: Ticker already held",
                ticker=ticker
            )
            return True

        logger.warning(
            "🔒 HOLD: No scheduled exit found for ticker",
            ticker=ticker
        )
        return False

    def is_ticker_held(self, ticker: str) -> bool:
        """Check if a ticker is being held (extended exit time)."""
        return ticker.upper() in self._held_tickers

    def get_held_tickers(self) -> list:
        """Get list of held tickers."""
        return list(self._held_tickers.keys())
    
    async def _handle_trade_executed(
        self,
        domain_event: TradeExecutedDomainEvent,
    ) -> None:
        """
        Handle Domain.TradeExecuted event and schedule exit if it's a BUY entry.
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            trade_result = domain_event.trade_result
            
            # Only schedule exits for successful BUY trades (entries)
            if not trade_result.is_successful():
                logger.debug(
                    "ExitTradeUseCase: Skipping exit scheduling for failed trade",
                    ticker=trade_result.get_ticker()
                )
                return
            
            trade_request = trade_result.get_trade_request()
            
            if not trade_request.is_buy():
                logger.debug(
                    "ExitTradeUseCase: Skipping exit scheduling for SELL trade",
                    ticker=trade_request.ticker
                )
                return
            
            # Check if we already have an exit scheduled for this ticker
            ticker = trade_request.ticker
            if ticker in self._scheduled_exits:
                existing_task = self._scheduled_exits[ticker]
                if not existing_task.done():
                    logger.info(
                        "ExitTradeUseCase: Exit already scheduled for ticker, cancelling old schedule",
                        ticker=ticker
                    )
                    existing_task.cancel()
            
            # Schedule exit trade
            logger.info(
                "⏰ EXIT USE CASE: Scheduling exit trade",
                ticker=ticker,
                shares=trade_result.shares,
                exit_delay_minutes=self.exit_delay_minutes,
                executed_at=trade_result.executed_at.isoformat()
            )

            # Store trade info for hold_ticker to use
            self._trade_info[ticker] = (trade_result, trade_request)

            # Create task to execute exit after delay
            exit_task = asyncio.create_task(
                self._execute_exit_after_delay(trade_result, trade_request)
            )
            self._scheduled_exits[ticker] = exit_task
            
        except Exception as e:
            logger.error(
                "❌ EXIT USE CASE: Error handling trade executed event",
                error=str(e),
                exc_info=True
            )
    
    async def _execute_exit_after_delay(
        self,
        entry_trade_result: TradeResult,
        entry_trade_request: TradeRequest,
        delay_minutes: Optional[int] = None,
        reason: str = "auto_exit"
    ) -> None:
        """
        Wait for delay period, then publish exit trade request.

        Args:
            entry_trade_result: The successful entry trade result
            entry_trade_request: The original entry trade request
            delay_minutes: Custom delay (defaults to exit_delay_minutes)
            reason: Reason for exit (auto_exit, hold_failsafe)
        """
        try:
            ticker = entry_trade_request.ticker
            shares = entry_trade_result.shares

            if not shares:
                logger.warning(
                    "ExitTradeUseCase: Cannot schedule exit - no shares in trade result",
                    ticker=ticker
                )
                return

            # Use custom delay or default
            actual_delay = delay_minutes if delay_minutes is not None else self.exit_delay_minutes

            # Wait for exit delay
            delay_seconds = actual_delay * 60
            logger.info(
                f"⏳ EXIT USE CASE: Waiting for {reason} delay",
                ticker=ticker,
                delay_minutes=actual_delay,
                shares=shares,
                reason=reason
            )

            await asyncio.sleep(delay_seconds)

            # GUARD: Check if position still exists before selling.
            # Prevents double-sell race condition (LSTA bug): if position_manager
            # already exited, this scheduled exit would create an accidental short.
            from ...services.brokerage.auto_trade import _active_positions
            if ticker not in _active_positions:
                logger.info(
                    "EXIT USE CASE: Scheduled exit cancelled — position already closed",
                    ticker=ticker,
                    reason=reason,
                    shares=shares,
                )
                # Clean up
                if ticker in self._scheduled_exits:
                    del self._scheduled_exits[ticker]
                if ticker in self._held_tickers:
                    del self._held_tickers[ticker]
                if ticker in self._trade_info:
                    del self._trade_info[ticker]
                return

            # Create exit trade request
            # Use exact shares from entry trade (supports fractional shares)
            # No amount_usd needed - we have explicit shares
            exit_trade_request = TradeRequest(
                ticker=ticker,
                action=TradeAction.SELL,
                amount_usd=None,  # Not needed - we have explicit shares
                shares=shares,  # Exit exact same number of shares (supports fractional)
                leverage=None,  # No leverage on exit
                instrument=entry_trade_request.instrument,
                article_id=entry_trade_request.article_id,
                requested_at=datetime.now(timezone.utc)
            )
            
            logger.info(
                "EXIT USE CASE: Created exit trade request",
                ticker=ticker,
                exit_shares=shares,
                shares_type="fractional" if shares and shares != int(shares) else "whole",
                entry_article_id=entry_trade_request.article_id,
                entry_executed_at=entry_trade_result.executed_at.isoformat(),
                exit_delay_minutes=self.exit_delay_minutes
            )
            
            # Publish exit trade request
            from ...domain.brokerage.events import TradeRequestDomainEvent
            
            exit_domain_event = TradeRequestDomainEvent(
                trade_request=exit_trade_request,
                article_id=entry_trade_request.article_id,
                requested_at=datetime.now(timezone.utc)
            )
            
            await self.event_bus.publish(DomainEventType.TRADE_REQUESTED, exit_domain_event.model_dump())
            
            logger.info(
                f"🚪 EXIT USE CASE: Published {reason} trade request",
                ticker=ticker,
                shares=shares,
                delay_minutes=actual_delay,
                reason=reason
            )

            # Clean up scheduled exit or held ticker
            if ticker in self._scheduled_exits:
                del self._scheduled_exits[ticker]
            if ticker in self._held_tickers:
                del self._held_tickers[ticker]

            # Clean up stored trade info
            if ticker in self._trade_info:
                del self._trade_info[ticker]
            
        except asyncio.CancelledError:
            logger.info(
                "ExitTradeUseCase: Scheduled exit cancelled",
                ticker=entry_trade_request.ticker
            )
        except Exception as e:
            logger.error(
                "❌ EXIT USE CASE: Error executing scheduled exit",
                error=str(e),
                ticker=entry_trade_request.ticker,
                exc_info=True
            )
            # Clean up on error
            if entry_trade_request.ticker in self._scheduled_exits:
                del self._scheduled_exits[entry_trade_request.ticker]

