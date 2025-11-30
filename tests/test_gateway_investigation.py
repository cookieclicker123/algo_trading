#!/usr/bin/env python3
"""
Active Gateway Connection Investigation

Test different scenarios to find why Gateway isn't accepting connections.
"""
import asyncio
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ib_insync import IB
from newsflash.utils.logging_config import setup_logging, get_logger
from newsflash.config import settings

setup_logging()
logger = get_logger(__name__)


def test_raw_socket_connection():
    """Test if we can even establish a TCP connection."""
    logger.info("TEST 1: Raw TCP Socket Connection")
    logger.info("-" * 80)
    
    port = settings.IBKR_PAPER_TRADING_PORT
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(('127.0.0.1', port))
        logger.info(f"✅ Raw socket connection SUCCESSFUL on port {port}")
        sock.close()
        return True
    except Exception as e:
        logger.error(f"❌ Raw socket connection FAILED: {e}")
        return False


async def test_different_client_ids():
    """Test with different client IDs to see if any work."""
    logger.info("")
    logger.info("TEST 2: Different Client IDs")
    logger.info("-" * 80)
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_ids = [0, 1, 5, 99]
    
    for client_id in client_ids:
        logger.info(f"Trying client ID {client_id}...")
        try:
            ib = IB()
            await asyncio.wait_for(
                ib.connectAsync('127.0.0.1', port, clientId=client_id),
                timeout=5
            )
            logger.info(f"✅ SUCCESS with client ID {client_id}!")
            ib.disconnect()
            return True
        except asyncio.TimeoutError:
            logger.info(f"❌ Timeout with client ID {client_id}")
        except Exception as e:
            logger.info(f"❌ Failed with client ID {client_id}: {e}")
    
    return False


async def test_connection_with_logging():
    """Test connection and log exactly what happens."""
    logger.info("")
    logger.info("TEST 3: Connection with Detailed Logging")
    logger.info("-" * 80)
    
    port = settings.IBKR_PAPER_TRADING_PORT
    client_id = 99
    
    logger.info(f"Creating IB instance...")
    ib = IB()
    
    logger.info(f"Setting timeout to 10 seconds...")
    ib.RequestTimeout = 10
    
    logger.info(f"Attempting connection to 127.0.0.1:{port} with clientId={client_id}...")
    logger.info("Watch Gateway logs NOW - does it show connection attempt?")
    
    try:
        await ib.connectAsync('127.0.0.1', port, clientId=client_id)
        logger.info("✅ CONNECTED!")
        return True
    except asyncio.TimeoutError:
        logger.error("❌ TIMEOUT after 10 seconds")
        logger.error("")
        logger.error("Check Gateway logs - did it show connection attempt?")
        logger.error("Check Gateway window - any error messages?")
        return False
    except Exception as e:
        logger.error(f"❌ ERROR: {e}")
        logger.error("Check Gateway logs for details")
        return False


def test_port_detailed():
    """Get detailed info about port 4001."""
    logger.info("")
    logger.info("TEST 4: Detailed Port Information")
    logger.info("-" * 80)
    
    port = settings.IBKR_PAPER_TRADING_PORT
    
    import subprocess
    
    # Check what's listening on port 4001
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.stdout:
            logger.info(f"Processes using port {port}:")
            logger.info(result.stdout)
        else:
            logger.warning(f"No processes found using port {port}")
    except Exception as e:
        logger.error(f"Error checking port: {e}")
    
    # Test connection
    logger.info("")
    logger.info("Attempting socket connection...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        
        if result == 0:
            logger.info(f"✅ Port {port} accepts connections")
        else:
            logger.warning(f"⚠️  Port {port} connection attempt returned code: {result}")
            logger.warning("   This might indicate port is filtered or Gateway is rejecting")
    except Exception as e:
        logger.error(f"Error testing socket: {e}")


async def main():
    """Run all investigation tests."""
    logger.info("🔍 Gateway Connection Investigation")
    logger.info("=" * 80)
    logger.info("")
    
    # Test 1: Raw socket
    socket_works = test_raw_socket_connection()
    
    # Test 2: Different client IDs
    client_id_works = await test_different_client_ids()
    
    # Test 3: Detailed connection test
    connection_works = await test_connection_with_logging()
    
    # Test 4: Port details
    test_port_detailed()
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("INVESTIGATION SUMMARY")
    logger.info("=" * 80)
    
    logger.info(f"Raw socket connection: {'✅ WORKS' if socket_works else '❌ FAILS'}")
    logger.info(f"Any client ID works: {'✅ YES' if client_id_works else '❌ NO'}")
    logger.info(f"Connection works: {'✅ YES' if connection_works else '❌ NO'}")
    
    logger.info("")
    if not socket_works:
        logger.error("🔴 CRITICAL: Cannot even establish TCP connection")
        logger.error("   Gateway is rejecting ALL connections at TCP level")
        logger.error("   Check: Firewall, Gateway not ready, port blocked")
    elif not connection_works:
        logger.warning("🟡 Gateway accepts TCP but rejects IB protocol")
        logger.warning("   Check: Gateway API settings, client ID restrictions")
        logger.warning("   Check: Gateway logs for rejection reasons")


if __name__ == "__main__":
    asyncio.run(main())

