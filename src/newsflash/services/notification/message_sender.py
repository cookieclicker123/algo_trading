"""
Message sending logic for Telegram notifications.

Stateless helper functions - all state is passed as parameters.
"""
from typing import Optional
from telegram import Bot
from telegram.error import TelegramError

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


async def send_telegram_message(
    bot: Optional[Bot],
    chat_id: str,
    message: str,
    bot_name: str
) -> bool:
    """
    Send a message to Telegram.
    
    Stateless function - all state passed as parameters.
    
    Args:
        bot: Telegram bot instance (may be None)
        chat_id: Chat ID to send to
        message: Message text to send
        bot_name: Name of the bot (for logging)
        
    Returns:
        True if sent successfully
    """
    if not bot or not chat_id:
        logger.error(f"{bot_name} not configured")
        return False
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown",  # Support Markdown formatting
            disable_web_page_preview=False,
        )
        logger.info(f"{bot_name} message sent successfully")
        return True
        
    except TelegramError as e:
        logger.error(f"Failed to send {bot_name} message", error=str(e))
        return False


async def send_to_all_bots(
    bot_1: Optional[Bot],
    config_1: dict,
    enabled_1: bool,
    bot_2: Optional[Bot],
    config_2: dict,
    enabled_2: bool,
    message: str,
    send_message_func,
) -> None:
    """
    Send a plain text message to all enabled bots.
    
    Stateless function - all state passed as parameters.
    
    Args:
        bot_1: Primary Telegram bot instance
        config_1: Primary bot configuration
        enabled_1: Whether primary bot is enabled
        bot_2: Secondary Telegram bot instance
        config_2: Secondary bot configuration
        enabled_2: Whether secondary bot is enabled
        message: Message text to send
        send_message_func: Function to send message (bot, chat_id, message, bot_name) -> bool
    """
    if enabled_1 and bot_1 and config_1:
        try:
            await send_message_func(
                bot_1, 
                config_1["chat_id"], 
                message, 
                "Bot 1"
            )
        except Exception as e:
            logger.error("Failed to send message to Bot 1", error=str(e))
    
    if enabled_2 and bot_2 and config_2:
        try:
            await send_message_func(
                bot_2, 
                config_2["chat_id"], 
                message, 
                "Bot 2"
            )
        except Exception as e:
            logger.error("Failed to send message to Bot 2", error=str(e))

