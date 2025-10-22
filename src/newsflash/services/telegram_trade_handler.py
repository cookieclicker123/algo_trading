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
    
    def __init__(self, bot_token: str, trading_service=None):
        """
        Initialize Telegram trade handler.
        
        Args:
            bot_token: Telegram bot token
            trading_service: IBKR trading service instance
        """
        self.bot_token = bot_token
        self.trading_service = trading_service or get_ibkr_trading_service()
        self.application = None
        self.is_running = False
        
        logger.info("Telegram trade handler initialized")
    
    async def start(self):
        """Start the Telegram bot handler."""
        try:
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
                    f"Please check your account or try again later."
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
            await update.message.reply_text(
                "❓ Invalid response. Please reply with:\n"
                "• 'trade' - to trade\n"
                "• 'trade TICKER' - to trade specific ticker\n"
                "• 'ignore' - to ignore\n\n"
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
