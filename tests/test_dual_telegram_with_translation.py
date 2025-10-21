#!/usr/bin/env python3
"""
Test script for the dual Telegram service with translation integration.
"""
import asyncio
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.dual_telegram_service import DualTelegramNotifier
from newsflash.models.benzinga_models import BenzingaArticle
from newsflash.models.classification_models import NewsClassification, ClassificationResult
from newsflash.utils.logging_config import setup_logging, get_logger
from datetime import datetime

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def test_dual_telegram_with_translation():
    """Test the dual Telegram service with translation."""
    
    # Initialize dual Telegram notifier
    telegram_notifier = DualTelegramNotifier(test_mode=True)
    
    # Create sample article with specific timestamp for testing
    test_time = datetime(2025, 10, 21, 18, 30, 0)  # 2025-10-21 18:30:00
    
    article = BenzingaArticle(
        benzinga_id=12345,
        title="EPAM Authorizes $1 Billion Share Repurchase Program, Highlighting Cash Flow And AI-Native Strategy",
        body="EPAM Systems, Inc. today announced that its Board of Directors has authorized a new $1 billion share repurchase program...",
        teaser="EPAM Systems announces $1 billion share repurchase program highlighting cash flow and AI strategy.",
        author="EPAM Systems",
        published=test_time,
        last_updated=test_time,
        url="https://www.benzinga.com/news/earnings/25/10/48298616/epam-authorizes-1-billion-share-repurchase-program-highlighting-cash-flow-and-ai-native-strategy",
        tickers=["EPAM"],
        tags=["Earnings", "Stock Buyback"],
        channels=["Earnings"],
        images=[]
    )
    
    # Create classification result
    classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Major share repurchase program worth $1 billion indicates strong cash flow and confidence in AI strategy"
    )
    
    logger.info("Testing dual Telegram service with translation")
    
    # Test message formatting
    message_data = telegram_notifier.format_message_data(article, classification)
    logger.info("Message data formatted", **message_data)
    
    # Test English message formatting
    english_message = telegram_notifier.format_message(message_data)
    logger.info("English message formatted", message=english_message)
    
    # Test translation (if available)
    if telegram_notifier.translator:
        try:
            chinese_message_data = await telegram_notifier.translator.translate_to_chinese(message_data)
            chinese_message = telegram_notifier.format_message(chinese_message_data)
            logger.info("Chinese message formatted", message=chinese_message)
        except Exception as e:
            logger.error("Translation test failed", error=str(e))
    else:
        logger.warning("Translation service not available")
    
    # Test full notification flow
    success = await telegram_notifier.send_notification(article, classification)
    
    if success:
        logger.info("✅ Dual Telegram notification test completed successfully")
    else:
        logger.error("❌ Dual Telegram notification test failed")
    
    logger.info("🎉 Translation integration testing completed!")


if __name__ == "__main__":
    asyncio.run(test_dual_telegram_with_translation())
