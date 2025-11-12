"""
Automatic trading service for IMMINENT news articles.
Trades automatically without user intervention when news is classified as IMMINENT.
"""
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

import pytz

from ..models.base_models import StandardizedArticle, TradeRequest, TradeInstrument, OptionContractParams
from .ibkr_trading_service import TradeResult
from .position_tracker import Position
from ..models.classification_models import ClassificationResult
from ..utils.logging_config import get_logger
from .yfinance_service import YFinanceService
from ..config.settings import (
    AUTO_TRADING_ENABLED,
    AUTO_TRADE_EXIT_DELAY_MINUTES,
    AUTO_TRADE_AMOUNT_USD,
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
    
    def __init__(
        self,
        trading_service,
        position_tracker,
        telegram_service=None,
        audit_trail=None,
        price_tracking_service=None,
        fundamentals_service: Optional[YFinanceService] = None,
    ):
        """
        Initialize auto-trade service.
        Args:
            trading_service: IBKRTradingService instance
            position_tracker: PositionTracker instance
            telegram_service: TelegramService instance for notifications
            audit_trail: ClassificationAuditTrail instance for logging
            price_tracking_service: PriceTrackingService instance for price tracking
            fundamentals_service: YFinanceService (or compatible) for market-cap lookups
        """
        self.trading_service = trading_service
        self.position_tracker = position_tracker
        self.telegram_service = telegram_service
        self.audit_trail = audit_trail
        self.price_tracking_service = price_tracking_service
        self.is_enabled = AUTO_TRADING_ENABLED
        self.trade_timeout_seconds = 10.0
        self._extended_hours_notional = 1000.0
        self._extended_hours_leverage = 2.0
        self._option_market_cap_threshold = 150_000_000_000  # $150B
        self._fundamentals_service = fundamentals_service or YFinanceService()
        self._delayed_trade_tasks: Dict[str, asyncio.Task] = {}
        
        logger.info(
            "AutoTradeService initialized",
            enabled=self.is_enabled,
            exit_delay_minutes=AUTO_TRADE_EXIT_DELAY_MINUTES,
            trade_timeout_seconds=self.trade_timeout_seconds,
            telegram_service_available=self.telegram_service is not None,
            audit_trail_available=self.audit_trail is not None,
            price_tracking_available=self.price_tracking_service is not None,
            option_market_cap_threshold=self._option_market_cap_threshold,
        )
        
        if not self.telegram_service:
            logger.warning("⚠️ Telegram service not provided - auto-trade notifications will NOT be sent!")
    
    @staticmethod
    def _format_nbbo_lines(nbbo: Optional[Dict[str, Any]]) -> List[str]:
        """Render NBBO telemetry into user-facing lines."""
        if not nbbo or not isinstance(nbbo, dict):
            return []

        def _extract_float(value) -> Optional[float]:
            try:
                if value is None:
                    return None
                if isinstance(value, (int, float)):
                    return float(value)
                if isinstance(value, str):
                    return float(value)
            except Exception:
                return None
            return None

        bid = _extract_float(nbbo.get("bid"))
        ask = _extract_float(nbbo.get("ask"))
        mid = _extract_float(nbbo.get("mid"))
        spread = _extract_float(nbbo.get("spread"))

        lines: List[str] = []
        if bid is not None:
            lines.append(f"NBBO Bid: ${bid:.2f}")
        if ask is not None:
            lines.append(f"NBBO Ask: ${ask:.2f}")
        if mid is not None:
            lines.append(f"NBBO Mid: ${mid:.2f}")

        if spread is not None:
            if mid and mid > 0:
                spread_pct = (spread / mid) * 100.0
                lines.append(f"NBBO Spread: ${spread:.2f} ({spread_pct:.2f}%)")
            else:
                lines.append(f"NBBO Spread: ${spread:.2f}")

        price_source = nbbo.get("price_source") or nbbo.get("source")
        if isinstance(price_source, str):
            lines.append(f"NBBO Source: {price_source}")

        return lines

    def _append_nbbo_sections(
        self,
        message_parts: List[str],
        instrument_details: Dict[str, Any],
        instrument_value: str,
    ) -> None:
        """
        Append NBBO telemetry lines to a message, handling option vs stock formatting.
        """
        if instrument_value == TradeInstrument.OPTION.value:
            option_lines = self._format_nbbo_lines(instrument_details.get("option_nbbo"))
            if option_lines:
                message_parts.append("*Option NBBO:*")
                message_parts.extend(option_lines)
            underlying_lines = self._format_nbbo_lines(
                instrument_details.get("underlying_nbbo") or instrument_details.get("nbbo")
            )
            if underlying_lines:
                message_parts.append("*Underlying NBBO:*")
                message_parts.extend(underlying_lines)
        else:
            nbbo_lines = self._format_nbbo_lines(
                instrument_details.get("nbbo") or instrument_details.get("underlying_nbbo")
            )
            if nbbo_lines:
                message_parts.extend(nbbo_lines)

    async def _fetch_fundamental_snapshot(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch fundamental data for a ticker, handling failures gracefully."""
        if not self._fundamentals_service:
            return None
        try:
            data = await self._fundamentals_service.get_fundamental_data(ticker)
            return data or None
        except Exception as exc:
            logger.warning(
                "Unable to fetch fundamental snapshot for market-cap gating",
                ticker=ticker,
                error=str(exc),
            )
            return None

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
        classification_result: ClassificationResult,
        *,
        is_retry: bool = False,
    ) -> None:
        """
        Process IMMINENT article and execute automatic trade if applicable.
        
        Args:
            article: The IMMINENT article
            classification_result: Classification result confirming IMMINENT
        """
        # Log all decision points for debugging
        logger.info("🤖 AUTO-TRADE: Processing IMMINENT article",
                   article_id=article.source_id,
                   title=article.title[:100],
                   tickers=article.tickers)

        if is_retry:
            self._delayed_trade_tasks.pop(article.source_id, None)
        
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
        
        # Determine session to select instrument strategy
        session, _ = self.trading_service.get_market_session()
        logger.info(
            "🚀 AUTO-TRADING: Executing automatic trade on IMMINENT news",
            ticker=ticker,
            article_id=article.source_id,
            title=article.title[:100],
            session=session,
        )
        use_option = False
        market_cap: Optional[int] = None
        if session == "market_hours":
            fundamentals = await self._fetch_fundamental_snapshot(ticker)
            if fundamentals:
                market_cap_value = fundamentals.get("market_cap") or fundamentals.get("marketCap")
                if isinstance(market_cap_value, (int, float)):
                    market_cap = int(market_cap_value)
            if market_cap and market_cap >= self._option_market_cap_threshold:
                use_option = True
            else:
                logger.info(
                    "Option trade gated in favor of leveraged shares",
                    ticker=ticker,
                    market_cap=market_cap,
                    threshold=self._option_market_cap_threshold,
                )

        if session == "closed" and not is_retry:
            delay_seconds = self._seconds_until_next_premarket()
            if delay_seconds is not None:
                logger.info(
                    "Market closed – queueing trade for next premarket window",
                    ticker=ticker,
                    article_id=article.source_id,
                    delay_seconds=delay_seconds,
                )
                if self.telegram_service:
                    await self._send_skip_notification(
                        article,
                        "Market closed. Trade queued for next premarket window.",
                    )
                existing = self._delayed_trade_tasks.get(article.source_id)
                if existing and not existing.done():
                    existing.cancel()
                task = asyncio.create_task(
                    self._run_delayed_trade(article, classification_result, delay_seconds)
                )
                self._delayed_trade_tasks[article.source_id] = task
                return
            else:
                logger.warning(
                    "Market closed but unable to compute delay to premarket",
                    ticker=ticker,
                    article_id=article.source_id,
                )

        # Execute trade using instrument determined above
        trade_placed_at = datetime.now()
        try:
            if use_option:
                trade_request = TradeRequest(
                    ticker=ticker,
                    amount_usd=AUTO_TRADE_AMOUNT_USD,
                    action="BUY",
                    shares=1,  # 1 contract
                    instrument=TradeInstrument.OPTION,
                    position_article_id=article.source_id,
                )
            else:
                trade_request = TradeRequest(
                    ticker=ticker,
                    amount_usd=self._extended_hours_notional,
                    action="BUY",
                    shares=None,  # let trading service size by notional
                    instrument=TradeInstrument.STOCK,
                    leverage=self._extended_hours_leverage,
                    position_article_id=article.source_id,
                )
            
            # Execute trade using existing trading service
            result = await self.trading_service.process_trade_request(
                trade_request, timeout_seconds=self.trade_timeout_seconds
            )
            
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
                    order_type=order_type,
                    instrument=result.instrument,
                    instrument_details=result.instrument_details,
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
                    article_id=article.source_id,
                    instrument=result.instrument,
                    instrument_details=result.instrument_details,
                    leverage=getattr(trade_request, "leverage", None),
                )
                position = self.position_tracker.get_position(ticker, article.source_id)
                if not position:
                    logger.error(
                        "Unable to retrieve newly created position for exit scheduling",
                        ticker=ticker,
                        article_id=article.source_id,
                    )
                    return

                # Schedule exit after 5 minutes
                asyncio.create_task(
                    self._schedule_exit(
                        position=position,
                        article=article
                    )
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
        position: Position,
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
            ticker = position.ticker
            article_id = position.article_id
            if not self.position_tracker.has_open_position(ticker, article_id):
                logger.warning("Position no longer exists - skipping exit",
                             ticker=ticker)
                return
            
            # Execute exit trade
            logger.info("🤖 AUTO-EXIT: Executing automatic position exit",
                       ticker=ticker,
                       shares=position.shares,
                       entry_time=position.entry_time)
            
            result = await self._execute_exit_trade(position)
            exit_time = datetime.now()
            
            # Get actual entry price from position tracker
            actual_entry_price = (
                self.position_tracker.get_entry_price(ticker, article_id)
                or position.entry_price
            )
            shares = position.shares
            
            # Update audit trail with exit info
            if self.audit_trail and article:
                audit_article_id = getattr(article, '_audit_article_id', article.source_id)
                if result.success and result.fill_price is not None:
                    pnl = (result.fill_price - actual_entry_price) * shares
                    pnl_percent = (
                        ((result.fill_price - actual_entry_price) / actual_entry_price * 100)
                        if actual_entry_price > 0
                        else None
                    )
                else:
                    pnl = None
                    pnl_percent = None
                session = result.session or "unknown"
                order_type = result.order_type or "unknown"

                self.audit_trail.update_trade_exit(
                    article_id=audit_article_id,
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
                self.position_tracker.remove_position(ticker, article_id)
            else:
                logger.error("❌ AUTO-EXIT FAILED",
                            ticker=ticker,
                            error=result.error)
                
        except asyncio.CancelledError:
            logger.info("Exit schedule cancelled", ticker=ticker)
        except Exception as e:
            instrument_value = getattr(position, "instrument", TradeInstrument.STOCK.value)
            failure_result = TradeResult(
                success=False,
                error=str(e),
                instrument=instrument_value,
            )
            try:
                await self._send_exit_notification(
                    position.ticker, position.shares, position.entry_price, failure_result
                )
            except Exception as notify_exc:  # pragma: no cover
                logger.error(
                    "❌ Failed to notify exit error",
                    ticker=position.ticker,
                    error=str(notify_exc),
                )
            logger.error("❌ Error in scheduled exit",
                        ticker=ticker,
                        error=str(e))
    
    async def _execute_exit_trade(self, position: Position):
        """
        Execute exit trade for exact number of shares.
        
        Args:
            position: Position details to exit
            
        Returns:
            TradeResult from the exit trade
        """
        instrument = getattr(position, "instrument", TradeInstrument.STOCK.value)
        instrument_enum = TradeInstrument(instrument) if isinstance(instrument, str) else instrument

        if instrument_enum == TradeInstrument.OPTION:
            details = position.instrument_details or {}
            if not details:
                logger.error(
                    "Missing option contract metadata for position exit",
                    ticker=position.ticker,
                    article_id=position.article_id,
                )
                raise ValueError("Missing option contract metadata for exit trade")
            strike_value = details.get("strike")
            if strike_value is None:
                logger.error(
                    "Missing strike information for option exit",
                    ticker=position.ticker,
                    article_id=position.article_id,
                    details=details,
                )
                raise ValueError("Missing strike information for option exit")
            option_params = OptionContractParams(
                symbol=position.ticker,
                last_trade_date_or_contract_month=details.get("expiry") or details.get("last_trade_date_or_contract_month"),
                strike=float(strike_value),
                right=details.get("right", "C"),
                exchange=details.get("exchange", "SMART"),
                currency=details.get("currency", "USD"),
                multiplier=str(details.get("multiplier", "100")),
                trading_class=details.get("trading_class"),
                con_id=details.get("con_id"),
            )
            estimated_value = position.shares * position.entry_price * float(option_params.multiplier or "100")
            trade_request = TradeRequest(
                ticker=position.ticker,
                amount_usd=estimated_value,
                action="SELL",
                shares=position.shares,
                instrument=TradeInstrument.OPTION,
                option_contract=option_params,
                position_article_id=position.article_id,
            )
        else:
            estimated_price = position.entry_price or 150.0
            trade_request = TradeRequest(
                ticker=position.ticker,
                amount_usd=estimated_price * position.shares,
                action="SELL",
                shares=position.shares,
                instrument=TradeInstrument.STOCK,
                leverage=getattr(position, "leverage", None),
                position_article_id=position.article_id,
            )

        logger.info(
            "Executing exit trade",
            ticker=position.ticker,
            instrument=instrument_enum.value,
            shares=position.shares,
            article_id=position.article_id,
        )

        result = await self.trading_service.process_trade_request(
            trade_request, timeout_seconds=self.trade_timeout_seconds
        )
        
        # Log actual shares traded vs requested
        if result.success and result.shares != position.shares:
            logger.warning(
                "Exit trade units mismatch",
                requested=position.shares,
                filled=result.shares,
                ticker=position.ticker,
            )
        
        return result
    
    async def _run_delayed_trade(
        self,
        article: StandardizedArticle,
        classification_result: ClassificationResult,
        delay_seconds: float,
    ) -> None:
        article_id = article.source_id
        try:
            await asyncio.sleep(delay_seconds)
            await self.process_imminent_article(
                article,
                classification_result,
                is_retry=True,
            )
        except asyncio.CancelledError:
            logger.info("Delayed auto-trade task cancelled", article_id=article_id)
        except Exception as exc:
            logger.error(
                "Delayed auto-trade task failed",
                article_id=article_id,
                error=str(exc),
            )
        finally:
            self._delayed_trade_tasks.pop(article_id, None)

    def _seconds_until_next_premarket(self) -> Optional[float]:
        try:
            eastern = pytz.timezone("US/Eastern")
        except Exception as exc:
            logger.error("Unable to load US/Eastern timezone", error=str(exc))
            return None

        now_et = datetime.now(eastern)
        today_premarket = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
        if now_et < today_premarket:
            target = today_premarket
        else:
            target = (now_et + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)

        delta = (target - now_et).total_seconds()
        return max(delta, 0.0)

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
            
            instrument_details = getattr(result, "instrument_details", {}) or {}

            if result.success:
                emoji = "✅"
                status = "SUCCESS"
                instrument_value = getattr(result, "instrument", TradeInstrument.STOCK.value)
                instrument_parts = [f"Instrument: {instrument_value.upper()}"]
                if instrument_value == TradeInstrument.OPTION.value:
                    expiry = instrument_details.get("expiry") or instrument_details.get("last_trade_date_or_contract_month")
                    strike = instrument_details.get("strike")
                    right = instrument_details.get("right", "C")
                    instrument_parts.append(f"Contract: {expiry} {strike} {right}")
                    exchange = instrument_details.get("exchange")
                    if exchange:
                        instrument_parts.append(f"Exchange: {exchange}")
                else:
                    leverage = instrument_details.get("leverage") or getattr(result, "instrument_details", {}).get("leverage")
                    if leverage:
                        instrument_parts.append(f"Leverage: {leverage}x")
                    target_notional = instrument_details.get("target_notional")
                    if target_notional:
                        instrument_parts.append(f"Target Notional: ${target_notional:.2f}")
                    projected_notional = instrument_details.get("projected_notional")
                    if projected_notional:
                        instrument_parts.append(f"Projected Notional: ${projected_notional:.2f}")
                    executed_notional = instrument_details.get("effective_notional")
                    if executed_notional:
                        instrument_parts.append(f"Executed Notional: ${executed_notional:.2f}")
                fill_venue = instrument_details.get("fill_venue")
                if fill_venue:
                    instrument_parts.append(f"Fill Venue: {fill_venue}")
                if instrument_details.get("used_price_fallback"):
                    instrument_parts.append("Used Quote Fallback: Yes")

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
                message_parts.extend(instrument_parts)
                self._append_nbbo_sections(message_parts, instrument_details, instrument_value)
                
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
                instrument_value = getattr(result, "instrument", TradeInstrument.STOCK.value)
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

                self._append_nbbo_sections(message_parts, instrument_details, instrument_value)
                
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
                instrument_value = getattr(result, "instrument", TradeInstrument.STOCK.value)
                instrument_details = getattr(result, "instrument_details", {}) or {}
                instrument_parts = [f"Instrument: {instrument_value.upper()}"]
                if instrument_value == TradeInstrument.OPTION.value:
                    expiry = instrument_details.get("expiry") or instrument_details.get("last_trade_date_or_contract_month")
                    strike = instrument_details.get("strike")
                    right = instrument_details.get("right", "C")
                    instrument_parts.append(f"Contract: {expiry} {strike} {right}")
                else:
                    leverage = instrument_details.get("leverage")
                    if leverage:
                        instrument_parts.append(f"Leverage: {leverage}x")
                fill_venue = instrument_details.get("fill_venue")
                if fill_venue:
                    instrument_parts.append(f"Fill Venue: {fill_venue}")
                if instrument_details.get("used_price_fallback"):
                    instrument_parts.append("Used Quote Fallback: Yes")
                
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
                message_parts.extend(instrument_parts)
                self._append_nbbo_sections(message_parts, instrument_details, instrument_value)
                
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
                instrument_value = getattr(result, "instrument", TradeInstrument.STOCK.value)
                instrument_details = getattr(result, "instrument_details", {}) or {}
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
                message_parts.append(f"Instrument: {instrument_value.upper()}")
                fill_venue = instrument_details.get("fill_venue")
                if fill_venue:
                    message_parts.append(f"Fill Venue: {fill_venue}")
                if instrument_details.get("used_price_fallback"):
                    message_parts.append("Used Quote Fallback: Yes")
                self._append_nbbo_sections(message_parts, instrument_details, instrument_value)
                
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

