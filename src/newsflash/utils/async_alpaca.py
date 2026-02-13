"""
Async wrappers for synchronous Alpaca SDK calls.

The Alpaca SDK uses `requests` internally, which is synchronous.
Calling these methods directly in async code blocks the event loop.
This module provides async wrappers that run sync calls in a thread pool.

Also provides connection pool configuration to prevent pool exhaustion
when making many concurrent requests.

Usage:
    from newsflash.utils.async_alpaca import run_sync_alpaca_call, configure_alpaca_client_pool

    # Configure connection pool (do this once after creating clients)
    configure_alpaca_client_pool(market_data_client)
    configure_alpaca_client_pool(trading_client)

    # Instead of:
    quotes = market_data_client.get_stock_quotes(request)  # BLOCKS!

    # Use:
    quotes = await run_sync_alpaca_call(
        market_data_client.get_stock_quotes,
        request
    )
"""
import asyncio
from typing import TypeVar, Callable, Any
from functools import partial

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

T = TypeVar('T')

# Connection pool settings
# Increase from default of 10 to handle concurrent requests
# With Algo Trader Plus (10k req/min), 100 is well within limits
POOL_CONNECTIONS = 100  # Number of connection pools to cache
POOL_MAXSIZE = 100      # Max connections per pool


def configure_alpaca_client_pool(client: Any, pool_size: int = POOL_MAXSIZE) -> None:
    """
    Configure larger connection pool for an Alpaca client.

    The Alpaca SDK uses requests internally with a default pool size of 10.
    Under load, this causes "Connection pool is full" warnings and SSL errors.

    Call this once after creating each Alpaca client:
        configure_alpaca_client_pool(market_data_client)
        configure_alpaca_client_pool(trading_client)

    Args:
        client: An Alpaca SDK client (TradingClient, StockHistoricalDataClient, etc.)
        pool_size: Max connections per pool (default: 50)
    """
    # Alpaca clients store their session in _session attribute
    if not hasattr(client, '_session'):
        return

    session = client._session

    # Create adapter with larger pool and retry logic
    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )

    adapter = HTTPAdapter(
        pool_connections=POOL_CONNECTIONS,
        pool_maxsize=pool_size,
        max_retries=retry_strategy,
        pool_block=False  # Don't block when pool is full, create new connection
    )

    # Mount for both HTTP and HTTPS
    session.mount("https://", adapter)
    session.mount("http://", adapter)


async def run_sync_alpaca_call(
    func: Callable[..., T],
    *args,
    **kwargs
) -> T:
    """
    Run a synchronous Alpaca SDK call in a thread pool executor.

    This prevents blocking the event loop when Alpaca is slow.

    Args:
        func: The synchronous Alpaca SDK method to call
        *args: Positional arguments to pass to the method
        **kwargs: Keyword arguments to pass to the method

    Returns:
        The result of the Alpaca SDK call

    Example:
        quotes = await run_sync_alpaca_call(
            client.get_stock_quotes,
            StockQuotesRequest(symbol_or_symbols="AAPL", ...)
        )
    """
    loop = asyncio.get_event_loop()

    # Create a partial function with all arguments
    if kwargs:
        call = partial(func, *args, **kwargs)
    elif args:
        call = partial(func, *args)
    else:
        call = func

    # Run in default thread pool executor (doesn't block event loop)
    return await loop.run_in_executor(None, call)
