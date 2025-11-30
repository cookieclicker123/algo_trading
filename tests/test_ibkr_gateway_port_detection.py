#!/usr/bin/env python3
"""
IB Gateway Port Detection

Find out what ports IB Gateway is actually using and why port 4001 isn't listening.
"""
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


def find_listening_ports():
    """Find all ports that IB Gateway might be using."""
    logger.info("=" * 80)
    logger.info("Finding IB Gateway Listening Ports")
    logger.info("=" * 80)
    logger.info("")
    
    # Common IB Gateway ports
    common_ports = [4001, 4002, 7496, 7497, 7777]
    
    logger.info("Checking common IB Gateway ports:")
    logger.info("")
    
    listening_ports = []
    
    for port in common_ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            
            if result == 0:
                logger.info(f"✅ Port {port}: OPEN")
                listening_ports.append(port)
            else:
                logger.info(f"❌ Port {port}: CLOSED")
        except Exception as e:
            logger.info(f"❌ Port {port}: ERROR - {e}")
    
    logger.info("")
    
    # Check what process is using ports
    logger.info("Checking what processes are using these ports:")
    logger.info("")
    
    try:
        # Use lsof to find processes using ports
        for port in common_ports:
            try:
                result = subprocess.run(
                    ["lsof", "-i", f":{port}"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    logger.info(f"Port {port}:")
                    logger.info(result.stdout.strip())
                    logger.info("")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Could not check process info: {e}")
    
    return listening_ports


def check_gateway_status():
    """Check Gateway UI status indicators."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Gateway Status Check")
    logger.info("=" * 80)
    logger.info("")
    logger.info("Please check IB Gateway UI and report:")
    logger.info("")
    logger.info("1. In the Gateway window, what does it show for:")
    logger.info("   - 'Interactive Brokers API Server' status: [connected/disconnected]")
    logger.info("   - 'Market Data Farm' status: [ON/OFF]")
    logger.info("   - 'API Client' status: [connected/disconnected]")
    logger.info("")
    logger.info("2. Is there a green checkmark or red X next to 'Interactive Brokers API Server'?")
    logger.info("")
    
    api_server_status = input("What is the 'Interactive Brokers API Server' status? (connected/disconnected/other): ").strip().lower()
    
    logger.info("")
    logger.info(f"API Server Status: {api_server_status}")
    
    if "disconnect" in api_server_status or "red" in api_server_status or api_server_status == "x":
        logger.error("")
        logger.error("⚠️  PROBLEM IDENTIFIED:")
        logger.error("   The 'Interactive Brokers API Server' is DISCONNECTED or not ready")
        logger.error("")
        logger.error("🔧 SOLUTION:")
        logger.error("   1. Check if Gateway is fully logged in to IBKR")
        logger.error("   2. Wait for Gateway to fully initialize (can take 30-60 seconds)")
        logger.error("   3. Look for error messages in Gateway window")
        logger.error("   4. Try restarting Gateway")
        return False
    elif "connect" in api_server_status or "green" in api_server_status:
        logger.info("")
        logger.info("✅ API Server appears to be connected")
        logger.info("   But port 4001 still not listening - this is unusual")
        logger.info("   Checking if Gateway is listening on a different port...")
        return True
    else:
        logger.warning("⚠️  Unclear status - Gateway may not be ready")
        return False


def check_alternative_ports():
    """Check if Gateway is listening on alternative ports."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Checking Alternative Ports")
    logger.info("=" * 80)
    logger.info("")
    
    # Try to find all listening ports
    logger.info("Scanning for listening ports (1000-50000)...")
    logger.info("(This may take a moment...)")
    
    listening_ports = []
    
    # Check common ranges (don't scan everything, just common ones)
    test_ports = list(range(4000, 4010)) + list(range(7496, 7500)) + [7777]
    
    for port in test_ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            
            if result == 0:
                listening_ports.append(port)
        except Exception:
            pass
    
    if listening_ports:
        logger.info("")
        logger.info(f"✅ Found listening ports: {listening_ports}")
        logger.info("")
        logger.warning("⚠️  Gateway may be using a different port than expected!")
        logger.warning("   Check Gateway API settings to see what port it's actually using")
    else:
        logger.info("")
        logger.warning("⚠️  No listening ports found in common ranges")
    
    return listening_ports


def main():
    """Run all checks."""
    logger.info("🔍 IB Gateway Port Detection")
    logger.info("")
    
    # Check what ports are listening
    listening_ports = find_listening_ports()
    
    # Check Gateway status
    api_ready = check_gateway_status()
    
    # Check alternative ports
    alt_ports = check_alternative_ports()
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info("")
    
    if not listening_ports:
        logger.error("❌ CRITICAL: No ports are listening!")
        logger.error("")
        logger.error("This means:")
        logger.error("  1. Gateway process is running BUT")
        logger.error("  2. Gateway API server is NOT started/ready")
        logger.error("")
        logger.error("🔧 LIKELY CAUSES:")
        logger.error("  - Gateway is still starting up (wait 30-60 seconds)")
        logger.error("  - Gateway failed to connect to IBKR servers")
        logger.error("  - Gateway login failed or session expired")
        logger.error("  - Gateway is in a broken state (needs restart)")
        logger.error("")
        logger.error("🔧 NEXT STEPS:")
        logger.error("  1. Check Gateway window for error messages")
        logger.error("  2. Wait for Gateway to fully initialize")
        logger.error("  3. Verify Gateway is logged in successfully")
        logger.error("  4. Try restarting Gateway")
    else:
        logger.info(f"✅ Found listening ports: {listening_ports}")
        logger.info("")
        if 4001 not in listening_ports:
            logger.warning("⚠️  Port 4001 is NOT in the listening ports!")
            logger.warning("   Gateway is using a different port")
            logger.warning("   Update your configuration to use one of these ports")


if __name__ == "__main__":
    main()

