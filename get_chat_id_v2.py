#!/usr/bin/env python3
"""
Get Telegram Chat ID for the second bot
"""

import asyncio
from telegram import Bot
from dotenv import load_dotenv
import os

load_dotenv()

async def get_chat_id(bot_token=None):
    # Use the NEW bot token
    if not bot_token:
        bot_token = input("Enter your NEW bot token: ").strip()
    
    if not bot_token:
        print("❌ No bot token provided!")
        return
    
    bot = Bot(token=bot_token)
    
    try:
        # Get updates to find your chat
        updates = await bot.get_updates()
        
        if not updates:
            print("❌ No messages found. Please send a message to your bot first!")
            return
        
        print("\n📱 Found chats:")
        print("-" * 50)
        
        for update in updates:
            if update.message:
                chat = update.message.chat
                print(f"Chat ID: {chat.id}")
                print(f"Type: {chat.type}")
                print(f"Username: @{chat.username}" if chat.username else "No username")
                print(f"First Name: {chat.first_name}" if chat.first_name else "No first name")
                print("-" * 50)
        
        if updates:
            latest_chat_id = updates[-1].message.chat.id
            print(f"\n✅ Use this Chat ID: {latest_chat_id}")
            print(f"\n🔧 Add to your .env file:")
            print(f"TELEGRAM_BOT_TOKEN_2={bot_token}")
            print(f"TELEGRAM_CHAT_ID_2={latest_chat_id}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    import sys
    bot_token = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(get_chat_id(bot_token))
