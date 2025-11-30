#!/usr/bin/env python3
"""
Test if OS-level blocking is preventing connections.

Error 35 (EWOULDBLOCK) on connect_ex suggests OS-level filtering.
"""
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


def check_macos_firewall():
    """Check macOS firewall status."""
    logger.info("=" * 80)
    logger.info("macOS Firewall Check")
    logger.info("=" * 80)
    logger.info("")
    
    try:
        # Check firewall status
        result = subprocess.run(
            ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        logger.info("Firewall global state:")
        logger.info(result.stdout)
        
        # Check if firewall is blocking connections
        result = subprocess.run(
            ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--listapps"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        logger.info("")
        logger.info("Firewall application rules:")
        if "gateway" in result.stdout.lower() or "ib" in result.stdout.lower():
            logger.warning("⚠️  Found Gateway in firewall rules - check if blocked")
            logger.info(result.stdout)
        else:
            logger.info("No Gateway-specific firewall rules found")
            
    except Exception as e:
        logger.warning(f"Could not check firewall: {e}")


def test_localhost_routing():
    """Test if localhost routing is working correctly."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Localhost Routing Test")
    logger.info("=" * 80)
    logger.info("")
    
    # Test if we can connect to localhost at all
    logger.info("Testing localhost connectivity...")
    
    # Try a known working port (like 80 or 443 if a server is running)
    # Or try creating our own server and connecting to it
    
    test_port = 9999
    
    try:
        # Create test server
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', test_port))
        server.listen(1)
        server.settimeout(2)
        
        logger.info(f"Created test server on port {test_port}")
        
        # Try to connect to it
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2)
        
        result = client.connect_ex(('127.0.0.1', test_port))
        
        if result == 0:
            logger.info("✅ Can connect to localhost (localhost routing works)")
            client.close()
        else:
            logger.error(f"❌ Cannot connect to localhost (error {result})")
            logger.error("   This suggests OS-level blocking of localhost connections")
        
        server.close()
        
    except Exception as e:
        logger.warning(f"Test server error: {e}")


def test_gateway_port_direct():
    """Try connecting to Gateway port with different socket options."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Direct Gateway Port Test with Socket Options")
    logger.info("=" * 80)
    logger.info("")
    
    port = 4001
    
    # Test with different socket options
    logger.info("Testing with TCP_NODELAY...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        
        if result == 0:
            logger.info("✅ Connection works with TCP_NODELAY")
            return True
        else:
            logger.info(f"❌ Failed with TCP_NODELAY (error {result})")
    except Exception as e:
        logger.error(f"Error: {e}")
    
    # Test with SO_REUSEADDR
    logger.info("")
    logger.info("Testing with SO_REUSEADDR...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        
        if result == 0:
            logger.info("✅ Connection works with SO_REUSEADDR")
            return True
        else:
            logger.info(f"❌ Failed with SO_REUSEADDR (error {result})")
    except Exception as e:
        logger.error(f"Error: {e}")
    
    return False


def check_gateway_process_connections():
    """Check what connections Gateway process actually has."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("Gateway Process Connection Analysis")
    logger.info("=" * 80)
    logger.info("")
    
    try:
        # Find Gateway process
        result = subprocess.run(
            ["pgrep", "-f", "gateway"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            logger.info(f"Found Gateway processes: {pids}")
            
            for pid in pids:
                logger.info(f"")
                logger.info(f"Process {pid} network connections:")
                
                # Get network connections for this process
                result2 = subprocess.run(
                    ["lsof", "-p", pid, "-i"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                logger.info(result2.stdout)
        else:
            logger.warning("Could not find Gateway process")
            
    except Exception as e:
        logger.error(f"Error checking Gateway process: {e}")


if __name__ == "__main__":
    check_macos_firewall()
    test_localhost_routing()
    test_gateway_port_direct()
    check_gateway_process_connections()
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("CONCLUSION:")
    logger.info("=" * 80)
    logger.info("")
    logger.info("Error 35 (EWOULDBLOCK) on connect_ex suggests:")
    logger.info("  1. OS-level filtering (firewall, pf, etc.)")
    logger.info("  2. Gateway process not actually accepting connections")
    logger.info("  3. Port bound but socket not in LISTEN state properly")
    logger.info("")
    logger.info("If localhost test works but Gateway doesn't:")
    logger.info("  → Gateway-specific blocking")
    logger.info("If localhost test also fails:")
    logger.info("  → OS-level blocking of localhost connections")

