"""
Notify exit trade use case - sends notifications when exit trades execute.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime, timezone
from typing import Final, Optional
from decimal import Decimal

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.brokerage.events import TradeExecutedDomainEvent
from ...domain.brokerage.models import TradeResult
from ...domain.notification.events import NotificationRequestedDomainEvent
from ...domain.notification.models import NotificationChannel, NotificationMessage

logger = get_logger(__name__)


def format_exit_trade_message(
    exit_trade_result: TradeResult,
    entry_trade_result: Optional[TradeResult] = None,
    instrument_details: Optional[dict] = None
) -> str:
    """
    Format exit trade notification message with profit/loss details.
    
    Args:
        exit_trade_result: Exit trade execution result
        entry_trade_result: Optional entry trade result for P/L calculation
        
    Returns:
        Formatted message string
    """
    exit_request = exit_trade_result.get_trade_request()
    notification_time = datetime.now(timezone.utc)
    
    message_parts = [
        "🚪 EXIT TRADE EXECUTED",
        "",
        f"📈 Ticker: {exit_trade_result.get_ticker()}",
        f"📊 Action: {exit_request.action.value}",
        f"📦 Shares: {exit_trade_result.shares}",
        f"💵 Exit Price: ${exit_trade_result.fill_price:.2f}",
        f"💸 Total Proceeds: ${exit_trade_result.total_cost:.2f}",
    ]
    
    # Calculate profit/loss if entry trade is available
    if entry_trade_result and entry_trade_result.is_successful():
        entry_price = entry_trade_result.fill_price
        exit_price = exit_trade_result.fill_price
        shares = exit_trade_result.shares
        
        if entry_price and exit_price and shares:
            entry_cost = float(entry_price) * shares
            exit_proceeds = float(exit_price) * shares
            pnl = exit_proceeds - entry_cost
            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
            
            message_parts.extend([
                "",
                "💰 PROFIT/LOSS:",
                f"   Entry Price: ${entry_price:.2f}",
                f"   Exit Price: ${exit_price:.2f}",
                f"   Entry Cost: ${entry_cost:.2f}",
                f"   Exit Proceeds: ${exit_proceeds:.2f}",
            ])
            
            if pnl >= 0:
                message_parts.append(f"   ✅ Profit: ${pnl:.2f} ({pnl_percent:+.2f}%)")
            else:
                message_parts.append(f"   ❌ Loss: ${pnl:.2f} ({pnl_percent:+.2f}%)")
    
    # Add detailed ladder statistics for extended hours exits
    if instrument_details:
        ladder_attempts = instrument_details.get("ladder_attempts")
        ladder_attempts_detail = instrument_details.get("ladder_attempts_detail", [])
        distance_to_mid = instrument_details.get("distance_to_mid")
        distance_to_bid = instrument_details.get("distance_to_target")  # For SELL, target is bid
        
        if ladder_attempts:
            message_parts.append(f"🔄 Ladder Attempts: {ladder_attempts}")
        
        if distance_to_mid is not None:
            message_parts.append(f"📏 Distance to Mid: ${distance_to_mid:.4f}")
            if distance_to_bid is not None:
                message_parts.append(f"📏 Distance to Bid: ${distance_to_bid:.4f}")
    
    # Add commission if present
    if exit_trade_result.commission and exit_trade_result.commission > 0:
        message_parts.append(f"💳 Commission: ${exit_trade_result.commission:.2f}")
    
    message_parts.extend([
        "",
        f"⏰ Exited At: {exit_trade_result.executed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"📱 Notification Received: {notification_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ])
    
    return "\n".join(message_parts)


class NotifyExitTradeUseCase:
    """
    Use case for sending notifications when exit trades execute.
    
    Responsibilities:
    - Subscribe to Domain.TradeExecuted events
    - Filter for SELL trades (exits)
    - Calculate profit/loss if entry trade is available
    - Format exit trade notification
    - Publish Domain.NotificationRequested event
    """
    
    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize notify exit trade use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
        """
        self.event_bus: Final[AsyncEventBus] = event_bus
        self._entry_trades: dict[str, TradeResult] = {}  # Track entry trades for P/L calculation
        
        # Subscribe to typed Domain.TradeExecuted events
        # Store wrapper for unsubscribe
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent,
            self._handle_trade_executed,
        )
        
        logger.info(
            "NotifyExitTradeUseCase initialized - subscribes to Domain.TradeExecuted events",
        )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("NotifyExitTradeUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe(DomainEventType.TRADE_EXECUTED, self._trade_executed_wrapper)
        logger.info("NotifyExitTradeUseCase stopped")
    
    async def _handle_trade_executed(
        self,
        domain_event: TradeExecutedDomainEvent,
    ) -> None:
        """
        Handle Domain.TradeExecuted event and send notification for exit trades.
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            trade_result = domain_event.trade_result
            
            # Only notify for successful trades
            if not trade_result.is_successful():
                logger.debug(
                    "NotifyExitTradeUseCase: Skipping notification for failed trade",
                    ticker=trade_result.get_ticker()
                )
                return
            
            trade_request = trade_result.get_trade_request()
            ticker = trade_request.ticker
            
            # Track entry trades (BUY) for P/L calculation on exit
            if trade_request.is_buy():
                self._entry_trades[ticker] = trade_result
                logger.info(
                    "✅ NOTIFY EXIT TRADE: Tracked entry trade for future exit P/L calculation",
                    ticker=ticker,
                    shares=trade_result.shares,
                    entry_price=trade_result.fill_price,
                    article_id=trade_request.article_id
                )
                return  # Don't notify for entry trades (handled by NotifyTradeExecutedUseCase)
            
            # Only notify for exit trades (SELL)
            if not trade_request.is_sell():
                return
            
            logger.info(
                "🎯 NOTIFY EXIT TRADE: Orchestrating exit notification request",
                ticker=ticker,
                shares=trade_result.shares,
                exit_price=trade_result.fill_price
            )
            
            # Get entry trade for P/L calculation
            entry_trade = self._entry_trades.get(ticker)
            
            if not entry_trade:
                logger.warning(
                    "⚠️ NOTIFY EXIT TRADE: No entry trade found in memory for P/L calculation",
                    ticker=ticker,
                    exit_shares=trade_result.shares,
                    exit_price=trade_result.fill_price,
                    note="This can happen if service restarted between entry and exit, or if entry trade notification wasn't tracked"
                )
            
            # Get instrument_details from trade_request dict metadata
            trade_request_dict = trade_result.trade_request
            instrument_details = trade_request_dict.get("_instrument_details", {})
            
            # Format exit trade message
            exit_message = format_exit_trade_message(
                exit_trade_result=trade_result,
                entry_trade_result=entry_trade,
                instrument_details=instrument_details
            )
            
            # Create notification message
            notification_message = NotificationMessage(
                article_id=trade_request.article_id or "exit-trade",
                title=f"Exit Trade: {ticker}",
                tickers=frozenset([ticker]),
                classification="",  # Not a classification notification
                confidence="",
                reasoning="",
                body=exit_message,
                channels=frozenset([NotificationChannel.TELEGRAM]),
                created_at=datetime.now(timezone.utc)
            )
            
            # Publish typed domain event
            domain_notification_event = NotificationRequestedDomainEvent(
                message=notification_message,
                requested_at=datetime.now(timezone.utc)
            )
            
            await self.event_bus.publish(DomainEventType.NOTIFICATION_REQUESTED, domain_notification_event.model_dump())
            
            logger.info(
                "✅ NOTIFY EXIT TRADE: Published notification request",
                ticker=ticker,
                channels=[c.value for c in notification_message.channels]
            )
            
            # Clean up entry trade after exit
            if ticker in self._entry_trades:
                del self._entry_trades[ticker]
            
        except Exception as e:
            logger.error(
                "❌ NOTIFY EXIT TRADE: Error orchestrating notification",
                error=str(e),
                exc_info=True
            )
