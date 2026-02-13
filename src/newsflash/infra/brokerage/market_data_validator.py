"""
Market data validator - validates market cap and price thresholds.

Pure infrastructure - uses Alpaca API for prices and YahooFinanceCoordinator for market cap.
Shares YahooFinanceCoordinator with stats engines for efficient single API call per ticker.
"""
from typing import Optional, Tuple
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockTradesRequest
from alpaca.data.enums import DataFeed

from ...utils.logging_config import get_logger
from ...utils.async_alpaca import run_sync_alpaca_call

logger = get_logger(__name__)


class MarketDataValidator:
    """
    Validates market cap and price thresholds for tickers.
    
    Responsibilities:
    - Fetch market cap and price data on-demand from Alpaca API and FinnhubCoordinator (shared with stats engines)
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
        market_data_client: StockHistoricalDataClient,
        yahoo_finance_coordinator=None  # Optional - will be injected from DI container
    ):
        """
        Initialize market data validator.
        
        Args:
            trading_client: Alpaca TradingClient instance for fetching asset info
            market_data_client: Alpaca StockHistoricalDataClient for fetching prices
            yahoo_finance_coordinator: Shared YahooFinanceCoordinator instance (for market cap, shared with stats engines)
        """
        self.trading_client = trading_client
        self.market_data_client = market_data_client
        self.yahoo_finance_coordinator = yahoo_finance_coordinator
        
        logger.info(
            "MarketDataValidator initialized",
            has_yahoo_finance_coordinator=yahoo_finance_coordinator is not None
        )
    
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

    async def has_recent_volume(
        self,
        ticker: str,
        start_time: datetime
    ) -> bool:
        """
        Check if there has been any trading volume since start_time.
        
        Args:
            ticker: Ticker symbol (uppercase)
            start_time: When to start checking for trades
            
        Returns:
            True if at least one trade was found, False otherwise (dead market)
        """
        try:
            from datetime import datetime
            end_time = datetime.now()
            
            # Fetch trades in window
            request = StockTradesRequest(
                symbol_or_symbols=ticker,
                start=start_time,
                end=end_time,
                feed=DataFeed.SIP
            )

            # Use async wrapper to avoid blocking event loop
            trades = await run_sync_alpaca_call(
                self.market_data_client.get_stock_trades, request
            )
            
            if trades and trades.data and ticker in trades.data:
                # Any data at all means there was news-driven activity
                return len(trades.data[ticker]) > 0
            
            return False
        except Exception as e:
            logger.debug(f"MarketDataValidator: Error checking recent volume for {ticker}: {e}")
            # On error, assume volume exists to avoid false positives in pre-filtering
            return True
    
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
                # Use async wrapper to avoid blocking event loop
                quotes = await run_sync_alpaca_call(
                    self.market_data_client.get_stock_latest_quote, request
                )
                
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
            
            # Fetch market cap from FinnhubCoordinator (shared with stats engines - single API call per ticker)
            market_cap_millions = None
            if price and self.yahoo_finance_coordinator:  # Only fetch market cap if we have price and coordinator is available
                try:
                    logger.debug("MarketDataValidator: Fetching market cap from YahooFinanceCoordinator", ticker=ticker)
                    # Use shared coordinator - will cache and share with stats engines
                    metadata = await self.yahoo_finance_coordinator.fetch_metadata(ticker, timeout=30.0)
                    
                    if metadata:
                        # Extract market cap from metadata (already in millions)
                        market_cap_millions = metadata.get('market_cap_millions')
                except Exception as e:
                    logger.debug(
                        "MarketDataValidator: Failed to fetch market cap from YahooFinanceCoordinator",
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
    
