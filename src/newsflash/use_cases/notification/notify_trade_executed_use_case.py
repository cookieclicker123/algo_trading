"""
Notify trade executed use case - sends notifications when trades execute.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime
from typing import Final

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.brokerage.events import TradeExecutedDomainEvent
from ...domain.brokerage.models import TradeResult
from ...domain.notification.events import NotificationRequestedDomainEvent
from ...domain.notification.factories import NotificationMessageFactory
from ...domain.notification.models import NotificationChannel, NotificationMessage
from ...services.storage import StorageQueryService

logger = get_logger(__name__)


def format_trade_execution_message(trade_result: TradeResult, article_title: str = None, publication_time: datetime = None, spread_info: dict = None) -> str:
    """
    Format trade execution notification message with all details.
    
    Args:
        trade_result: Trade execution result
        article_title: Optional article title
        publication_time: Optional article publication time
        
    Returns:
        Formatted message string
    """
    trade_request = trade_result.get_trade_request()
    notification_time = datetime.now()
    
    # Determine order type from session
    # Market hours uses market orders, extended hours uses ladder limit orders
    session_str = trade_result.session.value
    if session_str in ["market", "market_hours"]:
        order_type = "MARKET ORDER"
    elif session_str in ["premarket", "postmarket"]:
        order_type = "LADDER LIMIT ORDER"
    else:
        order_type = "LIMIT ORDER"
    
    # Calculate capital vs actual shares
    # With leverage: we put up capital for 1 share, but buy leverage × shares
    actual_cost = float(trade_result.total_cost) if trade_result.total_cost else float(trade_result.fill_price) * trade_result.shares
    leverage = float(trade_request.leverage) if trade_request.leverage else 1.0
    # Capital required = cost of 1 share, but we bought leverage × shares
    capital_required = float(trade_result.fill_price) if trade_result.fill_price else actual_cost / leverage
    
    message_parts = [
        "✅ TRADE EXECUTED",
        "",
        f"📈 Ticker: {trade_result.get_ticker()}",
        f"📊 Action: {trade_request.action.value}",
        f"📦 Shares: {trade_result.shares}",
        f"💵 Fill Price: ${trade_result.fill_price:.2f}",
        f"💸 Total Cost: ${trade_result.total_cost:.2f}",
    ]
    
    # Add leverage information showing capital vs actual shares
    if trade_request.leverage and leverage > 1.0:
        message_parts.append(f"📊 Leverage: {trade_request.leverage}x")
        message_parts.append(f"💰 Capital Required: ${capital_required:.2f} (for 1 share)")
        message_parts.append(f"📈 Actual Shares: {trade_result.shares:.4f} (profit/loss based on {trade_result.shares:.2f}x)")
    
    message_parts.extend([
        f"📋 Order Type: {order_type}",
        f"🕐 Session: {session_str.upper()}",
        f"⚙️  Instrument: {trade_request.instrument.value.upper()}",
    ])
    
    # Add spread information if available
    if spread_info and spread_info.get("bid") and spread_info.get("ask"):
        bid = spread_info.get("bid")
        ask = spread_info.get("ask")
        spread = spread_info.get("spread", ask - bid)
        spread_pct = (spread / ((bid + ask) / 2)) * 100 if (bid + ask) > 0 else 0
        message_parts.append(f"📊 Spread: ${spread:.4f} ({spread_pct:.3f}%) | Bid: ${bid:.2f} | Ask: ${ask:.2f}")
    
    # Add commission if present
    if trade_result.commission and trade_result.commission > 0:
        message_parts.append(f"💳 Commission: ${trade_result.commission:.2f}")
    
    message_parts.extend([
        "",
        f"⏰ Executed At: {trade_result.executed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ])
    
    # Add publication time and notification time if available
    if publication_time:
        message_parts.append(f"📰 Published At: {publication_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        time_diff = (notification_time - publication_time).total_seconds()
        message_parts.append(f"⏱️  Time to Notification: {time_diff:.2f} seconds")
    
    message_parts.append(f"📱 Notification Received: {notification_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    # Add article title if available
    if article_title:
        message_parts.extend([
            "",
            f"📄 Article: {article_title[:100]}..." if len(article_title) > 100 else f"📄 Article: {article_title}"
        ])
    
    return "\n".join(message_parts)


class NotifyTradeExecutedUseCase:
    """
    Use case for sending notifications when trades execute.
    
    Responsibilities:
    - Subscribe to Domain.TradeExecuted events
    - Fetch article from storage (for publication time and title)
    - Format trade execution notification
    - Publish Domain.NotificationRequested event
    """
    
    def __init__(self, event_bus: AsyncEventBus, storage_query_service: StorageQueryService):
        """
        Initialize notify trade executed use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Storage query service for fetching articles
        """
        self.event_bus: Final[AsyncEventBus] = event_bus
        self.notification_factory = NotificationMessageFactory()
        self.storage_query_service: Final[StorageQueryService] = storage_query_service
        
        # Subscribe to typed Domain.TradeExecuted events
        # Store wrapper for unsubscribe
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent,
            self._handle_trade_executed,
        )
        
        logger.info(
            "NotifyTradeExecutedUseCase initialized - subscribes to Domain.TradeExecuted events",
            has_storage_query=self.storage_query_service is not None,
        )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("NotifyTradeExecutedUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe(DomainEventType.TRADE_EXECUTED, self._trade_executed_wrapper)
        logger.info("NotifyTradeExecutedUseCase stopped")
    
    async def _handle_trade_executed(
        self,
        domain_event: TradeExecutedDomainEvent,
    ) -> None:
        """
        Handle Domain.TradeExecuted event and send notification.
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            trade_result = domain_event.trade_result
            
            # Only notify for successful trades
            if not trade_result.is_successful():
                logger.debug(
                    "NotifyTradeExecutedUseCase: Skipping notification for failed trade",
                    ticker=trade_result.get_ticker()
                )
                return
            
            # Skip SELL trades (exits) - those are handled by NotifyExitTradeUseCase
            trade_request = trade_result.get_trade_request()
            if trade_request.is_sell():
                logger.debug(
                    "NotifyTradeExecutedUseCase: Skipping notification for SELL trade (exit handled by NotifyExitTradeUseCase)",
                    ticker=trade_result.get_ticker()
                )
                return
            
            logger.info(
                "🎯 NOTIFY TRADE EXECUTED: Orchestrating notification request",
                ticker=trade_result.get_ticker(),
                shares=trade_result.shares,
                fill_price=trade_result.fill_price
            )
            
            # Fetch article from storage to get publication time and title
            trade_request = trade_result.get_trade_request()
            article_id = trade_request.article_id
            article = None
            publication_time = None
            article_title = None
            
            if article_id and self.storage_query_service:
                try:
                    article = await self.storage_query_service.fetch_article(article_id)
                    if article:
                        publication_time = article.published_at
                        article_title = article.title
                except Exception as e:
                    logger.warning(
                        "NotifyTradeExecutedUseCase: Could not fetch article for notification",
                        article_id=article_id,
                        error=str(e)
                    )
            
            # Get spread_info from trade_request dict metadata (stored by mapper)
            trade_request_dict = trade_result.trade_request
            spread_info = trade_request_dict.get("_spread_info", {})
            
            # Format trade execution message
            trade_message = format_trade_execution_message(
                trade_result=trade_result,
                article_title=article_title,
                publication_time=publication_time,
                spread_info=spread_info
            )
            
            # Create notification message
            notification_message = NotificationMessage(
                article_id=article_id or "unknown",
                title=article_title or f"Trade Executed: {trade_result.get_ticker()}",
                tickers=frozenset([trade_result.get_ticker()]),
                classification="",  # Not a classification notification
                confidence="",
                reasoning="",
                body=trade_message,
                channels=frozenset([NotificationChannel.TELEGRAM]),
                created_at=datetime.now()
            )
            
            # Publish typed domain event
            domain_notification_event = NotificationRequestedDomainEvent(
                message=notification_message,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish(DomainEventType.NOTIFICATION_REQUESTED, domain_notification_event.model_dump())
            
            logger.info(
                "✅ NOTIFY TRADE EXECUTED: Published notification request",
                ticker=trade_result.get_ticker(),
                channels=[c.value for c in notification_message.channels]
            )
            
        except Exception as e:
            logger.error(
                "❌ NOTIFY TRADE EXECUTED: Error orchestrating notification",
                error=str(e),
                exc_info=True
            )
