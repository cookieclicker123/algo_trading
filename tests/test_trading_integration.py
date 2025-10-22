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

from newsflash.services.telegram_service import TelegramNotifier
from newsflash.services.ibkr_trading_service import get_ibkr_trading_service, TradeRequest
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
    
    # Initialize services
    telegram_notifier = TelegramNotifier(test_mode=True)
    trading_service = get_ibkr_trading_service()
    
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
    
    # Test trade request processing
    logger.info("Testing trade request processing")
    
    # Simulate user responses
    test_responses = [
        "trade",           # Trade default ticker (AAPL)
        "trade MSFT",      # Trade specific ticker
        "ignore",          # Ignore the news
        "invalid",         # Invalid response
    ]
    
    for response in test_responses:
        logger.info(f"Testing user response: '{response}'")
        
        # Add pending trade
        trading_service.add_pending_trade("test_article_123", article.tickers, "test_chat_id")
        
        # Process user response
        trade_request = trading_service.process_user_response("test_chat_id", response)
        
        if trade_request:
            logger.info(f"Trade request created: {trade_request.ticker} for ${trade_request.amount_usd}")
            
            # Execute trade (simulated)
            success = await trading_service.process_trade_request(trade_request)
            logger.info(f"Trade execution result: {success}")
        else:
            logger.info(f"No trade request (ignored or invalid): '{response}'")
    
    # Test timeout functionality
    logger.info("Testing trade timeout")
    trading_service.add_pending_trade("timeout_test", ["AAPL"], "test_chat_id")
    
    # Simulate expired trade
    trading_service.pending_trades["timeout_test"]["expires_at"] = datetime.now() - timedelta(minutes=1)
    
    expired_trade = trading_service.process_user_response("test_chat_id", "trade")
    logger.info(f"Expired trade result: {expired_trade is None}")
    
    logger.info("✅ Trading integration test completed successfully!")


if __name__ == "__main__":
    asyncio.run(test_trading_integration())
