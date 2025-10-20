#!/usr/bin/env python3
"""
Test the second Telegram bot connection
"""

import asyncio
from telegram import Bot
from dotenv import load_dotenv
import os

load_dotenv()

async def test_second_bot():
    """Test the second Telegram bot connection."""
    
    # Get second bot configuration
    bot_token_2 = os.getenv("TELEGRAM_BOT_TOKEN_2")
    chat_id_2 = os.getenv("TELEGRAM_CHAT_ID_2")
    
    if not bot_token_2:
        print("❌ TELEGRAM_BOT_TOKEN_2 not found in environment variables!")
        print("Please add it to your .env file:")
        print("TELEGRAM_BOT_TOKEN_2=your_bot_token_here")
        return
    
    if not chat_id_2:
        print("❌ TELEGRAM_CHAT_ID_2 not found in environment variables!")
        print("Please add it to your .env file:")
        print("TELEGRAM_CHAT_ID_2=your_chat_id_here")
        return
    
    print(f"🤖 Testing second bot with token: {bot_token_2[:10]}...")
    print(f"💬 Chat ID: {chat_id_2}")
    
    bot = Bot(token=bot_token_2)
    
    try:
        # Test bot info
        bot_info = await bot.get_me()
        print(f"✅ Bot connected successfully!")
        print(f"   Name: {bot_info.first_name}")
        print(f"   Username: @{bot_info.username}")
        
        # Test sending message
        test_message = "🚀 **Second Bot Test** 🚀\n\nThis is a test message from your second Telegram bot!"
        
        await bot.send_message(
            chat_id=chat_id_2,
            text=test_message,
            parse_mode="Markdown"
        )
        
        print(f"✅ Test message sent successfully to chat {chat_id_2}!")
        print("📱 Check your phone for the message!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\n🔧 Troubleshooting:")
        print("1. Make sure you've started a chat with your second bot")
        print("2. Send a message to the bot first")
        print("3. Verify the bot token and chat ID are correct")
        print("4. Run get_chat_id_v2.py to get the correct chat ID")

if __name__ == "__main__":
    asyncio.run(test_second_bot())
