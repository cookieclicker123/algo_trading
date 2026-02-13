"""
Ticker validator - validates tickers are tradeable on NASDAQ/NYSE/AMEX.

Pure infrastructure - uses Alpaca API to fetch tradeable tickers.
Operational state (cache) - necessary for system operation, not business state.

Performance optimization:
- Loads from file cache on startup (instant)
- Only calls Alpaca API for background refresh (hourly)
- Saves to file after each refresh for next startup
"""
import asyncio
import json
from pathlib import Path
from typing import Set, Optional, List
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetStatus, AssetClass

from ...utils.logging_config import get_logger
from ...utils.async_alpaca import run_sync_alpaca_call

logger = get_logger(__name__)

# File cache for tradeable tickers (avoids 8000+ asset fetch on startup)
CACHE_FILE = Path("data/cache/alpaca_tradeable_tickers.json")


class TickerValidator:
    """
    Validates tickers are tradeable on NASDAQ/NYSE/AMEX exchanges.
    
    Responsibilities:
    - Fetch tradeable tickers from Alpaca API
    - Cache tickers in-memory for fast lookup
    - Refresh cache hourly with diff-based updates
    - Handle API failures with exponential backoff
    
    Does NOT:
    - Know about business logic
    - Know about domain models
    """
    
    def __init__(self, trading_client: TradingClient):
        """
        Initialize ticker validator.
        
        Args:
            trading_client: Alpaca TradingClient instance for fetching assets
        """
        self.trading_client = trading_client
        self._tradeable_tickers: Set[str] = set()  # In-memory cache
        self._lock = asyncio.Lock()  # Thread-safe updates
        self._last_update: Optional[datetime] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._is_running = False
        
        logger.info("TickerValidator initialized")
    
    async def start(self) -> None:
        """
        Start ticker validator and begin periodic refresh.

        Idempotent: Safe to call multiple times.

        Startup strategy:
        1. Try loading from file cache (instant, no API call)
        2. If file cache exists, use it and refresh in background
        3. If no file cache, fetch from Alpaca (blocking first time only)
        """
        if self._is_running:
            logger.debug("TickerValidator already running")
            return

        self._is_running = True

        # Try loading from file cache first (instant startup)
        loaded_from_file = await self._load_from_file_cache()

        if loaded_from_file:
            logger.info(
                "TickerValidator: Loaded from file cache (instant startup)",
                cache_size=len(self._tradeable_tickers)
            )
            # Start background refresh to update cache
            self._refresh_task = asyncio.create_task(self._periodic_refresh())
        else:
            # No file cache - must fetch from Alpaca (blocking first time)
            logger.info("TickerValidator: No file cache, fetching from Alpaca (first-time setup)...")
            await self._refresh_tradeable_tickers()
            logger.info(
                "TickerValidator: Cache loaded from Alpaca",
                cache_size=len(self._tradeable_tickers)
            )
            # Save to file for next startup
            await self._save_to_file_cache()
            # Start background refresh task (every hour)
            self._refresh_task = asyncio.create_task(self._periodic_refresh())

        logger.info("TickerValidator started - cache ready, hourly refresh scheduled")
    
    async def stop(self) -> None:
        """
        Stop ticker validator and cancel refresh task.
        
        Idempotent: Safe to call multiple times.
        """
        if not self._is_running:
            return
        
        self._is_running = False
        
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        
        logger.info("TickerValidator stopped")

    async def _load_from_file_cache(self) -> bool:
        """
        Load tradeable tickers from file cache.

        Returns:
            True if loaded successfully, False if no cache or error
        """
        try:
            if not CACHE_FILE.exists():
                logger.debug("TickerValidator: No file cache found")
                return False

            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)

            tickers = set(data.get("tickers", []))
            if not tickers:
                logger.warning("TickerValidator: File cache is empty")
                return False

            self._tradeable_tickers = tickers
            self._last_update = datetime.fromisoformat(data.get("updated_at", ""))

            logger.info(
                "TickerValidator: Loaded from file cache",
                count=len(tickers),
                updated_at=data.get("updated_at")
            )
            return True

        except Exception as e:
            logger.warning(f"TickerValidator: Failed to load file cache: {e}")
            return False

    async def _save_to_file_cache(self) -> None:
        """
        Save tradeable tickers to file cache for fast startup.
        """
        try:
            # Ensure directory exists
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "tickers": sorted(list(self._tradeable_tickers)),
                "updated_at": datetime.now().isoformat(),
                "count": len(self._tradeable_tickers)
            }

            with open(CACHE_FILE, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info(
                "TickerValidator: Saved to file cache",
                count=len(self._tradeable_tickers),
                path=str(CACHE_FILE)
            )

        except Exception as e:
            logger.warning(f"TickerValidator: Failed to save file cache: {e}")

    async def _periodic_refresh(self) -> None:
        """
        Background task: refresh ticker cache every hour.
        
        Runs continuously until stopped.
        """
        while self._is_running:
            try:
                # Wait 1 hour before refresh
                await asyncio.sleep(3600)  # 3600 seconds = 1 hour

                if self._is_running:
                    await self._refresh_tradeable_tickers()
                    # Save to file after successful refresh
                    await self._save_to_file_cache()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "TickerValidator: Error in periodic refresh task",
                    error=str(e),
                    exc_info=True
                )
                # Continue running - will retry next hour
    
    async def _fetch_from_alpaca(self) -> Set[str]:
        """
        Fetch tradeable NASDAQ, NYSE, and AMEX tickers from Alpaca API.
        
        Returns:
            Set of tradeable ticker symbols (uppercase)
        """
        try:
            # Fetch all active US equity assets from NASDAQ and NYSE
            # Use GetAssetsRequest filter object (correct Alpaca API format)
            filter_request = GetAssetsRequest(
                status=AssetStatus.ACTIVE,
                asset_class=AssetClass.US_EQUITY
            )
            # Use async wrapper to avoid blocking event loop (fetches 8000+ assets)
            assets = await run_sync_alpaca_call(
                self.trading_client.get_all_assets, filter=filter_request
            )
            
            # Filter for NASDAQ, NYSE, and AMEX exchanges and extract tradeable ticker symbols
            tradeable_tickers = {
                asset.symbol.upper()
                for asset in assets
                if asset.tradable and asset.exchange in ['NASDAQ', 'NYSE', 'AMEX']
            }
            
            logger.info(
                "TickerValidator: Fetched tradeable tickers from Alpaca",
                count=len(tradeable_tickers),
                exchanges=["NASDAQ", "NYSE", "AMEX"]
            )
            
            return tradeable_tickers
        
        except Exception as e:
            logger.error(
                "TickerValidator: Failed to fetch tickers from Alpaca",
                error=str(e),
                exc_info=True
            )
            raise
    
    async def _refresh_tradeable_tickers(self) -> None:
        """
        Refresh tradeable tickers cache from Alpaca API.
        
        Uses exponential backoff (3 retries) if API fails.
        Keeps old cache if all retries fail.
        """
        async with self._lock:
            max_retries = 3
            base_delay = 1.0  # Start with 1 second
            
            for attempt in range(max_retries):
                try:
                    # Fetch new tickers from Alpaca
                    new_tickers = await self._fetch_from_alpaca()
                    
                    # Compare with current cache
                    added = new_tickers - self._tradeable_tickers
                    removed = self._tradeable_tickers - new_tickers
                    
                    if added or removed:
                        # Update cache efficiently
                        self._tradeable_tickers.update(added)
                        self._tradeable_tickers.difference_update(removed)
                        
                        logger.info(
                            "TickerValidator: Updated ticker cache",
                            added=len(added),
                            removed=len(removed),
                            total=len(self._tradeable_tickers),
                            attempt=attempt + 1
                        )
                    else:
                        logger.debug(
                            "TickerValidator: Ticker cache unchanged",
                            total=len(self._tradeable_tickers),
                            attempt=attempt + 1
                        )
                    
                    # Success - update timestamp and return
                    self._last_update = datetime.now()
                    return
                
                except Exception as e:
                    if attempt < max_retries - 1:
                        # Exponential backoff: 1s, 2s, 4s
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "TickerValidator: API fetch failed, retrying with backoff",
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay_seconds=delay,
                            error=str(e)
                        )
                        await asyncio.sleep(delay)
                    else:
                        # All retries failed - keep old cache
                        logger.error(
                            "TickerValidator: All retries failed, keeping old cache",
                            max_retries=max_retries,
                            error=str(e),
                            cache_size=len(self._tradeable_tickers),
                            last_update=self._last_update.isoformat() if self._last_update else None
                        )
                        # Don't raise - keep old cache and try again next hour
    
    def is_tradeable(self, ticker: str) -> bool:
        """
        Check if a single ticker is tradeable (O(1) lookup).
        
        Args:
            ticker: Ticker symbol to check
            
        Returns:
            True if ticker is tradeable on NASDAQ/NYSE/AMEX, False otherwise
        """
        return ticker.upper() in self._tradeable_tickers
    
    def are_tradeable(self, tickers: List[str]) -> bool:
        """
        Check if any ticker in list is tradeable.
        
        Args:
            tickers: List of ticker symbols to check
            
        Returns:
            True if at least one ticker is tradeable, False otherwise
        """
        if not tickers:
            return False
        
        # If cache is empty, return False to block classification
        # This should NOT happen since cache loads blocking at startup, but keep as safety check
        if not self._tradeable_tickers:
            logger.error(
                "TickerValidator: Cache is empty (unexpected - should have loaded at startup), blocking classification",
                tickers=tickers,
                cache_size=len(self._tradeable_tickers)
            )
            return False
        
        tickers_upper = {t.upper() for t in tickers}
        return bool(tickers_upper & self._tradeable_tickers)  # Set intersection
    
    async def get_validation_reason(self, ticker: str) -> Optional[str]:
        """
        Get detailed reason why a ticker is not tradeable.

        Distinguishes between:
        - 'invalid_exchange': Exchange is not NASDAQ/NYSE/AMEX
        - 'broker_not_tradeable': Exchange is valid but ticker not tradeable on broker
        - None: Ticker is tradeable (should not be called if tradeable)

        Args:
            ticker: Ticker symbol to check
            
        Returns:
            'invalid_exchange', 'broker_not_tradeable', or None if cannot determine
        """
        ticker_upper = ticker.upper()
        
        # First check if ticker is in tradeable cache (fast path)
        # This method should only be called when are_tradeable() returns False,
        # but check anyway for safety
        if ticker_upper in self._tradeable_tickers:
            return None  # Ticker is tradeable (shouldn't happen, but safe)
        
        # If cache is empty, we can't reliably determine - default to broker_not_tradeable
        # This happens when cache is still loading
        if not self._tradeable_tickers:
            return 'broker_not_tradeable'
        
        # Ticker not in cache - check exchange to determine reason
        try:
            # Use async wrapper to avoid blocking event loop
            asset = await run_sync_alpaca_call(
                self.trading_client.get_asset, ticker
            )
            if asset and asset.exchange:
                exchange = asset.exchange
                
                # Check if exchange is in allowed list
                if exchange not in ['NASDAQ', 'NYSE', 'AMEX']:
                    return 'invalid_exchange'
                else:
                    # Exchange is valid (NASDAQ/NYSE/AMEX), but ticker not in tradeable cache
                    # This means broker doesn't support it (suspended, delisted, restricted, etc.)
                    return 'broker_not_tradeable'
        except Exception as e:
            # Asset lookup failed (ticker doesn't exist in Alpaca's system)
            # Default to broker_not_tradeable as safe fallback
            logger.debug(
                "TickerValidator: Failed to get asset for validation reason",
                ticker=ticker,
                error=str(e)
            )
            return 'broker_not_tradeable'
        
        # Fallback (shouldn't reach here)
        return 'broker_not_tradeable'
    
    def get_cache_stats(self) -> dict:
        """
        Get cache statistics for monitoring.
        
        Returns:
            Dictionary with cache statistics
        """
        return {
            "cache_size": len(self._tradeable_tickers),
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "is_running": self._is_running,
        }
