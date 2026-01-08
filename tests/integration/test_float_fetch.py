
import os
import pytest
from alpaca.trading.client import TradingClient
import yfinance as yf

# Load keys
API_KEY = os.environ.get("ALPACA_API_KEY") or os.environ.get("ALPACA_KEY")
SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_SECRET")

@pytest.mark.skipif(not API_KEY, reason="Alpaca keys not in env")
def test_float_fetch_alpaca():
    """Test if Alpaca Asset API provides share count."""
    client = TradingClient(API_KEY, SECRET_KEY, paper=True)
    try:
        asset = client.get_asset("AAPL")
        print(f"\nAlpaca Asset Data for AAPL: {asset}")
        
        # Check for float-like fields
        if hasattr(asset, 'shares_outstanding'):
            print(f"✅ Found shares_outstanding: {asset.shares_outstanding}")
        elif hasattr(asset, 'tradable'):
            print(f"Asset is tradable: {asset.tradable}")
            
        # Inspect raw dict if possible
        if hasattr(asset, 'dict'):
             print(f"Raw Dict: {asset.dict()}")
             
    except Exception as e:
        pytest.fail(f"Alpaca fetch failed: {e}")

def test_float_fetch_yfinance():
    """Test if yfinance provides share count."""
    try:
        ticker = yf.Ticker("AAPL")
        info = ticker.info
        shares = info.get("sharesOutstanding")
        float_shares = info.get("floatShares")
        
        print(f"\nYFinance Data for AAPL:")
        print(f"Shares Outstanding: {shares}")
        print(f"Float Shares: {float_shares}")
        
        assert shares is not None
        
    except Exception as e:
        pytest.fail(f"YFinance fetch failed: {e}")
