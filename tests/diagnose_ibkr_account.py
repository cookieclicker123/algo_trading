#!/usr/bin/env python3
"""
IBKR Account Diagnostics - Extended Hours Trading
This script will check your exact account permissions and settings.
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
import pytz

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ib_insync import IB, Stock
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)

async def diagnose_account():
    """Diagnose account permissions and settings."""
    logger.info("🔍 IBKR Account Diagnostics - Extended Hours Trading")
    logger.info("=" * 60)
    
    # Get current time
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    logger.info(f"🕐 Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Create IBKR connection
    ib = IB()
    
    try:
        # Connect to IBKR Paper Trading Gateway
        logger.info("🔌 Connecting to IBKR Paper Trading Gateway...")
        await ib.connectAsync('127.0.0.1', 4001, clientId=7)
        logger.info("✅ Connected to IBKR Paper Trading Gateway")
        
        # Get account summary
        logger.info("📊 Getting account summary...")
        account_summary = await ib.accountSummaryAsync()
        
        logger.info("💰 Account Summary:")
        logger.info("-" * 30)
        
        # Check key account details
        account_data = {}
        for item in account_summary:
            account_data[item.tag] = {
                'value': item.value,
                'currency': item.currency
            }
            logger.info(f"📈 {item.tag}: {item.value} {item.currency}")
        
        # Check for extended hours related settings
        logger.info("\n🔍 Extended Hours Trading Analysis:")
        logger.info("-" * 40)
        
        # Check account type
        maintenance_margin = account_data.get('MaintenanceMargin', {}).get('value', '0')
        if maintenance_margin and float(maintenance_margin) > 0:
            logger.info("✅ Margin account detected")
        else:
            logger.warning("⚠️ Cash account detected - may have restrictions")
        
        # Check buying power vs cash
        buying_power = account_data.get('BuyingPower', {}).get('value', '0')
        total_cash = account_data.get('TotalCashValue', {}).get('value', '0')
        
        if buying_power and total_cash:
            bp_ratio = float(buying_power) / float(total_cash) if float(total_cash) > 0 else 0
            logger.info(f"📊 Buying Power Ratio: {bp_ratio:.2f}x")
            if bp_ratio > 2:
                logger.info("✅ High margin buying power - good for extended hours")
            else:
                logger.warning("⚠️ Limited margin buying power")
        
        # Check account permissions
        logger.info("\n🔐 Account Permissions Check:")
        logger.info("-" * 30)
        
        # Try to get contract details for SOFI
        logger.info("📋 Checking SOFI contract details...")
        contract = Stock('SOFI', 'SMART', 'USD')
        
        try:
            # Request contract details
            contracts = ib.reqContractDetails(contract)
            if contracts:
                contract_details = contracts[0]
                logger.info(f"✅ Contract found: {contract_details.contract}")
                logger.info(f"📊 Exchange: {contract_details.contract.exchange}")
                logger.info(f"💰 Currency: {contract_details.contract.currency}")
                
                # Check if extended hours trading is supported
                if hasattr(contract_details, 'tradingHours'):
                    logger.info(f"🕐 Trading Hours: {contract_details.tradingHours}")
                
                if hasattr(contract_details, 'liquidHours'):
                    logger.info(f"💧 Liquid Hours: {contract_details.liquidHours}")
                
                # Check minimum price increment
                if hasattr(contract_details, 'minTick'):
                    logger.info(f"📏 Minimum Tick: {contract_details.minTick}")
                
            else:
                logger.error("❌ No contract details found for SOFI")
                
        except Exception as e:
            logger.error(f"❌ Error getting contract details: {e}")
        
        # Check market data permissions
        logger.info("\n📡 Market Data Check:")
        logger.info("-" * 20)
        
        try:
            # Try to request market data
            ticker = ib.reqMktData(contract)
            await asyncio.sleep(2)
            
            if ticker.last and ticker.last > 0:
                logger.info(f"✅ Market data available: ${ticker.last}")
            else:
                logger.warning("⚠️ No market data received")
                
        except Exception as e:
            logger.error(f"❌ Market data error: {e}")
        
        # Check account permissions
        logger.info("\n🔑 Permission Analysis:")
        logger.info("-" * 25)
        
        # Look for permission-related fields
        permission_fields = [
            'TradingPermissions', 'AccountType', 'AccountStatus', 
            'DayTradingBuyingPower', 'RegTMargin', 'RegTEquity'
        ]
        
        for field in permission_fields:
            if field in account_data:
                logger.info(f"🔐 {field}: {account_data[field]['value']}")
        
        # Provide specific recommendations
        logger.info("\n💡 Specific Recommendations:")
        logger.info("-" * 30)
        
        logger.info("1. Check IBKR Client Portal:")
        logger.info("   - Go to Account Management → Trading Permissions")
        logger.info("   - Look for 'Extended Hours Trading' or 'Pre-Market Trading'")
        logger.info("   - Enable if available")
        
        logger.info("2. Check TWS/Gateway Settings:")
        logger.info("   - In order entry, look for 'Allow Outside RTH' checkbox")
        logger.info("   - This must be checked for extended hours orders")
        
        logger.info("3. Market Data Subscriptions:")
        logger.info("   - Ensure you have US market data subscriptions")
        logger.info("   - Extended hours data may require additional subscriptions")
        
        logger.info("4. Account Type:")
        if maintenance_margin and float(maintenance_margin) > 0:
            logger.info("   - You have a margin account (good)")
        else:
            logger.info("   - Consider upgrading to margin account for more flexibility")
        
        logger.info("5. Paper Trading Limitations:")
        logger.info("   - Some paper accounts don't support extended hours")
        logger.info("   - This is a known limitation of IBKR paper trading")
        
    except Exception as e:
        logger.error(f"❌ Diagnosis failed: {e}")
        import traceback
        logger.error(f"📝 Traceback:\n{traceback.format_exc()}")
        
    finally:
        if ib.isConnected():
            ib.disconnect()
            logger.info("🔌 Disconnected from IBKR")

if __name__ == "__main__":
    logger.info("🔍 Starting IBKR Account Diagnostics...")
    asyncio.run(diagnose_account())
