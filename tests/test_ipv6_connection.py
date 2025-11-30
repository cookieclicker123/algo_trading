#!/usr/bin/env python3
"""
Test IPv6 connection to Gateway

Gateway is listening on IPv6, but we're connecting via IPv4!
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


def test_ipv6_socket():
    """Test IPv6 socket connection."""
    logger.info("Testing IPv6 socket connection...")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    
    try:
        # Try IPv6 localhost
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(('::1', port))
        logger.info("✅ IPv6 socket connection SUCCESS!")
        sock.close()
        return True
    except Exception as e:
        logger.error(f"❌ IPv6 socket failed: {e}")
        return False


async def test_ipv6_ib_connection():
    """Test ib_insync connection via IPv6."""
    logger.info("Testing ib_insync connection via IPv6...")
    
    port = settings.IBKR_PAPER_TRADING_PORT
    
    try:
        ib = IB()
        # Try IPv6 localhost
        await ib.connectAsync('::1', port, clientId=99)
        logger.info("✅ IPv6 ib_insync connection SUCCESS!")
        ib.disconnect()
        return True
    except Exception as e:
        logger.error(f"❌ IPv6 ib_insync failed: {e}")
        return False


async def test_ipv4_vs_ipv6():
    """Compare IPv4 and IPv6 connections."""
    logger.info("=" * 80)
    logger.info("IPv4 vs IPv6 Connection Test")
    logger.info("=" * 80)
    
    port = settings.IBKR_PAPER_TRADING_PORT
    
    # Test IPv4
    logger.info("")
    logger.info("TEST 1: IPv4 connection (127.0.0.1)")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        logger.info(f"   Result code: {result}")
        if result == 0:
            logger.info("   ✅ IPv4 socket works")
        else:
            logger.info("   ❌ IPv4 socket failed")
    except Exception as e:
        logger.error(f"   ❌ IPv4 error: {e}")
    
    # Test IPv6
    logger.info("")
    logger.info("TEST 2: IPv6 connection (::1)")
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('::1', port))
        sock.close()
        logger.info(f"   Result code: {result}")
        if result == 0:
            logger.info("   ✅ IPv6 socket works")
        else:
            logger.info("   ❌ IPv6 socket failed")
    except Exception as e:
        logger.error(f"   ❌ IPv6 error: {e}")
    
    # Test IPv6 via hostname
    logger.info("")
    logger.info("TEST 3: IPv6 connection (localhost)")
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', port))
        sock.close()
        logger.info(f"   Result code: {result}")
        if result == 0:
            logger.info("   ✅ IPv6 localhost socket works")
        else:
            logger.info("   ❌ IPv6 localhost socket failed")
    except Exception as e:
        logger.error(f"   ❌ IPv6 localhost error: {e}")


async def main():
    """Run IPv6 connection tests."""
    await test_ipv4_vs_ipv6()
    
    logger.info("")
    logger.info("=" * 80)
    
    # Test actual connections
    ipv6_socket = test_ipv6_socket()
    ipv6_ib = await test_ipv6_ib_connection()
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("RESULTS")
    logger.info("=" * 80)
    logger.info(f"IPv6 socket: {'✅ WORKS' if ipv6_socket else '❌ FAILS'}")
    logger.info(f"IPv6 ib_insync: {'✅ WORKS' if ipv6_ib else '❌ FAILS'}")


if __name__ == "__main__":
    asyncio.run(main())

