"""
Telegram client for notification infrastructure.

Pure infrastructure - handles Telegram API calls.
"""
from typing import Optional
from telegram import Bot
from telegram.error import TelegramError

from ...utils.logging_config import get_logger
# Config now injected via constructor - no direct import needed

logger = get_logger(__name__)


class TelegramNotificationClient:
    """
    Client for sending notifications via Telegram.
    
    Pure infrastructure - handles Telegram API operations.
    """
    
    def __init__(self, telegram_config_1: dict, telegram_config_2: dict, enabled: bool = True):
        """
        Initialize Telegram notification client.
        
        Args:
            telegram_config_1: Configuration dict for primary Telegram bot
            telegram_config_2: Configuration dict for secondary Telegram bot
            enabled: Whether Telegram notifications are enabled
        """
        self.enabled = enabled
        
        # Use injected configuration
        self.config_1 = telegram_config_1
        self.config_2 = telegram_config_2
        
        # Initialize bot 1
        self.bot_1: Optional[Bot] = None
        self.enabled_1 = self.config_1["enabled"] and enabled
        
        if self.config_1["bot_token"] and self.enabled_1:
            self.bot_1 = Bot(token=self.config_1["bot_token"])
            logger.info(
                "TelegramNotificationClient: Bot 1 initialized",
                chat_id=self.config_1["chat_id"]
            )
        
        # Initialize bot 2
        self.bot_2: Optional[Bot] = None
        self.enabled_2 = self.config_2["enabled"] and enabled
        
        if self.config_2["bot_token"] and self.enabled_2:
            self.bot_2 = Bot(token=self.config_2["bot_token"])
            logger.info(
                "TelegramNotificationClient: Bot 2 initialized",
                chat_id=self.config_2["chat_id"]
            )
        
        total_bots = sum([self.enabled_1, self.enabled_2])
        logger.info(
            "TelegramNotificationClient initialized",
            enabled=enabled,
            total_bots=total_bots
        )
    
    async def send_message(self, text: str, chat_id: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """
        Send a message via Telegram.
        
        Args:
            text: Message text to send
            chat_id: Optional specific chat ID (if None, uses configured chat IDs)
            
        Returns:
            Tuple of (success, error_message)
        """
        if not self.enabled:
            return False, "Telegram notifications are disabled"
        
        errors = []
        
        # Send to bot 1 if enabled
        bot_1_success = False
        if self.enabled_1 and self.bot_1:
            try:
                target_chat_id = chat_id or self.config_1["chat_id"]
                if target_chat_id:
                    await self.bot_1.send_message(chat_id=target_chat_id, text=text)
                    bot_1_success = True
                    logger.info(
                        "📱 TelegramNotificationClient: Message sent via bot 1",
                        chat_id=target_chat_id,
                        message_length=len(text)
                    )
                else:
                    errors.append("Bot 1: No chat ID configured")
                    logger.warning("TelegramNotificationClient: Bot 1 enabled but no chat_id configured")
            except TelegramError as e:
                error_msg = f"Bot 1 error: {str(e)}"
                errors.append(error_msg)
                logger.error(
                    "❌ TelegramNotificationClient: Failed to send via bot 1",
                    error=str(e),
                    chat_id=target_chat_id if 'target_chat_id' in locals() else None
                )
            except Exception as e:
                error_msg = f"Bot 1 unexpected error: {str(e)}"
                errors.append(error_msg)
                logger.error(
                    "❌ TelegramNotificationClient: Unexpected error sending via bot 1",
                    error=str(e),
                    exc_info=True
                )
        elif self.enabled_1 and not self.bot_1:
            errors.append("Bot 1: Enabled but not initialized")
            logger.warning("TelegramNotificationClient: Bot 1 enabled but not initialized (missing token?)")
        
        # Send to bot 2 if enabled
        bot_2_success = False
        if self.enabled_2 and self.bot_2:
            try:
                target_chat_id = chat_id or self.config_2["chat_id"]
                if target_chat_id:
                    await self.bot_2.send_message(chat_id=target_chat_id, text=text)
                    bot_2_success = True
                    logger.info(
                        "📱 TelegramNotificationClient: Message sent via bot 2",
                        chat_id=target_chat_id,
                        message_length=len(text)
                    )
                else:
                    errors.append("Bot 2: No chat ID configured")
                    logger.warning("TelegramNotificationClient: Bot 2 enabled but no chat_id configured")
            except TelegramError as e:
                error_msg = f"Bot 2 error: {str(e)}"
                errors.append(error_msg)
                logger.error(
                    "❌ TelegramNotificationClient: Failed to send via bot 2",
                    error=str(e),
                    chat_id=target_chat_id if 'target_chat_id' in locals() else None
                )
            except Exception as e:
                error_msg = f"Bot 2 unexpected error: {str(e)}"
                errors.append(error_msg)
                logger.error(
                    "❌ TelegramNotificationClient: Unexpected error sending via bot 2",
                    error=str(e),
                    exc_info=True
                )
        elif self.enabled_2 and not self.bot_2:
            errors.append("Bot 2: Enabled but not initialized")
            logger.warning("TelegramNotificationClient: Bot 2 enabled but not initialized (missing token?)")
        
        # Return success if at least one bot succeeded
        if bot_1_success or bot_2_success:
            if errors:
                # At least one succeeded but some failed
                logger.warning(
                    "TelegramNotificationClient: Partial success",
                    bot_1_success=bot_1_success,
                    bot_2_success=bot_2_success,
                    errors=errors
                )
            return True, None
        
        # All bots failed or none enabled
        if errors:
            logger.error(
                "❌ TelegramNotificationClient: All bots failed to send",
                errors=errors,
                enabled_1=self.enabled_1,
                enabled_2=self.enabled_2,
                bot_1_initialized=self.bot_1 is not None,
                bot_2_initialized=self.bot_2 is not None
            )
            return False, "; ".join(errors)
        
        # No bots enabled
        logger.warning("TelegramNotificationClient: No bots enabled")
        return False, "No Telegram bots enabled"

