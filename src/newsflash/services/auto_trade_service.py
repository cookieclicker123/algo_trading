"""
Automatic trading service for IMMINENT news articles.
Trades automatically without user intervention when news is classified as IMMINENT.
"""
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from ..models.base_models import StandardizedArticle, TradeRequest
from ..models.classification_models import ClassificationResult
from ..utils.logging_config import get_logger
from ..config.settings import (
    AUTO_TRADING_ENABLED, 
    AUTO_TRADE_EXIT_DELAY_MINUTES,
    AUTO_TRADE_AMOUNT_USD
)

logger = get_logger(__name__)


class AutoTradeService:
    """
    Automatically executes trades on IMMINENT news articles.
    
    Features:
    - Trades first ticker from IMMINENT articles
    - 1 share per trade (configurable)
    - Tracks positions for automatic exit
    - Preserves manual trading capabilities
    """
    
    def __init__(self, trading_service, position_tracker, telegram_service=None, audit_trail=None, price_tracking_service=None):
        """
        Initialize auto-trade service.
        
        Args:
            trading_service: IBKRTradingService instance
            position_tracker: PositionTracker instance
            telegram_service: TelegramService instance for notifications
            audit_trail: ClassificationAuditTrail instance for logging
            price_tracking_service: PriceTrackingService instance for price tracking
        """
        self.trading_service = trading_service
        self.position_tracker = position_tracker
        self.telegram_service = telegram_service
        self.audit_trail = audit_trail
        self.price_tracking_service = price_tracking_service
        self.is_enabled = AUTO_TRADING_ENABLED
        
        logger.info(
            "AutoTradeService initialized",
            enabled=self.is_enabled,
            exit_delay_minutes=AUTO_TRADE_EXIT_DELAY_MINUTES,
            telegram_service_available=self.telegram_service is not None,
            audit_trail_available=self.audit_trail is not None,
            price_tracking_available=self.price_tracking_service is not None
        )
        
        if not self.telegram_service:
            logger.warning("⚠️ Telegram service not provided - auto-trade notifications will NOT be sent!")
    
    def select_ticker(self, article: StandardizedArticle) -> Optional[str]:
        """
        Select which ticker to trade from an article.
        
        Rules:
        - If article has NO tickers: skip (no trade)
        - If article has 1 ticker: trade that ticker
        - If article has multiple tickers: trade the FIRST ticker in the list
          (usually the primary company mentioned in news)
        
        Args:
            article: Standardized article with tickers
            
        Returns:
            Ticker symbol to trade, or None if no valid ticker
        """
        if not article.tickers:
            logger.debug("Article has no tickers - skipping auto-trade", 
                        article_id=article.source_id)
            return None
        
        ticker = article.tickers[0]  # First ticker is primary
        logger.info("Selected ticker for auto-trade", 
                   ticker=ticker,
                   all_tickers=article.tickers,
                   article_id=article.source_id)
        return ticker
    
    async def process_imminent_article(
        self, 
        article: StandardizedArticle, 
        classification_result: ClassificationResult
    ) -> None:
        """
        Process IMMINENT article and execute automatic trade if applicable.
        
        NO MARKET CAP GATE - all IMMINENT articles with tickers are traded.
        
        Args:
            article: The IMMINENT article
            classification_result: Classification result confirming IMMINENT
        """
        # Log all decision points for debugging
        logger.info("🤖 AUTO-TRADE: Processing IMMINENT article",
                   article_id=article.source_id,
                   title=article.title[:100],
                   tickers=article.tickers)
        
        if not self.is_enabled:
            reason = "Auto-trading disabled (AUTO_TRADING_ENABLED=false)"
            logger.info(f"⏭️ AUTO-TRADE SKIPPED: {reason}", article_id=article.source_id)
            await self._send_skip_notification(article, reason)
            return
        
        # Verify classification
        if classification_result.classification.value.lower() != "imminent":
            reason = f"Classification is {classification_result.classification.value}, not IMMINENT"
            logger.warning(f"⏭️ AUTO-TRADE SKIPPED: {reason}", 
                          classification=classification_result.classification.value,
                          article_id=article.source_id)
            await self._send_skip_notification(article, reason)
            return
        
        # Select ticker - FIRST ticker if multiple exist
        ticker = self.select_ticker(article)
        if not ticker:
            reason = "Article has no tickers"
            logger.info(f"⏭️ AUTO-TRADE SKIPPED: {reason}", article_id=article.source_id)
            await self._send_skip_notification(article, reason)
            return
        
        # Check if we already have an open position for this ticker
        if self.position_tracker.has_open_position(ticker):
            reason = f"Open position already exists for {ticker}"
            logger.info(f"⏭️ AUTO-TRADE SKIPPED: {reason}", 
                       ticker=ticker,
                       article_id=article.source_id)
            await self._send_skip_notification(article, reason)
            return
        
        # Execute trade - NO MARKET CAP GATE
        trade_placed_at = datetime.now()
        logger.info("🚀 AUTO-TRADING: Executing automatic trade on IMMINENT news",
                   ticker=ticker,
                   article_id=article.source_id,
                   title=article.title[:100])
        
        try:
            # Create trade request for 1 share
            trade_request = TradeRequest(
                ticker=ticker,
                amount_usd=AUTO_TRADE_AMOUNT_USD,  # Will be converted to 1 share by trading service
                action="BUY"
            )
            
            # Execute trade using existing trading service
            result = await self.trading_service.process_trade_request(trade_request)
            
            # Update audit trail with auto-trade placement
            if self.audit_trail:
                article_id = getattr(article, '_audit_article_id', article.source_id)
                session = result.session or "unknown"
                order_type = result.order_type or "unknown"
                
                self.audit_trail.update_auto_trade_placed(
                    article_id=article_id,
                    trade_placed_at=trade_placed_at,
                    ticker=ticker,
                    entry_price=result.fill_price if result.success else None,
                    shares=result.shares if result.success else None,
                    session=session,
                    order_type=order_type
                )
            
            # Always send notification (success or failure)
            await self._send_entry_notification(ticker, article, result)
            
            if result.success:
                logger.info("✅ AUTO-TRADE SUCCESSFUL",
                           ticker=ticker,
                           shares=result.shares,
                           fill_price=result.fill_price,
                           article_id=article.source_id)
                
                # Start price tracking (background task, non-blocking)
                if self.price_tracking_service:
                    article_id = getattr(article, '_audit_article_id', article.source_id)
                    asyncio.create_task(
                        self.price_tracking_service.start_tracking(
                            article_id=article_id,
                            ticker=ticker,
                            trade_placed_at=trade_placed_at
                        )
                    )
                    logger.info("Started background price tracking for 20 minutes", ticker=ticker)
                
                # Add position to tracker and schedule exit
                entry_time = datetime.now()
                self.position_tracker.add_position(
                    ticker=ticker,
                    shares=result.shares,
                    entry_time=entry_time,
                    entry_price=result.fill_price,
                    article_id=article.source_id
                )
                
                # Schedule exit after 5 minutes
                asyncio.create_task(
                    self._schedule_exit(ticker, result.shares, entry_time, result.fill_price, article)
                )
                logger.info("🕐 Exit scheduled for 5 minutes from now", 
                           ticker=ticker,
                           exit_time=(entry_time + timedelta(minutes=AUTO_TRADE_EXIT_DELAY_MINUTES)).isoformat())
            else:
                # Trade execution failed - log detailed reason
                failure_reason = f"Trade execution failed: {result.error or 'Unknown error'}"
                logger.error("❌ AUTO-TRADE FAILED",
                            ticker=ticker,
                            error=result.error,
                            article_id=article.source_id,
                            failure_reason=failure_reason)
                # Notification already sent above
                
        except Exception as e:
            # Exception during trade execution - log detailed error
            error_msg = f"Exception during trade execution: {str(e)}"
            logger.error("❌ AUTO-TRADE FAILED",
                        ticker=ticker,
                        error=str(e),
                        article_id=article.source_id,
                        error_type=type(e).__name__,
                        exc_info=True)
            # Try to send error notification
            await self._send_error_notification(ticker, article, error_msg)
    
    async def _schedule_exit(
        self, 
        ticker: str, 
        shares: int, 
        entry_time: datetime,
        entry_price: float,
        article: Optional[StandardizedArticle] = None
    ) -> None:
        """
        Schedule a position exit after configured delay (default 5 minutes).
        
        Args:
            ticker: Stock ticker to exit
            shares: Number of shares to sell
            entry_time: When the position was entered
            entry_price: Entry price
            article: Original article (for audit trail updates)
        """
        try:
            # Wait for the exit delay
            await asyncio.sleep(AUTO_TRADE_EXIT_DELAY_MINUTES * 60)
            
            # Verify position still exists (might have been closed manually)
            if not self.position_tracker.has_open_position(ticker):
                logger.warning("Position no longer exists - skipping exit",
                             ticker=ticker)
                return
            
            # Execute exit trade
            logger.info("🤖 AUTO-EXIT: Executing automatic position exit",
                       ticker=ticker,
                       shares=shares,
                       entry_time=entry_time.isoformat())
            
            exit_request = TradeRequest(
                ticker=ticker,
                amount_usd=shares * 1000.0,  # Approximate - actual will use shares
                action="SELL"
            )
            
            # For exit, we need to trade exact shares
            # Modify trade request to specify shares instead of amount
            # Actually, let's use a helper method that trades shares directly
            result = await self._execute_exit_trade(ticker, shares)
            exit_time = datetime.now()
            
            # Get actual entry price from position tracker
            actual_entry_price = self.position_tracker.get_entry_price(ticker) or entry_price
            
            # Update audit trail with exit info
            if self.audit_trail and article:
                article_id = getattr(article, '_audit_article_id', article.source_id)
                pnl = (result.fill_price - actual_entry_price) * shares if result.success else None
                pnl_percent = ((result.fill_price - actual_entry_price) / actual_entry_price * 100) if result.success and actual_entry_price > 0 else None
                session = result.session or "unknown"
                order_type = result.order_type or "unknown"
                
                self.audit_trail.update_trade_exit(
                    article_id=article_id,
                    exit_price=result.fill_price if result.success else None,
                    exit_time=exit_time,
                    pnl=pnl,
                    pnl_percent=pnl_percent,
                    session=session,
                    order_type=order_type
                )
            
            # Send Telegram notification for exit (success or failure)
            await self._send_exit_notification(ticker, shares, actual_entry_price, result)
            
            if result.success:
                logger.info("✅ AUTO-EXIT SUCCESSFUL",
                           ticker=ticker,
                           shares=result.shares,
                           exit_price=result.fill_price,
                           entry_price=actual_entry_price)
                
                # Remove position from tracker
                self.position_tracker.remove_position(ticker)
            else:
                logger.error("❌ AUTO-EXIT FAILED",
                            ticker=ticker,
                            error=result.error)
                
        except asyncio.CancelledError:
            logger.info("Exit schedule cancelled", ticker=ticker)
        except Exception as e:
            logger.error("❌ Error in scheduled exit",
                        ticker=ticker,
                        error=str(e))
    
    async def _execute_exit_trade(self, ticker: str, shares: int):
        """
        Execute exit trade for exact number of shares.
        
        Args:
            ticker: Stock ticker
            shares: Exact number of shares to sell
            
        Returns:
            TradeResult from the exit trade
        """
        # Get current price to estimate value for TradeRequest
        # The trading service will handle the actual share count
        # For now, use a reasonable estimate - trading service should handle share-based orders
        # Estimate: assume price around $100-200 per share (adjust based on typical stocks)
        estimated_price = 150.0  # Conservative estimate
        trade_request = TradeRequest(
            ticker=ticker,
            amount_usd=shares * estimated_price,  # Estimate - actual will use shares
            action="SELL"
        )
        
        logger.info(f"Executing exit trade for {shares} shares of {ticker}",
                   estimated_amount_usd=shares * estimated_price)
        
        result = await self.trading_service.process_trade_request(trade_request)
        
        # Log actual shares traded vs requested
        if result.success:
            if result.shares != shares:
                logger.warning(f"Exit trade shares mismatch: requested {shares}, got {result.shares}",
                            ticker=ticker)
        
        return result
    
    async def _send_entry_notification(
        self, 
        ticker: str, 
        article: StandardizedArticle, 
        result: Any
    ) -> None:
        """
        Send Telegram notification for trade entry attempt.
        
        Args:
            ticker: Stock ticker
            article: Article that triggered the trade
            result: TradeResult from entry trade
        """
        if not self.telegram_service:
            logger.warning("⚠️ Cannot send entry notification - telegram_service is None",
                         ticker=ticker)
            return
        
        try:
            timing = result.timing_info or {}
            total_time = timing.get("total_time", 0.0)
            session = result.session or "unknown"
            order_type = result.order_type or "unknown"
            
            if result.success:
                emoji = "✅"
                status = "SUCCESS"
                message_parts = [
                    f"{emoji} *AUTO-TRADE ENTRY: {status}*",
                    f"Ticker: `{ticker}`",
                    f"Action: BUY",
                    f"Shares: {result.shares}",
                    f"Entry Price: ${result.fill_price:.2f}",
                    f"Order Type: {order_type}",
                    f"Session: {session.replace('_', ' ').title()}",
                    f"Total Time: {total_time:.2f}s",
                ]
                
                # Add limit order details if applicable
                if order_type == "LIMIT":
                    if result.limit_price_used:
                        message_parts.append(f"Limit Price: ${result.limit_price_used:.2f}")
                    if result.percentage_above_below is not None:
                        direction = "above" if result.percentage_above_below > 0 else "below"
                        message_parts.append(f"Limit: {abs(result.percentage_above_below):.2f}% {direction}")
                    
                    # Add timing breakdown for limit orders
                    if timing.get("attempts"):
                        message_parts.append(f"Attempts: {timing.get('attempts')}")
                
                # Add timing breakdown
                if timing:
                    breakdown = []
                    if "session_detection" in timing:
                        breakdown.append(f"Session: {timing['session_detection']:.2f}s")
                    if "connection" in timing:
                        breakdown.append(f"Connection: {timing['connection']:.2f}s")
                    if "order_placement" in timing:
                        breakdown.append(f"Placement: {timing['order_placement']:.2f}s")
                    if "fill_wait" in timing:
                        breakdown.append(f"Fill Wait: {timing['fill_wait']:.2f}s")
                    
                    if breakdown:
                        message_parts.append(f"_Timing:_ {', '.join(breakdown)}")
                
                message_parts.append(f"\n📰 _Triggered by:_ {article.title[:100]}")
                
            else:
                emoji = "❌"
                status = "FAILED"
                message_parts = [
                    f"{emoji} *AUTO-TRADE ENTRY: {status}*",
                    f"Ticker: `{ticker}`",
                    f"Action: BUY",
                    f"Error: {result.error or 'Unknown error'}",
                    f"Order Type: {order_type}",
                    f"Session: {session.replace('_', ' ').title()}",
                    f"Total Time: {total_time:.2f}s",
                ]
                
                # Add limit order details if applicable
                if order_type == "LIMIT" and result.limit_price_used:
                    message_parts.append(f"Last Limit Price: ${result.limit_price_used:.2f}")
                    if result.percentage_above_below is not None:
                        direction = "above" if result.percentage_above_below > 0 else "below"
                        message_parts.append(f"Last Limit: {abs(result.percentage_above_below):.2f}% {direction}")
                
                message_parts.append(f"\n📰 _Triggered by:_ {article.title[:100]}")
            
            message = "\n".join(message_parts)
            
            # Send to both bots
            await self.telegram_service._send_message_to_all_bots(message)
            logger.info("✅ Entry notification sent to Telegram", ticker=ticker, success=result.success)
            
        except Exception as e:
            logger.error("❌ Failed to send entry notification",
                        ticker=ticker,
                        error=str(e),
                        exc_info=True)
    
    async def _send_exit_notification(
        self, 
        ticker: str, 
        shares: int, 
        entry_price: float, 
        result: Any
    ) -> None:
        """
        Send Telegram notification for trade exit attempt with P/L calculation.
        
        Args:
            ticker: Stock ticker
            shares: Number of shares
            entry_price: Price at which position was entered
            result: TradeResult from exit trade
        """
        if not self.telegram_service:
            logger.warning("⚠️ Cannot send exit notification - telegram_service is None",
                         ticker=ticker)
            return
        
        try:
            timing = result.timing_info or {}
            total_time = timing.get("total_time", 0.0)
            session = result.session or "unknown"
            order_type = result.order_type or "unknown"
            
            if result.success:
                emoji = "✅"
                status = "SUCCESS"
                exit_price = result.fill_price
                
                # Calculate P/L
                pnl = (exit_price - entry_price) * shares
                pnl_percent = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
                pnl_emoji = "📈" if pnl >= 0 else "📉"
                
                message_parts = [
                    f"{emoji} *AUTO-TRADE EXIT: {status}*",
                    f"Ticker: `{ticker}`",
                    f"Action: SELL",
                    f"Shares: {shares}",
                    f"Entry Price: ${entry_price:.2f}",
                    f"Exit Price: ${exit_price:.2f}",
                    f"{pnl_emoji} *P/L: ${pnl:.2f} ({pnl_percent:+.2f}%)*",
                    f"Order Type: {order_type}",
                    f"Session: {session.replace('_', ' ').title()}",
                    f"Total Time: {total_time:.2f}s",
                ]
                
                # Add limit order details if applicable
                if order_type == "LIMIT":
                    if result.limit_price_used:
                        message_parts.append(f"Limit Price: ${result.limit_price_used:.2f}")
                    if result.percentage_above_below is not None:
                        direction = "above" if result.percentage_above_below > 0 else "below"
                        message_parts.append(f"Limit: {abs(result.percentage_above_below):.2f}% {direction}")
                    
                    # Add timing breakdown for limit orders
                    if timing.get("attempts"):
                        message_parts.append(f"Attempts: {timing.get('attempts')}")
                
                # Add timing breakdown
                if timing:
                    breakdown = []
                    if "session_detection" in timing:
                        breakdown.append(f"Session: {timing['session_detection']:.2f}s")
                    if "connection" in timing:
                        breakdown.append(f"Connection: {timing['connection']:.2f}s")
                    if "order_placement" in timing:
                        breakdown.append(f"Placement: {timing['order_placement']:.2f}s")
                    if "fill_wait" in timing:
                        breakdown.append(f"Fill Wait: {timing['fill_wait']:.2f}s")
                    
                    if breakdown:
                        message_parts.append(f"_Timing:_ {', '.join(breakdown)}")
                
            else:
                emoji = "❌"
                status = "FAILED"
                message_parts = [
                    f"{emoji} *AUTO-TRADE EXIT: {status}*",
                    f"Ticker: `{ticker}`",
                    f"Action: SELL",
                    f"Shares: {shares}",
                    f"Entry Price: ${entry_price:.2f}",
                    f"Error: {result.error or 'Unknown error'}",
                    f"Order Type: {order_type}",
                    f"Session: {session.replace('_', ' ').title()}",
                    f"Total Time: {total_time:.2f}s",
                ]
                
                # Add limit order details if applicable
                if order_type == "LIMIT" and result.limit_price_used:
                    message_parts.append(f"Last Limit Price: ${result.limit_price_used:.2f}")
                    if result.percentage_above_below is not None:
                        direction = "above" if result.percentage_above_below > 0 else "below"
                        message_parts.append(f"Last Limit: {abs(result.percentage_above_below):.2f}% {direction}")
                
                message_parts.append(f"\n⚠️ *Position remains open*")
            
            message = "\n".join(message_parts)
            
            # Send to both bots
            await self.telegram_service._send_message_to_all_bots(message)
            logger.info("✅ Exit notification sent to Telegram", ticker=ticker, success=result.success)
            
        except Exception as e:
            logger.error("❌ Failed to send exit notification",
                        ticker=ticker,
                        error=str(e),
                        exc_info=True)
    
    async def _send_skip_notification(self, article: StandardizedArticle, reason: str) -> None:
        """
        Send Telegram notification when auto-trade is skipped.
        
        Args:
            article: Article that triggered the auto-trade attempt
            reason: Reason why trade was skipped
        """
        if not self.telegram_service:
            return
        
        try:
            ticker = article.tickers[0] if article.tickers else "N/A"
            message = (
                f"⏭️ *AUTO-TRADE SKIPPED*\n\n"
                f"Ticker: `{ticker}`\n"
                f"Reason: {reason}\n\n"
                f"📰 _Article:_ {article.title[:100]}"
            )
            
            await self.telegram_service._send_message_to_all_bots(message)
            logger.debug("Skip notification sent", reason=reason, article_id=article.source_id)
            
        except Exception as e:
            logger.error("Failed to send skip notification", error=str(e), exc_info=True)
    
    async def _send_error_notification(self, ticker: str, article: StandardizedArticle, error: str) -> None:
        """
        Send Telegram notification when auto-trade encounters an error.
        
        Args:
            ticker: Stock ticker
            article: Article that triggered the trade
            error: Error message
        """
        if not self.telegram_service:
            return
        
        try:
            message = (
                f"❌ *AUTO-TRADE ERROR*\n\n"
                f"Ticker: `{ticker}`\n"
                f"Error: {error}\n\n"
                f"📰 _Article:_ {article.title[:100]}"
            )
            
            await self.telegram_service._send_message_to_all_bots(message)
            logger.info("Error notification sent", ticker=ticker, error=error)
            
        except Exception as e:
            logger.error("Failed to send error notification", error=str(e), exc_info=True)

