from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
import dotenv
import os
dotenv.load_dotenv()


trading_client = TradingClient(os.getenv('ALPACA_KEY'), os.getenv('ALPACA_SECRET'))

market_order_data = MarketOrderRequest(
                    symbol="MU",
                    qty=2,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY
)

order = trading_client.submit_order(market_order_data)
print(order)