"""
Telegram bot handler for processing user trade decisions.
Handles replies to IMMINENT news messages.
"""
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from .ibkr_trading_service import get_ibkr_trading_service, TradeRequest
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class TelegramTradeHandler:
    """
    Handles Telegram bot interactions for trade decisions.
    Processes user replies to IMMINENT news messages.
    """
    
    _instances = {}  # Class variable to store singleton instances
    
    def __new__(cls, bot_token: str, trading_service=None):
        """
        Singleton pattern - only one instance per bot token.
        
        Args:
            bot_token: Telegram bot token
            trading_service: IBKR trading service instance
            
        Returns:
            Existing instance if available, otherwise new instance
        """
        if bot_token not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[bot_token] = instance
        return cls._instances[bot_token]
    
    def __init__(self, bot_token: str, trading_service=None):
        """
        Initialize Telegram trade handler.
        
        Args:
            bot_token: Telegram bot token
            trading_service: IBKR trading service instance
        """
        # Only initialize if not already initialized
        if hasattr(self, 'bot_token'):
            return
            
        self.bot_token = bot_token
        self.trading_service = trading_service or get_ibkr_trading_service()
        self.application = None
        self.is_running = False
        
        logger.info("Telegram trade handler initialized", bot_token=bot_token[:10] + "...")
    
    async def start(self):
        """Start the Telegram bot handler."""
        try:
            # Stop any existing instance first to prevent conflicts
            if self.is_running:
                await self.stop()
            
            # Create application
            self.application = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            
            # Start polling
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            
            self.is_running = True
            logger.info("Telegram trade handler started")
            
        except Exception as e:
            logger.error("Failed to start Telegram trade handler", error=str(e))
            raise
    
    async def stop(self):
        """Stop the Telegram bot handler."""
        if self.application and self.is_running:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            self.is_running = False
            logger.info("Telegram trade handler stopped")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await update.message.reply_text(
            "🤖 NewsFlash Trading Bot\n\n"
            "I'll send you IMMINENT news alerts with trading options.\n"
            "Reply with:\n"
            "• 'trade' - Trade the default ticker\n"
            "• 'trade TICKER' - Trade specific ticker\n"
            "• 'ignore' - Ignore the news\n"
            "• No reply - Defaults to ignore\n\n"
            "Use /help for more info."
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        await update.message.reply_text(
            "📖 Trading Commands:\n\n"
            "When you receive IMMINENT news:\n"
            "• Reply 'trade' to buy $100 of the default ticker\n"
            "• Reply 'trade AAPL' to buy $100 of specific ticker\n"
            "• Reply 'ignore' to ignore the news\n"
            "• No reply = ignore (default)\n\n"
            "⏰ You have 30 minutes to decide before the next news arrives.\n"
            "💰 Each trade is $100 USD.\n"
            "🎯 Only IMMINENT news triggers trading options."
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages (trade decisions)."""
        try:
            user_chat_id = str(update.effective_chat.id)
            message_text = update.message.text
            
            logger.info("Received user message", 
                       chat_id=user_chat_id,
                       message=message_text)
            
            # Process the trade decision
            trade_request = self.trading_service.process_user_response(user_chat_id, message_text)
            
            if trade_request:
                # User wants to trade
                await self._handle_trade_request(update, trade_request)
            else:
                # User wants to ignore or invalid response
                await self._handle_ignore_or_invalid(update, message_text)
                
        except Exception as e:
            logger.error("Error handling user message", error=str(e))
            await update.message.reply_text(
                "❌ Error processing your request. Please try again."
            )
    
    async def _handle_trade_request(self, update: Update, trade_request: TradeRequest):
        """Handle a trade request."""
        try:
            # Send confirmation message
            await update.message.reply_text(
                f"🚀 Executing trade:\n"
                f"📈 Ticker: {trade_request.ticker}\n"
                f"💰 Amount: ${trade_request.amount_usd}\n"
                f"📊 Action: {trade_request.action}\n\n"
                f"⏳ Processing..."
            )
            
            # Execute the trade
            success = await self.trading_service.process_trade_request(trade_request)
            
            if success:
                await update.message.reply_text(
                    f"✅ Trade executed successfully!\n"
                    f"📈 {trade_request.ticker}: ${trade_request.amount_usd} {trade_request.action}\n"
                    f"🎯 Trade ID: {trade_request.article_id}"
                )
            else:
                await update.message.reply_text(
                    f"❌ Trade execution failed.\n"
                    f"📈 {trade_request.ticker}: ${trade_request.amount_usd} {trade_request.action}\n\n"
                    f"🔍 Check the server logs for detailed error information.\n"
                    f"Common issues:\n"
                    f"• Market closed (use limit orders)\n"
                    f"• Insufficient buying power\n"
                    f"• Invalid ticker symbol\n"
                    f"• IBKR Gateway connection issues"
                )
                
        except Exception as e:
            logger.error("Error executing trade", error=str(e))
            await update.message.reply_text(
                "❌ Error executing trade. Please try again later."
            )
    
    async def _handle_ignore_or_invalid(self, update: Update, message_text: str):
        """Handle ignore or invalid responses."""
        message_text = message_text.strip().lower()
        
        if message_text == "ignore":
            await update.message.reply_text(
                "👌 News ignored. Waiting for next IMMINENT alert..."
            )
        else:
            # Check if it's a "trade" command with no pending trade
            if message_text == "trade":
                await update.message.reply_text(
                    "📈 No recent news to trade. You can trade any ticker:\n\n"
                    "• 'trade AAPL' - Trade Apple stock\n"
                    "• 'trade MSFT' - Trade Microsoft stock\n"
                    "• 'trade TSLA' - Trade Tesla stock\n\n"
                    "Or wait for the next IMMINENT news alert!"
                )
            else:
                await update.message.reply_text(
                    "❓ Invalid response. Please reply with:\n"
                    "• 'trade' - Trade default ticker (if recent news)\n"
                    "• 'trade TICKER' - Trade specific ticker (any time)\n"
                    "• 'ignore' - Ignore news\n\n"
                    "Use /help for more info."
                )
    
    async def send_imminent_alert(self, chat_id: str, message_text: str, tickers: list):
        """
        Send IMMINENT news alert with trading options.
        
        Args:
            chat_id: Telegram chat ID
            message_text: Formatted news message
            tickers: List of tickers for trading
        """
        try:
            # Send the news message
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode='HTML'
            )
            
            # Add trading options message
            ticker_list = ", ".join(tickers) if len(tickers) > 1 else tickers[0]
            trading_message = (
                f"\n🎯 TRADING OPTIONS:\n"
                f"📊 Tickers: {ticker_list}\n"
                f"💰 Amount: $100 per trade\n"
                f"⏰ Reply within 30 minutes\n\n"
                f"Reply with:\n"
                f"• 'trade' - Trade default ticker\n"
                f"• 'trade {tickers[0]}' - Trade specific ticker\n"
                f"• 'ignore' - Ignore this news\n"
                f"• No reply = ignore"
            )
            
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=trading_message
            )
            
            # Add to pending trades
            article_id = f"imminent_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.trading_service.add_pending_trade(article_id, tickers, chat_id)
            
            logger.info("Sent IMMINENT alert with trading options",
                       chat_id=chat_id,
                       tickers=tickers)
            
        except Exception as e:
            logger.error("Failed to send IMMINENT alert", 
                        chat_id=chat_id,
                        error=str(e))


def get_telegram_trade_handler(bot_token: str, trading_service=None) -> TelegramTradeHandler:
    """
    Get Telegram trade handler instance.
    
    Args:
        bot_token: Telegram bot token
        trading_service: Optional IBKR trading service instance
        
    Returns:
        TelegramTradeHandler instance
    """
    return TelegramTradeHandler(bot_token, trading_service)


def clear_trade_handler_instances():
    """Clear all trade handler instances (for testing)."""
    TelegramTradeHandler._instances.clear()
    logger.info("Cleared all Telegram trade handler instances")


async def stop_all_trade_handlers():
    """Stop all running trade handler instances and clear webhooks."""
    import asyncio
    import subprocess
    import time
    
    # First, kill any existing Python processes that might be using these bots
    try:
        # Find processes using our bot tokens
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        
        bot_tokens = list(TelegramTradeHandler._instances.keys())
        for line in lines:
            for token in bot_tokens:
                if 'python' in line.lower() and token[:10] in line:
                    pid = line.split()[1]
                    try:
                        subprocess.run(['kill', '-9', pid], check=True)
                        logger.info("Killed conflicting process", pid=pid, token=token[:10] + "...")
                    except:
                        pass
    except Exception as e:
        logger.warning("Failed to kill conflicting processes", error=str(e))
    
    # Wait a moment for processes to die
    await asyncio.sleep(2)
    
    # Stop all instances
    stop_tasks = []
    for token, instance in TelegramTradeHandler._instances.items():
        if instance.is_running:
            stop_tasks.append(instance.stop())
    
    if stop_tasks:
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        logger.info("Stopped all running trade handler instances")
    
    # Clear webhooks for all bot tokens with retries
    for token, instance in TelegramTradeHandler._instances.items():
        for attempt in range(3):  # Retry up to 3 times
            try:
                bot = instance.application.bot if instance.application else None
                if bot:
                    await bot.delete_webhook(drop_pending_updates=True)
                    logger.info("Cleared webhook for bot", token=token[:10] + "...", attempt=attempt+1)
                    break
                else:
                    # Create a temporary bot just to clear webhook
                    from telegram import Bot
                    temp_bot = Bot(token=token)
                    await temp_bot.delete_webhook(drop_pending_updates=True)
                    logger.info("Cleared webhook for bot (temp)", token=token[:10] + "...", attempt=attempt+1)
                    break
            except Exception as e:
                logger.warning("Failed to clear webhook", token=token[:10] + "...", attempt=attempt+1, error=str(e))
                if attempt < 2:  # Don't sleep on last attempt
                    await asyncio.sleep(1)
    
    # Final wait to ensure cleanup is complete
    await asyncio.sleep(3)
