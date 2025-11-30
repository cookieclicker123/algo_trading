#!/usr/bin/env python3
"""
Check Gateway API settings and test connection behavior.

Since Gateway is accepting then closing connections, we need to check:
1. Are API clients enabled?
2. Are there client ID restrictions?
3. Are there trusted IP restrictions?
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


async def test_connection_and_watch():
    """Connect and immediately see if Gateway accepts or rejects."""
    logger.info("=" * 80)
    logger.info("Gateway Connection Behavior Test")
    logger.info("=" * 80)
    logger.info("")
    logger.info("Watch Gateway window/logs while this runs!")
    logger.info("Does Gateway show connection attempt?")
    logger.info("Does Gateway immediately close it?")
    logger.info("Any error messages?")
    logger.info("")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_id = 1  # Try client ID 1 (lowest)
    
    logger.info(f"Attempting connection with client ID {client_id}...")
    logger.info("Connection should happen in the next few seconds...")
    logger.info("")
    
    ib = IB()
    ib.RequestTimeout = 15
    
    try:
        # Add event handlers to see what Gateway does
        def on_connected():
            logger.info("✅ ib_insync reports: CONNECTED event")
        
        def on_disconnected():
            logger.info("⚠️  ib_insync reports: DISCONNECTED event")
        
        def on_error(reqId, errorCode, errorString, contract):
            logger.error(f"⚠️  ib_insync reports ERROR: {errorCode} - {errorString}")
        
        ib.connectedEvent += on_connected
        ib.disconnectedEvent += on_disconnected
        ib.errorEvent += on_error
        
        logger.info("Calling connectAsync()...")
        await ib.connectAsync('127.0.0.1', port, clientId=client_id)
        
        logger.info("")
        logger.info("✅✅✅ CONNECTION SUCCESSFUL! ✅✅✅")
        logger.info("")
        
        # Try to get account info to verify
        try:
            accounts = ib.accountValues()
            logger.info(f"Verified: Got {len(accounts)} account values")
        except Exception as e:
            logger.warning(f"Connection works but account query failed: {e}")
        
        ib.disconnect()
        return True
        
    except asyncio.TimeoutError:
        logger.error("")
        logger.error("❌❌❌ CONNECTION TIMEOUT ❌❌❌")
        logger.error("")
        logger.error("Gateway did not respond within 15 seconds.")
        logger.error("")
        logger.error("CHECK GATEWAY:")
        logger.error("  1. Open Gateway window")
        logger.error("  2. Go to Configure → API")
        logger.error("  3. Is 'Enable ActiveX and Socket Clients' checked?")
        logger.error("  4. Check 'Trusted IP addresses' - should allow 127.0.0.1")
        logger.error("  5. Check 'Master API client ID' - should not restrict client ID 1")
        logger.error("  6. Look at Gateway logs - any errors when we try to connect?")
        return False
        
    except Exception as e:
        logger.error("")
        logger.error(f"❌❌❌ CONNECTION FAILED: {e} ❌❌❌")
        logger.error("")
        logger.error("Check Gateway logs for details")
        return False


if __name__ == "__main__":
    success = asyncio.run(test_connection_and_watch())
    sys.exit(0 if success else 1)

