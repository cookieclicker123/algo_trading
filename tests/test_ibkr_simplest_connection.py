#!/usr/bin/env python3
"""
Simplest possible IB Gateway connection test.

This replicates EXACTLY what the working tests do - no threading, no complexity.
If this works, the problem is in our connection manager code.
If this fails, the problem is Gateway configuration.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ib_insync import IB
from newsflash.utils.logging_config import setup_logging, get_logger
from newsflash.config import settings

setup_logging()
logger = get_logger(__name__)


async def test_simplest_connection():
    """Simplest possible connection - exactly like working tests."""
    logger.info("=" * 80)
    logger.info("Simplest IB Gateway Connection Test")
    logger.info("=" * 80)
    logger.info("")
    logger.info("This test uses the EXACT same pattern as the working tests.")
    logger.info("No threading, no event loop manipulation, just pure async.")
    logger.info("")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_id = 99  # Use unique client ID
    
    logger.info(f"Port: {port}")
    logger.info(f"Client ID: {client_id}")
    logger.info("")
    
    ib = IB()
    
    try:
        logger.info("Connecting...")
        await ib.connectAsync('127.0.0.1', port, clientId=client_id)
        
        logger.info("✅ SUCCESS! Connected to IB Gateway")
        
        # Verify connection
        try:
            accounts = ib.accountValues()
            logger.info(f"✅ Verification successful: {len(accounts) if accounts else 0} accounts")
        except Exception as e:
            logger.warning(f"⚠️  Connection works but verification failed: {e}")
        
        # Disconnect
        ib.disconnect()
        logger.info("✅ Disconnected cleanly")
        
        return True
        
    except TimeoutError as e:
        logger.error(f"❌ TIMEOUT: {e}")
        logger.error("")
        logger.error("This means Gateway is not responding to connection attempts.")
        logger.error("Check:")
        logger.error("  1. Is Gateway fully logged in?")
        logger.error("  2. Is 'Interactive Brokers API Server' showing 'connected'?")
        logger.error("  3. Are there any error messages in Gateway logs?")
        logger.error("  4. Is port 4001 actually listening? (check with: lsof -i :4001)")
        return False
        
    except Exception as e:
        logger.error(f"❌ FAILED: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = asyncio.run(test_simplest_connection())
    sys.exit(0 if success else 1)

