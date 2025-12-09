"""
Market data validator - validates market cap and price thresholds.

Pure infrastructure - uses Alpaca API for prices and yfinance for market cap.
Fetches data on-demand (no caching) - prices change frequently, always fresh data.
"""
from typing import Optional, Tuple

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from ...utils.logging_config import get_logger
import yfinance as yf

logger = get_logger(__name__)


class MarketDataValidator:
    """
    Validates market cap and price thresholds for tickers.
    
    Responsibilities:
    - Fetch market cap and price data on-demand from Alpaca API and yfinance
    - No caching - always fresh data (prices change frequently)
    - Handle API failures gracefully
    
    Does NOT:
    - Know about business logic
    - Know about domain models
    - Cache data (fetch on-demand for freshness)
    """
    
    def __init__(
        self,
        trading_client: TradingClient,
        market_data_client: StockHistoricalDataClient
    ):
        """
        Initialize market data validator.
        
        Args:
            trading_client: Alpaca TradingClient instance for fetching asset info
            market_data_client: Alpaca StockHistoricalDataClient for fetching prices
        """
        self.trading_client = trading_client
        self.market_data_client = market_data_client
        
        logger.info("MarketDataValidator initialized (on-demand fetching, no cache)")
    
    async def start(self) -> None:
        """
        Start market data validator (no-op, kept for interface consistency).
        
        Idempotent: Safe to call multiple times.
        """
        logger.info("MarketDataValidator started (on-demand fetching)")
    
    async def stop(self) -> None:
        """
        Stop market data validator (no-op, kept for interface consistency).
        
        Idempotent: Safe to call multiple times.
        """
        logger.info("MarketDataValidator stopped")
    
    async def get_market_cap_and_price(
        self,
        ticker: str
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Get market cap (in millions) and price for a ticker.
        
        Fetches fresh data on-demand (no caching - prices change frequently).
        
        Args:
            ticker: Ticker symbol (uppercase)
            
        Returns:
            Tuple of (market_cap_millions, price) or (None, None) if unavailable
        """
        ticker_upper = ticker.upper()
        
        # Fetch fresh data on-demand
        market_cap_millions, price = await self._fetch_market_data(ticker_upper)
        
        return market_cap_millions, price
    
    async def _fetch_market_data(
        self,
        ticker: str
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Fetch market cap and price from Alpaca API.
        
        Args:
            ticker: Ticker symbol (uppercase)
            
        Returns:
            Tuple of (market_cap_millions, price) or (None, None) if unavailable
        """
        try:
            # Fetch price from market data client
            price = None
            try:
                logger.debug("MarketDataValidator: Fetching price from Alpaca", ticker=ticker)
                request = StockLatestQuoteRequest(symbol_or_symbols=[ticker])
                quotes = self.market_data_client.get_stock_latest_quote(request)
                
                if quotes and ticker in quotes:
                    quote = quotes[ticker]
                    # Use ask price if available, otherwise bid, otherwise mid
                    if quote.ask_price and quote.ask_price > 0:
                        price = float(quote.ask_price)
                    elif quote.bid_price and quote.bid_price > 0:
                        price = float(quote.bid_price)
                    elif quote.bid_price and quote.ask_price:
                        # Calculate mid price
                        price = (float(quote.bid_price) + float(quote.ask_price)) / 2.0
            except Exception as e:
                logger.debug(
                    "MarketDataValidator: Failed to fetch price",
                    ticker=ticker,
                    error=str(e)
                )
            
            # Fetch market cap from yfinance (run in executor to avoid blocking event loop)
            market_cap_millions = None
            if price:  # Only fetch market cap if we have price
                try:
                    import asyncio
                    logger.debug("MarketDataValidator: Fetching market cap from yfinance", ticker=ticker)
                    loop = asyncio.get_event_loop()
                    # Run yfinance in executor to avoid blocking event loop
                    stock = await loop.run_in_executor(None, lambda: yf.Ticker(ticker))
                    info = await loop.run_in_executor(None, lambda: stock.info)
                    
                    # Get market cap from yfinance (in USD)
                    market_cap_raw = info.get('marketCap')
                    if market_cap_raw:
                        # Convert to millions
                        market_cap_millions = market_cap_raw / 1_000_000
                except Exception as e:
                    logger.debug(
                        "MarketDataValidator: Failed to fetch market cap from yfinance",
                        ticker=ticker,
                        error=str(e)
                    )
            
            return market_cap_millions, price
        
        except Exception as e:
            logger.error(
                "MarketDataValidator: Failed to fetch market data",
                ticker=ticker,
                error=str(e),
                exc_info=True
            )
            return None, None
    
