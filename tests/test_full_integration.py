#!/usr/bin/env python3
"""
FULL INTEGRATION TEST: Telegram → IBKR Trading
This test simulates the complete workflow from IMMINENT news to actual IBKR trade execution.
"""
import asyncio
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.telegram_service import TelegramNotifier
from newsflash.services.ibkr_trading_service import get_ibkr_trading_service, TradeRequest
from newsflash.services.telegram_trade_handler import get_telegram_trade_handler
from newsflash.models.benzinga_models import BenzingaArticle
from newsflash.models.classification_models import NewsClassification, ClassificationResult
from newsflash.utils.logging_config import setup_logging, get_logger
from datetime import datetime

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def test_full_integration():
    """
    FULL INTEGRATION TEST:
    1. Simulate IMMINENT news about AAPL
    2. Send Telegram message with trading options
    3. Simulate user response "trade AAPL"
    4. Execute real IBKR trade
    5. Verify trade appears in IBKR account
    """
    
    logger.info("🚀 Starting FULL INTEGRATION TEST")
    logger.info("This will execute a REAL $100 trade on AAPL through IBKR")
    
    # Initialize services
    trading_service = get_ibkr_trading_service()
    telegram_handler = get_telegram_trade_handler("dummy_token")  # We'll use direct service calls
    
    # Create sample IMMINENT article about AAPL
    test_time = datetime(2025, 10, 21, 18, 30, 0)
    
    article = BenzingaArticle(
        benzinga_id=99999,  # Special ID for integration test
        title="Apple Announces Major AI Breakthrough Worth $5 Billion Partnership",
        body="Apple Inc. today announced a revolutionary AI breakthrough...",
        teaser="Apple announces $5 billion AI partnership breakthrough.",
        author="Apple Inc.",
        published=test_time,
        last_updated=test_time,
        url="https://example.com/apple-ai-breakthrough",
        tickers=["AAPL"],  # Single ticker for clean test
        tags=["AI", "Breakthrough", "Partnership"],
        channels=["Breaking News"],
        images=[]
    )
    
    # Create IMMINENT classification
    classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Major AI breakthrough with $5 billion partnership indicates significant market impact"
    )
    
    print("\n" + "="*80)
    print("🎯 FULL INTEGRATION TEST - AAPL TRADE")
    print("="*80)
    print(f"📰 Article: {article.title}")
    print(f"📊 Ticker: {article.tickers[0]}")
    print(f"💰 Amount: $100")
    print(f"🎯 Classification: {classification.classification.value}")
    print("="*80)
    
    # Step 1: Add pending trade decision
    article_id = f"integration_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    user_chat_id = "integration_test_user"
    
    trading_service.add_pending_trade(article_id, article.tickers, user_chat_id)
    logger.info("✅ Step 1: Pending trade decision added")
    
    # Step 2: Simulate user response "trade AAPL"
    logger.info("📱 Step 2: Simulating user response 'trade AAPL'")
    
    trade_request = trading_service.process_user_response(user_chat_id, "trade AAPL")
    
    if not trade_request:
        logger.error("❌ FAILED: No trade request created")
        return False
    
    logger.info(f"✅ Step 2: Trade request created - {trade_request.ticker} for ${trade_request.amount_usd}")
    
    # Step 3: Execute the actual IBKR trade
    logger.info("🚀 Step 3: Executing REAL IBKR trade")
    logger.info("⚠️  WARNING: This will place a REAL $100 trade on AAPL!")
    
    # Ask for confirmation
    print("\n⚠️  WARNING: This will execute a REAL $100 trade on AAPL!")
    print("Make sure IBKR Gateway is running on port 7497")
    print("Make sure you have sufficient funds in your account")
    
    confirmation = input("\nType 'EXECUTE' to proceed with real trade: ")
    
    if confirmation != "EXECUTE":
        logger.info("❌ Test cancelled by user")
        return False
    
    # Execute the trade
    logger.info("🚀 Executing real IBKR trade...")
    success = await trading_service.process_trade_request(trade_request)
    
    if success:
        logger.info("✅ Step 3: IBKR trade executed successfully!")
        print("\n🎉 SUCCESS: Real trade executed!")
        print("📊 Check your IBKR account - you should see:")
        print(f"   • New AAPL position")
        print(f"   • ~$100 investment")
        print(f"   • Order filled at market price")
        
        # Step 4: Verify trade (user needs to check IBKR account)
        print("\n" + "="*80)
        print("🔍 VERIFICATION REQUIRED:")
        print("="*80)
        print("1. Open your IBKR Desktop/TWS")
        print("2. Check your positions")
        print("3. Look for new AAPL shares")
        print("4. Verify the trade amount (~$100)")
        print("5. Note the fill price")
        print("="*80)
        
        verification = input("\nDid you see the AAPL trade in your IBKR account? (yes/no): ")
        
        if verification.lower() == "yes":
            logger.info("🎉 FULL INTEGRATION TEST PASSED!")
            print("\n🎉 FULL INTEGRATION TEST PASSED!")
            print("✅ Telegram → IBKR workflow is working!")
            print("✅ Ready for live trading system!")
            return True
        else:
            logger.error("❌ Trade verification failed")
            print("\n❌ Trade verification failed")
            print("Check IBKR account and try again")
            return False
            
    else:
        logger.error("❌ Step 3: IBKR trade execution failed")
        print("\n❌ FAILED: IBKR trade execution failed")
        print("Check IBKR Gateway connection and try again")
        return False


async def main():
    """Main test function."""
    try:
        success = await test_full_integration()
        
        if success:
            print("\n🚀 INTEGRATION TEST COMPLETE - SYSTEM READY!")
            print("Your automated trading system is now fully operational!")
        else:
            print("\n❌ INTEGRATION TEST FAILED")
            print("Please check the issues and try again")
            
    except KeyboardInterrupt:
        print("\n⏹️  Test cancelled by user")
    except Exception as e:
        logger.error("Integration test error", error=str(e))
        print(f"\n❌ Test error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
