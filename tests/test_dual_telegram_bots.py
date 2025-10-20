#!/usr/bin/env python3
"""
Test both Telegram bots working together
"""

import asyncio
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

async def test_dual_bots():
    """Test both Telegram bots working together."""
    
    # Import here to avoid import issues
    from newsflash.services.dual_telegram_service import DualTelegramNotifier
    from newsflash.models.benzinga_models import BenzingaArticle
    from newsflash.models.classification_models import NewsClassification, ClassificationResult
    from datetime import datetime
    
    print("🤖 Testing Dual Telegram Bot Setup")
    print("=" * 50)
    
    # Check environment variables
    bot_1_token = os.getenv("TELEGRAM_BOT_TOKEN")
    bot_1_chat = os.getenv("TELEGRAM_CHAT_ID")
    bot_2_token = os.getenv("TELEGRAM_BOT_TOKEN_2")
    bot_2_chat = os.getenv("TELEGRAM_CHAT_ID_2")
    
    print(f"Primary Bot Token: {'✅ Set' if bot_1_token else '❌ Missing'}")
    print(f"Primary Chat ID: {'✅ Set' if bot_1_chat else '❌ Missing'}")
    print(f"Secondary Bot Token: {'✅ Set' if bot_2_token else '❌ Missing'}")
    print(f"Secondary Chat ID: {'✅ Set' if bot_2_chat else '❌ Missing'}")
    print()
    
    if not all([bot_1_token, bot_1_chat, bot_2_token, bot_2_chat]):
        print("❌ Missing environment variables!")
        print("Please add to your .env file:")
        print("TELEGRAM_BOT_TOKEN=your_primary_token")
        print("TELEGRAM_CHAT_ID=your_primary_chat_id")
        print("TELEGRAM_BOT_TOKEN_2=your_secondary_token")
        print("TELEGRAM_CHAT_ID_2=your_secondary_chat_id")
        return
    
    # Initialize dual notifier
    print("🔧 Initializing Dual Telegram Notifier...")
    notifier = DualTelegramNotifier(test_mode=False)
    
    print(f"Primary Bot Enabled: {'✅' if notifier.enabled_1 else '❌'}")
    print(f"Secondary Bot Enabled: {'✅' if notifier.enabled_2 else '❌'}")
    print()
    
    # Create test article
    test_article = BenzingaArticle(
        benzinga_id=123,
        title="🚀 Dual Bot Test - Tesla Announces Major Breakthrough",
        url="https://example.com/test",
        tickers=["TSLA"],
        author="Test Author",
        published=datetime.now(),
        last_updated=datetime.now()
    )
    
    # Create test classification
    test_classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Test message for dual bot verification"
    )
    
    # Test message formatting
    print("📝 Testing Message Formatting...")
    message = notifier.format_message(test_article, test_classification)
    print("Formatted Message:")
    print("-" * 30)
    print(message)
    print("-" * 30)
    print()
    
    # Test sending to both bots
    print("📱 Sending Test Messages to Both Bots...")
    success = await notifier.send_notification(test_article, test_classification)
    
    if success:
        print("✅ Test messages queued for both bots!")
        print("📱 Check both of your Telegram chats for the test message")
        
        # Start the notifier to process the queue
        print("🔄 Starting notifier to process messages...")
        notifier.is_running = True
        
        # Process messages for a few seconds
        await asyncio.sleep(3)
        
        print("⏹️ Stopping notifier...")
        await notifier.stop()
        
        print("✅ Dual bot test completed!")
        
    else:
        print("❌ Failed to queue test messages")

if __name__ == "__main__":
    asyncio.run(test_dual_bots())
