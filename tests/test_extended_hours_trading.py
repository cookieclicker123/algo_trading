#!/usr/bin/env python3
"""
Extended Hours Trading Test for IBKR - OPTIMIZED VERSION.
Uses real-time IBKR data with aggressive continuation from 0.25% to 10% above ask.
Designed for news trading speed with 0.0001s intervals between attempts.
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

from ib_insync import IB, Stock, LimitOrder
import pytz
import yfinance as yf
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

async def get_yfinance_price(ticker_symbol: str) -> Optional[float]:
    """Get INSTANT price from yfinance with prepost=True."""
    try:
        logger.info(f"📊 Getting INSTANT price from yfinance for {ticker_symbol}...")
        
        ticker = yf.Ticker(ticker_symbol)
        data = ticker.history(period="1d", interval="1m", prepost=True)
        
        if not data.empty:
            # Use the last available price
            current_price = data['Close'].iloc[-1]
            logger.info(f"💰 yfinance price: ${current_price}")
            return float(current_price)
        else:
            logger.error(f"❌ No data available for {ticker_symbol}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error fetching {ticker_symbol} price from yfinance: {e}")
        return None

async def test_extended_hours_trading():
    """Test IBKR extended hours trading - OPTIMIZED VERSION."""
    logger.info("🚀 Testing OPTIMIZED Extended Hours IBKR Trading")
    logger.info("=" * 60)
    
    # Start total timing
    total_start_time = time.time()
    
    # Check market session
    session_start = time.time()
    session, is_extended = get_market_session()
    session_time = time.time() - session_start
    logger.info(f"⏱️ Market session detection: {session_time:.3f}s")
    
    if session == 'market_hours':
        logger.warning("⚠️ Currently in market hours - this test is designed for extended hours")
        logger.info("💡 Use test_simple_trading.py for market hours trading")
        return False
    
    if session == 'closed':
        logger.error("❌ Market is currently closed - no extended hours trading available")
        return False
    
    logger.info(f"✅ Confirmed {session} - proceeding with optimized trading")
    
    # Create IBKR connection
    ib = IB()
    
    try:
        # Connect to IBKR Gateway
        connect_start = time.time()
        logger.info("🔌 Connecting to IBKR Gateway...")
        await ib.connectAsync('127.0.0.1', 4001, clientId=3)  # Paper trading port
        connect_time = time.time() - connect_start
        logger.info(f"✅ Connected to IBKR Gateway - {connect_time:.3f}s")
        
        # Create stock contract for SOFI
        contract_start = time.time()
        logger.info("📋 Creating SOFI contract...")
        contract = Stock('SOFI', 'SMART', 'USD')
        contract_time = time.time() - contract_start
        logger.info(f"✅ Contract created: {contract} - {contract_time:.3f}s")
        
        # Get INSTANT price from yfinance
        price_start = time.time()
        current_price = await get_yfinance_price(contract.symbol)
        price_time = time.time() - price_start
        logger.info(f"💰 Price retrieval: {price_time:.3f}s")
        
        if not current_price:
            logger.error("❌ Could not get price from yfinance - aborting trade")
            return False
        
        logger.info(f"💰 Current SOFI price: ${current_price}")
        
        # AGGRESSIVE CONTINUATION: 0.25% to 10% with 0.0001s intervals
        logger.info("🚀 Starting AGGRESSIVE CONTINUATION: 0.25% to 10% above price with 0.0001s intervals")
        
        base_percentage = 0.25   # Start at 0.25% above price
        max_percentage = 10.0    # Go up to 10% above price
        increment = 0.25        # Increase by 0.25% each time
        wait_time = 0.0001      # Wait 0.0001 seconds between attempts (INSTANT)
        
        current_percentage = base_percentage
        attempt_number = 1
        
        # Start trading timing
        trading_start = time.time()
        
        while current_percentage <= max_percentage:
            attempt_start = time.time()
            logger.info(f"🚀 Attempt {attempt_number}: {current_percentage}% above yfinance price")
            
            # Calculate limit price using yfinance price
            calc_start = time.time()
            limit_price = round(current_price * (1 + current_percentage / 100), 2)
            calc_time = time.time() - calc_start
            logger.info(f"📈 Limit price: ${limit_price:.2f} (calc: {calc_time:.3f}s)")
            
            # Create limit order
            order_create_start = time.time()
            order_id = ib.client.getReqId()
            order = LimitOrder('BUY', 1, limit_price, orderId=order_id)
            order.outsideRth = True
            order_create_time = time.time() - order_create_start
            logger.info(f"✅ Limit order created: {order} (create: {order_create_time:.3f}s)")
            
            # Place order
            place_start = time.time()
            logger.info("🚀 Placing limit order...")
            trade = ib.placeOrder(contract, order)
            place_time = time.time() - place_start
            logger.info(f"✅ Order placed: {trade} (place: {place_time:.3f}s)")
            
            # Wait for INSTANT fill detection
            fill_wait_start = time.time()
            logger.info("⚡ Waiting for INSTANT fill...")
            filled = False
            
            for check_attempt in range(5):  # 5 attempts × 0.1s = 0.5 seconds
                await asyncio.sleep(0.1)
                
                if trade.isDone():
                    fill_wait_time = time.time() - fill_wait_start
                    fill_price = trade.orderStatus.avgFillPrice
                    total_trading_time = time.time() - trading_start
                    total_time = time.time() - total_start_time
                    
                    logger.info(f"🎉 ORDER FILLED! Price: ${fill_price}")
                    logger.info(f"✅ SUCCESS at attempt {attempt_number}: {current_percentage}% above yfinance price")
                    logger.info(f"⏱️ Fill wait time: {fill_wait_time:.3f}s")
                    logger.info(f"⏱️ Total trading time: {total_trading_time:.3f}s")
                    logger.info(f"⏱️ TOTAL TIME: {total_time:.3f}s")
                    
                    # Performance summary
                    logger.info("🚀 OPTIMIZED PERFORMANCE SUMMARY:")
                    logger.info(f"   📊 Market session detection: {session_time:.3f}s")
                    logger.info(f"   🔌 Connection: {connect_time:.3f}s")
                    logger.info(f"   📋 Contract creation: {contract_time:.3f}s")
                    logger.info(f"   📊 yfinance price retrieval: {price_time:.3f}s")
                    logger.info(f"   🚀 Trading (to fill): {total_trading_time:.3f}s")
                    logger.info(f"   ⚡ TOTAL: {total_time:.3f}s")
                    
                    return True
                
                if trade.orderStatus and trade.orderStatus.status in ['Cancelled', 'Rejected']:
                    logger.warning(f"⚠️ Order rejected at {current_percentage}%: {trade.orderStatus.status}")
                    # Get rejection reason
                    if trade.log and len(trade.log) > 0:
                        last_log = trade.log[-1]
                        if last_log.message:
                            logger.warning(f"📝 Rejection reason: {last_log.message}")
                    break
            
            attempt_time = time.time() - attempt_start
            logger.info(f"⏱️ Attempt {attempt_number} total time: {attempt_time:.3f}s")
            
            if not filled:
                logger.info(f"⚡ No fill at {current_percentage}% - INSTANTLY trying next level")
                # Cancel current order
                try:
                    ib.cancelOrder(order)
                    logger.info(f"🚫 Cancelled order at {current_percentage}%")
                except:
                    pass
            
            # INSTANT continuation - 0.0001 seconds
            logger.info(f"⚡ INSTANT continuation: {wait_time}s before next attempt...")
            await asyncio.sleep(wait_time)
            
            # Increase percentage for next attempt
            current_percentage += increment
            attempt_number += 1
        
        logger.error("❌ AGGRESSIVE CONTINUATION FAILED - no fill up to 10% above yfinance price")
        logger.error("🚨 This should NEVER happen - check market conditions!")
        return False
        
    except Exception as e:
        logger.error(f"❌ Optimized trading test failed: {e}")
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
    logger.info("🚀 OPTIMIZED EXTENDED HOURS TRADING TEST")
    logger.info("📄 yfinance data with aggressive continuation (0.25% to 10%)")
    
    result = asyncio.run(test_extended_hours_trading())
    
    if result:
        logger.info("✅ OPTIMIZED TRADING TEST PASSED!")
    else:
        logger.info("❌ OPTIMIZED TRADING TEST FAILED!")