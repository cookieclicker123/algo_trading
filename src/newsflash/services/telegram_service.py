"""
Telegram notification service for news alerts.
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

logger = structlog.get_logger(__name__)


class TelegramNotifier:
    """
    Service for sending formatted news alerts to Telegram.
    
    Supports both real bot integration and test mode (writing to JSON).
    """
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
        test_mode: bool = False,
    ):
        """
        Initialize Telegram notifier.
        
        Args:
            bot_token: Telegram bot token from BotFather
            chat_id: Telegram chat ID to send messages to
            enabled: Whether notifications are enabled
            test_mode: If True, write to JSON instead of sending to Telegram
        """
        self.enabled = enabled
        self.test_mode = test_mode
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot: Optional[Bot] = None
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self.is_running = False
        
        # Initialize bot if not in test mode and credentials provided
        if not test_mode and bot_token and enabled:
            self.bot = Bot(token=bot_token)
            logger.info("Telegram bot initialized", chat_id=chat_id)
        elif test_mode:
            logger.info("Telegram service in test mode - messages will be logged only")
        else:
            logger.info("Telegram notifications disabled")
    
    def format_message(
        self,
        article: Union[BenzingaArticle, StandardizedArticle],
        classification: Optional[ClassificationResult] = None,
    ) -> str:
        """
        Format article into Telegram message.
        
        Args:
            article: Article to format
            classification: Optional classification result (for Phase 2)
            
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
                # (should be filtered before sending)
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
        Send notification for an article.
        
        Args:
            article: Article to notify about
            classification: Optional classification result
            
        Returns:
            True if notification was sent/queued successfully
        """
        if not self.enabled:
            logger.debug("Telegram notifications disabled, skipping")
            return False
        
        # Format message
        message = self.format_message(article, classification)
        
        # In test mode, just log the message
        if self.test_mode:
            logger.info(
                "TEST MODE: Would send Telegram message",
                message=message,
                article_id=getattr(article, 'benzinga_id', getattr(article, 'source_id', 'unknown'))
            )
            return True
        
        # Queue message for sending
        await self.message_queue.put((message, article))
        logger.debug("Message queued for Telegram", article_count=self.message_queue.qsize())
        
        return True
    
    async def _send_message(self, message: str) -> bool:
        """
        Actually send message to Telegram.
        
        Args:
            message: Message text to send
            
        Returns:
            True if sent successfully
        """
        if not self.bot or not self.chat_id:
            logger.error("Telegram bot not configured")
            return False
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=None,  # Plain text for now
                disable_web_page_preview=False,
            )
            logger.info("Telegram message sent successfully")
            return True
            
        except TelegramError as e:
            logger.error("Failed to send Telegram message", error=str(e))
            return False
    
    async def _process_queue(self) -> None:
        """
        Process queued messages.
        
        Implements rate limiting to avoid hitting Telegram API limits.
        """
        while self.is_running:
            try:
                # Get message from queue (with timeout to allow shutdown)
                try:
                    message, article = await asyncio.wait_for(
                        self.message_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Send message
                success = await self._send_message(message)
                
                if success:
                    logger.info(
                        "Telegram notification sent",
                        article_id=getattr(article, 'benzinga_id', getattr(article, 'source_id', 'unknown'))
                    )
                else:
                    logger.warning(
                        "Failed to send Telegram notification",
                        article_id=getattr(article, 'benzinga_id', getattr(article, 'source_id', 'unknown'))
                    )
                
                # Rate limit: Telegram allows 30 messages/second, use 20 to be safe
                await asyncio.sleep(0.05)  # 50ms = 20 messages/second
                
            except Exception as e:
                logger.error("Error processing Telegram queue", error=str(e))
                await asyncio.sleep(1.0)
    
    async def start(self) -> None:
        """Start the Telegram notification service."""
        if not self.enabled or self.test_mode:
            logger.info("Telegram service not started (disabled or test mode)")
            return
        
        if self.is_running:
            logger.warning("Telegram service already running")
            return
        
        self.is_running = True
        logger.info("Telegram notification service started")
        
        # Start queue processor
        await self._process_queue()
    
    async def stop(self) -> None:
        """Stop the Telegram notification service."""
        if not self.is_running:
            return
        
        logger.info("Stopping Telegram notification service")
        self.is_running = False
        
        # Process remaining messages in queue
        while not self.message_queue.empty():
            try:
                message, _ = self.message_queue.get_nowait()
                await self._send_message(message)
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error("Error sending queued message during shutdown", error=str(e))
        
        logger.info("Telegram notification service stopped")

