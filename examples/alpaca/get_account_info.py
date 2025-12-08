from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest

trading_client = TradingClient('PK457NJ67N3EEORVXS2ZHLFN2S', '5Esz8XhyTX5i2uKQy5dtefRBnDMvVw7tZoCMetG5sCnJ')

# Get our account information.
account = trading_client.get_account()

# Check if our account is restricted from trading.
if account.trading_blocked:
    print('Account is currently restricted from trading.')

# Check how much money we can use to open new positions.
print(f'${account.buying_power} is available as buying power.')