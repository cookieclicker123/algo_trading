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
    - Schedule exit SELL trade after AUTO_TRADE_EXIT_DELAY_MINUTES
    - Publish Domain.TradeRequested event for exit
    
    This ensures positions are automatically closed after the configured delay.
    """
    
    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize exit trade use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
        """
        self.event_bus: Final[AsyncEventBus] = event_bus
        self.exit_delay_minutes: Final[int] = settings.AUTO_TRADE_EXIT_DELAY_MINUTES
        self._scheduled_exits: Dict[str, asyncio.Task] = {}
        
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
            exit_delay_minutes=self.exit_delay_minutes
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
        return False
    
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
        entry_trade_request: TradeRequest
    ) -> None:
        """
        Wait for delay period, then publish exit trade request.
        
        Args:
            entry_trade_result: The successful entry trade result
            entry_trade_request: The original entry trade request
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
            
            # Wait for exit delay
            delay_seconds = self.exit_delay_minutes * 60
            logger.info(
                "⏳ EXIT USE CASE: Waiting for exit delay",
                ticker=ticker,
                delay_minutes=self.exit_delay_minutes,
                shares=shares
            )
            
            await asyncio.sleep(delay_seconds)
            
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
                "🚪 EXIT USE CASE: Published exit trade request",
                ticker=ticker,
                shares=shares,
                delay_minutes=self.exit_delay_minutes
            )
            
            # Clean up scheduled exit
            if ticker in self._scheduled_exits:
                del self._scheduled_exits[ticker]
            
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

