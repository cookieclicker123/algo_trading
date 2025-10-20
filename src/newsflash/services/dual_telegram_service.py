"""
Dual Telegram notification service for news alerts.
Supports sending to both primary and secondary Telegram bots.
"""
import asyncio
from typing import Optional, List, Union
from datetime import datetime
import structlog
from telegram import Bot
from telegram.error import TelegramError

from ..models.base_models import StandardizedArticle
from ..models.benzinga_models import BenzingaArticle
from ..models.classification_models import NewsClassification, ClassificationResult
from ..config.settings import get_telegram_config, get_telegram_config_2

logger = structlog.get_logger(__name__)


class DualTelegramNotifier:
    """
    Service for sending formatted news alerts to both Telegram bots.
    
    Supports both real bot integration and test mode (writing to JSON).
    """
    
    def __init__(
        self,
        test_mode: bool = False,
    ):
        """
        Initialize dual Telegram notifier.
        
        Args:
            test_mode: If True, write to JSON instead of sending to Telegram
        """
        self.test_mode = test_mode
        
        # Get configuration for both bots
        self.config_1 = get_telegram_config()
        self.config_2 = get_telegram_config_2()
        
        # Initialize bot 1
        self.bot_1: Optional[Bot] = None
        self.enabled_1 = self.config_1["enabled"]
        if not test_mode and self.config_1["bot_token"] and self.enabled_1:
            self.bot_1 = Bot(token=self.config_1["bot_token"])
            logger.info("Primary Telegram bot initialized", chat_id=self.config_1["chat_id"])
        
        # Initialize bot 2
        self.bot_2: Optional[Bot] = None
        self.enabled_2 = self.config_2["enabled"]
        if not test_mode and self.config_2["bot_token"] and self.enabled_2:
            self.bot_2 = Bot(token=self.config_2["bot_token"])
            logger.info("Secondary Telegram bot initialized", chat_id=self.config_2["chat_id"])
        
        # Message queues for both bots
        self.message_queue_1: asyncio.Queue = asyncio.Queue()
        self.message_queue_2: asyncio.Queue = asyncio.Queue()
        self.is_running = False
        
        if test_mode:
            logger.info("Dual Telegram service in test mode - messages will be logged only")
        else:
            total_bots = sum([self.enabled_1, self.enabled_2])
            logger.info(f"Dual Telegram service initialized with {total_bots} active bots")
    
    def format_message(
        self,
        article: Union[BenzingaArticle, StandardizedArticle],
        classification: Optional[ClassificationResult] = None,
    ) -> str:
        """
        Format article into Telegram message.
        
        Args:
            article: Article to format
            classification: Optional classification result
            
        Returns:
            Formatted message string
        """
        # Get classification emoji and label
        if classification:
            if classification.classification == NewsClassification.IMMINENT:
                emoji = "🚨"
                label = "IMMINENT"
                confidence = classification.confidence
                header = f"{emoji} {label} | {confidence} CONFIDENCE"
            else:
                # IGNORE classification - should never reach here
                logger.error("IGNORE classification sent to Telegram - this is a bug!")
                return ""  # Return empty string to prevent sending
        else:
            # No classification provided - this is an error, should not happen
            logger.error("Article sent to Telegram without classification - this is a bug!")
            return ""  # Return empty string to prevent sending
        
        # Extract tickers
        if isinstance(article, BenzingaArticle):
            tickers = article.tickers
            title = article.title
            url = article.url or "No URL available"
            source = "Benzinga"
        else:  # StandardizedArticle
            tickers = article.tickers
            title = article.title
            url = article.url or "No URL available"
            source = article.source.value.title()
        
        # Format tickers
        ticker_line = ", ".join(tickers) if tickers else "No tickers"
        
        # Build message
        message_parts = [
            header,
            ticker_line,
            title,
            f"🔗 {url}",
            f"📡 Source: {source}",
        ]
        
        return "\n".join(message_parts)
    
    async def send_notification(
        self,
        article: Union[BenzingaArticle, StandardizedArticle],
        classification: Optional[ClassificationResult] = None,
    ) -> bool:
        """
        Send notification for an article to both bots.
        
        Args:
            article: Article to notify about
            classification: Optional classification result
            
        Returns:
            True if notification was sent/queued successfully to at least one bot
        """
        # Format message
        message = self.format_message(article, classification)
        
        if not message:  # Empty message means we shouldn't send
            return False
        
        # In test mode, just log the message
        if self.test_mode:
            logger.info(
                "TEST MODE: Would send Telegram message to both bots",
                message=message,
                article_id=getattr(article, 'benzinga_id', getattr(article, 'source_id', 'unknown'))
            )
            return True
        
        # Queue messages for both bots
        success_count = 0
        
        if self.enabled_1:
            await self.message_queue_1.put((message, article))
            success_count += 1
            logger.debug("Message queued for primary Telegram bot")
        
        if self.enabled_2:
            await self.message_queue_2.put((message, article))
            success_count += 1
            logger.debug("Message queued for secondary Telegram bot")
        
        return success_count > 0
    
    async def _send_message(self, bot: Bot, chat_id: str, message: str, bot_name: str) -> bool:
        """
        Actually send message to Telegram.
        
        Args:
            bot: Telegram bot instance
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
                parse_mode=None,  # Plain text for now
                disable_web_page_preview=False,
            )
            logger.info(f"{bot_name} message sent successfully")
            return True
            
        except TelegramError as e:
            logger.error(f"Failed to send {bot_name} message", error=str(e))
            return False
    
    async def _process_queue(self, queue: asyncio.Queue, bot: Bot, chat_id: str, bot_name: str) -> None:
        """
        Process queued messages for a specific bot.
        
        Args:
            queue: Message queue for this bot
            bot: Telegram bot instance
            chat_id: Chat ID to send to
            bot_name: Name of the bot (for logging)
        """
        while self.is_running:
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
                success = await self._send_message(bot, chat_id, message, bot_name)
                
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
    
    async def start(self) -> None:
        """Start the dual Telegram notification service."""
        if self.test_mode:
            logger.info("Dual Telegram service not started (test mode)")
            return
        
        if self.is_running:
            logger.warning("Dual Telegram service already running")
            return
        
        self.is_running = True
        logger.info("Dual Telegram notification service started")
        
        # Start queue processors for both bots
        tasks = []
        
        if self.enabled_1 and self.bot_1:
            task_1 = asyncio.create_task(
                self._process_queue(
                    self.message_queue_1,
                    self.bot_1,
                    self.config_1["chat_id"],
                    "Primary Bot"
                )
            )
            tasks.append(task_1)
        
        if self.enabled_2 and self.bot_2:
            task_2 = asyncio.create_task(
                self._process_queue(
                    self.message_queue_2,
                    self.bot_2,
                    self.config_2["chat_id"],
                    "Secondary Bot"
                )
            )
            tasks.append(task_2)
        
        if tasks:
            # Wait for all tasks to complete (they run until self.is_running = False)
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def stop(self) -> None:
        """Stop the dual Telegram notification service."""
        if not self.is_running:
            return
        
        logger.info("Stopping dual Telegram notification service")
        self.is_running = False
        
        # Process remaining messages in both queues
        for queue, bot, chat_id, bot_name in [
            (self.message_queue_1, self.bot_1, self.config_1["chat_id"], "Primary Bot"),
            (self.message_queue_2, self.bot_2, self.config_2["chat_id"], "Secondary Bot")
        ]:
            while not queue.empty():
                try:
                    message, _ = queue.get_nowait()
                    await self._send_message(bot, chat_id, message, bot_name)
                except asyncio.QueueEmpty:
                    break
                except Exception as e:
                    logger.error(f"Error sending queued message during shutdown ({bot_name})", error=str(e))
        
        logger.info("Dual Telegram notification service stopped")
