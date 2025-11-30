#!/usr/bin/env python3
"""
Compare our current connection method with what might have worked before.

Since user says it worked before, let's test different connection patterns
to see if our threading/event loop approach changed something.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ib_insync import IB, util
from newsflash.utils.logging_config import setup_logging, get_logger
from newsflash.config import settings

setup_logging()
logger = get_logger(__name__)


async def test_simple_connect_pattern():
    """Test the simplest possible pattern - what might have worked before."""
    logger.info("=" * 80)
    logger.info("Test 1: Simplest Connection Pattern")
    logger.info("=" * 80)
    logger.info("")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_id = 1
    
    logger.info("Using ib.connectAsync() directly (no threading, no complexity)")
    
    try:
        ib = IB()
        await ib.connectAsync('127.0.0.1', port, clientId=client_id)
        logger.info("✅ SUCCESS!")
        ib.disconnect()
        return True
    except Exception as e:
        logger.error(f"❌ FAILED: {e}")
        return False


def test_sync_connect_pattern():
    """Test synchronous connection - what older code might have used."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Test 2: Synchronous Connection Pattern")
    logger.info("=" * 80)
    logger.info("")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_id = 2
    
    logger.info("Using ib.connect() synchronously (like older ib_insync patterns)")
    
    try:
        ib = IB()
        ib.connect('127.0.0.1', port, clientId=client_id)
        logger.info("✅ SUCCESS!")
        ib.disconnect()
        return True
    except Exception as e:
        logger.error(f"❌ FAILED: {e}")
        return False


async def test_util_run_pattern():
    """Test using ib_insync's util.run() wrapper - recommended pattern."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Test 3: util.run() Pattern")
    logger.info("=" * 80)
    logger.info("")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_id = 3
    
    logger.info("Using util.run() wrapper (ib_insync recommended pattern)")
    
    def connect():
        ib = IB()
        ib.connect('127.0.0.1', port, clientId=client_id)
        logger.info("✅ SUCCESS!")
        ib.disconnect()
        return True
    
    try:
        return util.run(connect())
    except Exception as e:
        logger.error(f"❌ FAILED: {e}")
        return False


async def test_with_different_timeouts():
    """Test if timeout settings matter."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Test 4: Different Timeout Settings")
    logger.info("=" * 80)
    logger.info("")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_id = 4
    
    timeouts = [5, 10, 30, 60]
    
    for timeout in timeouts:
        logger.info(f"Testing with RequestTimeout={timeout}s...")
        try:
            ib = IB()
            ib.RequestTimeout = timeout
            await asyncio.wait_for(
                ib.connectAsync('127.0.0.1', port, clientId=client_id),
                timeout=timeout + 2
            )
            logger.info(f"✅ SUCCESS with timeout {timeout}s!")
            ib.disconnect()
            return True
        except asyncio.TimeoutError:
            logger.info(f"❌ Timeout with {timeout}s")
        except Exception as e:
            logger.info(f"❌ Failed with {timeout}s: {e}")
    
    return False


async def test_gateway_logs_connection():
    """Test if Gateway logs show our connection attempt."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Test 5: Check Gateway Response")
    logger.info("=" * 80)
    logger.info("")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_id = 99
    
    logger.info("Connecting now - PLEASE CHECK GATEWAY LOGS:")
    logger.info("  1. Click 'Show API messages' in Gateway")
    logger.info("  2. Watch for connection attempts in next 5 seconds")
    logger.info("  3. Do you see ANY log entry when we try to connect?")
    logger.info("")
    
    await asyncio.sleep(2)  # Give user time to check logs
    
    try:
        ib = IB()
        ib.RequestTimeout = 5
        logger.info("Attempting connection...")
        await ib.connectAsync('127.0.0.1', port, clientId=client_id)
        logger.info("✅ SUCCESS!")
        ib.disconnect()
        return True
    except Exception as e:
        logger.error(f"❌ FAILED: {e}")
        logger.error("")
        logger.error("Did Gateway logs show ANY connection attempt?")
        logger.error("This tells us if Gateway sees us at all.")
        return False


async def main():
    """Run all comparison tests."""
    results = {}
    
    # Test 1: Simple async
    results['simple_async'] = await test_simple_connect_pattern()
    
    # Test 2: Sync (run in thread to not block)
    import threading
    sync_result = [False]
    def run_sync():
        try:
            sync_result[0] = test_sync_connect_pattern()
        except Exception as e:
            logger.error(f"Sync test error: {e}")
    
    thread = threading.Thread(target=run_sync)
    thread.start()
    thread.join(timeout=10)
    results['sync'] = sync_result[0]
    
    # Test 3: util.run
    results['util_run'] = await test_util_run_pattern()
    
    # Test 4: Different timeouts
    results['timeouts'] = await test_with_different_timeouts()
    
    # Test 5: Gateway logs
    results['gateway_logs'] = await test_gateway_logs_connection()
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("RESULTS SUMMARY")
    logger.info("=" * 80)
    
    for test, success in results.items():
        status = "✅ WORKS" if success else "❌ FAILS"
        logger.info(f"{test}: {status}")
    
    logger.info("")
    if not any(results.values()):
        logger.error("ALL connection patterns failed!")
        logger.error("This confirms: Gateway is not accepting ANY connections")
        logger.error("Check Gateway logs - if NO logs appear, Gateway doesn't see us")
        logger.error("If logs DO appear, Gateway sees us but rejects - check log messages")


if __name__ == "__main__":
    asyncio.run(main())

