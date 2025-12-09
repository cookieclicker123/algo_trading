"""
Notify imminent article use case - orchestrates notification workflow.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows

This use case sends article headline notifications AFTER trades execute,
ensuring the order: IMMINENT → Trade → Trade Notification → Article Notification
"""
from datetime import datetime
from typing import Final

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.brokerage.events import TradeExecutedDomainEvent
from ...domain.notification.events import NotificationRequestedDomainEvent
from ...domain.notification.factories import NotificationMessageFactory
from ...domain.notification.models import NotificationChannel
from ...services.storage import StorageQueryService

logger = get_logger(__name__)


class NotifyImminentArticleUseCase:
    """
    Use case for orchestrating notification workflow for IMMINENT articles.
    
    Responsibilities:
    - Subscribe to Domain.TradeExecuted events (after trade completes)
    - Fetch article and classification from storage
    - Create notification message from article + classification
    - Publish Domain.NotificationRequested event
    - (Domain listener → Infrastructure → Telegram API → Domain.NotificationSent)
    
    This ensures article notifications are sent AFTER trades execute, not before.
    Order: IMMINENT → Trade → Trade Notification → Article Notification
    
    Services provide focused operations - use case orchestrates them.
    """
    
    def __init__(self, event_bus: AsyncEventBus, storage_query_service: StorageQueryService):
        """
        Initialize notify imminent article use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Storage query service for fetching articles and classifications
        """
        self.event_bus = event_bus
        self.notification_factory = NotificationMessageFactory()
        self.storage_query_service: Final[StorageQueryService] = storage_query_service
        
        # Subscribe to typed Domain.TradeExecuted events (not ArticleClassified)
        # This ensures article notifications are sent AFTER trades execute
        self._trade_executed_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.TRADE_EXECUTED,
            TradeExecutedDomainEvent,
            self._handle_trade_executed,
        )
        
        logger.info(
            "NotifyImminentArticleUseCase initialized - subscribes to Domain.TradeExecuted events (sends article notification after trade)",
            has_storage_query=self.storage_query_service is not None,
        )
    
    async def start(self) -> None:
        """Start the use case (already subscribed in __init__)."""
        logger.info("NotifyImminentArticleUseCase started")
    
    async def stop(self) -> None:
        """Stop the use case."""
        self.event_bus.unsubscribe(DomainEventType.TRADE_EXECUTED, self._trade_executed_wrapper)
        logger.info("NotifyImminentArticleUseCase stopped")
    
    async def _handle_trade_executed(
        self,
        domain_event: TradeExecutedDomainEvent,
    ) -> None:
        """
        Handle Domain.TradeExecuted event and send article headline notification.
        
        This is called AFTER a trade executes, ensuring the order:
        1. IMMINENT classification
        2. Trade executes
        3. Trade execution notification (from NotifyTradeExecutedUseCase)
        4. Article headline notification (this use case)
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            trade_result = domain_event.trade_result
            
            # Only notify for successful trades
            if not trade_result.is_successful():
                logger.debug(
                    "NotifyImminentArticleUseCase: Skipping article notification for failed trade",
                    ticker=trade_result.get_ticker()
                )
                return
            
            # Get article_id from trade request
            trade_request = trade_result.get_trade_request()
            article_id = trade_request.article_id
            
            if not article_id:
                logger.debug(
                    "NotifyImminentArticleUseCase: Trade has no associated article_id, skipping article notification",
                    ticker=trade_result.get_ticker()
                )
                return
            
            logger.info(
                "🎯 NOTIFY USE CASE: Sending article headline notification after trade execution",
                article_id=article_id,
                ticker=trade_result.get_ticker()
            )
            
            # Fetch article from storage
            domain_article = await self.storage_query_service.fetch_article(article_id)
            
            if not domain_article:
                logger.warning(
                    "NotifyImminentArticleUseCase: Article not found in storage for notification",
                    article_id=article_id
                )
                return
            
            # Create a minimal ClassificationResult for IMMINENT articles
            # Since a trade was executed, we know this was classified as IMMINENT
            from ...domain.classification.models import ClassificationResult, ClassificationCategory, ClassificationConfidence
            classification_result = ClassificationResult(
                article_id=article_id,
                classification=ClassificationCategory.IMMINENT,
                confidence=ClassificationConfidence.HIGH,  # Default to HIGH since trade was executed
                reasoning="Article triggered auto-trade execution",
                classified_at=domain_article.published_at,  # Use article publication time
                latency_ms=0.0  # Not applicable for reconstructed classification
            )
            
            # Create notification message from article and classification using factory
            # Get websocket received time from stored article (if available)
            # Fetch stored article to get websocket_received_at timestamp
            websocket_received_at = None
            try:
                stored_article_dict = await self.storage_query_service.article_repository.fetch_article(article_id)
                if stored_article_dict:
                    stored_article_model = self.storage_query_service.stored_article_factory.create_from_dict(stored_article_dict)
                    if stored_article_model and stored_article_model.websocket_received_at:
                        websocket_received_at = stored_article_model.websocket_received_at
            except Exception as e:
                logger.debug(
                    "NotifyImminentArticleUseCase: Could not fetch websocket_received_at",
                    article_id=article_id,
                    error=str(e)
                )
            
            # Add publication time, websocket received time, and notification time to body
            notification_time = datetime.now()
            time_to_notification = (notification_time - domain_article.published_at).total_seconds()
            time_ws_to_notification = None
            if websocket_received_at:
                time_ws_to_notification = (notification_time - websocket_received_at).total_seconds()
            
            # Generate body with timing information
            tickers_str = ", ".join(sorted(domain_article.tickers)) if domain_article.tickers else "None"
            timing_lines = [
                f"📅 Published At: {domain_article.published_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            ]
            if websocket_received_at:
                timing_lines.append(f"📡 WebSocket Received: {websocket_received_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                timing_lines.append(f"⏱️  WebSocket → Notification: {time_ws_to_notification:.2f} seconds")
            timing_lines.append(f"📱 Notification Received: {notification_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            timing_lines.append(f"⏱️  Published → Notification: {time_to_notification:.2f} seconds")
            
            body = (
                f"🚨 IMMINENT NEWS ALERT\n\n"
                f"📰 {domain_article.title}\n\n"
                f"🏷️ Tickers: {tickers_str}\n"
                f"📊 Classification: {classification_result.classification.value.upper()}\n"
                f"🎯 Confidence: {classification_result.confidence.value}\n"
                f"💭 Reasoning: {classification_result.reasoning}\n\n"
                + "\n".join(timing_lines) + "\n\n"
                f"🔗 {domain_article.url if domain_article.url else 'No URL'}"
            )
            
            # Default to Telegram channel
            channels = frozenset([NotificationChannel.TELEGRAM])
            notification_message = self.notification_factory.create_from_article_and_classification(
                article=domain_article,
                classification_result=classification_result,
                channels=channels,
                body=body  # Use custom body with timing info
            )
            
            if not notification_message:
                logger.warning(
                    "NotifyImminentArticleUseCase: Failed to create notification message",
                    article_id=article_id
                )
                return
            
            # Publish typed domain event with NotificationMessage domain model (domain listener will forward to infrastructure)
            domain_notification_event = NotificationRequestedDomainEvent(
                message=notification_message,
                requested_at=datetime.now()
            )
            
            await self.event_bus.publish(DomainEventType.NOTIFICATION_REQUESTED, domain_notification_event.model_dump())
            
            logger.info(
                "✅ NOTIFY USE CASE: Published article headline notification request (after trade execution)",
                article_id=article_id,
                ticker=trade_result.get_ticker(),
                channels=[c.value for c in notification_message.channels]
            )
            
        except Exception as e:
            logger.error(
                "❌ NOTIFY USE CASE: Error orchestrating article notification after trade execution",
                error=str(e),
                exc_info=True
            )

