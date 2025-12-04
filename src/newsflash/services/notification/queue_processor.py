"""
Queue processing logic for Telegram notifications.

Stateless helper functions - all state is passed as parameters.
"""
import asyncio
from typing import Optional
from telegram import Bot

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


async def process_message_queue(
    queue: asyncio.Queue,
    bot: Bot,
    chat_id: str,
    bot_name: str,
    queue_processing_active: bool,
    send_message_func,
) -> None:
    """
    Process queued messages for a specific bot.
    
    Stateless function - all state passed as parameters.
    
    Args:
        queue: Message queue for this bot
        bot: Telegram bot instance
        chat_id: Chat ID to send to
        bot_name: Name of the bot (for logging)
        queue_processing_active: Flag indicating if processing should continue
        send_message_func: Function to send message (bot, chat_id, message, bot_name) -> bool
    """
    while queue_processing_active:
        try:
            # Get message from queue (with timeout to allow shutdown)
            try:
                message, article = await asyncio.wait_for(
                    queue.get(),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            
            # Send message
            success = await send_message_func(bot, chat_id, message, bot_name)
            
            if success:
                logger.info(
                    f"{bot_name} notification sent",
                    article_id=getattr(article, 'benzinga_id', getattr(article, 'source_id', 'unknown'))
                )
            else:
                logger.warning(
                    f"Failed to send {bot_name} notification",
                    article_id=getattr(article, 'benzinga_id', getattr(article, 'source_id', 'unknown'))
                )
            
            # Rate limit: Telegram allows 30 messages/second, use 20 to be safe
            await asyncio.sleep(0.05)  # 50ms = 20 messages/second
            
        except Exception as e:
            logger.error(f"Error processing {bot_name} queue", error=str(e))
            await asyncio.sleep(1.0)


async def drain_queue_on_shutdown(
    queue: asyncio.Queue,
    bot: Optional[Bot],
    chat_id: str,
    bot_name: str,
    send_message_func,
) -> None:
    """
    Drain remaining messages from queue during shutdown.
    
    Stateless function - all state passed as parameters.
    
    Args:
        queue: Message queue to drain
        bot: Telegram bot instance (may be None)
        chat_id: Chat ID to send to
        bot_name: Name of the bot (for logging)
        send_message_func: Function to send message (bot, chat_id, message, bot_name) -> bool
    """
    while not queue.empty():
        try:
            message, _ = queue.get_nowait()
            await send_message_func(bot, chat_id, message, bot_name)
        except asyncio.QueueEmpty:
            break
        except Exception as e:
            logger.error(f"Error sending queued message during shutdown ({bot_name})", error=str(e))

