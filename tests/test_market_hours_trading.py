#!/usr/bin/env python3
"""
Market Hours Trading Test for IBKR.
Uses market orders for immediate fills during market hours.
Queues orders for next day at 9:30 AM ET if outside market hours.
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta
import os
import time
from typing import Optional, Tuple

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ib_insync import IB, Stock, MarketOrder
import pytz
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)

def get_market_session() -> Tuple[str, bool]:
    """Determine current market session based on Eastern Time."""
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    premarket_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    postmarket_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
    
    logger.info(f"🕐 Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    if market_open <= now_et < market_close:
        logger.info("📈 Currently in MARKET HOURS")
        return 'market_hours', False
    elif premarket_start <= now_et < market_open:
        logger.info("🌅 Currently in PREMARKET")
        return 'premarket', True
    elif market_close <= now_et < postmarket_end:
        logger.info("🌆 Currently in POSTMARKET")
        return 'postmarket', True
    else:
        logger.info("🌙 Currently MARKET CLOSED")
        return 'closed', True

async def test_market_hours_trading():
    """Test IBKR market hours trading with market orders."""
    logger.info("📈 Testing Market Hours IBKR Trading")
    logger.info("=" * 50)
    
    # Start total timing
    total_start_time = time.time()
    
    # Check market session
    session_start = time.time()
    session, is_extended = get_market_session()
    session_time = time.time() - session_start
    logger.info(f"⏱️ Market session detection: {session_time:.3f}s")
    
    # Create IBKR connection
    ib = IB()
    
    try:
        # Connect to IBKR Gateway (Paper Trading)
        connect_start = time.time()
        logger.info("🔌 Connecting to IBKR Paper Trading Gateway...")
        await ib.connectAsync('127.0.0.1', 4001, clientId=4)  # Different client ID
        connect_time = time.time() - connect_start
        logger.info(f"✅ Connected to IBKR Gateway - {connect_time:.3f}s")
        
        # Create stock contract for SOFI
        contract_start = time.time()
        logger.info("📋 Creating SOFI contract...")
        contract = Stock('SOFI', 'SMART', 'USD')
        contract_time = time.time() - contract_start
        logger.info(f"✅ Contract created: {contract} - {contract_time:.3f}s")
        
        # Create market order for 1 share
        order_create_start = time.time()
        logger.info("📝 Creating market order for 1 share...")
        order = MarketOrder('BUY', 1)
        order_create_time = time.time() - order_create_start
        logger.info(f"✅ Market order created: {order} (create: {order_create_time:.3f}s)")
        
        # Place the order
        place_start = time.time()
        logger.info("🚀 Placing market order...")
        trade = ib.placeOrder(contract, order)
        place_time = time.time() - place_start
        logger.info(f"✅ Order placed: {trade} (place: {place_time:.3f}s)")
        
        # Wait for order status and analyze response
        logger.info("⏳ Waiting for order response...")
        
        # Wait a bit for order status updates
        await asyncio.sleep(2)
        
        # Check order status and log messages
        if trade.log and len(trade.log) > 0:
            logger.info("📝 Order log entries:")
            for i, log_entry in enumerate(trade.log):
                logger.info(f"   {i+1}. Status: {log_entry.status}, Message: '{log_entry.message}'")
        
        # Check for queueing warning in order status
        logger.info(f"📊 Order status: {trade.orderStatus.status}")
        logger.info(f"📊 Order remaining: {trade.orderStatus.remaining}")
        
        # Analyze the response based on market session
        if session == 'market_hours':
            logger.info("🎯 MARKET HOURS: Order should execute immediately")
            
            # Wait for fill
            fill_wait_start = time.time()
            logger.info("⏳ Waiting for immediate fill...")
            
            for attempt in range(10):  # 10 attempts × 0.5s = 5 seconds
                await asyncio.sleep(0.5)
                
                if trade.isDone():
                    fill_price = trade.orderStatus.avgFillPrice
                    fill_wait_time = time.time() - fill_wait_start
                    total_time = time.time() - total_start_time
                    
                    logger.info(f"🎉 ORDER FILLED! Price: ${fill_price}")
                    logger.info(f"⏱️ Fill wait time: {fill_wait_time:.3f}s")
                    logger.info(f"⏱️ TOTAL TIME: {total_time:.3f}s")
                    
                    # Performance summary
                    logger.info("📈 MARKET HOURS PERFORMANCE SUMMARY:")
                    logger.info(f"   📊 Market session detection: {session_time:.3f}s")
                    logger.info(f"   🔌 Connection: {connect_time:.3f}s")
                    logger.info(f"   📋 Contract creation: {contract_time:.3f}s")
                    logger.info(f"   📝 Order creation: {order_create_time:.3f}s")
                    logger.info(f"   🚀 Order placement: {place_time:.3f}s")
                    logger.info(f"   ⏳ Fill wait: {fill_wait_time:.3f}s")
                    logger.info(f"   ⚡ TOTAL: {total_time:.3f}s")
                    
                    return True
                
                logger.debug(f"⏳ Attempt {attempt + 1}: Status = {trade.orderStatus.status}")
            
            logger.warning("⚠️ ORDER TIMEOUT - Did not fill within 5 seconds")
            return False
            
        else:
            logger.info(f"📅 OUTSIDE MARKET HOURS ({session}): Order should be queued for next day")
            
            # Check if order is queued - look for PreSubmitted status with remaining quantity
            if trade.orderStatus.status == 'PreSubmitted' and trade.orderStatus.remaining == 1.0:
                logger.info("✅ ORDER QUEUED SUCCESSFULLY for next trading day at 9:30 AM ET")
                logger.info("📅 Status: PreSubmitted with remaining quantity = 1.0")
                logger.info("📅 This is the expected behavior outside market hours")
                logger.info("⚠️ Note: Warning 399 shows 'Your order will not be placed at the exchange until 2025-10-24 09:30:00 US/Eastern'")
                
                total_time = time.time() - total_start_time
                logger.info(f"⏱️ TOTAL TIME: {total_time:.3f}s")
                
                # Performance summary
                logger.info("📅 QUEUEING PERFORMANCE SUMMARY:")
                logger.info(f"   📊 Market session detection: {session_time:.3f}s")
                logger.info(f"   🔌 Connection: {connect_time:.3f}s")
                logger.info(f"   📋 Contract creation: {contract_time:.3f}s")
                logger.info(f"   📝 Order creation: {order_create_time:.3f}s")
                logger.info(f"   🚀 Order placement: {place_time:.3f}s")
                logger.info(f"   ⚡ TOTAL: {total_time:.3f}s")
                
                return True
            else:
                logger.warning(f"⚠️ Unexpected order status: {trade.orderStatus.status}, remaining: {trade.orderStatus.remaining}")
                return False
        
    except Exception as e:
        logger.error(f"❌ Market hours trading test failed: {e}")
        logger.error(f"📝 Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"📝 Full traceback:\n{traceback.format_exc()}")
        return False
        
    finally:
        # Disconnect
        if ib.isConnected():
            ib.disconnect()
            logger.info("🔌 Disconnected from IBKR")

if __name__ == "__main__":
    logger.info("📈 MARKET HOURS TRADING TEST")
    logger.info("📄 Market orders with queueing for next day outside market hours")
    
    result = asyncio.run(test_market_hours_trading())
    
    if result:
        logger.info("✅ MARKET HOURS TRADING TEST PASSED!")
    else:
        logger.info("❌ MARKET HOURS TRADING TEST FAILED!")
