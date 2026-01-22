"""
Notify trade failed use case - sends notifications when trades fail for imminent articles.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime, timezone
from typing import Final

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.brokerage.events import TradeFailedDomainEvent
from ...domain.notification.events import NotificationRequestedDomainEvent
from ...domain.notification.factories import NotificationMessageFactory
from ...domain.notification.models import NotificationChannel, NotificationMessage
from ...services.storage import StorageQueryService

logger = get_logger(__name__)


def format_trade_failed_message(trade_request, error: str, article_title: str = None, publication_time: datetime = None, ladder_attempts_detail: list = None, ladder_attempts: int = None) -> str:
    """
    Format trade failure notification message with all details.
    
    Args:
        trade_request: Trade request that failed
        error: Error message explaining why the trade failed
        article_title: Optional article title
        publication_time: Optional article publication time
        
    Returns:
        Formatted message string
    """
    notification_time = datetime.now()
    
    # Normalize error messages for user-friendly explanations
    error_lower = error.lower()
    if "nbbo" in error_lower or "snapshot" in error_lower:
        user_error = "Could not retrieve market data (NBBO snapshot) for extended hours trading"
    elif "liquidity" in error_lower or "fill" in error_lower:
        user_error = "Not enough liquidity for a fill"
    elif "market closed" in error_lower:
        user_error = "Market is closed"
    elif "insufficient" in error_lower and "buying power" in error_lower:
        user_error = "Insufficient buying power"
    elif "invalid ticker" in error_lower:
        user_error = "Invalid ticker symbol"
    elif "connection" in error_lower or "brokerage" in error_lower:
        user_error = "Brokerage connection issue"
    else:
        user_error = error
    
    message_parts = [
        "❌ TRADE FAILED",
        "",
        f"📈 Ticker: {trade_request.ticker}",
        f"📊 Action: {trade_request.action.value}",
    ]
    
    # Add leverage information if present
    if trade_request.leverage and trade_request.leverage > 1.0:
        message_parts.append(f"📊 Leverage: {trade_request.leverage}x (2 shares for price of 1)")
    
    message_parts.extend([
        f"⚙️  Instrument: {trade_request.instrument.value.upper()}",
        "",
        f"🚨 Reason: {user_error}",
    ])
    
    # Add ladder attempts detail for extended hours trades
    if ladder_attempts and ladder_attempts > 0:
        message_parts.append(f"🔄 Total Attempts: {ladder_attempts}")
        
        if ladder_attempts_detail and len(ladder_attempts_detail) > 0:
            message_parts.append("")
            message_parts.append("📊 Ladder Attempts Detail:")
            for i, attempt in enumerate(ladder_attempts_detail[:10], 1):  # Show first 10 attempts
                limit_price = attempt.get("limit_price")
                time_since_prev = attempt.get("time_since_previous", 0)
                if limit_price:
                    time_str = f"{time_since_prev:.2f}s" if time_since_prev > 0 else "0.00s"
                    message_parts.append(f"   Attempt {i}: ${limit_price:.2f} (wait: {time_str})")
            if len(ladder_attempts_detail) > 10:
                message_parts.append(f"   ... and {len(ladder_attempts_detail) - 10} more attempts")
    
    message_parts.extend([
        "",
        f"⏰ Failed At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ])
    
    # Add publication time if available
    if publication_time:
        # Ensure both datetimes are timezone-aware for subtraction
        if publication_time.tzinfo is None:
            publication_time = publication_time.replace(tzinfo=timezone.utc)
        if notification_time.tzinfo is None:
            notification_time = notification_time.replace(tzinfo=timezone.utc)
        
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


class NotifyTradeFailedUseCase:
    """
    Use case for sending notifications when trades fail for imminent articles.
    
    Responsibilities:
    - Subscribe to Domain.TradeFailed events
    - Only notify for trades triggered by imminent articles (article_id present)
    - Fetch article from storage (for publication time and title)
    - Format trade failure notification with clear error explanation
    - Publish Domain.NotificationRequested event
    """
    
    def __init__(self, event_bus: AsyncEventBus, storage_query_service: StorageQueryService):
        """
        Initialize notify trade failed use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Storage query service for fetching articles
        """
        self.event_bus: Final[AsyncEventBus] = event_bus
        self.notification_factory = NotificationMessageFactory()
        self.storage_query_service: Final[StorageQueryService] = storage_query_service
        
        # Subscribe to typed Domain.TradeFailed events
        # Store wrapper for unsubscribe
        self._trade_failed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_FAILED,
            TradeFailedDomainEvent,
            self._handle_trade_failed,
        )
        
        logger.info(
            "NotifyTradeFailedUseCase initialized - subscribes to Domain.TradeFailed events",
            has_storage_query=self.storage_query_service is not None,
        )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("NotifyTradeFailedUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe(DomainEventType.TRADE_FAILED, self._trade_failed_wrapper)
        logger.info("NotifyTradeFailedUseCase stopped")
    
    async def _handle_trade_failed(
        self,
        domain_event: TradeFailedDomainEvent,
    ) -> None:
        """
        Handle Domain.TradeFailed event and send notification.
        
        Only sends notifications for trades triggered by imminent articles (article_id present).
        """
        try:
            trade_request = domain_event.trade_request
            
            # Only notify for trades triggered by imminent articles
            if not trade_request.article_id:
                logger.debug(
                    "NotifyTradeFailedUseCase: Skipping notification - trade not triggered by article",
                    ticker=trade_request.ticker
                )
                return
            
            # Notify for both BUY and SELL trade failures
            # Exit trade failures are important - user needs to know why exits failed
            
            logger.info(
                "🎯 NOTIFY TRADE FAILED: Orchestrating notification request",
                ticker=trade_request.ticker,
                article_id=trade_request.article_id,
                error=domain_event.error
            )
            
            # Fetch article from storage to get publication time and title
            # SPEED FIX: Use very short timeout (0.5s) to avoid blocking notifications
            article_id = trade_request.article_id
            article = None
            publication_time = None
            article_title = None

            if article_id and self.storage_query_service:
                try:
                    article = await self.storage_query_service.fetch_article(article_id, timeout_seconds=0.5)
                    if article:
                        publication_time = article.published_at
                        article_title = article.title
                except Exception:
                    # Don't log - expected under load, notification speed is priority
                    pass
            
            # Extract ladder attempts detail from domain event (if available)
            ladder_attempts_detail = domain_event.ladder_attempts_detail
            ladder_attempts = domain_event.ladder_attempts
            
            # Format trade failure message
            failure_message = format_trade_failed_message(
                trade_request=trade_request,
                error=domain_event.error,
                article_title=article_title,
                publication_time=publication_time,
                ladder_attempts_detail=ladder_attempts_detail,
                ladder_attempts=ladder_attempts
            )
            
            # Create notification message
            notification_message = NotificationMessage(
                article_id=article_id or "unknown",
                title=article_title or f"Trade Failed: {trade_request.ticker}",
                tickers=frozenset([trade_request.ticker]),
                classification="",  # Not a classification notification
                confidence="",
                reasoning="",
                body=failure_message,
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
                "✅ NOTIFY TRADE FAILED: Published notification request",
                ticker=trade_request.ticker,
                article_id=article_id,
                channels=[c.value for c in notification_message.channels]
            )
            
        except Exception as e:
            logger.error(
                "❌ NOTIFY TRADE FAILED: Error orchestrating notification",
                error=str(e),
                exc_info=True
            )
