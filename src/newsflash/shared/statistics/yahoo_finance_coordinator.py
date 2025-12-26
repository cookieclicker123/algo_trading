"""
Yahoo Finance coordinator - fetches industry, sector, market_cap using yfinance.

yfinance scrapes Yahoo Finance - no API key required, no hard rate limits.
Much simpler than FinnhubCoordinator since we don't need rate limiting queues.
"""
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class YahooFinanceCoordinator:
    """
    Coordinates Yahoo Finance metadata fetches via yfinance.
    
    Features:
    - Session-based caching (avoids duplicate fetches within same session)
    - Thread pool executor for non-blocking async calls
    - Simple and reliable (no rate limit management needed)
    
    Provides: industry, sector, market_cap_millions
    (price and exchange come from Alpaca)
    """
    
    def __init__(self, max_concurrent: int = 5):
        """
        Initialize coordinator.
        
        Args:
            max_concurrent: Max concurrent yfinance calls (to be polite to Yahoo)
        """
        # Session-based cache: ticker -> metadata dict
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_session: Optional[str] = None  # Current session for cache validity
        
        # Semaphore to limit concurrent calls (be polite to Yahoo)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
        # Thread pool for blocking yfinance calls
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        
        # Lock for cache operations
        self._cache_lock = asyncio.Lock()
        
        # Compatibility with FinnhubCoordinator interface (checked by recall_engine)
        self._worker_task: Optional[asyncio.Task] = None
        logger.info(
            "YahooFinanceCoordinator initialized",
            max_concurrent=max_concurrent
        )
    
    async def start(self) -> None:
        """Start the coordinator (no background tasks needed)."""
        logger.info("YahooFinanceCoordinator started")
    
    async def stop(self) -> None:
        """Stop the coordinator and cleanup."""
        self._executor.shutdown(wait=False)
        logger.info("YahooFinanceCoordinator stopped")
    
    async def fetch_metadata(
        self,
        ticker: str,
        timeout: float = 30.0
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch ticker metadata (industry, sector, market_cap_millions).
        
        Args:
            ticker: Ticker symbol
            timeout: Maximum time to wait for result
            
        Returns:
            Metadata dict with: industry, sector, market_cap_millions
            Returns None if fetch fails
        """
        # Check cache first
        cached = await self._get_from_cache(ticker)
        if cached is not None:
            logger.debug(
                "YahooFinance: Cache hit",
                ticker=ticker
            )
            return cached
        
        # Fetch from Yahoo Finance
        try:
            async with self._semaphore:
                result = await asyncio.wait_for(
                    self._fetch_async(ticker),
                    timeout=timeout
                )
                
                if result:
                    await self._set_cache(ticker, result)
                    logger.debug(
                        "YahooFinance: Fetched metadata",
                        ticker=ticker,
                        industry=result.get("industry"),
                        sector=result.get("sector"),
                        market_cap=result.get("market_cap_millions")
                    )
                    return result
                else:
                    logger.debug(
                        "YahooFinance: No metadata available",
                        ticker=ticker
                    )
                    return None
                    
        except asyncio.TimeoutError:
            logger.warning(
                "YahooFinance: Timeout fetching metadata",
                ticker=ticker,
                timeout=timeout
            )
            return None
        except Exception as e:
            logger.warning(
                "YahooFinance: Error fetching metadata",
                ticker=ticker,
                error=str(e)
            )
            return None
    
    async def _fetch_async(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata asynchronously using thread pool.
        
        yfinance is blocking, so we run it in an executor.
        """
        loop = asyncio.get_event_loop()
        
        try:
            result = await loop.run_in_executor(
                self._executor,
                self._fetch_sync,
                ticker
            )
            return result
        except Exception as e:
            logger.debug(
                "YahooFinance: Executor error",
                ticker=ticker,
                error=str(e)
            )
            return None
    
    def _fetch_sync(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Synchronous fetch using yfinance (runs in thread pool).
        
        Returns:
            Dict with industry, sector, market_cap_millions or None
        """
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            if not info:
                return None
            
            # Extract the 3 fields we need
            industry = info.get("industry")
            sector = info.get("sector")
            market_cap = info.get("marketCap")
            
            # Convert market cap to millions
            market_cap_millions = None
            if market_cap is not None:
                market_cap_millions = market_cap / 1_000_000
            
            # Only return if we got at least one field
            if industry or sector or market_cap_millions:
                return {
                    "industry": industry,
                    "sector": sector,
                    "market_cap_millions": market_cap_millions
                }
            
            return None
            
        except Exception as e:
            logger.debug(
                "YahooFinance: Sync fetch error",
                ticker=ticker,
                error=str(e)
            )
            return None
    
    async def _get_from_cache(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get metadata from cache if valid."""
        from ...utils.brokerage.session_detector import get_market_session
        
        current_session, _ = get_market_session()
        
        async with self._cache_lock:
            # Invalidate cache if session changed
            if self._cache_session != current_session:
                self._cache.clear()
                self._cache_session = current_session
                return None
            
            return self._cache.get(ticker)
    
    async def _set_cache(self, ticker: str, metadata: Dict[str, Any]) -> None:
        """Store metadata in cache."""
        async with self._cache_lock:
            self._cache[ticker] = metadata
