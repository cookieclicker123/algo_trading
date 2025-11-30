#!/usr/bin/env python3
"""
Critical test: Does Gateway see our connection attempts?

The key question: When we try to connect, does Gateway log anything?
If Gateway doesn't log, it never sees us (OS-level filtering).
If Gateway logs but rejects, it sees us (Gateway-level filtering).
"""
import socket
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


def test_if_gateway_sees_us():
    """Make connection attempt and check if Gateway sees it."""
    logger.info("=" * 80)
    logger.info("CRITICAL TEST: Does Gateway See Our Connection Attempts?")
    logger.info("=" * 80)
    logger.info("")
    logger.info("📋 INSTRUCTIONS:")
    logger.info("  1. Open Gateway window")
    logger.info("  2. Click 'Show API messages' tab")
    logger.info("  3. Watch the log window")
    logger.info("  4. When I say 'NOW', watch for any log entries")
    logger.info("")
    
    input("Press ENTER when Gateway log window is open and ready...")
    
    port = 4001
    
    logger.info("")
    logger.info("Making connection attempt NOW - watch Gateway logs!")
    logger.info("")
    
    # Try multiple connection methods
    for i in range(3):
        logger.info(f"Connection attempt {i+1}/3...")
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            
            if result == 0:
                logger.info(f"   ✅ Connected!")
                break
            else:
                logger.info(f"   ❌ Failed (error {result})")
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"   Exception: {e}")
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("QUESTION: Did Gateway logs show ANYTHING?")
    logger.info("=" * 80)
    logger.info("")
    logger.info("If YES - Gateway sees us but is rejecting:")
    logger.info("  → Check log messages for rejection reason")
    logger.info("  → Gateway API configuration issue")
    logger.info("")
    logger.info("If NO - Gateway doesn't see us at all:")
    logger.info("  → Connection blocked before reaching Gateway")
    logger.info("  → Port bound but socket not accepting")
    logger.info("  → Gateway in stuck/broken state")
    logger.info("")
    
    response = input("Did Gateway logs show ANY entry? (yes/no): ").strip().lower()
    
    if 'yes' in response:
        logger.info("")
        logger.info("✅ Gateway SEES our connection attempts")
        logger.info("   This means Gateway is actively rejecting connections")
        logger.info("   Check Gateway log messages for the rejection reason")
        logger.info("")
        logger.info("Common reasons:")
        logger.info("  - Client ID not allowed")
        logger.info("  - IP not trusted (though 127.0.0.1 should be)")
        logger.info("  - API client access disabled internally")
        logger.info("  - Gateway version/protocol mismatch")
    else:
        logger.info("")
        logger.info("❌ Gateway does NOT see our connection attempts")
        logger.info("   This means connections are blocked before reaching Gateway")
        logger.info("")
        logger.info("Possible causes:")
        logger.info("  - Gateway socket not actually in LISTEN state")
        logger.info("  - Gateway process stuck/frozen")
        logger.info("  - Gateway needs restart")
        logger.info("  - Java process issue with socket binding")


if __name__ == "__main__":
    test_if_gateway_sees_us()

