"""
YFinance API coordinator - manages rate limiting, caching, and concurrent access.

Shared by both recall and signal engines to:
- Limit concurrent API calls (semaphore)
- Cache metadata per session (avoid duplicate fetches)
- Enforce rate limits (20 requests/min)
- Queue requests during high throughput
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from collections import deque

import yfinance as yf

from ...utils.logging_config import get_logger
from ...utils.brokerage.session_detector import get_market_session

logger = get_logger(__name__)


class YFinanceCoordinator:
    """
    Coordinates yfinance API calls across all statistics engines.
    
    Features:
    - Semaphore: Limits concurrent API calls (max 5)
    - Cache: Session-based caching (avoids duplicate fetches)
    - Rate Limiter: Enforces 20 requests/min limit
    - Queue: Queues requests during high throughput
    - Worker: Processes queue respecting rate limits
    """
    
    # Rate limits (conservative)
    MAX_CONCURRENT_REQUESTS = 5  # Semaphore limit
    MAX_REQUESTS_PER_MINUTE = 18  # Conservative (20 is limit, use 18 for safety)
    CACHE_TTL_MINUTES = 60  # Cache for 1 hour (entire session)
    
    def __init__(self):
        """Initialize coordinator."""
        # Semaphore to limit concurrent API calls
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)
        
        # Cache: ticker -> (metadata, cached_at, session)
        self._cache: Dict[str, tuple[Dict[str, Any], datetime, str]] = {}
        self._cache_lock = asyncio.Lock()
        
        # Rate limiter: track requests in last minute
        self._request_times: deque = deque()
        self._rate_limit_lock = asyncio.Lock()
        
        # Queue for metadata fetch requests
        self._request_queue: asyncio.Queue = asyncio.Queue()
        
        # Worker task that processes queue
        self._worker_task: Optional[asyncio.Task] = None
        
        # Results: future_id -> (future, ticker)
        self._pending_results: Dict[str, tuple[asyncio.Future, str]] = {}
        self._results_lock = asyncio.Lock()
        
        # Track current session for cache invalidation
        self._current_session: Optional[str] = None
        
        logger.info(
            "YFinanceCoordinator initialized",
            max_concurrent=self.MAX_CONCURRENT_REQUESTS,
            max_per_minute=self.MAX_REQUESTS_PER_MINUTE
        )
    
    async def start(self) -> None:
        """Start the worker task."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("YFinanceCoordinator worker started")
    
    async def stop(self) -> None:
        """Stop the worker task."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            logger.info("YFinanceCoordinator worker stopped")
    
    async def fetch_metadata(
        self,
        ticker: str,
        timeout: float = 30.0
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch ticker metadata (with caching and rate limiting).
        
        Args:
            ticker: Ticker symbol
            timeout: Maximum time to wait for result
            
        Returns:
            Metadata dict or None if fetch fails
        """
        # Normalize ticker
        ticker = ticker.upper().strip()
        
        # Check cache first
        cached = await self._get_from_cache(ticker)
        if cached is not None:
            logger.debug("YFinance: Cache hit", ticker=ticker)
            return cached
        
        # Create future for result
        future_id = f"{ticker}_{id(asyncio.current_task())}"
        future = asyncio.Future()
        
        async with self._results_lock:
            self._pending_results[future_id] = (future, ticker)
        
        # Add to queue
        await self._request_queue.put((future_id, ticker))
        
        logger.debug("YFinance: Queued request", ticker=ticker, queue_size=self._request_queue.qsize())
        
        # Wait for result with timeout
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("YFinance: Request timed out", ticker=ticker, timeout=timeout)
            async with self._results_lock:
                self._pending_results.pop(future_id, None)
            return None
    
    async def _get_from_cache(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get metadata from cache if valid."""
        async with self._cache_lock:
            if ticker not in self._cache:
                return None
            
            metadata, cached_at, cached_session = self._cache[ticker]
            
            # Check if cache is still valid (same session and not expired)
            current_session, _ = get_market_session()
            
            # Invalidate cache if session changed
            if cached_session != current_session:
                self._cache.pop(ticker, None)
                return None
            
            # Check TTL
            age = datetime.now() - cached_at
            if age > timedelta(minutes=self.CACHE_TTL_MINUTES):
                self._cache.pop(ticker, None)
                return None
            
            # Cache hit
            return metadata.copy()  # Return copy to avoid mutation
    
    async def _set_cache(self, ticker: str, metadata: Dict[str, Any]) -> None:
        """Store metadata in cache."""
        current_session, _ = get_market_session()
        async with self._cache_lock:
            self._cache[ticker] = (metadata.copy(), datetime.now(), current_session)
    
    async def _check_rate_limit(self) -> tuple[bool, float]:
        """
        Check if we can make a request (within rate limit).
        
        Returns:
            Tuple of (can_proceed: bool, wait_seconds: float)
        """
        async with self._rate_limit_lock:
            now = datetime.now()
            one_minute_ago = now - timedelta(minutes=1)
            
            # Remove old requests
            while self._request_times and self._request_times[0] < one_minute_ago:
                self._request_times.popleft()
            
            # Check if we're at limit
            if len(self._request_times) >= self.MAX_REQUESTS_PER_MINUTE:
                # Calculate wait time until oldest request expires
                oldest_time = self._request_times[0]
                wait_until = oldest_time + timedelta(minutes=1)
                wait_seconds = (wait_until - now).total_seconds()
                
                if wait_seconds > 0:
                    logger.info(
                        "YFinance: Rate limit reached, waiting",
                        current_requests=len(self._request_times),
                        wait_seconds=wait_seconds
                    )
                    return False, wait_seconds
            
            return True, 0.0
    
    async def _record_request(self) -> None:
        """Record that a request was made."""
        async with self._rate_limit_lock:
            self._request_times.append(datetime.now())
    
    async def _worker_loop(self) -> None:
        """
        Worker loop that processes the request queue.
        
        Respects rate limits and processes requests sequentially.
        """
        logger.info("YFinanceCoordinator worker loop started")
        
        while True:
            try:
                # Get next request from queue (with timeout to allow cancellation)
                try:
                    future_id, ticker = await asyncio.wait_for(
                        self._request_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # Queue empty, continue loop (allows cancellation check)
                    continue
                
                # Check rate limit
                can_proceed, wait_seconds = await self._check_rate_limit()
                if not can_proceed:
                    # Wait until we can proceed
                    await asyncio.sleep(wait_seconds)
                    # Re-queue the request
                    await self._request_queue.put((future_id, ticker))
                    continue
                
                # Check cache again (might have been cached while waiting)
                cached = await self._get_from_cache(ticker)
                if cached is not None:
                    # Resolve future with cached data
                    async with self._results_lock:
                        future, _ = self._pending_results.pop(future_id, (None, None))
                    if future and not future.done():
                        future.set_result(cached)
                    continue
                
                # Fetch metadata (with semaphore)
                try:
                    metadata = await self._fetch_with_semaphore(ticker)
                    
                    # Record request for rate limiting
                    await self._record_request()
                    
                    # Store in cache if successful
                    if metadata:
                        await self._set_cache(ticker, metadata)
                    
                    # Resolve future
                    async with self._results_lock:
                        future, _ = self._pending_results.pop(future_id, (None, None))
                    if future and not future.done():
                        future.set_result(metadata)
                    
                except Exception as e:
                    logger.error(
                        "YFinance: Error fetching metadata in worker",
                        ticker=ticker,
                        error=str(e),
                        exc_info=True
                    )
                    # Resolve future with None (failure)
                    async with self._results_lock:
                        future, _ = self._pending_results.pop(future_id, (None, None))
                    if future and not future.done():
                        future.set_result(None)
                
            except asyncio.CancelledError:
                logger.info("YFinanceCoordinator worker loop cancelled")
                break
            except Exception as e:
                logger.error(
                    "YFinance: Error in worker loop",
                    error=str(e),
                    exc_info=True
                )
                await asyncio.sleep(1.0)  # Brief pause before retry
    
    async def _fetch_with_semaphore(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata with semaphore protection.
        
        This is the actual API call - protected by semaphore.
        """
        async with self._semaphore:
            try:
                loop = asyncio.get_event_loop()
                
                # Run yfinance calls in executor (they're blocking)
                # Add timeout to prevent hanging
                stock = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: yf.Ticker(ticker)),
                    timeout=10.0
                )
                info = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: stock.info),
                    timeout=10.0
                )
                
                # Validate info is not empty and has meaningful data
                if not info or not isinstance(info, dict):
                    raise ValueError(f"yfinance returned empty or invalid info for {ticker}")
                
                if len(info) == 0:
                    raise ValueError(f"yfinance returned empty info dict for {ticker}")
                
                # Check for common error indicators
                if 'error' in info or 'Error' in info:
                    error_msg = info.get('error') or info.get('Error', 'Unknown error')
                    raise ValueError(f"yfinance returned error for {ticker}: {error_msg}")
                
                # Extract market cap and convert to millions
                market_cap_raw = info.get('marketCap')
                market_cap_millions = None
                if market_cap_raw:
                    try:
                        market_cap_millions = float(market_cap_raw) / 1_000_000
                    except (TypeError, ValueError):
                        pass
                
                # Extract price (try multiple fields)
                price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
                
                metadata = {
                    "industry": info.get('industry'),
                    "sector": info.get('sector'),
                    "market_cap_millions": market_cap_millions,
                    "price": float(price) if price else None,
                    "exchange": info.get('exchange')
                }
                
                logger.debug("YFinance: Successfully fetched metadata", ticker=ticker)
                return metadata
                
            except asyncio.TimeoutError:
                logger.warning("YFinance: Timeout fetching metadata", ticker=ticker)
                return None
            except Exception as e:
                logger.warning(
                    "YFinance: Failed to fetch metadata",
                    ticker=ticker,
                    error=str(e),
                    error_type=type(e).__name__
                )
                return None
