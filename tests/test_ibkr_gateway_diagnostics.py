#!/usr/bin/env python3
"""
IB Gateway Diagnostic Tests

This script performs progressive diagnostic tests to identify why
IB Gateway connections are failing. Each test rules out a category
of problems.
"""
import asyncio
import socket
import subprocess
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.utils.logging_config import setup_logging, get_logger
from newsflash.config import settings

# Setup logging
setup_logging()
logger = get_logger(__name__)


def test_1_gateway_process_running():
    """Test 1: Is IB Gateway process running?"""
    logger.info("=" * 80)
    logger.info("TEST 1: Is IB Gateway Process Running?")
    logger.info("=" * 80)
    
    try:
        # Check for Gateway processes
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        processes = result.stdout.lower()
        gateway_keywords = ["gateway", "ib gateway", "ibgateway"]
        
        found = any(keyword in processes for keyword in gateway_keywords)
        
        if found:
            logger.info("✅ IB Gateway process appears to be running")
            logger.info("   (Found gateway-related process in process list)")
            return True
        else:
            logger.warning("⚠️  IB Gateway process not found in process list")
            logger.warning("   ACTION: Start IB Gateway application")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error checking processes: {e}")
        return False


def test_2_port_listening():
    """Test 2: Is port 4001 (paper trading) listening?"""
    logger.info("")
    logger.info("=" * 80)
    logger.info("TEST 2: Is Port 4001 Listening?")
    logger.info("=" * 80)
    
    port = settings.IBKR_PAPER_TRADING_PORT
    logger.info(f"Checking port {port} (paper trading)...")
    
    try:
        # Try to connect to the port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        
        if result == 0:
            logger.info(f"✅ Port {port} is OPEN and accepting connections")
            return True
        else:
            logger.warning(f"⚠️  Port {port} is CLOSED or not accepting connections")
            logger.warning("   ACTION: Check if Gateway is running and configured for API access")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error checking port: {e}")
        return False


def test_3_socket_connectivity():
    """Test 3: Can we establish a raw socket connection?"""
    logger.info("")
    logger.info("=" * 80)
    logger.info("TEST 3: Raw Socket Connectivity Test")
    logger.info("=" * 80)
    
    port = settings.IBKR_PAPER_TRADING_PORT
    logger.info(f"Attempting raw socket connection to 127.0.0.1:{port}...")
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        logger.info("Connecting...")
        sock.connect(('127.0.0.1', port))
        
        logger.info("✅ Raw socket connection successful")
        logger.info("   Gateway is accepting TCP connections")
        
        sock.close()
        return True
        
    except socket.timeout:
        logger.error(f"❌ Connection TIMEOUT after 5 seconds")
        logger.error("   Gateway is not responding on this port")
        return False
    except ConnectionRefusedError:
        logger.error(f"❌ Connection REFUSED")
        logger.error("   Port is closed or Gateway is not accepting connections")
        return False
    except Exception as e:
        logger.error(f"❌ Connection failed: {e}")
        return False


def test_4_minimal_ib_connection():
    """Test 4: Simplest possible ib_insync connection (no threading, no event loops)."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("TEST 4: Minimal ib_insync Connection (Simplest Possible)")
    logger.info("=" * 80)
    
    try:
        from ib_insync import IB
        
        logger.info("Creating IB instance...")
        ib = IB()
        
        logger.info(f"Connecting to 127.0.0.1:{settings.IBKR_PAPER_TRADING_PORT} (clientId=99)...")
        logger.info("   (This is the SIMPLEST possible connection - no threading, no event loops)")
        
        try:
            ib.connect('127.0.0.1', settings.IBKR_PAPER_TRADING_PORT, clientId=99)
            
            logger.info("✅ Minimal connection SUCCESSFUL!")
            logger.info("   Gateway accepts connections via ib_insync")
            
            # Verify connection
            try:
                accounts = ib.accountValues()
                logger.info(f"   Verified: Found {len(accounts) if accounts else 0} accounts")
            except Exception as e:
                logger.warning(f"   Connection works but verification failed: {e}")
            
            ib.disconnect()
            return True
            
        except TimeoutError:
            logger.error("❌ Connection TIMEOUT")
            logger.error("   Gateway is not responding to ib_insync connection attempts")
            logger.error("   This suggests Gateway might not be ready or API client not enabled")
            return False
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            logger.error("   Gateway rejected the connection")
            return False
            
    except ImportError:
        logger.error("❌ ib_insync not installed")
        return False
    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        return False


def test_5_gateway_configuration():
    """Test 5: Check Gateway configuration."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("TEST 5: Gateway Configuration Check")
    logger.info("=" * 80)
    
    logger.info("Please verify the following in IB Gateway UI:")
    logger.info("")
    logger.info("1. ✅ Gateway is running and shows 'Ready' status")
    logger.info("2. ✅ 'Enable ActiveX and Socket Clients' is checked")
    logger.info("3. ✅ Port matches: Paper Trading = 4001")
    logger.info("4. ✅ 'Trusted IP addresses' includes 127.0.0.1 (or allows all)")
    logger.info("5. ✅ No error messages in Gateway logs")
    logger.info("6. ✅ API Client status shows 'disconnected' (waiting for connection)")
    logger.info("")
    
    response = input("Are all of these configured correctly? (y/n): ").strip().lower()
    
    if response == 'y':
        logger.info("✅ Configuration appears correct")
        return True
    else:
        logger.warning("⚠️  Please fix configuration issues and run tests again")
        return False


def test_6_port_configuration():
    """Test 6: Verify port configuration."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("TEST 6: Port Configuration Check")
    logger.info("=" * 80)
    
    logger.info(f"Paper Trading Port: {settings.IBKR_PAPER_TRADING_PORT}")
    logger.info(f"Live Trading Port: {settings.IBKR_LIVE_TRADING_PORT}")
    logger.info("")
    logger.info("Please verify in IB Gateway UI:")
    logger.info(f"  - Paper Trading is configured for port {settings.IBKR_PAPER_TRADING_PORT}")
    logger.info("  - Gateway shows this port in the API settings")
    logger.info("")
    
    response = input("Does Gateway port match? (y/n): ").strip().lower()
    
    if response == 'y':
        logger.info("✅ Port configuration matches")
        return True
    else:
        logger.warning("⚠️  Port mismatch detected - check Gateway configuration")
        return False


def run_all_diagnostics():
    """Run all diagnostic tests and provide summary."""
    logger.info("")
    logger.info("🔍 IB Gateway Connection Diagnostics")
    logger.info("=" * 80)
    logger.info("")
    logger.info("This will test each component progressively to identify the issue.")
    logger.info("")
    
    results = {}
    
    # Test 1: Process running
    results["process_running"] = test_1_gateway_process_running()
    
    # Test 2: Port listening
    results["port_listening"] = test_2_port_listening()
    
    # Test 3: Socket connectivity
    results["socket_connectivity"] = test_3_socket_connectivity()
    
    # Test 4: Minimal ib_insync connection
    results["minimal_connection"] = test_4_minimal_ib_connection()
    
    # Test 5: Configuration (manual check)
    results["configuration"] = test_5_gateway_configuration()
    
    # Test 6: Port configuration (manual check)
    results["port_config"] = test_6_port_configuration()
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("DIAGNOSTIC SUMMARY")
    logger.info("=" * 80)
    logger.info("")
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{test_name}: {status}")
    
    logger.info("")
    
    # Recommendations
    if not results["process_running"]:
        logger.info("🔧 RECOMMENDATION: Start IB Gateway application")
    elif not results["port_listening"]:
        logger.info("🔧 RECOMMENDATION: Check Gateway configuration - enable API client access")
    elif not results["socket_connectivity"]:
        logger.info("🔧 RECOMMENDATION: Gateway may not be ready yet - wait a minute and try again")
    elif not results["minimal_connection"]:
        logger.info("🔧 RECOMMENDATION: Gateway connection issue - check Gateway logs and settings")
    elif results["minimal_connection"]:
        logger.info("✅ Gateway connection works!")
        logger.info("🔧 The problem is in our connection code, not Gateway itself")
    
    return results


if __name__ == "__main__":
    try:
        run_all_diagnostics()
    except KeyboardInterrupt:
        logger.info("\n\n⚠️  Diagnostics interrupted by user")
    except Exception as e:
        logger.error(f"\n\n❌ Diagnostic script crashed: {e}", exc_info=True)

