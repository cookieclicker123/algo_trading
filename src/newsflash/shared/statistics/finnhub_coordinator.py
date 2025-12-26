"""
Finnhub API coordinator - manages rate limiting, caching, and concurrent access.

Finnhub API coordinator for:
- Industry, sector, market cap, shares outstanding
- Better rate limits (60 calls/min vs 20)
- More reliable API

Shared by all statistics engines.
"""
import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from collections import deque

import finnhub

from ...utils.logging_config import get_logger
from ...utils.brokerage.session_detector import get_market_session

logger = get_logger(__name__)


class FinnhubCoordinator:
    """
    Coordinates Finnhub API calls across all statistics engines.
    
    Features:
    - Semaphore: Limits concurrent API calls (max 10)
    - Cache: Session-based caching (avoids duplicate fetches)
    - Rate Limiter: Enforces 60 requests/min limit (free tier)
    - Queue: Queues requests during high throughput
    - Worker: Processes queue respecting rate limits
    """
    
    # Rate limits (Finnhub free tier: 60 calls/min)
    MAX_CONCURRENT_REQUESTS = 10  # Semaphore limit
    MAX_REQUESTS_PER_MINUTE = 55  # Conservative (60 is limit, use 55 for safety)
    CACHE_TTL_MINUTES = 60  # Cache for 1 hour (entire session)
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize coordinator.
        
        Args:
            api_key: Finnhub API key (from FINNHUB_API_KEY env var if not provided)
        """
        # Get API key from env if not provided
        if api_key is None:
            api_key = os.getenv("FINNHUB_API_KEY")
        
        if not api_key:
            raise ValueError("FINNHUB_API_KEY must be set in environment or passed to constructor")
        
        # Initialize Finnhub client
        self.client = finnhub.Client(api_key=api_key)
        
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
            "FinnhubCoordinator initialized",
            max_concurrent=self.MAX_CONCURRENT_REQUESTS,
            max_per_minute=self.MAX_REQUESTS_PER_MINUTE
        )
    
    async def start(self) -> None:
        """Start the worker task."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("FinnhubCoordinator worker started")
    
    async def stop(self) -> None:
        """Stop the worker task."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            logger.info("FinnhubCoordinator worker stopped")
    
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
            (price and exchange come from Alpaca)
        """
        # Normalize ticker
        ticker = ticker.upper().strip()
        
        # Check cache first
        cached = await self._get_from_cache(ticker)
        if cached is not None:
            logger.debug("Finnhub: Cache hit", ticker=ticker)
            return cached
        
        # Create future for result
        future_id = f"{ticker}_{id(asyncio.current_task())}"
        future = asyncio.Future()
        
        async with self._results_lock:
            self._pending_results[future_id] = (future, ticker)
        
        # Add to queue
        await self._request_queue.put((future_id, ticker))
        
        logger.debug("Finnhub: Queued request", ticker=ticker, queue_size=self._request_queue.qsize())
        
        # Wait for result with timeout
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("Finnhub: Request timed out", ticker=ticker, timeout=timeout)
            async with self._results_lock:
                self._pending_results.pop(future_id, None)
            return None
    
    async def _get_from_cache(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get metadata from cache if valid."""
        async with self._cache_lock:
            if ticker not in self._cache:
                return None
            
            metadata, cached_at, cached_session = self._cache[ticker]
            
            # Check if cache is still valid (same session, within TTL)
            current_session, _ = get_market_session()
            if cached_session != current_session:
                # Session changed - invalidate cache
                del self._cache[ticker]
                return None
            
            age_minutes = (datetime.now() - cached_at).total_seconds() / 60
            if age_minutes > self.CACHE_TTL_MINUTES:
                # Cache expired
                del self._cache[ticker]
                return None
            
            return metadata
    
    async def _set_cache(self, ticker: str, metadata: Dict[str, Any]) -> None:
        """Store metadata in cache."""
        async with self._cache_lock:
            current_session, _ = get_market_session()
            self._cache[ticker] = (metadata, datetime.now(), current_session)
    
    async def _check_rate_limit(self) -> tuple[bool, float]:
        """
        Check if we can make a request (rate limit check).
        
        Returns:
            Tuple of (can_proceed, wait_seconds)
        """
        async with self._rate_limit_lock:
            now = datetime.now()
            one_minute_ago = now - timedelta(minutes=1)
            
            # Remove requests older than 1 minute
            while self._request_times and self._request_times[0] < one_minute_ago:
                self._request_times.popleft()
            
            if len(self._request_times) < self.MAX_REQUESTS_PER_MINUTE:
                return True, 0.0
            
            # Need to wait - calculate wait time
            oldest_request = self._request_times[0]
            wait_until = oldest_request + timedelta(minutes=1)
            wait_seconds = (wait_until - now).total_seconds()
            return False, max(0.0, wait_seconds)
    
    async def _record_request(self) -> None:
        """Record a request for rate limiting."""
        async with self._rate_limit_lock:
            self._request_times.append(datetime.now())
    
    async def _worker_loop(self) -> None:
        """
        Worker loop that processes the request queue.
        
        Respects rate limits and processes requests sequentially.
        """
        logger.info("FinnhubCoordinator worker loop started")
        
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
                        "Finnhub: Error fetching metadata in worker",
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
                logger.info("FinnhubCoordinator worker loop cancelled")
                break
            except Exception as e:
                logger.error(
                    "Finnhub: Error in worker loop",
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
                
                # Run Finnhub API call in executor (it's blocking)
                # Finnhub client.company_profile2() is synchronous
                profile = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: self.client.company_profile2(symbol=ticker)),
                    timeout=10.0
                )
                
                if not profile or not isinstance(profile, dict):
                    logger.warning("Finnhub: Empty or invalid profile", ticker=ticker)
                    return None
                
                # Extract data from profile
                industry = profile.get('finnhubIndustry')
                # Sector might not be directly available - try to get it or map from industry
                sector = profile.get('sector') or profile.get('gicsSector') or None
                
                # Market cap (in USD, convert to millions)
                market_cap_raw = profile.get('marketCapitalization')
                market_cap_millions = None
                if market_cap_raw:
                    try:
                        market_cap_millions = float(market_cap_raw) / 1_000_000
                    except (TypeError, ValueError):
                        pass
                
                # Shares outstanding (for potential market cap calculation)
                shares_outstanding = profile.get('shareOutstanding')
                
                metadata = {
                    "industry": industry,
                    "sector": sector,
                    "market_cap_millions": market_cap_millions,
                    "shares_outstanding": float(shares_outstanding) if shares_outstanding else None,
                    # price and exchange will be added from Alpaca
                }
                
                logger.debug("Finnhub: Successfully fetched metadata", ticker=ticker)
                return metadata
                
            except asyncio.TimeoutError:
                logger.warning("Finnhub: Timeout fetching metadata", ticker=ticker)
                return None
            except Exception as e:
                logger.warning(
                    "Finnhub: Failed to fetch metadata",
                    ticker=ticker,
                    error=str(e),
                    error_type=type(e).__name__
                )
                return None
