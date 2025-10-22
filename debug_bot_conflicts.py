#!/usr/bin/env python3
"""
Professional debugging tool for Telegram bot conflicts.
This will help us understand exactly what's causing the 409 errors.
"""
import asyncio
import subprocess
import sys
from pathlib import Path
import json
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from newsflash.config.settings import get_telegram_config, get_telegram_config_2
from newsflash.utils.logging_config import setup_logging, get_logger
from telegram import Bot
from telegram.error import Conflict, TelegramError

# Setup logging
setup_logging()
logger = get_logger(__name__)


class BotConflictDebugger:
    """Professional debugging tool for Telegram bot conflicts."""
    
    def __init__(self):
        self.config_1 = get_telegram_config()
        self.config_2 = get_telegram_config_2()
        self.bot_tokens = [
            self.config_1.get("bot_token", ""),
            self.config_2.get("bot_token", "")
        ]
        self.chat_ids = [
            self.config_1.get("chat_id", ""),
            self.config_2.get("chat_id", "")
        ]
    
    async def debug_comprehensive(self):
        """Run comprehensive debugging analysis."""
        print("🔍 TELEGRAM BOT CONFLICT DEBUGGER")
        print("=" * 50)
        
        # Step 1: Check system processes
        await self._check_system_processes()
        
        # Step 2: Check bot connectivity
        await self._check_bot_connectivity()
        
        # Step 3: Check webhook status
        await self._check_webhook_status()
        
        # Step 4: Test polling directly
        await self._test_polling_directly()
        
        # Step 5: Check Telegram API limits
        await self._check_api_limits()
        
        print("\n🎯 DEBUGGING COMPLETE")
        print("=" * 50)
    
    async def _check_system_processes(self):
        """Check for any Python processes that might be using our bots."""
        print("\n📋 STEP 1: SYSTEM PROCESS ANALYSIS")
        print("-" * 30)
        
        try:
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            lines = result.stdout.split('\n')
            
            python_processes = []
            bot_processes = []
            
            for line in lines:
                if 'python' in line.lower():
                    python_processes.append(line)
                    
                    # Check if any of our bot tokens appear in the process
                    for i, token in enumerate(self.bot_tokens):
                        if token[:10] in line:
                            bot_processes.append({
                                'line': line,
                                'bot_index': i + 1,
                                'token_prefix': token[:10]
                            })
            
            print(f"Found {len(python_processes)} Python processes")
            print(f"Found {len(bot_processes)} processes using our bot tokens")
            
            if bot_processes:
                print("\n🚨 CONFLICTING PROCESSES DETECTED:")
                for proc in bot_processes:
                    print(f"  Bot {proc['bot_index']} ({proc['token_prefix']}...): {proc['line']}")
            else:
                print("✅ No conflicting processes found")
                
        except Exception as e:
            print(f"❌ Error checking processes: {e}")
    
    async def _check_bot_connectivity(self):
        """Check basic bot connectivity."""
        print("\n📋 STEP 2: BOT CONNECTIVITY CHECK")
        print("-" * 30)
        
        for i, token in enumerate(self.bot_tokens):
            try:
                bot = Bot(token=token)
                me = await bot.get_me()
                print(f"✅ Bot {i+1} ({token[:10]}...): Connected as @{me.username}")
            except Exception as e:
                print(f"❌ Bot {i+1} ({token[:10]}...): Connection failed - {e}")
    
    async def _check_webhook_status(self):
        """Check webhook status for both bots."""
        print("\n📋 STEP 3: WEBHOOK STATUS CHECK")
        print("-" * 30)
        
        for i, token in enumerate(self.bot_tokens):
            try:
                bot = Bot(token=token)
                webhook_info = await bot.get_webhook_info()
                
                if webhook_info.url:
                    print(f"🔗 Bot {i+1} ({token[:10]}...): Webhook active at {webhook_info.url}")
                    print(f"   Pending updates: {webhook_info.pending_update_count}")
                else:
                    print(f"✅ Bot {i+1} ({token[:10]}...): No webhook (polling mode)")
                    
            except Exception as e:
                print(f"❌ Bot {i+1} ({token[:10]}...): Webhook check failed - {e}")
    
    async def _test_polling_directly(self):
        """Test polling directly to see what happens."""
        print("\n📋 STEP 4: DIRECT POLLING TEST")
        print("-" * 30)
        
        for i, token in enumerate(self.bot_tokens):
            try:
                bot = Bot(token=token)
                
                print(f"Testing Bot {i+1} ({token[:10]}...) polling...")
                
                # Try to get updates
                updates = await bot.get_updates(limit=1, timeout=1)
                print(f"✅ Bot {i+1}: Polling successful, got {len(updates)} updates")
                
            except Conflict as e:
                print(f"🚨 Bot {i+1}: CONFLICT ERROR - {e}")
                print(f"   This means another instance is already polling!")
                
            except Exception as e:
                print(f"❌ Bot {i+1}: Polling failed - {e}")
    
    async def _check_api_limits(self):
        """Check if we're hitting API rate limits."""
        print("\n📋 STEP 5: API LIMITS CHECK")
        print("-" * 30)
        
        for i, token in enumerate(self.bot_tokens):
            try:
                bot = Bot(token=token)
                
                # Make multiple rapid requests to test rate limits
                for j in range(3):
                    try:
                        await bot.get_me()
                        print(f"✅ Bot {i+1}: API request {j+1} successful")
                    except Exception as e:
                        print(f"❌ Bot {i+1}: API request {j+1} failed - {e}")
                        
            except Exception as e:
                print(f"❌ Bot {i+1}: API limits check failed - {e}")
    
    async def force_cleanup(self):
        """Force cleanup of all bot states."""
        print("\n🧹 FORCE CLEANUP")
        print("-" * 30)
        
        for i, token in enumerate(self.bot_tokens):
            try:
                bot = Bot(token=token)
                
                # Force delete webhook
                await bot.delete_webhook(drop_pending_updates=True)
                print(f"✅ Bot {i+1}: Webhook deleted")
                
                # Wait a moment
                await asyncio.sleep(1)
                
                # Try to get updates to clear any pending state
                try:
                    updates = await bot.get_updates(limit=100, timeout=1)
                    print(f"✅ Bot {i+1}: Cleared {len(updates)} pending updates")
                except:
                    print(f"✅ Bot {i+1}: No pending updates to clear")
                    
            except Exception as e:
                print(f"❌ Bot {i+1}: Cleanup failed - {e}")
    
    async def test_sequential_startup(self):
        """Test starting bots one at a time to isolate the conflict."""
        print("\n🔄 SEQUENTIAL STARTUP TEST")
        print("-" * 30)
        
        # Start bot 1 first
        print("Starting Bot 1...")
        try:
            bot1 = Bot(token=self.bot_tokens[0])
            updates1 = await bot1.get_updates(limit=1, timeout=1)
            print(f"✅ Bot 1: Started successfully, got {len(updates1)} updates")
        except Exception as e:
            print(f"❌ Bot 1: Failed to start - {e}")
            return
        
        # Wait a moment
        await asyncio.sleep(2)
        
        # Try to start bot 2
        print("Starting Bot 2...")
        try:
            bot2 = Bot(token=self.bot_tokens[1])
            updates2 = await bot2.get_updates(limit=1, timeout=1)
            print(f"✅ Bot 2: Started successfully, got {len(updates2)} updates")
        except Exception as e:
            print(f"❌ Bot 2: Failed to start - {e}")
            print("   This suggests the conflict is between the two bots themselves!")


async def main():
    """Main debugging function."""
    debugger = BotConflictDebugger()
    
    print(f"Bot 1 Token: {debugger.bot_tokens[0][:10]}...")
    print(f"Bot 2 Token: {debugger.bot_tokens[1][:10]}...")
    print(f"Chat 1 ID: {debugger.chat_ids[0]}")
    print(f"Chat 2 ID: {debugger.chat_ids[1]}")
    
    # Run comprehensive debugging
    await debugger.debug_comprehensive()
    
    # Force cleanup
    await debugger.force_cleanup()
    
    # Test sequential startup
    await debugger.test_sequential_startup()


if __name__ == "__main__":
    asyncio.run(main())
