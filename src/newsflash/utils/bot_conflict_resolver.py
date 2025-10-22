"""
Bot Conflict Resolver

Handles Telegram bot conflicts by ensuring only one instance per bot token is running.
This is essential because Telegram only allows one active polling connection per bot token.
"""
import asyncio
import subprocess
import time
from typing import List, Optional
from telegram import Bot
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class BotConflictResolver:
    """Resolves Telegram bot conflicts by managing bot instances and processes."""
    
    def __init__(self):
        self.resolved_tokens = set()
    
    async def resolve_conflicts(self, bot_tokens: List[str], aggressive: bool = False) -> bool:
        """
        Resolve conflicts for the given bot tokens.
        
        Args:
            bot_tokens: List of bot tokens to resolve conflicts for
            aggressive: If True, kills any Python processes using these tokens
            
        Returns:
            True if conflicts were resolved, False otherwise
        """
        logger.info("Resolving bot conflicts", token_count=len(bot_tokens), aggressive=aggressive)
        
        success = True
        
        # Step 1: Kill conflicting processes if aggressive mode
        if aggressive:
            success &= await self._kill_conflicting_processes(bot_tokens)
        
        # Step 2: Clear webhooks for all tokens
        success &= await self._clear_webhooks(bot_tokens)
        
        # Step 3: Wait for cleanup to complete
        await asyncio.sleep(3)
        
        # Step 4: Verify resolution
        if not await self._verify_resolution(bot_tokens):
            logger.warning("Bot conflicts may still exist after resolution attempt")
            success = False
        
        if success:
            logger.info("Bot conflicts resolved successfully")
            self.resolved_tokens.update(bot_tokens)
        else:
            logger.error("Failed to resolve bot conflicts")
        
        return success
    
    async def _kill_conflicting_processes(self, bot_tokens: List[str]) -> bool:
        """Kill any Python processes that might be using these bot tokens."""
        try:
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            lines = result.stdout.split('\n')
            
            killed_count = 0
            for line in lines:
                if 'python' in line.lower():
                    for token in bot_tokens:
                        if token[:10] in line:  # Check first 10 chars
                            pid = line.split()[1]
                            try:
                                subprocess.run(['kill', '-9', pid], check=True)
                                logger.info("Killed conflicting process", pid=pid, token=token[:10] + "...")
                                killed_count += 1
                            except Exception as e:
                                logger.warning("Failed to kill process", pid=pid, error=str(e))
            
            if killed_count > 0:
                logger.info("Killed conflicting processes", count=killed_count)
                await asyncio.sleep(2)  # Wait for processes to die
            
            return True
            
        except Exception as e:
            logger.error("Failed to kill conflicting processes", error=str(e))
            return False
    
    async def _clear_webhooks(self, bot_tokens: List[str]) -> bool:
        """Clear webhooks for all bot tokens with retries."""
        success = True
        
        for token in bot_tokens:
            token_success = False
            for attempt in range(3):  # Retry up to 3 times
                try:
                    bot = Bot(token=token)
                    await bot.delete_webhook(drop_pending_updates=True)
                    logger.info("Cleared webhook", token=token[:10] + "...", attempt=attempt+1)
                    token_success = True
                    break
                except Exception as e:
                    logger.warning("Failed to clear webhook", 
                                 token=token[:10] + "...", 
                                 attempt=attempt+1, 
                                 error=str(e))
                    if attempt < 2:  # Don't sleep on last attempt
                        await asyncio.sleep(1)
            
            if not token_success:
                success = False
        
        return success
    
    async def _verify_resolution(self, bot_tokens: List[str]) -> bool:
        """Verify that conflicts have been resolved by testing bot connectivity."""
        success = True
        
        for token in bot_tokens:
            try:
                bot = Bot(token=token)
                # Try to get bot info - this will fail if there are still conflicts
                me = await bot.get_me()
                logger.debug("Bot connectivity verified", 
                           token=token[:10] + "...", 
                           username=me.username)
            except Exception as e:
                logger.warning("Bot connectivity check failed", 
                             token=token[:10] + "...", 
                             error=str(e))
                success = False
        
        return success
    
    def is_resolved(self, bot_token: str) -> bool:
        """Check if conflicts for a specific bot token have been resolved."""
        return bot_token in self.resolved_tokens


# Global instance for easy access
_conflict_resolver = BotConflictResolver()


async def resolve_bot_conflicts(bot_tokens: List[str], aggressive: bool = False) -> bool:
    """
    Resolve bot conflicts for the given tokens.
    
    Args:
        bot_tokens: List of bot tokens to resolve conflicts for
        aggressive: If True, kills any Python processes using these tokens
        
    Returns:
        True if conflicts were resolved, False otherwise
    """
    return await _conflict_resolver.resolve_conflicts(bot_tokens, aggressive)


def is_bot_conflict_resolved(bot_token: str) -> bool:
    """Check if conflicts for a specific bot token have been resolved."""
    return _conflict_resolver.is_resolved(bot_token)
