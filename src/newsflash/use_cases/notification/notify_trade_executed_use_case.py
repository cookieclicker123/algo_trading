"""
Notify trade executed use case - sends notifications when trades execute.

USE CASES ORCHESTRATE SERVICES:
- Use cases subscribe to domain events
- Use cases work with domain models (they orchestrate domain workflows)
- Use cases publish domain events to trigger workflows
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Optional, List

from alpaca.data.historical import StockHistoricalDataClient

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...shared.statistics.volume_analyzer import (
    analyze_volume_around_event,
    format_volume_stats_for_notification,
    VolumeSurgeAnalysis
)
from ...domain.brokerage.events import TradeExecutedDomainEvent
from ...domain.brokerage.models import TradeResult
from ...domain.notification.events import NotificationRequestedDomainEvent
from ...domain.notification.factories import NotificationMessageFactory
from ...domain.notification.models import NotificationChannel, NotificationMessage
from ...services.storage import StorageQueryService
from ...jobs.headline_exit_profiles import load_profiles, HeadlineExitProfile

logger = get_logger(__name__)

# Load headline exit profiles once at import (cached in memory, zero I/O during trades)
# Updated nightly by headline_exit_profiles job
_EXIT_PROFILES_PATH = Path("tmp/statistics/headline_exit_profiles")
_exit_profiles: dict[str, HeadlineExitProfile] = {}


def _get_exit_profiles() -> dict[str, HeadlineExitProfile]:
    """Lazy-load exit profiles from disk on first access, then cache."""
    global _exit_profiles
    if not _exit_profiles:
        try:
            _exit_profiles = load_profiles(_EXIT_PROFILES_PATH)
        except Exception:
            pass  # Profiles not available yet, no-op
    return _exit_profiles


def reload_exit_profiles() -> None:
    """Force reload profiles from disk (called after nightly job updates them)."""
    global _exit_profiles
    try:
        _exit_profiles = load_profiles(_EXIT_PROFILES_PATH)
        logger.info(f"Reloaded {len(_exit_profiles)} headline exit profiles")
    except Exception as e:
        logger.warning(f"Failed to reload exit profiles: {e}")


def _market_cap_category(market_cap_millions: float) -> str:
    """Categorize market cap for display."""
    if market_cap_millions < 50:
        return "micro-cap"
    elif market_cap_millions < 300:
        return "small-cap"
    elif market_cap_millions < 2000:
        return "mid-cap"
    return "large-cap"


def _format_market_cap(market_cap_millions: float) -> str:
    """Format market cap for display (e.g. $2.7M, $1.2B)."""
    if market_cap_millions >= 1000:
        return f"${market_cap_millions / 1000:.1f}B"
    return f"${market_cap_millions:.1f}M"


def _format_float_shares(float_shares: int) -> str:
    """Format float shares for display (e.g. 2.8M, 450K)."""
    if float_shares >= 1_000_000:
        return f"{float_shares / 1_000_000:.1f}M shares"
    elif float_shares >= 1_000:
        return f"{float_shares / 1_000:.0f}K shares"
    return f"{float_shares} shares"


def _build_context_block(metadata: dict) -> list[str]:
    """Build context block for Telegram notification from trade metadata."""
    lines = []
    market_cap_millions = metadata.get("market_cap_millions")
    industry = metadata.get("industry", "")
    ai_size = metadata.get("ai_position_size", "")
    headline_type = metadata.get("headline_type", "")
    float_shares = metadata.get("float_shares")
    is_hc = metadata.get("is_high_conviction", False)
    is_cb = metadata.get("is_clinical_breakthrough", False)

    # Context line: market cap + industry
    context_parts = []
    if market_cap_millions:
        cap_cat = _market_cap_category(market_cap_millions)
        context_parts.append(f"{_format_market_cap(market_cap_millions)} ({cap_cat})")
    if industry:
        context_parts.append(industry)
    sizing_parts = []
    if ai_size:
        sizing_parts.append(f"AI Size: {ai_size}")
    if headline_type:
        sizing_parts.append(f"Type: {headline_type}")

    if context_parts or sizing_parts:
        lines.append("")
        lines.append("📊 Context:")
        if context_parts:
            lines.append(f"   Market Cap: {' | '.join(context_parts)}")
        if sizing_parts:
            lines.append(f"   {' | '.join(sizing_parts)}")
        if float_shares:
            lines.append(f"   Float: {_format_float_shares(float_shares)}")

    # Hold note
    hold_parts = []
    if headline_type in ("cancer_catalyst", "clinical_breakthrough"):
        hold_parts.append(f"Cancer catalyst ({headline_type})")
        if market_cap_millions and market_cap_millions < 50:
            hold_parts.append("Micro-caps often run +50-100%")
            hold_parts.append("Exit tiers: +15% / +20% / +30%")
        else:
            hold_parts.append("Avg biotech winner: +21%")
            hold_parts.append("Exit tiers: +10% / +15% / +20%")
    elif is_hc:
        hold_parts.append(f"High conviction ({headline_type})")
        hold_parts.append("Exit tiers: +10% / +15% / +20%")
    elif headline_type == "ai_breakthrough":
        hold_parts.append("AI breakthrough")
        hold_parts.append("Exit tiers: +8% / +12% / +15%")
    else:
        hold_parts.append("Normal signal")
        hold_parts.append("Exit tiers: +5% / +8% / +10%")

    if market_cap_millions and market_cap_millions < 50 and headline_type not in ("cancer_catalyst", "clinical_breakthrough"):
        hold_parts.append(f"Micro-cap ({_format_market_cap(market_cap_millions)}) — volatile")

    if hold_parts:
        lines.append("")
        lines.append(f"💡 {hold_parts[0]}")
        for part in hold_parts[1:]:
            lines.append(f"   {part}")

    # Add headline exit profile stats if available
    if headline_type:
        profiles = _get_exit_profiles()
        profile = profiles.get(headline_type)
        if profile and profile.sample_count >= 2:
            time_str = (
                f"{profile.median_time_to_peak_seconds}s"
                if profile.median_time_to_peak_seconds < 60
                else f"{profile.median_time_to_peak_seconds // 60}m{profile.median_time_to_peak_seconds % 60:02d}s"
            )
            lines.append("")
            lines.append(
                f"📈 Profile ({profile.sample_count}x): "
                f"peaks +{profile.median_peak_gain_pct:.1f}% @ {time_str}, "
                f"10min +{profile.median_10min_outcome_pct:.1f}%, "
                f"fade {profile.median_fade_from_peak_pct:.1f}%"
            )

    return lines


def format_trade_execution_message(
    trade_result: TradeResult,
    article_title: str = None,
    publication_time: datetime = None,
    spread_info: dict = None,
    instrument_details: dict = None,
    volume_stats: VolumeSurgeAnalysis = None,
    is_high_signal: bool = False,
    headline_type: str = None,
    metadata: dict = None,
) -> str:
    """
    Format trade execution notification message with all details.
    
    Args:
        trade_result: Trade execution result
        article_title: Optional article title
        publication_time: Optional article publication time
        volume_stats: Optional volume surge analysis
        
    Returns:
        Formatted message string
    """
    trade_request = trade_result.get_trade_request()
    notification_time = datetime.now(timezone.utc)
    
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
    # With leverage: 2x leverage on one share = buy 2 shares for the price of one
    # Capital required = price of 1 share
    # Quantity = leverage (e.g., 2.0 for 2x leverage)
    leverage = float(trade_request.leverage) if trade_request.leverage else 1.0
    actual_cost = float(trade_result.total_cost) if trade_result.total_cost else float(trade_result.fill_price) * trade_result.shares
    # Capital required = price of 1 share (what we leverage from)
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
    
    # Add leverage information: 2x leverage on one share = buy 2 shares for the price of one
    if trade_request.leverage and leverage > 1.0:
        message_parts.append(f"📊 Leverage: {trade_request.leverage}x")
        message_parts.append(f"💰 Capital Required: ${capital_required:.2f} (price of 1 share)")
        message_parts.append(f"📈 Shares Purchased: {trade_result.shares:.4f} (leverage × 1 share)")
    
    message_parts.extend([
        f"📋 Order Type: {order_type}",
        f"🕐 Session: {session_str.upper()}",
        f"⚙️  Instrument: {trade_request.instrument.value.upper()}",
    ])

    # Add HIGH SIGNAL indicator for high-conviction and clinical breakthrough trades
    if is_high_signal:
        message_parts.append(f"🎯 Signal: HIGH SIGNAL ({headline_type or 'unknown'})")

    # Add spread information if available
    if spread_info and spread_info.get("bid") and spread_info.get("ask"):
        bid = spread_info.get("bid")
        ask = spread_info.get("ask")
        spread = spread_info.get("spread", ask - bid)
        spread_pct = (spread / ((bid + ask) / 2)) * 100 if (bid + ask) > 0 else 0
        message_parts.append(f"📊 Spread: ${spread:.4f} ({spread_pct:.3f}%) | Bid: ${bid:.2f} | Ask: ${ask:.2f}")
    
    # Add detailed ladder statistics for extended hours trades
    if instrument_details and session_str in ["premarket", "postmarket"]:
        ladder_attempts = instrument_details.get("ladder_attempts")
        ladder_attempts_detail = instrument_details.get("ladder_attempts_detail", [])
        distance_to_mid = instrument_details.get("distance_to_mid")
        distance_to_target = instrument_details.get("distance_to_target")
        
        if ladder_attempts:
            message_parts.append(f"🔄 Ladder Attempts: {ladder_attempts}")
        
        if distance_to_mid is not None:
            target_label = "Ask" if trade_request.action.value == "BUY" else "Bid"
            mid_label = "Mid"
            message_parts.append(f"📏 Distance to {mid_label}: ${distance_to_mid:.4f}")
            if distance_to_target is not None:
                message_parts.append(f"📏 Distance to {target_label}: ${distance_to_target:.4f}")
    
    # Add commission if present
    if trade_result.commission and trade_result.commission > 0:
        message_parts.append(f"💳 Commission: ${trade_result.commission:.2f}")
    
    # Add volume analysis if available (NO FILTERING - just data for future research)
    if volume_stats:
        volume_lines = format_volume_stats_for_notification(volume_stats)
        message_parts.extend(volume_lines)
    
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

    # Add context block with market cap, sizing, float, and hold note
    if metadata:
        context_lines = _build_context_block(metadata)
        if context_lines:
            message_parts.extend(context_lines)

    return "\n".join(message_parts)


class NotifyTradeExecutedUseCase:
    """
    Use case for sending notifications when trades execute.
    
    Responsibilities:
    - Subscribe to Domain.TradeExecuted events
    - Fetch article from storage (for publication time and title)
    - Fetch volume stats around article publication (for data collection)
    - Format trade execution notification
    - Publish Domain.NotificationRequested event
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        storage_query_service: StorageQueryService,
        market_data_client: Optional[StockHistoricalDataClient] = None
    ):
        """
        Initialize notify trade executed use case.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Storage query service for fetching articles
            market_data_client: Alpaca market data client for volume analysis (optional)
        """
        self.event_bus: Final[AsyncEventBus] = event_bus
        self.notification_factory = NotificationMessageFactory()
        self.storage_query_service: Final[StorageQueryService] = storage_query_service
        self.market_data_client: Final[Optional[StockHistoricalDataClient]] = market_data_client
        
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
            has_market_data_client=self.market_data_client is not None,
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
        
        CRITICAL WORKFLOW POINT:
        - This handler is called by subscribe_typed wrapper when TradeExecutedDomainEvent is published
        - If this handler isn't called, check for "Error in subscriber for event Domain.TradeExecuted" in logs
        - If handler is called but no notification published, check for errors below
        
        Use cases work with domain models - they orchestrate domain workflows.
        """
        try:
            trade_result = domain_event.trade_result
            
            logger.info(
                "🎯 NOTIFY TRADE EXECUTED: Handler called",
                ticker=trade_result.get_ticker(),
                success=trade_result.success,
                status=trade_result.status.value,
                article_id=trade_result.trade_request.get("article_id")
            )
            
            # Only notify for successful trades
            if not trade_result.is_successful():
                logger.info(
                    "⏭️  NotifyTradeExecutedUseCase: Skipping notification for failed trade",
                    ticker=trade_result.get_ticker(),
                    success=trade_result.success,
                    status=trade_result.status.value
                )
                return
            
            # Skip SELL trades (exits) - those are handled by NotifyExitTradeUseCase
            trade_request = trade_result.get_trade_request()
            if trade_request.is_sell():
                logger.info(
                    "⏭️  NotifyTradeExecutedUseCase: Skipping notification for SELL trade (exit handled by NotifyExitTradeUseCase)",
                    ticker=trade_result.get_ticker()
                )
                return
            
            trade_request = trade_result.get_trade_request()
            article_id = trade_request.article_id
            
            logger.info(
                "🎯 NOTIFY TRADE EXECUTED: Orchestrating notification request",
                ticker=trade_result.get_ticker(),
                shares=trade_result.shares,
                fill_price=trade_result.fill_price,
                article_id=article_id
            )
            
            # Fetch article from storage to get publication time and title
            # SPEED FIX: Use very short timeout (0.5s) to avoid blocking notifications
            # Article details are nice-to-have, not critical. Under load, skip rather than delay.
            article = None
            publication_time = None
            article_title = None

            if article_id and self.storage_query_service:
                try:
                    # Very short timeout - notification speed is critical
                    article = await self.storage_query_service.fetch_article(article_id, timeout_seconds=0.5)
                    if article:
                        publication_time = article.published_at
                        article_title = article.title
                        logger.debug(
                            "NotifyTradeExecutedUseCase: Article details added to notification",
                            article_id=article_id
                        )
                except Exception as e:
                    # Don't log errors for timeouts - expected under load
                    logger.debug(
                        "NotifyTradeExecutedUseCase: Article fetch skipped (speed priority)",
                        article_id=article_id
                    )
            
            # Get spread_info, instrument_details, and signal metadata from trade_request dict
            trade_request_dict = trade_result.trade_request
            spread_info = trade_request_dict.get("_spread_info", {})
            instrument_details = trade_request_dict.get("_instrument_details", {})
            metadata_dict = trade_request_dict.get("metadata", {})
            is_high_signal = metadata_dict.get("is_high_conviction", False) or metadata_dict.get("is_clinical_breakthrough", False)
            headline_type = metadata_dict.get("headline_type")

            # SPEED FIX: Skip volume analysis for trade notifications
            # Volume stats were causing 5+ minute delays due to slow API calls
            # This data is collected elsewhere (recall stats) - no need to block notifications
            volume_stats = None
            
            # Format trade execution message
            trade_message = format_trade_execution_message(
                trade_result=trade_result,
                article_title=article_title,
                publication_time=publication_time,
                spread_info=spread_info,
                instrument_details=instrument_details,
                volume_stats=volume_stats,
                is_high_signal=is_high_signal,
                headline_type=headline_type,
                metadata=metadata_dict,
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
                created_at=datetime.now(timezone.utc)
            )
            
            # Publish typed domain event
            domain_notification_event = NotificationRequestedDomainEvent(
                message=notification_message,
                requested_at=datetime.now(timezone.utc)
            )
            
            await self.event_bus.publish(DomainEventType.NOTIFICATION_REQUESTED, domain_notification_event.model_dump())
            
            logger.info(
                "✅ NOTIFY TRADE EXECUTED: Published notification request",
                ticker=trade_result.get_ticker(),
                shares=trade_result.shares,
                fill_price=trade_result.fill_price,
                article_id=article_id,
                has_article_details=article is not None,
                channels=[c.value for c in notification_message.channels]
            )
            
        except Exception as e:
            logger.error(
                "❌ NOTIFY TRADE EXECUTED: Error orchestrating notification",
                error=str(e),
                exc_info=True
            )
