#!/usr/bin/env python3
"""
Test script for yfinance integration with dual Telegram service.
"""
import asyncio
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.telegram_service import TelegramNotifier
from newsflash.models.benzinga_models import BenzingaArticle
from newsflash.models.classification_models import NewsClassification, ClassificationResult
from newsflash.utils.logging_config import setup_logging, get_logger
from datetime import datetime

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def test_yfinance_integration():
    """Test the yfinance integration with dual Telegram service."""
    
    # Initialize dual Telegram notifier
    telegram_notifier = TelegramNotifier(test_mode=True)
    
    # Create sample article with AAPL ticker for testing
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
        tickers=["AAPL"],  # Using AAPL for testing
        tags=["Partnership", "AI"],
        channels=["Breaking News"],
        images=[]
    )
    
    # Create classification result
    classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Major partnership between two tech giants worth $2 billion indicates significant AI collaboration"
    )
    
    logger.info("Testing yfinance integration with dual Telegram service")
    
    # Test message formatting with fundamental data
    message_data = await telegram_notifier.format_message_data(article, classification)
    logger.info("Message data with fundamental data formatted", 
                ticker=message_data.get('tickers'),
                has_fundamental_data=message_data.get('fundamental_data') is not None)
    
    # Test English message formatting
    english_message = telegram_notifier.format_message(message_data)
    logger.info("English message with fundamental data formatted")
    print("\n" + "="*80)
    print("ENGLISH MESSAGE WITH FUNDAMENTAL DATA:")
    print("="*80)
    print(english_message)
    print("="*80)
    
    # Test translation (if available)
    if telegram_notifier.translator and message_data.get('fundamental_data'):
        try:
            chinese_message_data = await telegram_notifier.translator.translate_to_chinese(message_data)
            chinese_message = telegram_notifier.format_message(chinese_message_data)
            logger.info("Chinese message with fundamental data formatted")
            print("\n" + "="*80)
            print("CHINESE MESSAGE WITH FUNDAMENTAL DATA:")
            print("="*80)
            print(chinese_message)
            print("="*80)
        except Exception as e:
            logger.error("Translation test failed", error=str(e))
    else:
        logger.warning("Info: Translation service not available or no fundamental data")
    
    # Test full notification flow
    success = await telegram_notifier.send_notification(article, classification)
    
    if success:
        logger.info("✅ YFinance integration test completed successfully")
    else:
        logger.error("❌ YFinance integration test failed")
    
    logger.info("🎉 YFinance integration testing completed!")


if __name__ == "__main__":
    asyncio.run(test_yfinance_integration())
