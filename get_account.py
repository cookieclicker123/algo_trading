#!/usr/bin/env python3
"""Test both live and paper Alpaca credentials."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_account(name: str, api_key: str, api_secret: str, paper: bool):
    """Test an Alpaca account."""
    base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"

    try:
        response = requests.get(
            f"{base_url}/v2/account",
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
        )
        if response.status_code == 200:
            account = response.json()
            print(f"\n✅ {name} ({('PAPER' if paper else 'LIVE')})")
            print(f"   Account: {account['account_number']}")
            print(f"   Buying Power: ${float(account['buying_power']):,.2f}")
            print(f"   Cash: ${float(account['cash']):,.2f}")
            print(f"   Portfolio: ${float(account['portfolio_value']):,.2f}")
            return True
        else:
            print(f"\n❌ {name}: HTTP {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"\n❌ {name}: {e}")
        return False

print("=" * 50)
print("ALPACA CREDENTIALS TEST")
print("=" * 50)

# Test LIVE credentials (ALPACA_KEY/ALPACA_SECRET)
live_key = os.getenv("ALPACA_KEY")
live_secret = os.getenv("ALPACA_SECRET")
live_ok = test_account("LIVE ACCOUNT", live_key, live_secret, paper=False)

# Test PAPER credentials (ALPACA_KEY_PAPER/ALPACA_SECRET_PAPER)
paper_key = os.getenv("ALPACA_KEY_PAPER")
paper_secret = os.getenv("ALPACA_SECRET_PAPER")
paper_ok = test_account("PAPER ACCOUNT", paper_key, paper_secret, paper=True)

print("\n" + "=" * 50)
print("POSITION SIZES (LIVE)")
print("=" * 50)
print("  MINIMUM (Score 0):    $100")
print("  STANDARD (Score 1):   $150")
print("  HIGH (Score 2):       $200")
print("  VERY_HIGH (Score 3):  $300")
print("\nPAPER SHADOW: 50x live size")
print("  MINIMUM:    $5,000")
print("  STANDARD:   $7,500")
print("  HIGH:       $10,000")
print("  VERY_HIGH:  $15,000")

print("\n" + "=" * 50)
if live_ok and paper_ok:
    print("✅ READY FOR DUAL TRADING (LIVE + PAPER SHADOW)")
elif live_ok:
    print("⚠️  LIVE ONLY (no paper shadow - check ALPACA_KEY_PAPER)")
else:
    print("❌ CREDENTIALS FAILED - DO NOT START SERVER")
print("=" * 50)
