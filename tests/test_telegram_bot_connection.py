"""
Test actual Telegram bot connection and message sending.

THIS TEST REQUIRES:
1. A Telegram bot token from @BotFather
2. Your Telegram chat ID (get from @userinfobot)

Set these in your .env file:
    TELEGRAM_BOT_TOKEN=your_bot_token_here
    TELEGRAM_CHAT_ID=your_chat_id_here
    TELEGRAM_ENABLED=true
"""
import asyncio
from datetime import datetime
import os
from dotenv import load_dotenv

from newsflash.models.base_models import StandardizedArticle, NewsSource
from newsflash.models.classification_models import (
    ClassificationResult,
    NewsClassification,
)
from newsflash.services.dual_telegram_service import DualTelegramNotifier

# Load environment variables
load_dotenv()


def check_credentials() -> tuple[str, str]:
    """Check if Telegram credentials are configured."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    
    if not bot_token or bot_token == "your_telegram_bot_token_here":
        print("❌ TELEGRAM_BOT_TOKEN not configured in .env")
        print("\n📝 To set up Telegram:")
        print("1. Message @BotFather on Telegram")
        print("2. Send: /newbot")
        print("3. Follow instructions to create your bot")
        print("4. Copy the bot token to .env as TELEGRAM_BOT_TOKEN")
        return "", ""
    
    if not chat_id or chat_id == "your_telegram_chat_id_here":
        print("❌ TELEGRAM_CHAT_ID not configured in .env")
        print("\n📝 To get your chat ID:")
        print("1. Message @userinfobot on Telegram")
        print("2. It will reply with your chat ID")
        print("3. Copy the ID to .env as TELEGRAM_CHAT_ID")
        return "", ""
    
    return bot_token, chat_id


async def test_bot_connection():
    """Test sending a message to Telegram."""
    print("\n" + "=" * 80)
    print("TESTING TELEGRAM BOT CONNECTION")
    print("=" * 80 + "\n")
    
    # Check credentials
    bot_token, chat_id = check_credentials()
    
    if not bot_token or not chat_id:
        print("\n⚠️  Configure credentials in .env file first!")
        print("Then set TELEGRAM_ENABLED=true in .env")
        return False
    
    print(f"✅ Bot token configured: {bot_token[:20]}...")
    print(f"✅ Chat ID configured: {chat_id}")
    print("\nAttempting to send test message...\n")
    
    # Initialize notifier (NOT in test mode)
    notifier = DualTelegramNotifier(
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=True,
        test_mode=False,  # Real mode!
    )
    
    # Create a test article
    test_article = StandardizedArticle(
        source=NewsSource.FINLIGHT,
        source_id="test_123",
        title="🧪 TEST: NewsFlash Bot Connection Successful!",
        content="This is a test message to verify your Telegram bot is working correctly.",
        summary="Test message for bot verification",
        author="NewsFlash System",
        published=datetime.now(),
        url="https://github.com/newsflash",
        tickers=["TEST"],
        tags=["system-test"],
        categories=["testing"],
        images=[],
        raw_data={},
    )
    
    # Create a test classification
    test_classification = ClassificationResult(
        classification=NewsClassification.IGNORE,
        confidence="HIGH",
        reasoning="System test message for bot verification",
    )
    
    # Queue the notification
    success = await notifier.send_notification(test_article, test_classification)
    
    if success:
        print("✅ Message queued successfully!")
        print("\n📱 Processing message queue...")
        
        # Process the queue (with timeout)
        try:
            # Process just one message
            message, article = await asyncio.wait_for(
                notifier.message_queue.get(),
                timeout=5.0
            )
            
            result = await notifier._send_message(message)
            
            if result:
                print("\n🎉 SUCCESS! Message sent to Telegram!")
                print("\n✅ Check your Telegram app - you should see the test message!")
                print("\nYou're ready to integrate with the feed manager.")
                return True
            else:
                print("\n❌ Failed to send message. Check your credentials.")
                return False
                
        except asyncio.TimeoutError:
            print("\n❌ Timeout waiting for message in queue")
            return False
    else:
        print("❌ Failed to queue notification")
        return False


async def test_multiple_messages():
    """Test sending multiple messages to verify rate limiting."""
    print("\n" + "=" * 80)
    print("TESTING MULTIPLE MESSAGES (Rate Limiting)")
    print("=" * 80 + "\n")
    
    bot_token, chat_id = check_credentials()
    
    if not bot_token or not chat_id:
        print("⚠️  Skipping - credentials not configured")
        return
    
    notifier = DualTelegramNotifier(
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=True,
        test_mode=False,
    )
    
    # Send 3 test messages
    for i in range(3):
        test_article = StandardizedArticle(
            source=NewsSource.BENZINGA,
            source_id=f"test_{i}",
            title=f"🧪 Test Message #{i+1}/3",
            content=f"Testing rate limiting - message {i+1}",
            published=datetime.now(),
            url="https://github.com/newsflash",
            tickers=["TEST"],
            raw_data={},
        )
        
        await notifier.send_notification(test_article, None)
    
    print("✅ Queued 3 messages")
    print("📤 Sending with rate limiting (50ms delay between messages)...\n")
    
    # Process all messages
    sent_count = 0
    while not notifier.message_queue.empty():
        try:
            message, _ = await asyncio.wait_for(
                notifier.message_queue.get(),
                timeout=2.0
            )
            
            result = await notifier._send_message(message)
            if result:
                sent_count += 1
                print(f"  ✓ Message {sent_count} sent")
            
            # Respect rate limit
            await asyncio.sleep(0.05)
            
        except asyncio.TimeoutError:
            break
    
    print(f"\n✅ Sent {sent_count}/3 messages successfully!")
    print("Check your Telegram - you should see all messages.")


async def main():
    """Run all tests."""
    # Test 1: Single message
    success = await test_bot_connection()
    
    if success:
        # Test 2: Multiple messages (rate limiting)
        await asyncio.sleep(2)  # Brief pause
        await test_multiple_messages()
    
    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())

