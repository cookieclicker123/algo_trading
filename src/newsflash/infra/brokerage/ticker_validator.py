"""
Ticker validator - validates tickers are tradeable on NASDAQ/NYSE/AMEX.

Pure infrastructure - uses Alpaca API to fetch tradeable tickers.
Operational state (cache) - necessary for system operation, not business state.
"""
import asyncio
from typing import Set, Optional, List
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetStatus, AssetClass

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


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
        """
        if self._is_running:
            logger.debug("TickerValidator already running")
            return
        
        self._is_running = True
        
        # Initial load (async, non-blocking)
        asyncio.create_task(self._refresh_tradeable_tickers())
        
        # Start background refresh task (every hour)
        self._refresh_task = asyncio.create_task(self._periodic_refresh())
        
        logger.info("TickerValidator started - initial load in progress, hourly refresh scheduled")
    
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
            assets = self.trading_client.get_all_assets(filter=filter_request)
            
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
        
        # If cache is empty (still loading), return False to block classification
        # This prevents wasting Groq API calls until cache is ready
        if not self._tradeable_tickers:
            logger.warning(
                "TickerValidator: Cache is empty (still loading), blocking classification",
                tickers=tickers,
                cache_size=len(self._tradeable_tickers)
            )
            return False
        
        tickers_upper = {t.upper() for t in tickers}
        return bool(tickers_upper & self._tradeable_tickers)  # Set intersection
    
    def get_validation_reason(self, ticker: str) -> Optional[str]:
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
            asset = self.trading_client.get_asset(ticker)
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
