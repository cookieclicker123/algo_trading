#!/usr/bin/env python3
"""
Progressive IBKR Connection Integration Test

This test progressively verifies IB Gateway connection functionality:
1. Test 1: Minimal connection (isolated)
2. Test 2: Connection with event bus
3. Test 3: Connection with Telegram notifications
4. Test 4: Connection with health monitoring
5. Test 5: Full workflow (connection manager start/stop)

Each test builds on the previous to isolate issues.
"""
import asyncio
import threading
import time
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.infra.brokerage.connection_manager import IBKRConnectionManager
from newsflash.shared.event_bus import get_event_bus
from newsflash.services.websocket.feed_health_monitor import FeedHealthMonitor
from newsflash.services.telegram_service import TelegramNotifier
from newsflash.utils.logging_config import setup_logging, get_logger
from newsflash.config import settings

# Setup logging
setup_logging()
logger = get_logger(__name__)


# ============================================================================
# Test 1: Minimal IB Connection (Isolated)
# ============================================================================
async def test_1_minimal_connection():
    """Test 1: Connect to IB Gateway with minimal setup - just connection manager."""
    logger.info("=" * 80)
    logger.info("TEST 1: Minimal IB Connection (Isolated)")
    logger.info("=" * 80)
    
    connection_manager = IBKRConnectionManager(
        paper_trading=True,
        client_id=6  # Use different client ID to avoid conflicts
    )
    
    try:
        # Start connection manager
        await connection_manager.start()
        logger.info("✅ Connection manager started")
        
        # Wait for connection attempt
        await asyncio.sleep(5)
        
        # Check connection status
        is_connected = connection_manager.is_connected
        ib_instance = connection_manager.get_ib_connection()
        
        logger.info(f"Connection status: {'✅ CONNECTED' if is_connected else '❌ NOT CONNECTED'}")
        logger.info(f"IB instance available: {'Yes' if ib_instance else 'No'}")
        
        if is_connected and ib_instance:
            # Verify connection with a simple call
            try:
                accounts = ib_instance.accountValues()
                logger.info(f"✅ Connection verified: {len(accounts) if accounts else 0} accounts found")
                return True
            except Exception as e:
                logger.error(f"❌ Connection verification failed: {e}")
                return False
        else:
            logger.warning("⚠️ Connection not established")
            return False
            
    except Exception as e:
        logger.error(f"❌ Test 1 failed: {e}", exc_info=True)
        return False
    finally:
        try:
            await connection_manager.stop()
        except Exception:
            pass


# ============================================================================
# Test 2: Connection with Event Bus
# ============================================================================
async def test_2_connection_with_events():
    """Test 2: Connect with event bus - verify events are published."""
    logger.info("=" * 80)
    logger.info("TEST 2: Connection with Event Bus")
    logger.info("=" * 80)
    
    event_bus = get_event_bus()
    connection_events = []
    
    async def capture_connection_event(event_type: str, event_data: dict):
        """Capture connection status events."""
        logger.info(f"📡 Event received: {event_type} - {event_data}")
        connection_events.append((event_type, event_data))
    
    # Subscribe to connection events
    event_bus.subscribe("ConnectionStatusChanged", capture_connection_event)
    logger.info("✅ Subscribed to ConnectionStatusChanged events")
    
    connection_manager = IBKRConnectionManager(
        paper_trading=True,
        client_id=7  # Different client ID
    )
    
    try:
        await connection_manager.start()
        logger.info("✅ Connection manager started")
        
        # Wait for connection and events
        await asyncio.sleep(8)
        
        # Check events were received
        logger.info(f"Events received: {len(connection_events)}")
        for event_type, event_data in connection_events:
            logger.info(f"  - {event_type}: {event_data}")
        
        # Check connection status
        is_connected = connection_manager.is_connected
        
        if connection_events:
            logger.info("✅ Events are being published")
            event_success = True
        else:
            logger.warning("⚠️ No events received")
            event_success = False
        
        connection_success = is_connected
        
        return event_success and connection_success
        
    except Exception as e:
        logger.error(f"❌ Test 2 failed: {e}", exc_info=True)
        return False
    finally:
        try:
            event_bus.unsubscribe("ConnectionStatusChanged", capture_connection_event)
            await connection_manager.stop()
        except Exception:
            pass


# ============================================================================
# Test 3: Connection with Telegram Notifications
# ============================================================================
async def test_3_connection_with_telegram():
    """Test 3: Connect with Telegram notifications enabled."""
    logger.info("=" * 80)
    logger.info("TEST 3: Connection with Telegram Notifications")
    logger.info("=" * 80)
    
    # Initialize Telegram service
    try:
        telegram_service = TelegramNotifier(
            test_mode=False,  # Send real notifications
            trade_handler=None,
            trade_handler_2=None
        )
        await telegram_service.start()
        logger.info("✅ Telegram service started")
    except Exception as e:
        logger.error(f"❌ Failed to start Telegram service: {e}")
        return False
    
    event_bus = get_event_bus()
    connection_events = []
    
    async def capture_connection_event(event_type: str, event_data: dict):
        """Capture and log connection events."""
        logger.info(f"📡 Connection event: {event_data}")
        connection_events.append((event_type, event_data))
        
        # Send Telegram notification manually to verify
        is_connected = event_data.get("is_connected", False)
        reason = event_data.get("reason", "")
        
        message = f"🧪 Test 3: IB Gateway {'✅ connected' if is_connected else '❌ disconnected'}\n\n"
        message += f"Reason: {reason}\n"
        message += f"Mode: Paper Trading"
        
        try:
            await telegram_service._send_message_to_all_bots(message)
            logger.info("✅ Telegram notification sent")
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram notification: {e}")
    
    # Subscribe to connection events
    event_bus.subscribe("ConnectionStatusChanged", capture_connection_event)
    logger.info("✅ Subscribed to ConnectionStatusChanged events")
    
    connection_manager = IBKRConnectionManager(
        paper_trading=True,
        client_id=8  # Different client ID
    )
    
    try:
        await connection_manager.start()
        logger.info("✅ Connection manager started")
        
        # Wait for connection and Telegram notifications
        logger.info("⏳ Waiting 10 seconds for connection and Telegram notifications...")
        await asyncio.sleep(10)
        
        # Check results
        events_received = len(connection_events) > 0
        is_connected = connection_manager.is_connected
        
        logger.info(f"Events received: {events_received}")
        logger.info(f"Connection status: {'✅ CONNECTED' if is_connected else '❌ NOT CONNECTED'}")
        
        if events_received:
            logger.info("✅ Events and Telegram notifications working")
        else:
            logger.warning("⚠️ No events received")
        
        return events_received
        
    except Exception as e:
        logger.error(f"❌ Test 3 failed: {e}", exc_info=True)
        return False
    finally:
        try:
            event_bus.unsubscribe("ConnectionStatusChanged", capture_connection_event)
            await connection_manager.stop()
            await telegram_service.stop()
        except Exception:
            pass


# ============================================================================
# Test 4: Connection with Health Monitoring
# ============================================================================
async def test_4_connection_with_health_monitor():
    """Test 4: Connect with health monitoring enabled."""
    logger.info("=" * 80)
    logger.info("TEST 4: Connection with Health Monitoring")
    logger.info("=" * 80)
    
    # Initialize Telegram service
    try:
        telegram_service = TelegramNotifier(
            test_mode=False,
            trade_handler=None,
            trade_handler_2=None
        )
        await telegram_service.start()
        logger.info("✅ Telegram service started")
    except Exception as e:
        logger.error(f"❌ Failed to start Telegram service: {e}")
        return False
    
    # Initialize health monitor
    health_monitor = FeedHealthMonitor(telegram_service)
    await health_monitor.start()
    logger.info("✅ Health monitor started")
    
    connection_manager = IBKRConnectionManager(
        paper_trading=True,
        client_id=9  # Different client ID
    )
    
    try:
        await connection_manager.start()
        logger.info("✅ Connection manager started")
        
        # Wait for connection, events, and health monitoring
        logger.info("⏳ Waiting 12 seconds for connection, events, and health checks...")
        await asyncio.sleep(12)
        
        # Check results
        is_connected = connection_manager.is_connected
        is_healthy = connection_manager.is_healthy()
        
        logger.info(f"Connection status: {'✅ CONNECTED' if is_connected else '❌ NOT CONNECTED'}")
        logger.info(f"Health status: {'✅ HEALTHY' if is_healthy else '❌ UNHEALTHY'}")
        
        return is_connected and is_healthy
        
    except Exception as e:
        logger.error(f"❌ Test 4 failed: {e}", exc_info=True)
        return False
    finally:
        try:
            await health_monitor.stop()
            await connection_manager.stop()
            await telegram_service.stop()
        except Exception:
            pass


# ============================================================================
# Test 5: Full Workflow (Connection Manager Start/Stop)
# ============================================================================
async def test_5_full_workflow():
    """Test 5: Full workflow - start, verify, stop, restart."""
    logger.info("=" * 80)
    logger.info("TEST 5: Full Workflow (Start → Verify → Stop → Restart)")
    logger.info("=" * 80)
    
    connection_manager = IBKRConnectionManager(
        paper_trading=True,
        client_id=10  # Different client ID
    )
    
    try:
        # Start
        logger.info("Step 1: Starting connection manager...")
        await connection_manager.start()
        await asyncio.sleep(8)
        
        is_connected_1 = connection_manager.is_connected
        logger.info(f"Step 1 Result: {'✅ CONNECTED' if is_connected_1 else '❌ NOT CONNECTED'}")
        
        if not is_connected_1:
            logger.warning("⚠️ Initial connection failed - continuing test anyway")
        
        # Verify connection works
        if is_connected_1:
            logger.info("Step 2: Verifying connection...")
            ib = connection_manager.get_ib_connection()
            if ib:
                try:
                    accounts = ib.accountValues()
                    logger.info(f"✅ Verification successful: {len(accounts) if accounts else 0} accounts")
                except Exception as e:
                    logger.error(f"❌ Verification failed: {e}")
        
        # Stop
        logger.info("Step 3: Stopping connection manager...")
        await connection_manager.stop()
        await asyncio.sleep(2)
        
        is_connected_2 = connection_manager.is_connected
        logger.info(f"Step 3 Result: {'✅ STOPPED' if not is_connected_2 else '⚠️ STILL CONNECTED'}")
        
        # Restart
        logger.info("Step 4: Restarting connection manager...")
        await connection_manager.start()
        await asyncio.sleep(8)
        
        is_connected_3 = connection_manager.is_connected
        logger.info(f"Step 4 Result: {'✅ CONNECTED' if is_connected_3 else '❌ NOT CONNECTED'}")
        
        # Final verification
        success = is_connected_1 or is_connected_3  # At least one connection succeeded
        logger.info(f"Overall Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Test 5 failed: {e}", exc_info=True)
        return False
    finally:
        try:
            await connection_manager.stop()
        except Exception:
            pass


# ============================================================================
# Main Test Runner
# ============================================================================
async def run_all_tests():
    """Run all tests progressively and report results."""
    logger.info("🚀 Starting Progressive IBKR Connection Integration Tests")
    logger.info(f"Paper Trading Port: {settings.IBKR_PAPER_TRADING_PORT}")
    logger.info(f"Client IDs: 6, 7, 8, 9, 10 (to avoid conflicts)")
    logger.info("")
    
    results = {}
    
    # Test 1: Minimal connection
    try:
        results["test_1_minimal"] = await test_1_minimal_connection()
        await asyncio.sleep(2)  # Brief pause between tests
    except Exception as e:
        logger.error(f"Test 1 crashed: {e}", exc_info=True)
        results["test_1_minimal"] = False
    
    # Test 2: With events
    try:
        results["test_2_events"] = await test_2_connection_with_events()
        await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Test 2 crashed: {e}", exc_info=True)
        results["test_2_events"] = False
    
    # Test 3: With Telegram
    try:
        results["test_3_telegram"] = await test_3_connection_with_telegram()
        await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Test 3 crashed: {e}", exc_info=True)
        results["test_3_telegram"] = False
    
    # Test 4: With health monitor
    try:
        results["test_4_health"] = await test_4_connection_with_health_monitor()
        await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Test 4 crashed: {e}", exc_info=True)
        results["test_4_health"] = False
    
    # Test 5: Full workflow
    try:
        results["test_5_full"] = await test_5_full_workflow()
    except Exception as e:
        logger.error(f"Test 5 crashed: {e}", exc_info=True)
        results["test_5_full"] = False
    
    # Print summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)
    
    for test_name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        logger.info(f"{test_name}: {status}")
    
    total = len(results)
    passed = sum(1 for r in results.values() if r)
    logger.info("")
    logger.info(f"Results: {passed}/{total} tests passed")
    
    return results


if __name__ == "__main__":
    asyncio.run(run_all_tests())

