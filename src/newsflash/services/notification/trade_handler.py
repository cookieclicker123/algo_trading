"""
Telegram bot handler for processing user trade decisions.
Handles replies to IMMINENT news messages.
"""
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from ...models.base_models import TradeRequest
from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class TelegramTradeHandler:
    """
    Handles Telegram bot interactions for trade decisions.
    Processes user replies to IMMINENT news messages.
    
    ✅ STATELESS DESIGN: No singleton pattern - DI container manages instances
    """
    
    def __init__(self, bot_token: str, trading_service=None):
        """
        Initialize Telegram trade handler.
        
        Args:
            bot_token: Telegram bot token
            trading_service: Trading service instance
        """
        self.bot_token = bot_token
        # New brokerage service (or compatible interface)
        self.trading_service = trading_service
        if not self.trading_service:
            logger.warning("No trading service provided to TelegramTradeHandler - trades will fail")
        self.application = None
        
        logger.info("Telegram trade handler initialized", bot_token=bot_token[:10] + "...")
    
    async def start(self):
        """
        Start the Telegram bot handler.
        
        Idempotent: Safe to call multiple times. Stops existing instance before starting new one.
        """
        try:
            # Stop any existing instance first to prevent conflicts
            if self.application:
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
            
            logger.info("Telegram trade handler started")
            
        except Exception as e:
            logger.error("Failed to start Telegram trade handler", error=str(e))
            raise
    
    async def stop(self):
        """
        Stop the Telegram bot handler.
        
        Idempotent: Safe to call multiple times.
        """
        if self.application:
            try:
                # Stop polling first (this is the blocking operation)
                if self.application.updater.running:
                    await asyncio.wait_for(
                        self.application.updater.stop(),
                        timeout=2.0  # 2 second timeout
                    )
                
                # Stop application
                await asyncio.wait_for(
                    self.application.stop(),
                    timeout=2.0
                )
                
                # Shutdown
                await asyncio.wait_for(
                    self.application.shutdown(),
                    timeout=2.0
                )
                
                self.application = None
                logger.info("Telegram trade handler stopped")
            except asyncio.TimeoutError:
                logger.warning("Telegram bot stop timed out, forcing shutdown")
                self.application = None
            except Exception as e:
                logger.error(f"Error stopping Telegram bot: {e}")
                self.application = None
    
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
            # New service uses execute_trade, old service uses process_trade_request
            if hasattr(self.trading_service, 'execute_trade'):
                trade_result = await self.trading_service.execute_trade(trade_request)
                # New service returns dict
                success = trade_result.get("success", False)
                shares = trade_result.get("shares", 0)
                fill_price = trade_result.get("fill_price", 0.0)
                total_cost = trade_result.get("total_cost", 0.0)
                error = trade_result.get("error", "")
            else:
                # Old service returns TradeResult object
                trade_result = await self.trading_service.process_trade_request(trade_request)
                success = trade_result.success
                shares = trade_result.shares
                fill_price = trade_result.fill_price
                total_cost = trade_result.total_cost
                error = trade_result.error
            
            if success:
                await update.message.reply_text(
                    f"✅ Trade executed successfully!\n"
                    f"📈 {trade_request.ticker}: {shares} share(s) at ${fill_price:.2f}\n"
                    f"💰 Total cost: ${total_cost:.2f}\n"
                    f"🎯 Fill price: ${fill_price:.2f}"
                )
            else:
                await update.message.reply_text(
                    f"❌ Trade execution failed.\n"
                    f"📈 {trade_request.ticker}: ${trade_request.amount_usd} {trade_request.action}\n"
                    f"🚨 Error: {error}\n\n"
                    f"🔍 Check the server logs for detailed error information.\n"
                    f"Common issues:\n"
                    f"• Market closed (trade will be queued)\n"
                    f"• Insufficient buying power\n"
                    f"• Invalid ticker symbol\n"
                    f"• Brokerage connection issues"
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
            
            # Note: Pending trade tracking removed - new brokerage service doesn't track pending trades
            # Trades are executed immediately or queued if market is closed
            
            logger.info("Sent IMMINENT alert with trading options",
                       chat_id=chat_id,
                       tickers=tickers)
            
        except Exception as e:
            logger.error("Failed to send IMMINENT alert", 
                        chat_id=chat_id,
                        error=str(e))


def get_telegram_trade_handler(bot_token: str, trading_service=None) -> TelegramTradeHandler:
    """
    Factory function for Telegram trade handler.
    
    ✅ DI CONTAINER: This is used by DI container factories.
    Container manages singleton instances via providers.Singleton.
    
    Args:
        bot_token: Telegram bot token
        trading_service: Optional trading service instance
        
    Returns:
        TelegramTradeHandler instance
    """
    return TelegramTradeHandler(bot_token, trading_service)