"""
Telegram bot handler for manual trade interventions.
Handles exit commands for manual position management.
"""
import asyncio
import re
from typing import Optional
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class TelegramTradeHandler:
    """
    Handles Telegram bot interactions for manual trade interventions.
    Processes exit commands: "exit TICKER AMOUNT"

    ✅ STATELESS DESIGN: No singleton pattern - DI container manages instances
    """

    def __init__(
        self,
        bot_token: str,
        brokerage_service=None,
        exit_trade_use_case=None,
        position_manager=None,
    ):
        """
        Initialize Telegram trade handler.

        Args:
            bot_token: Telegram bot token
            brokerage_service: BrokerageService instance for executing exits
            exit_trade_use_case: ExitTradeUseCase instance for cancelling scheduled exits
            position_manager: PositionManager instance for stop loss tracking
        """
        self.bot_token = bot_token
        self.brokerage_service = brokerage_service
        self.exit_trade_use_case = exit_trade_use_case
        self.position_manager = position_manager
        self.application = None

        if not self.brokerage_service:
            logger.warning("No brokerage service provided to TelegramTradeHandler - exits will fail")
        if not self.exit_trade_use_case:
            logger.warning("No exit trade use case provided - scheduled exits won't be cancelled")
        if not self.position_manager:
            logger.warning("No position manager provided - stop loss monitoring won't work")

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
            self.application.add_handler(CommandHandler("positions", self.positions_command))
            self.application.add_handler(CommandHandler("exit", self.exit_command))
            self.application.add_handler(CommandHandler("hold", self.hold_command))
            
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
            "Commands:\n"
            "• `/exit TICKER` - Exit position immediately\n"
            "• `/hold TICKER` - Disable 10-min auto-exit for runners\n"
            "• `/positions` - Show tracked positions\n\n"
            "Manual exit:\n"
            "• `exit TICKER` - Exit 100% of position\n"
            "• `exit TICKER 0.5` - Exit 50% of position\n\n"
            "Use /help for more info."
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        await update.message.reply_text(
            "📖 Commands:\n\n"
            "📊 Position Management:\n"
            "• `/positions` - Show tracked positions with P&L\n"
            "• `/exit TICKER` - Exit position immediately\n"
            "• `/hold TICKER` - Disable 10-min auto-exit for runners\n\n"
            "Manual exit (text commands):\n"
            "• `exit TICKER` - Exit 100% of position\n"
            "• `exit TICKER 0.4` - Exit 40% of position\n\n"
            "Examples:\n"
            "• `/exit AAPL` - Exit all AAPL shares now\n"
            "• `/hold TSLA` - Let TSLA run (30-min failsafe)\n"
            "• `exit TSLA 0.5` - Exit 50% of TSLA\n\n"
            "Exit Strategy:\n"
            "• Stop loss: 5% below entry\n"
            "• Auto-exit: 10 minutes (unless /hold)\n"
            "• /hold adds 30-min failsafe\n"
            "• Exit manually when you see weakness"
        )

    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions command - show tracked positions with P&L."""
        if not self.position_manager:
            await update.message.reply_text("❌ Position manager not available")
            return

        try:
            positions = await self.position_manager.get_all_positions()

            if not positions:
                await update.message.reply_text("📊 No open positions being tracked")
                return

            message_parts = ["📊 *Tracked Positions:*\n"]

            for pos in positions:
                profit_pct = pos.current_profit_pct
                unrealized = pos.unrealized_pnl

                # Emoji based on profit
                if profit_pct and profit_pct > 0.10:
                    emoji = "🚀"
                elif profit_pct and profit_pct > 0:
                    emoji = "📈"
                elif profit_pct and profit_pct < 0:
                    emoji = "📉"
                else:
                    emoji = "📊"

                profit_str = f"{profit_pct*100:+.1f}%" if profit_pct else "N/A"
                pnl_str = f"${unrealized:+.2f}" if unrealized else "N/A"

                message_parts.append(
                    f"\n{emoji} *{pos.ticker}*\n"
                    f"   Entry: ${pos.entry_price:.2f}\n"
                    f"   Shares: {pos.shares_remaining:.0f}\n"
                    f"   P&L: {profit_str} ({pnl_str})"
                )

            message_parts.append("\n\n_Stop loss: 5% | Auto-exit: 10 min_")
            await update.message.reply_text("\n".join(message_parts), parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error in positions_command: {e}", exc_info=True)
            await update.message.reply_text("❌ Error fetching positions")

    async def hold_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /hold TICKER command - disable auto-exit for meteoric winners."""
        if not context.args:
            await update.message.reply_text(
                "Usage: `/hold TICKER`\n\n"
                "Disables the 10-min auto-exit for a runner.\n"
                "A 30-min failsafe remains in case you forget.\n\n"
                "Example: `/hold FRSX`",
                parse_mode="Markdown"
            )
            return

        ticker = context.args[0].upper()

        if not self.exit_trade_use_case:
            await update.message.reply_text("❌ Exit trade use case not available")
            return

        try:
            success = self.exit_trade_use_case.hold_ticker(ticker)

            if success:
                await update.message.reply_text(
                    f"🔒 *HOLD ACTIVATED*\n\n"
                    f"📈 Ticker: {ticker}\n"
                    f"✅ 10-min auto-exit cancelled\n"
                    f"⏰ 30-min failsafe active\n\n"
                    f"Exit manually via `/exit {ticker}` when ready.",
                    parse_mode="Markdown"
                )
            else:
                # Check if already held
                if self.exit_trade_use_case.is_ticker_held(ticker):
                    await update.message.reply_text(
                        f"🔒 {ticker} is already being held.\n"
                        f"30-min failsafe is active.\n\n"
                        f"Exit manually via `/exit {ticker}` when ready."
                    )
                else:
                    await update.message.reply_text(
                        f"❌ No scheduled auto-exit found for {ticker}\n\n"
                        f"Either the position doesn't exist or it already exited."
                    )

        except Exception as e:
            logger.error(f"Error in hold_command: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Error: {e}")

    async def exit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /exit TICKER command - immediately exit a tracked position."""
        if not context.args:
            await update.message.reply_text(
                "Usage: `/exit TICKER`\n"
                "Example: `/exit AAPL`",
                parse_mode="Markdown"
            )
            return

        ticker = context.args[0].upper()

        # Try PositionManager first (for tracked positions)
        if self.position_manager:
            position = await self.position_manager.get_position(ticker)
            if position:
                await update.message.reply_text(
                    f"📤 Requesting manual exit for {ticker}...\n"
                    f"Shares remaining: {position.shares_remaining:.0f}\n"
                    f"Entry: ${position.entry_price:.2f}"
                )

                success = await self.position_manager.request_manual_exit(ticker)
                if success:
                    await update.message.reply_text(
                        f"✅ Manual exit triggered for {ticker}\n"
                        f"Exit order being processed..."
                    )
                else:
                    await update.message.reply_text(f"❌ Failed to trigger exit for {ticker}")
                return

        # Fallback to brokerage service for non-tracked positions
        await self._handle_exit_command(update, ticker, 1.0)
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages - parse exit commands."""
        try:
            message_text = update.message.text.strip()
            
            logger.info("Received user message", message=message_text)
            
            # Parse exit command: "exit TICKER" or "exit TICKER AMOUNT"
            exit_match = re.match(r'^exit\s+([A-Za-z]+)(?:\s+([0-9.]+))?$', message_text, re.IGNORECASE)
            
            if exit_match:
                ticker = exit_match.group(1).upper()
                amount_str = exit_match.group(2)
                
                # Parse amount (default to 1.0 = 100% if not provided)
                if amount_str is None:
                    exit_percentage = 1.0
                else:
                    try:
                        exit_percentage = float(amount_str)
                        if exit_percentage <= 0 or exit_percentage > 1.0:
                            await update.message.reply_text(
                                f"❌ Invalid exit amount: {amount_str}\n"
                                f"Please use a value between 0 and 1 (e.g., 0.4 for 40%, 1 for 100%)"
                            )
                            return
                    except ValueError:
                        await update.message.reply_text(
                            f"❌ Invalid exit amount: {amount_str}\n"
                            f"Please use a number between 0 and 1 (e.g., 0.4 for 40%)"
                        )
                        return
                
                # Handle exit command
                await self._handle_exit_command(update, ticker, exit_percentage)
            else:
                # Unknown command
                await update.message.reply_text(
                    "❓ Unknown command. Available commands:\n\n"
                    "• `exit TICKER` - Exit 100% of position\n"
                    "• `exit TICKER 0.4` - Exit 40% of position\n"
                    "• `exit TICKER 1` - Exit 100% of position\n\n"
                    "Example: `exit AAPL 0.5`"
                )
                
        except Exception as e:
            logger.error("Error handling user message", error=str(e), exc_info=True)
            await update.message.reply_text(
                "❌ Error processing your request. Please try again."
            )
    
    async def _handle_exit_command(
        self,
        update: Update,
        ticker: str,
        exit_percentage: float
    ) -> None:
        """Handle exit command: exit TICKER AMOUNT"""
        try:
            if not self.brokerage_service:
                await update.message.reply_text(
                    "❌ Brokerage service not available. Cannot execute exit."
                )
                return
            
            # Get position info first
            positions = await self.brokerage_service.get_positions()
            position = next((p for p in positions if p["symbol"].upper() == ticker.upper()), None)
            
            if not position:
                await update.message.reply_text(
                    f"❌ No open position found for {ticker}\n\n"
                    f"Available positions:\n" +
                    "\n".join([f"• {p['symbol']}: {p['qty']:.0f} shares" for p in positions]) if positions else "• None"
                )
                return
            
            total_shares = position["qty"]
            shares_to_sell = int(total_shares * exit_percentage)
            entry_price = position.get("avg_entry_price", 0.0)
            
            if shares_to_sell <= 0:
                await update.message.reply_text(
                    f"❌ Invalid: Would sell 0 shares\n"
                    f"Total position: {total_shares:.0f} shares\n"
                    f"Exit percentage: {exit_percentage * 100:.1f}%"
                )
                return
            
            # Send "sending order" message
            await update.message.reply_text(
                f"📤 Sending exit order...\n"
                f"📈 Ticker: {ticker}\n"
                f"📦 Selling: {shares_to_sell:.0f} shares ({exit_percentage * 100:.1f}%)\n"
                f"📊 Total position: {total_shares:.0f} shares"
            )
            
            # Cancel scheduled auto-exit if exists (for full exits)
            if exit_percentage >= 1.0 and self.exit_trade_use_case:
                cancelled = self.exit_trade_use_case.cancel_scheduled_exit(ticker)
                if cancelled:
                    logger.info(
                        "TelegramTradeHandler: Cancelled scheduled auto-exit due to manual exit",
                        ticker=ticker
                    )
            
            # Execute manual exit
            result = await self.brokerage_service.manual_exit_position(
                ticker=ticker,
                exit_percentage=exit_percentage,
                entry_price=entry_price
            )
            
            if result.get("success"):
                fill_price = result.get("fill_price", 0.0)
                shares_sold = result.get("shares", 0)
                position_info = result.get("position_info", {})
                shares_remaining = position_info.get("shares_remaining", 0)
                total_shares_original = position_info.get("total_shares", 0)
                
                # Calculate P&L
                entry_price_used = entry_price
                exit_price_used = fill_price
                pnl = (exit_price_used - entry_price_used) * shares_sold
                pnl_percent = ((exit_price_used - entry_price_used) / entry_price_used * 100) if entry_price_used > 0 else 0
                
                # Build success message
                message_parts = [
                    f"✅ Exit order filled!",
                    "",
                    f"📈 Ticker: {ticker}",
                    f"📦 Shares sold: {shares_sold:.0f}",
                    f"💵 Exit price: ${exit_price_used:.2f}",
                    f"💰 Entry price: ${entry_price_used:.2f}",
                    "",
                    "💰 PROFIT/LOSS:",
                    f"   Entry Price: ${entry_price_used:.2f}",
                    f"   Exit Price: ${exit_price_used:.2f}",
                    f"   Entry Cost: ${entry_price_used * shares_sold:.2f}",
                    f"   Exit Proceeds: ${exit_price_used * shares_sold:.2f}",
                ]
                
                if pnl >= 0:
                    message_parts.append(f"   ✅ Profit: ${pnl:.2f} ({pnl_percent:+.2f}%)")
                else:
                    message_parts.append(f"   ❌ Loss: ${pnl:.2f} ({pnl_percent:+.2f}%)")
                
                # Add position status
                if shares_remaining > 0:
                    remaining_pct = (shares_remaining / total_shares_original * 100) if total_shares_original > 0 else 0
                    message_parts.extend([
                        "",
                        f"📊 Position Status:",
                        f"   Remaining: {shares_remaining:.0f} shares ({remaining_pct:.1f}%)"
                    ])
                else:
                    message_parts.extend([
                        "",
                        f"📊 Position Status:",
                        f"   ✅ Position fully closed"
                    ])
                
                await update.message.reply_text("\n".join(message_parts))
                
            else:
                error = result.get("error", "Unknown error")
                await update.message.reply_text(
                    f"❌ Exit order failed\n"
                    f"📈 Ticker: {ticker}\n"
                    f"🚨 Error: {error}"
                )
                
        except Exception as e:
            logger.error("Error handling exit command", error=str(e), exc_info=True)
            await update.message.reply_text(
                "❌ Error executing exit. Please try again."
            )
    


def get_telegram_trade_handler(
    bot_token: str,
    brokerage_service=None,
    exit_trade_use_case=None,
    position_manager=None,
) -> TelegramTradeHandler:
    """
    Factory function for Telegram trade handler.

    ✅ DI CONTAINER: This is used by DI container factories.
    Container manages singleton instances via providers.Singleton.

    Args:
        bot_token: Telegram bot token
        brokerage_service: Optional BrokerageService instance
        exit_trade_use_case: Optional ExitTradeUseCase instance
        position_manager: Optional PositionManager instance

    Returns:
        TelegramTradeHandler instance
    """
    return TelegramTradeHandler(
        bot_token=bot_token,
        brokerage_service=brokerage_service,
        exit_trade_use_case=exit_trade_use_case,
        position_manager=position_manager,
    )