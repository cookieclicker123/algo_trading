"""
Telegram notification service for news alerts.
Supports sending to both primary and secondary Telegram bots.
"""
import asyncio
from typing import Optional, Union
from telegram import Bot
from telegram.error import TelegramError

from ..models.base_models import StandardizedArticle
from ..models.benzinga_models import BenzingaArticle
from ..models.classification_models import NewsClassification, ClassificationResult
from ..config.settings import get_telegram_config, get_telegram_config_2
from ..utils.logging_config import get_logger
# Removed imports to prevent circular dependencies - use dependency injection instead

logger = get_logger(__name__)


class TelegramNotifier:
    """
    Service for sending formatted news alerts to both Telegram bots.
    
    Supports both real bot integration and test mode (writing to JSON).
    """
    
    def __init__(
        self,
        test_mode: bool = False,
        translator=None,
        yfinance_service=None,
        trade_handler=None,
        trade_handler_2=None,
    ):
        """
        Initialize Telegram notifier.
        
        Args:
            test_mode: If True, write to JSON instead of sending to Telegram
            translator: Optional translation service (injected dependency)
            yfinance_service: Optional yfinance service (injected dependency)
            trade_handler: Optional trade handler (injected dependency)
        """
        self.test_mode = test_mode
        
        # Get configuration for both bots
        self.config_1 = get_telegram_config()
        self.config_2 = get_telegram_config_2()
        
        # Use injected dependencies or create defaults
        self.translator = translator
        self.yfinance_service = yfinance_service
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
        self.is_running = False
        
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
        
        Args:
            article: The article to format
            classification: The classification result
            
        Returns:
            Message data dictionary
        """
        # Get classification emoji and label
        if classification:
            if classification.classification == NewsClassification.IMMINENT:
                emoji = "🚨"
                label = "IMMINENT"
                confidence = classification.confidence
            else:
                # IGNORE classification - should never reach here
                logger.error("IGNORE classification sent to Telegram - this is a bug!")
                return {}  # Return empty dict to prevent sending
        else:
            # No classification provided - this is an error, should not happen
            logger.error("Article sent to Telegram without classification - this is a bug!")
            return {}  # Return empty dict to prevent sending
        
        # Extract tickers
        if isinstance(article, BenzingaArticle):
            tickers = article.tickers
            title = article.title
            url = article.url or "No URL available"
            source = "Benzinga (REST)"
        else:  # StandardizedArticle
            tickers = article.tickers
            title = article.title
            url = article.url or "No URL available"
            # Format source nicely: "benzinga_websocket" -> "Benzinga WebSocket"
            source_map = {
                "benzinga": "Benzinga (REST)",
                "benzinga_websocket": "Benzinga WebSocket"
            }
            source = source_map.get(article.source.value, article.source.value.replace('_', ' ').title())
        
        # Format tickers with "Company Symbol:" prefix
        ticker_display = f"Company Symbol: '{', '.join(tickers)}'" if tickers else "Company Symbol: 'N/A'"
        
        # Format publication timestamp (already in UTC from API)
        published_gmt = article.published.strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Get fundamental data for the first ticker (if available)
        fundamental_data = None
        if tickers and len(tickers) > 0:
            try:
                fundamental_data = await self.yfinance_service.get_fundamental_data(tickers[0])
                logger.info("Fundamental data fetched", ticker=tickers[0])
            except Exception as e:
                logger.error("Failed to fetch fundamental data", ticker=tickers[0], error=str(e))
        
        # Build message data
        message_data = {
            "emoji": emoji,
            "classification": label,
            "confidence": confidence,
            "tickers": ticker_display,
            "headline": title,
            "url": url,
            "source": source,
            "published_gmt": published_gmt,
            "fundamental_data": fundamental_data
        }
        
        return message_data
    
    def format_message(self, message_data: dict) -> str:
        """
        Format message data into Telegram message string.
        
        Args:
            message_data: The message data dictionary
            
        Returns:
            Formatted message string
        """
        if not message_data:
            return ""
            
        header = f"{message_data['emoji']} {message_data['classification']} | {message_data['confidence']} CONFIDENCE"
        
        message_parts = [
            header,
            message_data["tickers"],
            message_data["headline"],
            f"🔗 {message_data['url']}",
            f"📡 Source: {message_data['source']}",
            f"🕐 Published: {message_data.get('published_gmt', 'Unknown')} GMT",
        ]
        
        # Add fundamental data if available
        fundamental_data = message_data.get('fundamental_data')
        if fundamental_data:
            message_parts.extend([
                "",
                "📊 FUNDAMENTAL DATA:",
                f"💰 Price: {fundamental_data['price_volume']['current_price']} ({fundamental_data['price_volume']['price_change_10min']})",
                f"💵 Earnings: {fundamental_data['earnings']['current_earnings']} ({fundamental_data['earnings']['earnings_growth']})",
                f"📈 Revenue: {fundamental_data['revenue']['current_revenue']} ({fundamental_data['revenue']['revenue_growth']})",
                f"📊 Margins: Gross {fundamental_data['margins']['gross_margin']}, Net {fundamental_data['margins']['net_margin']}",
                f"📊 Volume: {fundamental_data['price_volume']['current_volume']} ({fundamental_data['price_volume']['volume_change_10min']})"
            ])
        
        return "\n".join(message_parts)
    
    def _format_trading_options(self, tickers: list) -> str:
        """Format trading options for IMMINENT news."""
        if not tickers:
            return ""
        
        ticker_list = ", ".join(tickers) if len(tickers) > 1 else tickers[0]
        
        trading_options = (
            f"\n\n🎯 TRADING OPTIONS:\n"
            f"📊 Tickers: {ticker_list}\n"
            f"💰 Amount: $100 per trade\n"
            f"⏰ Reply within 30 minutes\n\n"
            f"Reply with:\n"
            f"• 'trade' - Trade default ticker\n"
            f"• 'trade {tickers[0]}' - Trade specific ticker\n"
            f"• 'ignore' - Ignore this news\n"
            f"• No reply = ignore"
        )
        
        return trading_options
    
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
        if classification and classification.classification == NewsClassification.IMMINENT:
            if self.trade_handler and self.trade_handler.trading_service:
                self.trade_handler.trading_service.add_pending_trade(
                    article_id=article_id,
                    tickers=tickers,
                    user_chat_id=self.config_1["chat_id"]
                )
                logger.info("Created pending trade for bot 1", article_id=article_id, tickers=tickers)
            
            if self.trade_handler_2 and self.trade_handler_2.trading_service:
                self.trade_handler_2.trading_service.add_pending_trade(
                    article_id=article_id,
                    tickers=tickers,
                    user_chat_id=self.config_2["chat_id"]
                )
                logger.info("Created pending trade for bot 2", article_id=article_id, tickers=tickers)
        
        if self.enabled_1:
            # Bot 1 gets Chinese translation
            if self.translator:
                try:
                    chinese_message_data = await self.translator.translate_to_chinese(message_data)
                    chinese_message = self.format_message(chinese_message_data)
                    
                    # Add trading options for IMMINENT news
                    if classification and classification.classification == NewsClassification.IMMINENT:
                        chinese_message += self._format_trading_options(tickers)
                    
                    await self.message_queue_1.put((chinese_message, article))
                    success_count += 1
                    logger.debug("Chinese message queued for primary Telegram bot")
                except Exception as e:
                    logger.error("Failed to translate message for bot 1", error=str(e))
                    # Fallback to English
                    english_message = self.format_message(message_data)
                    
                    # Add trading options for IMMINENT news
                    if classification and classification.classification == NewsClassification.IMMINENT:
                        english_message += self._format_trading_options(tickers)
                    
                    await self.message_queue_1.put((english_message, article))
                    success_count += 1
            else:
                # No translator available, send English
                english_message = self.format_message(message_data)
                
                # Add trading options for IMMINENT news
                if classification and classification.classification == NewsClassification.IMMINENT:
                    english_message += self._format_trading_options(tickers)
                
                await self.message_queue_1.put((english_message, article))
                success_count += 1
        
        if self.enabled_2:
            # Bot 2 gets English message
            english_message = self.format_message(message_data)
            # Add trading options for IMMINENT news
            if classification and classification.classification == NewsClassification.IMMINENT:
                english_message += self._format_trading_options(tickers)
            await self.message_queue_2.put((english_message, article))
            success_count += 1
            logger.debug("English message queued for secondary Telegram bot")
        
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
                parse_mode="Markdown",  # Support Markdown formatting
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
        """Start the Telegram notification service."""
        if self.test_mode:
            logger.info("Telegram service not started (test mode)")
            return
        
        if self.is_running:
            logger.warning("Telegram service already running")
            return
        
        self.is_running = True
        logger.info("Telegram notification service started")
        
        # Start trade handlers with staggered timing to prevent conflicts
        if self.trade_handler:
            await self.trade_handler.start()
            logger.info("Started Telegram trade handler (bot 1)")
            
            # Wait a moment before starting the second bot to prevent conflicts
            await asyncio.sleep(3)
        
        if self.trade_handler_2:
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
            # Start queue processors in background (they run until self.is_running = False)
            self._queue_tasks = tasks
            logger.info("Started queue processors in background")
    
    async def stop(self) -> None:
        """Stop the Telegram notification service."""
        if not self.is_running:
            return
        
        logger.info("Stopping Telegram notification service")
        self.is_running = False
        
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
        
        logger.info("Telegram notification service stopped")
    
    async def _send_message_to_all_bots(self, message: str) -> None:
        """
        Send a plain text message to all enabled bots.
        
        Args:
            message: Message text to send
        """
        if self.enabled_1 and self.bot_1 and self.config_1:
            try:
                await self._send_message(
                    self.bot_1, 
                    self.config_1["chat_id"], 
                    message, 
                    "Bot 1"
                )
            except Exception as e:
                logger.error("Failed to send message to Bot 1", error=str(e))
        
        if self.enabled_2 and self.bot_2 and self.config_2:
            try:
                await self._send_message(
                    self.bot_2, 
                    self.config_2["chat_id"], 
                    message, 
                    "Bot 2"
                )
            except Exception as e:
                logger.error("Failed to send message to Bot 2", error=str(e))


def get_telegram_notifier(
    test_mode: bool = False,
    translator=None,
    yfinance_service=None,
    trade_handler=None,
    trade_handler_2=None,
) -> TelegramNotifier:
    """
    Get Telegram notifier instance with optional dependencies.
    
    Args:
        test_mode: If True, write to JSON instead of sending to Telegram
        translator: Optional translation service (injected dependency)
        yfinance_service: Optional yfinance service (injected dependency)
        trade_handler: Optional trade handler (injected dependency)
        
    Returns:
        TelegramNotifier instance
    """
    return TelegramNotifier(
        test_mode=test_mode,
        translator=translator,
        yfinance_service=yfinance_service,
        trade_handler=trade_handler,
        trade_handler_2=trade_handler_2,
    )
