#!/usr/bin/env python3
"""
Simple, isolated IBKR trading test.
Tests trading directly without service dependencies.
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta
import os

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ib_insync import IB, Stock, MarketOrder
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)

async def test_simple_trading():
    """Test IBKR trading with minimal code."""
    logger.info("🧪 Testing Simple IBKR Trading")
    logger.info("=" * 50)
    
    # Create IBKR connection
    ib = IB()
    
    try:
        # Connect to IBKR Gateway
        logger.info("🔌 Connecting to IBKR Gateway...")
        await ib.connectAsync('127.0.0.1', 7497, clientId=2)  # Use different client ID
        logger.info("✅ Connected to IBKR Gateway")
        
        # Create stock contract for SOFI (much cheaper to avoid currency issues)
        logger.info("📋 Creating SOFI contract...")
        contract = Stock('SOFI', 'SMART', 'USD')
        logger.info(f"✅ Contract created: {contract}")
        
        # Get market data
        logger.info("📊 Requesting market data...")
        ticker = ib.reqMktData(contract)
        logger.info("✅ Market data requested")
        
        # Wait for market data
        await asyncio.sleep(2)
        
        if ticker.last and ticker.last > 0:
            logger.info(f"💰 Current SOFI price: ${ticker.last}")
            
            # Create market order with proper order ID
            logger.info("📝 Creating market order for 1 share...")
            order_id = ib.client.getReqId()
            logger.info(f"📋 Generated order ID: {order_id}")
            order = MarketOrder('BUY', 1, orderId=order_id)
            logger.info(f"✅ Order created: {order}")
            
            # Place order
            logger.info("🚀 Placing order...")
            trade = ib.placeOrder(contract, order)
            logger.info(f"✅ Order placed: {trade}")
            
            # Wait for fill
            logger.info("⏳ Waiting for order to fill...")
            for attempt in range(20):  # 20 attempts × 0.5s = 10 seconds
                await asyncio.sleep(0.5)
                
                if trade.fills and len(trade.fills) > 0:
                    fill_price = trade.fills[0].execution.price
                    logger.info(f"🎉 ORDER FILLED! Price: ${fill_price}")
                    return True
                
                if trade.orderStatus and trade.orderStatus.status == 'Filled':
                    fill_price = trade.orderStatus.avgFillPrice
                    logger.info(f"🎉 ORDER FILLED! Price: ${fill_price}")
                    return True
                
                if trade.orderStatus and trade.orderStatus.status in ['Cancelled', 'Rejected']:
                    logger.error(f"❌ ORDER REJECTED: {trade.orderStatus.status}")
                    return False
                
                logger.debug(f"⏳ Attempt {attempt + 1}: Status = {trade.orderStatus.status if trade.orderStatus else 'No status'}")
            
            logger.error("❌ ORDER TIMEOUT - Did not fill within 10 seconds")
            return False
            
        else:
            logger.error("❌ No market data received")
            return False
            
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False
        
    finally:
        # Disconnect
        if ib.isConnected():
            ib.disconnect()
            logger.info("🔌 Disconnected from IBKR")

if __name__ == "__main__":
    logger.info("🚨 IMPORTANT: Ensure IBKR Gateway is running and connected.")
    logger.info("             This test will place a REAL trade for 1 share of SOFI (~$7).")
    
    result = asyncio.run(test_simple_trading())
    
    if result:
        logger.info("✅ SIMPLE TRADING TEST PASSED!")
    else:
        logger.info("❌ SIMPLE TRADING TEST FAILED!")
