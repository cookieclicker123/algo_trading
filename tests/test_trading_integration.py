#!/usr/bin/env python3
"""
Test script for IBKR trading integration with Telegram.
Demonstrates the complete trading workflow.
"""
import asyncio
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.service_container import initialize_services
from newsflash.services.telegram_trade_handler import clear_trade_handler_instances, stop_all_trade_handlers
from newsflash.utils.bot_conflict_resolver import resolve_bot_conflicts
from newsflash.models.benzinga_models import BenzingaArticle
from newsflash.models.classification_models import NewsClassification, ClassificationResult
from newsflash.utils.logging_config import setup_logging, get_logger
from datetime import datetime, timedelta

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def test_trading_integration():
    """Test the complete trading integration workflow."""
    
    logger.info("Testing IBKR trading integration with Telegram")
    
    # GUARANTEED CONFLICT RESOLUTION: Use the bot conflict resolver
    logger.info("Performing guaranteed bot conflict resolution...")
    
    # Get the actual bot tokens from config
    from newsflash.config.settings import get_telegram_config, get_telegram_config_2
    config_1 = get_telegram_config()
    config_2 = get_telegram_config_2()
    bot_tokens = [
        config_1.get("bot_token", ""),
        config_2.get("bot_token", "")
    ]
    
    # Resolve conflicts aggressively (kills conflicting processes)
    conflict_resolved = await resolve_bot_conflicts(bot_tokens, aggressive=True)
    
    if not conflict_resolved:
        logger.error("Failed to resolve bot conflicts - test may fail")
        return
    
    # Clear singleton instances
    clear_trade_handler_instances()
    
    # Initialize services using the service container (REAL MODE - will send to Telegram)
    container = initialize_services()
    telegram_notifier = container.get_telegram_notifier()
    trading_service = container.get_service('trading')
    
    # Start the Telegram service (this starts the trade handlers)
    await telegram_notifier.start()
    
    # Create sample IMMINENT article
    test_time = datetime(2025, 10, 21, 18, 30, 0)
    
    article = BenzingaArticle(
        benzinga_id=12345,
        title="Apple Announces Major Partnership with Microsoft Worth $2 Billion for AI Integration",
        body="Apple Inc. today announced a major partnership with Microsoft Corporation...",
        teaser="Apple announces $2 billion partnership with Microsoft for AI integration.",
        author="Apple Inc.",
        published=test_time,
        last_updated=test_time,
        url="https://example.com/apple-microsoft-partnership",
        tickers=["AAPL", "MSFT"],  # Multiple tickers for testing
        tags=["Partnership", "AI"],
        channels=["Breaking News"],
        images=[]
    )
    
    # Create IMMINENT classification
    classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Major partnership between two tech giants worth $2 billion indicates significant AI collaboration"
    )
    
    logger.info("Testing IMMINENT news with trading options")
    
    # Test message formatting with trading options
    message_data = await telegram_notifier.format_message_data(article, classification)
    english_message = telegram_notifier.format_message(message_data)
    
    # Add trading options
    trading_options = telegram_notifier._format_trading_options(article.tickers)
    full_message = english_message + trading_options
    
    print("\n" + "="*80)
    print("IMMINENT NEWS WITH TRADING OPTIONS:")
    print("="*80)
    print(full_message)
    print("="*80)
    
    # Send the message to Telegram
    logger.info("Sending IMMINENT news to Telegram...")
    success = await telegram_notifier.send_notification(article, classification)
    
    if success:
        logger.info("✅ News sent to Telegram successfully!")
        logger.info("📱 Check your Telegram bot - you should see the IMMINENT news with trading options")
        logger.info("💬 Reply with 'trade', 'trade AAPL', 'trade MSFT', or 'ignore' to test trading")
    else:
        logger.error("❌ Failed to send news to Telegram")
        return
    
    # Wait for user interaction
    logger.info("⏳ Waiting for your reply in Telegram...")
    logger.info("💡 Reply with: 'trade' (default AAPL), 'trade AAPL', 'trade MSFT', or 'ignore'")
    
    logger.info("✅ Test setup complete! Check your Telegram and reply to test trading.")
    
    # Keep the service running for a bit to allow replies
    logger.info("⏳ Keeping service running for 60 seconds to allow replies...")
    await asyncio.sleep(60)
    
    # Cleanup
    await telegram_notifier.stop()
    logger.info("🛑 Telegram service stopped")


if __name__ == "__main__":
    asyncio.run(test_trading_integration())
