"""
Yahoo Finance coordinator - fetches industry, sector, market_cap using yfinance.

OPTIMIZED: Uses two-tier persistent cache for instant lookups:
- Permanent cache: sector, industry (never changes)
- Daily cache: market_cap_millions (refreshed at 4am UK)

Only calls yfinance for cache misses, reducing latency from 200-2000ms to ~0ms.
"""
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from ...utils.logging_config import get_logger

if TYPE_CHECKING:
    from ...infra.cache.metadata_cache import MetadataCache

logger = get_logger(__name__)


class YahooFinanceCoordinator:
    """
    Coordinates Yahoo Finance metadata fetches via yfinance.

    Features:
    - Two-tier persistent cache (permanent + daily) for instant lookups
    - Session-based in-memory caching
    - Thread pool executor for non-blocking async calls
    - Background retry queue for failed fetches

    Provides: industry, sector, market_cap_millions
    (price and exchange come from Alpaca)
    """

    def __init__(
        self,
        max_concurrent: int = 3,
        max_retries: int = 3,
        num_workers: int = 10,
        metadata_cache: Optional["MetadataCache"] = None
    ):
        """
        Initialize coordinator with rate limiting, retry logic, and background queue.

        Args:
            max_concurrent: Max concurrent yfinance calls
            max_retries: Maximum retry attempts with exponential backoff
            num_workers: Number of background worker tasks
            metadata_cache: Optional persistent cache for instant lookups
        """
        # Persistent metadata cache (permanent + daily)
        self._metadata_cache = metadata_cache

        # Session-based in-memory cache: ticker -> metadata dict
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_session: Optional[str] = None

        # Semaphore to limit concurrent calls
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # Thread pool for blocking yfinance calls
        self._executor = ThreadPoolExecutor(max_workers=num_workers)

        # Lock for cache operations
        self._cache_lock = asyncio.Lock()

        # Retry configuration
        self.max_retries = max_retries
        self._retry_delays = [1.0, 2.0, 5.0]

        # Background queue for pending metadata fetches
        self._fetch_queue: Optional[asyncio.Queue] = None
        self._worker_tasks: list[asyncio.Task] = []
        self.num_workers = num_workers

        # Track pending fetches (ticker -> callbacks)
        self._pending_fetches: Dict[str, list[Callable[[str, Optional[Dict[str, Any]]], Any]]] = {}
        self._pending_lock = asyncio.Lock()

        # Compatibility field
        self._worker_task: Optional[asyncio.Task] = None

        logger.info(
            "YahooFinanceCoordinator initialized",
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            num_workers=num_workers,
            has_persistent_cache=metadata_cache is not None
        )
    
    async def start(self) -> None:
        """Start the coordinator and background worker tasks."""
        # Initialize queue for background processing
        self._fetch_queue = asyncio.Queue()
        
        # Start background workers to process queued fetches (prevents blocking during bulk delivery)
        # Use 10 workers for I/O-bound metadata fetching
        self._worker_tasks = [
            asyncio.create_task(self._worker_loop(f"worker_{i}"))
            for i in range(self.num_workers)
        ]
        
        logger.info("YahooFinanceCoordinator started", workers=self.num_workers)
    
    async def stop(self) -> None:
        """Stop the coordinator and cleanup."""
        # Cancel worker tasks
        for task in self._worker_tasks:
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        
        self._executor.shutdown(wait=False)
        logger.info("YahooFinanceCoordinator stopped")
    
    async def fetch_metadata(
        self,
        ticker: str,
        timeout: float = 30.0,
        queue_on_failure: bool = True,
        callback: Optional[Callable[[str, Optional[Dict[str, Any]]], Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch ticker metadata (industry, sector, market_cap_millions).

        OPTIMIZED: Checks persistent cache first for instant lookups (~0ms).
        Only calls yfinance for cache misses.

        Args:
            ticker: Ticker symbol
            timeout: Maximum time to wait for result
            queue_on_failure: If True, automatically queue for background retry on failure
            callback: Optional async callback(ticker, metadata) called when fetch completes

        Returns:
            Metadata dict with: industry, sector, market_cap_millions
            Returns None if fetch fails (but will be queued for background retry if queue_on_failure=True)
        """
        # Check persistent cache first (instant, ~0ms)
        if self._metadata_cache:
            cached = await self._metadata_cache.get(ticker)
            if cached and cached.get("sector") and cached.get("industry"):
                # Full cache hit - return immediately
                logger.debug("YahooFinance: Persistent cache hit", ticker=ticker)
                return cached

        # Check session-based in-memory cache
        cached = await self._get_from_cache(ticker)
        if cached is not None:
            logger.debug("YahooFinance: Session cache hit", ticker=ticker)
            return cached
        
        # Fetch from Yahoo Finance with exponential backoff retry
        # Critical for high-load scenarios (12pm-1pm ET bulk delivery)
        for attempt in range(self.max_retries):
            try:
                async with self._semaphore:
                    result = await asyncio.wait_for(
                        self._fetch_async(ticker),
                        timeout=timeout
                    )
                    
                    if result:
                        await self._set_cache(ticker, result)

                        # Save to persistent cache for future instant lookups
                        if self._metadata_cache:
                            await self._metadata_cache.set_from_full_metadata(ticker, result)

                        logger.debug(
                            "YahooFinance: Fetched metadata",
                            ticker=ticker,
                            industry=result.get("industry"),
                            sector=result.get("sector"),
                            market_cap=result.get("market_cap_millions"),
                            attempt=attempt + 1
                        )
                        
                        # If this was a queued retry, notify any pending callbacks
                        if callback:
                            await callback(ticker, result)
                        else:
                            await self._notify_pending_callbacks(ticker, result)
                        
                        return result
                    else:
                        # No metadata available - don't retry (ticker might not exist)
                        logger.debug(
                            "YahooFinance: No metadata available",
                            ticker=ticker
                        )
                        return None
                        
            except asyncio.TimeoutError:
                if attempt < self.max_retries - 1:
                    delay = self._retry_delays[attempt]
                    logger.warning(
                        "YahooFinance: Timeout, retrying after delay",
                        ticker=ticker,
                        attempt=attempt + 1,
                        max_retries=self.max_retries,
                        delay=delay
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning(
                        "YahooFinance: Timeout after all retries - queuing for background retry",
                        ticker=ticker,
                        attempts=self.max_retries,
                        timeout=timeout
                    )
                    # Queue for background retry to ensure eventual completion
                    if queue_on_failure and self._fetch_queue is not None:
                        await self._queue_for_retry(ticker, callback)
                    return None
            except Exception as e:
                # Check if it's a rate limit error (common during bulk delivery)
                error_str = str(e).lower()
                is_rate_limit = any(phrase in error_str for phrase in [
                    "rate limit", "too many requests", "429", "throttle", "limit exceeded"
                ])
                
                if attempt < self.max_retries - 1:
                    delay = self._retry_delays[attempt]
                    logger.warning(
                        "YahooFinance: Error, retrying after delay",
                        ticker=ticker,
                        attempt=attempt + 1,
                        max_retries=self.max_retries,
                        delay=delay,
                        error=str(e),
                        is_rate_limit=is_rate_limit
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning(
                        "YahooFinance: Error after all retries - queuing for background retry",
                        ticker=ticker,
                        attempts=self.max_retries,
                        error=str(e),
                        is_rate_limit=is_rate_limit
                    )
                    # Queue for background retry to ensure eventual completion
                    if queue_on_failure and self._fetch_queue is not None:
                        await self._queue_for_retry(ticker, callback)
                    return None
        
        return None
    
    async def _queue_for_retry(self, ticker: str, callback: Optional[Callable[[str, Optional[Dict[str, Any]]], Any]] = None) -> None:
        """
        Queue a failed fetch for background retry.
        
        CRITICAL: This ensures metadata is eventually populated even if initial fetch fails.
        Non-blocking - doesn't delay real-time trade operations.
        """
        if self._fetch_queue is None:
            return
        
        # Store callback for when fetch eventually succeeds
        if callback:
            async with self._pending_lock:
                if ticker not in self._pending_fetches:
                    self._pending_fetches[ticker] = []
                self._pending_fetches[ticker].append(callback)
        
        # Queue for background worker to retry
        await self._fetch_queue.put((ticker, None))  # None callback - will use stored callbacks
        logger.debug(
            "YahooFinance: Queued failed fetch for background retry",
            ticker=ticker,
            queue_size=self._fetch_queue.qsize()
        )
    
    async def _notify_pending_callbacks(self, ticker: str, metadata: Dict[str, Any]) -> None:
        """Notify all pending callbacks that metadata was successfully fetched."""
        async with self._pending_lock:
            callbacks = self._pending_fetches.pop(ticker, [])
        
        # Fire-and-forget: notify callbacks without blocking
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(ticker, metadata))
                else:
                    callback(ticker, metadata)
            except Exception as e:
                logger.warning(
                    "YahooFinance: Callback error",
                    ticker=ticker,
                    error=str(e)
                )
    
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
            
            # Extract fields we need
            industry = info.get("industry")
            sector = info.get("sector")
            market_cap = info.get("marketCap")
            float_shares = info.get("floatShares")

            # Convert market cap to millions
            market_cap_millions = None
            if market_cap is not None:
                market_cap_millions = market_cap / 1_000_000

            # Only return if we got at least one field
            if industry or sector or market_cap_millions or float_shares:
                result = {
                    "industry": industry,
                    "sector": sector,
                    "market_cap_millions": market_cap_millions
                }
                if float_shares is not None:
                    result["float_shares"] = int(float_shares)
                return result
            
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
    
    async def queue_metadata_fetch(
        self,
        ticker: str,
        callback: Optional[Callable[[str, Optional[Dict[str, Any]]], Any]] = None
    ) -> None:
        """
        Queue metadata fetch for background processing.
        
        Useful during high-load scenarios (bulk delivery) to prevent blocking.
        Fetches are processed by background workers with rate limiting and retry logic.
        
        Args:
            ticker: Ticker symbol to fetch
            callback: Optional async callback(ticker, metadata) called when fetch completes
        """
        if self._fetch_queue is None:
            # Queue not initialized - fall back to direct fetch
            metadata = await self.fetch_metadata(ticker)
            if callback:
                await callback(ticker, metadata)
            return
        
        await self._fetch_queue.put((ticker, callback))
        logger.debug("YahooFinance: Queued metadata fetch", ticker=ticker, queue_size=self._fetch_queue.qsize())
    
    async def _worker_loop(self, worker_name: str) -> None:
        """
        Background worker loop to process queued metadata fetches.
        
        Handles rate limiting, retries, and prevents overwhelming Yahoo Finance
        during extreme stress cases (12pm-1pm ET bulk delivery).
        
        CRITICAL: These workers ensure failed fetches are eventually completed,
        preventing null metadata fields. Non-blocking - doesn't delay trade operations.
        """
        logger.debug(f"YahooFinance: Worker {worker_name} started")
        
        while True:
            try:
                # Wait for queued fetch (with timeout to allow graceful shutdown)
                try:
                    ticker, callback = await asyncio.wait_for(
                        self._fetch_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # No items in queue - continue loop (allows graceful shutdown)
                    continue
                
                try:
                    # Get stored callbacks for this ticker (if any)
                    async with self._pending_lock:
                        stored_callbacks = self._pending_fetches.get(ticker, [])
                        # Use provided callback if available, otherwise use stored callbacks
                        callbacks_to_use = [callback] if callback else stored_callbacks
                    
                    # Fetch metadata with retry logic (queue_on_failure=False to prevent infinite loops)
                    # If this retry also fails, it will be logged but not re-queued
                    metadata = await self.fetch_metadata(
                        ticker,
                        timeout=30.0,
                        queue_on_failure=False,  # Don't re-queue if worker retry fails
                        callback=None  # Callbacks will be notified via _notify_pending_callbacks
                    )
                    
                    if metadata:
                        # Notify all stored callbacks (fire-and-forget)
                        for cb in callbacks_to_use:
                            if cb:
                                try:
                                    if asyncio.iscoroutinefunction(cb):
                                        asyncio.create_task(cb(ticker, metadata))
                                    else:
                                        cb(ticker, metadata)
                                except Exception as cb_error:
                                    logger.warning(
                                        "YahooFinance: Worker callback error",
                                        worker=worker_name,
                                        ticker=ticker,
                                        error=str(cb_error)
                                    )
                        
                        logger.debug(
                            "YahooFinance: Worker successfully fetched metadata",
                            worker=worker_name,
                            ticker=ticker,
                            queue_size=self._fetch_queue.qsize(),
                            callbacks_notified=len(callbacks_to_use)
                        )
                    else:
                        logger.warning(
                            "YahooFinance: Worker failed to fetch metadata after retry",
                            worker=worker_name,
                            ticker=ticker
                        )
                    
                except Exception as fetch_error:
                    logger.warning(
                        "YahooFinance: Worker fetch error",
                        worker=worker_name,
                        ticker=ticker,
                        error=str(fetch_error)
                    )
                finally:
                    # Mark task as done
                    self._fetch_queue.task_done()
                    
            except asyncio.CancelledError:
                logger.debug(f"YahooFinance: Worker {worker_name} cancelled")
                break
            except Exception as e:
                logger.error(
                    f"YahooFinance: Worker {worker_name} error",
                    error=str(e),
                    exc_info=True
                )
                await asyncio.sleep(1.0)  # Brief pause before retrying
        
        logger.debug(f"YahooFinance: Worker {worker_name} stopped")
