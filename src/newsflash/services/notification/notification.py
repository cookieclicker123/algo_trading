"""
Telegram notification service for news alerts.
Supports sending to both primary and secondary Telegram bots.
"""
import asyncio
from typing import Optional, Union
from telegram import Bot

from ...models.base_models import StandardizedArticle
from ...models.benzinga_models import BenzingaArticle
from ...models.classification_models import NewsClassification, ClassificationResult
from ...utils.logging_config import get_logger
from .message_formatter import (
    format_message_data,
    format_telegram_message,
    format_trading_options,
)
from .queue_processor import process_message_queue, drain_queue_on_shutdown
from .message_sender import send_telegram_message, send_to_all_bots

logger = get_logger(__name__)


class TelegramNotifier:
    """
    Service for sending formatted news alerts to both Telegram bots.
    
    Supports both real bot integration and test mode (writing to JSON).
    """
    
    def __init__(
        self,
        telegram_config_1: dict,
        telegram_config_2: dict,
        test_mode: bool = False,
        trade_handler=None,
        trade_handler_2=None,
    ):
        """
        Initialize Telegram notifier.
        
        Args:
            telegram_config_1: Configuration dict for primary Telegram bot
            telegram_config_2: Configuration dict for secondary Telegram bot
            test_mode: If True, write to JSON instead of sending to Telegram
            trade_handler: Optional trade handler (injected dependency)
        """
        self.test_mode = test_mode
        
        # Use injected configuration
        self.config_1 = telegram_config_1
        self.config_2 = telegram_config_2
        # Trade handlers for each bot (optional)
        self.trade_handler = trade_handler
        self.trade_handler_2 = trade_handler_2
        
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
        # Queue processing control (operational state for async tasks)
        self._queue_processing_active = False
        
        if test_mode:
            logger.info("Telegram service in test mode - messages will be logged only")
        else:
            total_bots = sum([self.enabled_1, self.enabled_2])
            logger.info(f"Telegram service initialized with {total_bots} active bots")
    
    async def format_message_data(
        self,
        article: Union[BenzingaArticle, StandardizedArticle],
        classification: Optional[ClassificationResult] = None,
    ) -> dict:
        """
        Format article and classification into message data structure.
        
        Uses pure function from message_formatter service.
        
        Args:
            article: The article to format
            classification: The classification result
            
        Returns:
            Message data dictionary
        """
        return format_message_data(article, classification)
    
    def format_message(self, message_data: dict) -> str:
        """
        Format message data into Telegram message string.
        
        Uses pure function from message_formatter service.
        
        Args:
            message_data: The message data dictionary
            
        Returns:
            Formatted message string
        """
        return format_telegram_message(message_data)
    
    def _format_trading_options(self, tickers: list) -> str:
        """Format trading options for IMMINENT news - uses pure function."""
        return format_trading_options(tickers)
    
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
        # Format message data
        message_data = await self.format_message_data(article, classification)
        
        if not message_data:  # Empty data means we shouldn't send
            return False
        
        # In test mode, just log the message
        if self.test_mode:
            english_message = self.format_message(message_data)
            logger.info(
                "TEST MODE: Would send Telegram message to both bots",
                message=english_message,
                article_id=getattr(article, 'benzinga_id', getattr(article, 'source_id', 'unknown'))
            )
            return True
        
        # Queue messages for both bots
        success_count = 0
        
        # Extract tickers for trading options
        if isinstance(article, BenzingaArticle):
            tickers = article.tickers
            article_id = str(article.benzinga_id)
        else:  # StandardizedArticle
            tickers = article.tickers
            article_id = str(article.source_id)
        
        # Create pending trades for IMMINENT news
        # Note: Pending trade tracking removed - new brokerage service doesn't track pending trades
        # Trades are executed immediately via auto-trade use case or queued if market is closed
        
        if self.enabled_1:
            english_message = self.format_message(message_data)
            
            # Add trading options for IMMINENT news
            if classification and classification.classification == NewsClassification.IMMINENT:
                english_message += format_trading_options(tickers)
            
            await self.message_queue_1.put((english_message, article))
            success_count += 1
            logger.debug("English message queued for primary Telegram bot")
        
        if self.enabled_2:
            # Bot 2 gets English message
            english_message = self.format_message(message_data)
            # Add trading options for IMMINENT news
            if classification and classification.classification == NewsClassification.IMMINENT:
                english_message += format_trading_options(tickers)
            await self.message_queue_2.put((english_message, article))
            success_count += 1
            logger.debug("English message queued for secondary Telegram bot")
        
        return success_count > 0
    
    async def _send_message(self, bot: Bot, chat_id: str, message: str, bot_name: str) -> bool:
        """
        Actually send message to Telegram.
        
        Delegates to stateless helper function.
        
        Args:
            bot: Telegram bot instance
            chat_id: Chat ID to send to
            message: Message text to send
            bot_name: Name of the bot (for logging)
            
        Returns:
            True if sent successfully
        """
        return await send_telegram_message(bot, chat_id, message, bot_name)
    
    async def _process_queue(self, queue: asyncio.Queue, bot: Bot, chat_id: str, bot_name: str) -> None:
        """
        Process queued messages for a specific bot.
        
        Delegates to stateless helper function.
        
        Args:
            queue: Message queue for this bot
            bot: Telegram bot instance
            chat_id: Chat ID to send to
            bot_name: Name of the bot (for logging)
        """
        await process_message_queue(
            queue=queue,
            bot=bot,
            chat_id=chat_id,
            bot_name=bot_name,
            queue_processing_active=self._queue_processing_active,
            send_message_func=self._send_message
        )
    
    async def start(self) -> None:
        """Start the Telegram notification service."""
        if self.test_mode:
            logger.info("Telegram service not started (test mode)")
            return
        
        logger.info("Telegram notification service started")
        
        # Start queue processing (operational state)
        self._queue_processing_active = True
        
        # Start trade handlers with staggered timing to prevent conflicts
        # Only start if the bot is enabled (defensive check)
        if self.trade_handler and self.enabled_1:
            await self.trade_handler.start()
            logger.info("Started Telegram trade handler (bot 1)")
            
            # Wait a moment before starting the second bot to prevent conflicts
            await asyncio.sleep(3)
        elif self.trade_handler:
            logger.info("Skipping trade handler 1 start (bot 1 disabled)")
        
        if self.trade_handler_2 and self.enabled_2:
            # Retry logic for the second bot in case of conflicts
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await self.trade_handler_2.start()
                    logger.info("Started Telegram trade handler (bot 2)")
                    break
                except Exception as e:
                    if "Conflict" in str(e) and attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2  # Exponential backoff: 2, 4, 6 seconds
                        logger.warning(f"Bot 2 conflict on attempt {attempt + 1}, retrying in {wait_time}s", error=str(e))
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error("Failed to start bot 2 after all retries", error=str(e))
                        raise
        
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
            # Start queue processors in background (they run until self._queue_processing_active = False)
            self._queue_tasks = tasks
            logger.info("Started queue processors in background")
    
    async def stop(self) -> None:
        """
        Stop the Telegram notification service.
        
        Idempotent: Safe to call multiple times.
        """
        logger.info("Stopping Telegram notification service")
        
        # Stop queue processing (operational state)
        self._queue_processing_active = False
        
        # Cancel background queue tasks
        if hasattr(self, '_queue_tasks'):
            for task in self._queue_tasks:
                task.cancel()
            logger.info("Cancelled queue processor tasks")
        
        # Stop trade handlers
        if self.trade_handler:
            await self.trade_handler.stop()
            logger.info("Stopped Telegram trade handler (bot 1)")
        if self.trade_handler_2:
            await self.trade_handler_2.stop()
            logger.info("Stopped Telegram trade handler (bot 2)")
        
        # Process remaining messages in both queues
        await drain_queue_on_shutdown(
            self.message_queue_1, self.bot_1, self.config_1["chat_id"], "Primary Bot", self._send_message
        )
        await drain_queue_on_shutdown(
            self.message_queue_2, self.bot_2, self.config_2["chat_id"], "Secondary Bot", self._send_message
        )
        
        logger.info("Telegram notification service stopped")
    
    async def _send_message_to_all_bots(self, message: str) -> None:
        """
        Send a plain text message to all enabled bots.
        
        Delegates to stateless helper function.
        
        Args:
            message: Message text to send
        """
        await send_to_all_bots(
            bot_1=self.bot_1,
            config_1=self.config_1,
            enabled_1=self.enabled_1,
            bot_2=self.bot_2,
            config_2=self.config_2,
            enabled_2=self.enabled_2,
            message=message,
            send_message_func=self._send_message
        )
