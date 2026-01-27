"""
Fast trade notification - bypasses event bus for immediate Telegram delivery.

This module sends trade notifications directly to Telegram without going through
the multi-hop event bus architecture. Used for time-critical notifications where
~2 minute delays from the standard path are unacceptable.

The standard event bus flow continues in parallel for stats/logging.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from telegram import Bot
from telegram.error import TelegramError

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class FastTradeNotifier:
    """
    Sends trade notifications directly to Telegram with minimal latency.

    Fire-and-forget design - call notify_trade_executed() and it returns immediately
    after spawning a background task. Does not block trade execution flow.
    """

    def __init__(
        self,
        bot_token_1: Optional[str] = None,
        chat_id_1: Optional[str] = None,
        bot_token_2: Optional[str] = None,
        chat_id_2: Optional[str] = None,
        enabled: bool = True,
    ):
        """
        Initialize fast trade notifier.

        Args:
            bot_token_1: Primary Telegram bot token
            chat_id_1: Primary Telegram chat ID
            bot_token_2: Secondary Telegram bot token
            chat_id_2: Secondary Telegram chat ID
            enabled: Whether fast notifications are enabled
        """
        self.enabled = enabled
        self._background_tasks: set = set()

        # Initialize bots
        self.bot_1: Optional[Bot] = None
        self.chat_id_1 = chat_id_1
        if bot_token_1 and chat_id_1 and enabled:
            self.bot_1 = Bot(token=bot_token_1)
            logger.info("FastTradeNotifier: Bot 1 initialized")

        self.bot_2: Optional[Bot] = None
        self.chat_id_2 = chat_id_2
        if bot_token_2 and chat_id_2 and enabled:
            self.bot_2 = Bot(token=bot_token_2)
            logger.info("FastTradeNotifier: Bot 2 initialized")

        logger.info(
            "FastTradeNotifier initialized",
            enabled=enabled,
            bot_1_ready=self.bot_1 is not None,
            bot_2_ready=self.bot_2 is not None,
        )

    def notify_trade_executed(
        self,
        ticker: str,
        action: str,
        shares: float,
        fill_price: float,
        total_cost: float,
        session: str,
        order_type: str,
        spread_info: Optional[Dict[str, Any]] = None,
        article_title: Optional[str] = None,
        publication_time: Optional[datetime] = None,
    ) -> None:
        """
        Send trade execution notification immediately (fire-and-forget).

        This method returns immediately after spawning a background task.
        Does not block the calling code.

        Args:
            ticker: Stock ticker symbol
            action: Trade action (BUY/SELL)
            shares: Number of shares
            fill_price: Fill price per share
            total_cost: Total cost of trade
            session: Trading session (premarket/market/postmarket)
            order_type: Order type used
            spread_info: Optional spread information
            article_title: Optional article title
            publication_time: Optional article publication time
        """
        if not self.enabled:
            return

        if not self.bot_1 and not self.bot_2:
            return

        # Format message
        message = self._format_trade_message(
            ticker=ticker,
            action=action,
            shares=shares,
            fill_price=fill_price,
            total_cost=total_cost,
            session=session,
            order_type=order_type,
            spread_info=spread_info,
            article_title=article_title,
            publication_time=publication_time,
        )

        # Fire and forget - spawn background task
        task = asyncio.create_task(self._send_to_all_bots(message, ticker))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        logger.debug("FastTradeNotifier: Spawned notification task", ticker=ticker)

    def _format_trade_message(
        self,
        ticker: str,
        action: str,
        shares: float,
        fill_price: float,
        total_cost: float,
        session: str,
        order_type: str,
        spread_info: Optional[Dict[str, Any]] = None,
        article_title: Optional[str] = None,
        publication_time: Optional[datetime] = None,
    ) -> str:
        """Format trade execution message."""
        notification_time = datetime.now(timezone.utc)

        message_parts = [
            "✅ TRADE EXECUTED",
            "",
            f"📈 Ticker: {ticker}",
            f"📊 Action: {action}",
            f"📦 Shares: {shares:.4f}",
            f"💵 Fill Price: ${fill_price:.2f}",
            f"💸 Total Cost: ${total_cost:.2f}",
            f"📋 Order Type: {order_type}",
            f"🕐 Session: {session.upper()}",
        ]

        # Add spread information if available
        if spread_info and spread_info.get("bid") and spread_info.get("ask"):
            bid = spread_info.get("bid")
            ask = spread_info.get("ask")
            spread = spread_info.get("spread", ask - bid)
            spread_pct = (spread / ((bid + ask) / 2)) * 100 if (bid + ask) > 0 else 0
            message_parts.append(f"📊 Spread: ${spread:.4f} ({spread_pct:.3f}%) | Bid: ${bid:.2f} | Ask: ${ask:.2f}")

        message_parts.extend([
            "",
            f"⏰ Executed At: {notification_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ])

        # Add publication time if available
        if publication_time:
            message_parts.append(f"📰 Published At: {publication_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            time_diff = (notification_time - publication_time).total_seconds()
            message_parts.append(f"⏱️  Time to Notification: {time_diff:.2f} seconds")

        message_parts.append(f"📱 Notification Received: {notification_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        # Add article title if available
        if article_title:
            title_display = f"{article_title[:100]}..." if len(article_title) > 100 else article_title
            message_parts.extend([
                "",
                f"📄 Article: {title_display}"
            ])

        return "\n".join(message_parts)

    def notify_exit_triggered(
        self,
        ticker: str,
        exit_reason: str,
        shares: float,
        entry_price: float,
        exit_price: float,
        profit_pct: float,
        pnl_usd: float,
        stop_loss_price: Optional[float] = None,
    ) -> None:
        """
        Send exit notification immediately (fire-and-forget).

        Called when stop loss or manual exit is triggered.
        """
        if not self.enabled:
            return

        if not self.bot_1 and not self.bot_2:
            return

        # Format message
        message = self._format_exit_message(
            ticker=ticker,
            exit_reason=exit_reason,
            shares=shares,
            entry_price=entry_price,
            exit_price=exit_price,
            profit_pct=profit_pct,
            pnl_usd=pnl_usd,
            stop_loss_price=stop_loss_price,
        )

        # Fire and forget - spawn background task
        task = asyncio.create_task(self._send_to_all_bots(message, ticker))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        logger.debug("FastTradeNotifier: Spawned exit notification task", ticker=ticker, exit_reason=exit_reason)

    def _format_exit_message(
        self,
        ticker: str,
        exit_reason: str,
        shares: float,
        entry_price: float,
        exit_price: float,
        profit_pct: float,
        pnl_usd: float,
        stop_loss_price: Optional[float] = None,
    ) -> str:
        """Format exit notification message."""
        notification_time = datetime.now(timezone.utc)

        # Choose emoji based on exit reason and P&L
        if exit_reason == "stop_loss":
            header = "🛑 STOP LOSS TRIGGERED"
        elif exit_reason == "manual_exit":
            header = "🚪 MANUAL EXIT"
        else:
            header = "📤 POSITION EXIT"

        pnl_emoji = "🟢" if pnl_usd >= 0 else "🔴"

        message_parts = [
            header,
            "",
            f"📈 Ticker: {ticker}",
            f"📦 Shares: {int(shares)}",
            f"💵 Entry: ${entry_price:.2f}",
            f"💸 Exit: ${exit_price:.2f}",
            f"{pnl_emoji} P&L: ${pnl_usd:+.2f} ({profit_pct*100:+.1f}%)",
        ]

        if stop_loss_price and exit_reason == "stop_loss":
            message_parts.append(f"🎯 Stop Loss Price: ${stop_loss_price:.2f}")

        message_parts.extend([
            "",
            f"⏰ Triggered At: {notification_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ])

        return "\n".join(message_parts)

    async def _send_to_all_bots(self, message: str, ticker: str) -> None:
        """Send message to all configured bots."""
        tasks = []

        if self.bot_1 and self.chat_id_1:
            tasks.append(self._send_message(self.bot_1, self.chat_id_1, message, "Bot 1"))

        if self.bot_2 and self.chat_id_2:
            tasks.append(self._send_message(self.bot_2, self.chat_id_2, message, "Bot 2"))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            success_count = sum(1 for r in results if r is True)
            logger.info(
                "FastTradeNotifier: Sent notification",
                ticker=ticker,
                success_count=success_count,
                total_bots=len(tasks),
            )

    async def _send_message(self, bot: Bot, chat_id: str, message: str, bot_name: str) -> bool:
        """Send message via a specific bot."""
        try:
            await bot.send_message(chat_id=chat_id, text=message)
            logger.debug(f"FastTradeNotifier: {bot_name} message sent")
            return True
        except TelegramError as e:
            logger.error(f"FastTradeNotifier: {bot_name} Telegram error", error=str(e))
            return False
        except Exception as e:
            logger.error(f"FastTradeNotifier: {bot_name} unexpected error", error=str(e))
            return False


def create_fast_trade_notifier(
    telegram_config_1: dict,
    telegram_config_2: dict,
) -> FastTradeNotifier:
    """
    Factory function for creating FastTradeNotifier.

    Args:
        telegram_config_1: Primary Telegram configuration dict
        telegram_config_2: Secondary Telegram configuration dict

    Returns:
        Configured FastTradeNotifier instance
    """
    enabled = telegram_config_1.get("enabled", False) or telegram_config_2.get("enabled", False)

    return FastTradeNotifier(
        bot_token_1=telegram_config_1.get("bot_token") if telegram_config_1.get("enabled") else None,
        chat_id_1=telegram_config_1.get("chat_id") if telegram_config_1.get("enabled") else None,
        bot_token_2=telegram_config_2.get("bot_token") if telegram_config_2.get("enabled") else None,
        chat_id_2=telegram_config_2.get("chat_id") if telegram_config_2.get("enabled") else None,
        enabled=enabled,
    )
