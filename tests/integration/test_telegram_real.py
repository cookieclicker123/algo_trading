"""
Real Telegram test - sends actual message to verify Telegram connectivity.

This test sends a real message to your Telegram to confirm/rule out Telegram as the problem.
Run with: python3 tests/integration/test_telegram_real.py
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

# Remove any telegram mocks that might exist from other test imports
if 'telegram' in sys.modules:
    mod = sys.modules['telegram']
    if 'Mock' in str(type(mod)):
        del sys.modules['telegram']
        if 'telegram.error' in sys.modules:
            del sys.modules['telegram.error']

# Import real telegram modules
import telegram
from telegram import Bot
import telegram.error

# Now import our client
from newsflash.infra.notification.telegram_client import TelegramNotificationClient
from newsflash.config import settings


import pytest

@pytest.mark.asyncio
async def test_send_real_telegram_message():
    """Send a real message to Telegram to verify connectivity."""
    print("\n" + "=" * 80)
    print("REAL TELEGRAM CONNECTIVITY TEST")
    print("=" * 80)
    
    # Get Telegram config from settings
    telegram_config_1 = settings.get_telegram_config()
    telegram_config_2 = settings.get_telegram_config_2()
    
    print(f"\n📱 Telegram Bot 1 Config:")
    print(f"   Enabled: {telegram_config_1.get('enabled', False)}")
    print(f"   Bot Token: {'***' + telegram_config_1.get('bot_token', '')[-4:] if telegram_config_1.get('bot_token') else 'NOT SET'}")
    print(f"   Chat ID: {telegram_config_1.get('chat_id', 'NOT SET')}")
    
    print(f"\n📱 Telegram Bot 2 Config:")
    print(f"   Enabled: {telegram_config_2.get('enabled', False)}")
    print(f"   Bot Token: {'***' + telegram_config_2.get('bot_token', '')[-4:] if telegram_config_2.get('bot_token') else 'NOT SET'}")
    print(f"   Chat ID: {telegram_config_2.get('chat_id', 'NOT SET')}")
    
    # Check if at least one bot is enabled
    bot_1_enabled = telegram_config_1.get('enabled', False) and telegram_config_1.get('bot_token')
    bot_2_enabled = telegram_config_2.get('enabled', False) and telegram_config_2.get('bot_token')
    
    if not bot_1_enabled and not bot_2_enabled:
        print("\n❌ ERROR: No Telegram bots are enabled or configured!")
        print("   Please set TELEGRAM_BOT_TOKEN_1 and TELEGRAM_CHAT_ID_1 in your .env file")
        assert False, "No Telegram bots configured"
    
    # Create Telegram client
    print("\n🔧 Creating Telegram client...")
    client = TelegramNotificationClient(
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2,
        enabled=True
    )
    
    if not client.enabled:
        print("\n❌ ERROR: Telegram client is not enabled!")
        assert False, "Telegram client not enabled"
    
    print(f"✅ Telegram client created")
    print(f"   Bot 1 initialized: {client.bot_1 is not None}")
    print(f"   Bot 2 initialized: {client.bot_2 is not None}")
    
    # Send test message
    test_message = (
        f"🧪 TEST MESSAGE\n\n"
        f"This is a test message from the notification workflow test.\n"
        f"Sent at: {datetime.now().isoformat()}\n\n"
        f"If you receive this, Telegram connectivity is working correctly!"
    )
    
    print(f"\n📤 Sending test message...")
    print(f"   Message length: {len(test_message)} characters")
    
    success, error = await client.send_message(text=test_message)
    
    if success:
        print(f"\n✅ SUCCESS: Message sent to Telegram!")
        print(f"   Check your Telegram chat to confirm receipt.")
        return True
    else:
        print(f"\n❌ FAILED: Message could not be sent")
        print(f"   Error: {error}")
        print(f"\n   This indicates a Telegram API connectivity issue:")
        print(f"   - Check bot token is correct")
        print(f"   - Check chat ID is correct")
        print(f"   - Check network connectivity to api.telegram.org")
        print(f"   - Check if bot is blocked or deleted")
        return False


if __name__ == "__main__":
    success = asyncio.run(test_send_real_telegram_message())
    sys.exit(0 if success else 1)
