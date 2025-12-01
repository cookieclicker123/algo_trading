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
        if self.enabled_1 and self.bot_1:
            try:
                target_chat_id = chat_id or self.config_1["chat_id"]
                if target_chat_id:
                    await self.bot_1.send_message(chat_id=target_chat_id, text=text)
                    logger.debug(
                        "TelegramNotificationClient: Message sent via bot 1",
                        chat_id=target_chat_id
                    )
            except TelegramError as e:
                error_msg = f"Bot 1 error: {str(e)}"
                errors.append(error_msg)
                logger.error(
                    "TelegramNotificationClient: Failed to send via bot 1",
                    error=str(e)
                )
        
        # Send to bot 2 if enabled
        if self.enabled_2 and self.bot_2:
            try:
                target_chat_id = chat_id or self.config_2["chat_id"]
                if target_chat_id:
                    await self.bot_2.send_message(chat_id=target_chat_id, text=text)
                    logger.debug(
                        "TelegramNotificationClient: Message sent via bot 2",
                        chat_id=target_chat_id
                    )
            except TelegramError as e:
                error_msg = f"Bot 2 error: {str(e)}"
                errors.append(error_msg)
                logger.error(
                    "TelegramNotificationClient: Failed to send via bot 2",
                    error=str(e)
                )
        
        # Return success if at least one bot succeeded
        if errors:
            if self.enabled_1 and self.enabled_2:
                # Both bots enabled but both failed
                return False, "; ".join(errors)
            elif (self.enabled_1 and not self.bot_1) or (self.enabled_2 and not self.bot_2):
                # One bot not initialized
                return False, "; ".join(errors)
            # One bot failed but other might have succeeded
            return True, None
        
        return True, None

