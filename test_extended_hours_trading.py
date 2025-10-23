#!/usr/bin/env python3
"""
Extended Hours Trading Integration Test

Tests the full trading flow during premarket/after-hours with progressive limit pricing.
This test will:
1. Send IMMINENT news to Telegram
2. Wait for user to reply "trade AAPL" 
3. Execute trade with progressive limit orders (0.25%, 0.5%, 1%, 1.5%, 2%)
4. Show detailed fill information

Run this during extended hours (4:00 AM - 9:30 AM ET or 4:00 PM - 8:00 PM ET)
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.service_container import initialize_services
from newsflash.services.telegram_trade_handler import clear_trade_handler_instances, stop_all_trade_handlers
from newsflash.utils.bot_conflict_resolver import resolve_bot_conflicts
from newsflash.models.benzinga_models import BenzingaArticle
from newsflash.models.base_models import NewsSource
from newsflash.models.classification_models import ClassificationResult, NewsClassification
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)

async def test_extended_hours_trading():
    """Test extended hours trading with progressive limit pricing."""
    logger.info("🌙 EXTENDED HOURS TRADING TEST")
    logger.info("==================================================")
    
    # Check if we're in extended hours
    from newsflash.services.ibkr_trading_service import IBKRTradingService
    trading_service = IBKRTradingService()
    is_extended_hours = trading_service._is_extended_hours()
    
    if not is_extended_hours:
        logger.warning("⚠️  NOT IN EXTENDED HOURS!")
        logger.info("Extended hours: 4:00 AM - 9:30 AM ET (premarket) or 4:00 PM - 8:00 PM ET (after-hours)")
        logger.info("Current time may be in regular market hours (9:30 AM - 4:00 PM ET)")
        logger.info("This test will still run but may use market orders instead of limit orders")
    
    logger.info("Extended hours detected", is_extended_hours=is_extended_hours)
    
    # AGGRESSIVE CLEANUP: Kill any conflicting processes and clear webhooks
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
    
    # Create a test IMMINENT article about Apple
    test_article = BenzingaArticle(
        benzinga_id=99999999,  # Fake ID for testing
        title="Apple Announces Major Partnership with Microsoft Worth $2 Billion for AI Integration",
        author="Test Author",
        content="Apple Inc. and Microsoft Corporation have announced a groundbreaking partnership worth $2 billion to integrate advanced AI capabilities across their platforms. The deal includes immediate revenue sharing and technology licensing agreements that will significantly impact both companies' bottom lines starting this quarter.",
        summary="Major $2B partnership between Apple and Microsoft for AI integration with immediate financial impact.",
        published=datetime.now(),
        last_updated=datetime.now(),
        url="https://example.com/test-article",
        tickers=["AAPL", "MSFT"],
        channels=["partnerships", "major news", "ai", "technology"],
        source=NewsSource.BENZINGA,
        raw_data={
            "test": True,
            "extended_hours_test": True,
            "partnership_value": "$2B",
            "immediate_impact": True
        }
    )
    
    # Create IMMINENT classification
    test_classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Major $2B partnership between Apple and Microsoft with immediate financial impact - clear catalyst for significant price movement"
    )
    
    # Send the message to Telegram
    logger.info("📱 Sending IMMINENT news to Telegram...")
    success = await telegram_notifier.send_notification(test_article, test_classification)
    
    if success:
        logger.info("✅ News sent to Telegram successfully!")
        logger.info("📱 Check your Telegram bot - you should see the IMMINENT news with trading options")
        logger.info("💬 Reply with 'trade AAPL' to test extended hours trading")
        
        if is_extended_hours:
            logger.info("🌙 EXTENDED HOURS MODE:")
            logger.info("   • Will use progressive limit orders (0.25%, 0.5%, 1%, 1.5%, 2%)")
            logger.info("   • Each attempt has 10-second timeout")
            logger.info("   • Will show detailed fill information")
            logger.info("   • Should get filled at 0.25% limit if market is liquid")
        else:
            logger.info("🕐 REGULAR HOURS MODE:")
            logger.info("   • Will use market orders")
            logger.info("   • Fast execution expected")
    else:
        logger.error("❌ Failed to send news to Telegram")
        return
    
    # Wait for user interaction
    logger.info("⏳ Waiting for your reply in Telegram...")
    logger.info("💡 Reply with: 'trade AAPL' to test the trading system")
    
    logger.info("✅ Test setup complete! Check your Telegram and reply to test trading.")
    
    # Keep the service running for a bit to allow replies
    logger.info("⏳ Keeping service running for 120 seconds to allow replies...")
    await asyncio.sleep(120)
    
    # Cleanup
    await telegram_notifier.stop()
    logger.info("🛑 Telegram service stopped")
    
    logger.info("🎉 Extended hours trading test completed!")
    logger.info("Check the logs above for detailed trading information")

if __name__ == "__main__":
    asyncio.run(test_extended_hours_trading())
