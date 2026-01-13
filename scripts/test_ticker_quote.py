#!/usr/bin/env python3
"""
Test script to verify ticker quote availability.

This script attempts to fetch a quote for a given ticker to verify
if the lack of quotes is due to illiquidity (market closed/ticker not trading).
"""
import os
import sys
import asyncio
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not required

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.data.models import Quote


async def test_ticker_quote(ticker: str):
    """
    Test if we can get a quote for a ticker.
    
    Args:
        ticker: Ticker symbol to test
    """
    print(f"\n{'='*60}")
    print(f"Testing ticker: {ticker}")
    print(f"{'='*60}\n")
    
    # Get API credentials from environment
    api_key = os.getenv("ALPACA_KEY")
    api_secret = os.getenv("ALPACA_SECRET")
    
    if not api_key or not api_secret:
        print("❌ ERROR: ALPACA_KEY and ALPACA_SECRET must be set")
        return
    
    try:
        # Create client
        client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=api_secret
        )
        
        print(f"✅ Client created")
        print(f"📅 Current time: {datetime.now().isoformat()}\n")
        
        # Try to get latest quote (IEX feed - works in paper trading)
        print("Attempting to fetch latest quote (IEX feed)...")
        try:
            request = StockLatestQuoteRequest(
                symbol_or_symbols=[ticker],
                feed="iex"
            )
            quotes = client.get_stock_latest_quote(request)
            
            if quotes and ticker in quotes:
                quote = quotes[ticker]
                print(f"\n✅ SUCCESS: Quote retrieved!")
                print(f"   Bid: ${quote.bid_price:.4f}" if quote.bid_price else "   Bid: None")
                print(f"   Ask: ${quote.ask_price:.4f}" if quote.ask_price else "   Ask: None")
                print(f"   Bid Size: {quote.bid_size}" if hasattr(quote, 'bid_size') and quote.bid_size else "   Bid Size: None")
                print(f"   Ask Size: {quote.ask_size}" if hasattr(quote, 'ask_size') and quote.ask_size else "   Ask Size: None")
                print(f"   Timestamp: {quote.timestamp}" if hasattr(quote, 'timestamp') and quote.timestamp else "   Timestamp: None")
                
                if quote.bid_price and quote.ask_price:
                    spread = quote.ask_price - quote.bid_price
                    mid = (quote.bid_price + quote.ask_price) / 2.0
                    spread_pct = (spread / mid) * 100 if mid > 0 else None
                    print(f"   Spread: ${spread:.4f} ({spread_pct:.2f}%)" if spread_pct else f"   Spread: ${spread:.4f}")
                    print(f"   Mid: ${mid:.4f}")
                    print(f"\n✅ Ticker is LIQUID - quotes available")
                else:
                    print(f"\n⚠️  Ticker quote retrieved but missing bid/ask (likely ILLIQUID)")
            else:
                print(f"\n❌ FAILED: No quote data returned for {ticker}")
                print(f"   This indicates ILLIQUIDITY (market closed, ticker not trading, or no data)")
                
        except Exception as quote_error:
            error_type = type(quote_error).__name__
            error_msg = str(quote_error)
            print(f"\n❌ ERROR fetching quote: {error_type}: {error_msg}")
            
            # Check if this is likely an illiquidity error
            is_illiquidity = (
                "not found" in error_msg.lower() or
                "no data" in error_msg.lower() or
                "no quotes" in error_msg.lower() or
                error_type == "ValueError"
            )
            
            if is_illiquidity:
                print(f"\n✅ Likely ILLIQUIDITY (market closed/ticker not trading)")
            else:
                print(f"\n⚠️  Unexpected error (may not be illiquidity)")
        
        # Try SIP feed as well (if available)
        print(f"\n{'─'*60}")
        print("Attempting to fetch latest quote (SIP feed - requires subscription)...")
        try:
            request_sip = StockLatestQuoteRequest(
                symbol_or_symbols=[ticker],
                feed="sip"
            )
            quotes_sip = client.get_stock_latest_quote(request_sip)
            
            if quotes_sip and ticker in quotes_sip:
                quote_sip = quotes_sip[ticker]
                print(f"✅ SIP quote also available!")
                print(f"   Bid: ${quote_sip.bid_price:.4f}" if quote_sip.bid_price else "   Bid: None")
                print(f"   Ask: ${quote_sip.ask_price:.4f}" if quote_sip.ask_price else "   Ask: None")
            else:
                print(f"⚠️  SIP feed not available (no subscription or no data)")
        except Exception as sip_error:
            print(f"⚠️  SIP feed error: {str(sip_error)}")
            print(f"   (This is expected if you don't have SIP subscription)")
        
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test ticker quote availability")
    parser.add_argument("ticker", help="Ticker symbol to test (e.g., SLDB)")
    args = parser.parse_args()
    
    asyncio.run(test_ticker_quote(args.ticker.upper()))
