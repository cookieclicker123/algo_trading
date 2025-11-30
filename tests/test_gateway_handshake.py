#!/usr/bin/env python3
"""
Test Gateway connection handshake step-by-step to see where it fails.

This test will:
1. Capture raw socket data exchange
2. Test connection sequence
3. Compare with what Gateway expects
"""
import socket
import struct
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


def test_raw_gateway_handshake():
    """Try to establish raw connection and see Gateway's response."""
    logger.info("=" * 80)
    logger.info("Raw Gateway Handshake Test")
    logger.info("=" * 80)
    logger.info("")
    
    port = 4001
    host = '127.0.0.1'
    
    logger.info(f"Connecting to {host}:{port}...")
    
    try:
        # Create socket with short timeout
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        # Try to connect
        logger.info("Attempting TCP connection...")
        start_time = time.time()
        
        try:
            sock.connect((host, port))
            elapsed = time.time() - start_time
            logger.info(f"✅ TCP connection established in {elapsed:.3f}s!")
            
            # Try to read Gateway's initial message
            logger.info("Waiting for Gateway's initial response...")
            sock.settimeout(2)
            try:
                data = sock.recv(1024)
                if data:
                    logger.info(f"✅ Received {len(data)} bytes from Gateway:")
                    logger.info(f"   Hex: {data.hex()[:100]}...")
                    logger.info(f"   Ascii (if printable): {repr(data[:50])}")
                else:
                    logger.info("⚠️  No data received (connection closed immediately)")
            except socket.timeout:
                logger.info("⚠️  Gateway did not send initial data (timeout)")
            except Exception as e:
                logger.error(f"❌ Error receiving data: {e}")
            
            sock.close()
            return True
            
        except ConnectionRefusedError:
            elapsed = time.time() - start_time
            logger.error(f"❌ Connection REFUSED immediately (after {elapsed:.3f}s)")
            logger.error("   Gateway is actively rejecting the connection")
            return False
            
        except socket.timeout:
            elapsed = time.time() - start_time
            logger.error(f"❌ Connection TIMEOUT (after {elapsed:.3f}s)")
            logger.error("   Gateway is not responding at all")
            return False
            
    except Exception as e:
        logger.error(f"❌ Socket error: {e}", exc_info=True)
        return False


def test_gateway_response_detailed():
    """More detailed test of Gateway's response behavior."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Detailed Gateway Response Test")
    logger.info("=" * 80)
    logger.info("")
    
    port = 4001
    
    # Test multiple connection attempts to see pattern
    logger.info("Testing multiple rapid connection attempts...")
    logger.info("(Gateway might reject rapid connections)")
    logger.info("")
    
    for i in range(3):
        logger.info(f"Attempt {i+1}/3...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            
            start = time.time()
            result = sock.connect_ex(('127.0.0.1', port))
            elapsed = time.time() - start
            
            sock.close()
            
            if result == 0:
                logger.info(f"   ✅ Connected in {elapsed:.3f}s")
            else:
                logger.info(f"   ❌ Failed with code {result} after {elapsed:.3f}s")
                # Error codes: 35=EWOULDBLOCK, 61=ECONNREFUSED, 51=ENETUNREACH
                error_names = {35: "EWOULDBLOCK (would block)", 61: "ECONNREFUSED (refused)", 
                              51: "ENETUNREACH (network unreachable)", 60: "ETIMEDOUT (timeout)"}
                if result in error_names:
                    logger.info(f"      Meaning: {error_names[result]}")
            
            time.sleep(0.5)  # Small delay between attempts
            
        except Exception as e:
            logger.error(f"   ❌ Exception: {e}")


def check_gateway_version_compatibility():
    """Check if there's a version mismatch issue."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Gateway Version Check")
    logger.info("=" * 80)
    logger.info("")
    
    # Try to see if Gateway expects a specific protocol version
    logger.info("Gateway is version 10.37 (from UI)")
    logger.info("Testing if Gateway accepts connection but rejects protocol...")
    logger.info("")
    
    # IB Gateway/TWS protocol typically starts with version negotiation
    # If Gateway accepts TCP but rejects protocol, we might see different behavior
    
    port = 4001
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        # Connect
        logger.info("Connecting...")
        try:
            sock.connect(('127.0.0.1', port))
            logger.info("✅ TCP connection established")
            
            # Try sending minimal IB protocol message
            # IB protocol typically starts with version string
            # But we don't know exact format, so just check if Gateway closes immediately
            
            logger.info("Checking if Gateway maintains connection...")
            sock.settimeout(2)
            
            # Wait a moment
            time.sleep(0.5)
            
            # Check if still connected
            try:
                sock.send(b'\x00')  # Send minimal byte
                logger.info("✅ Can send data to Gateway")
            except BrokenPipeError:
                logger.error("❌ Gateway closed connection immediately")
            except Exception as e:
                logger.warning(f"⚠️  Send error: {e}")
            
            sock.close()
            
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            
    except Exception as e:
        logger.error(f"❌ Socket setup failed: {e}")


async def main():
    """Run all handshake tests."""
    # Test 1: Raw handshake
    test_raw_gateway_handshake()
    
    # Test 2: Detailed response
    test_gateway_response_detailed()
    
    # Test 3: Version compatibility
    check_gateway_version_compatibility()
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("INTERPRETATION:")
    logger.info("=" * 80)
    logger.info("")
    logger.info("If TCP connection works but Gateway closes immediately:")
    logger.info("  → Protocol/handshake mismatch")
    logger.info("  → Gateway version incompatibility")
    logger.info("  → Gateway requires specific client authentication")
    logger.info("")
    logger.info("If TCP connection is refused:")
    logger.info("  → Gateway firewall/security blocking")
    logger.info("  → Gateway not actually ready despite UI showing connected")
    logger.info("  → Port bound but not accepting connections")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

