"""
Quick script to get your Telegram chat ID using your bot token.
"""
import os
from dotenv import load_dotenv
import httpx
import asyncio

load_dotenv()

async def get_chat_id():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not bot_token or bot_token == "your_telegram_bot_token_here":
        print("❌ TELEGRAM_BOT_TOKEN not found in .env")
        return
    
    print(f"✅ Using bot token: {bot_token[:20]}...")
    print("\n📱 Fetching recent messages from Telegram API...")
    
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=10.0)
            data = response.json()
            
            if not data.get("ok"):
                print(f"❌ API Error: {data.get('description')}")
                return
            
            updates = data.get("result", [])
            
            if not updates:
                print("\n⚠️  No messages found!")
                print("\nPlease:")
                print("1. Open Telegram")
                print("2. Find your bot (search for its username)")
                print("3. Click START if you haven't already")
                print("4. Send ANY message to your bot (e.g., 'hello' or 'test')")
                print("5. Run this script again")
                return
            
            print(f"\n✅ Found {len(updates)} message(s)!\n")
            
            # Get the most recent chat ID
            chat_ids = set()
            for update in updates:
                if "message" in update:
                    chat_id = update["message"]["chat"]["id"]
                    chat_ids.add(chat_id)
                    
                    # Show details
                    from_user = update["message"]["from"]
                    print(f"Message from: {from_user.get('first_name', 'Unknown')}")
                    print(f"Username: @{from_user.get('username', 'N/A')}")
                    print(f"Chat ID: {chat_id}")
                    print(f"Message: {update['message'].get('text', 'N/A')}")
                    print("-" * 60)
            
            if chat_ids:
                your_chat_id = list(chat_ids)[0]  # Use the first one
                print(f"\n🎉 YOUR CHAT ID IS: {your_chat_id}")
                print(f"\n📝 Add this to your .env file:")
                print(f"TELEGRAM_CHAT_ID={your_chat_id}")
                
                # Offer to update .env automatically
                print("\n💡 Update .env file automatically? (y/n): ", end="")
                
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(get_chat_id())


