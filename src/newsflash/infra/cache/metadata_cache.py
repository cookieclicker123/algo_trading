"""
Metadata cache - permanent and daily-refreshed ticker metadata.

Eliminates Yahoo Finance API latency for known tickers by caching:
- Permanent: sector, industry, exchange (never changes)
- Daily: market_cap_millions, float_shares (refreshed at 4am UK time)

Cache files stored in data/cache/ directory.

FMP (Financial Modeling Prep) is used as primary source for daily data (faster).
YFinance is used as fallback.
"""
import asyncio
import json
import os
from datetime import datetime, time, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Set, Tuple
from concurrent.futures import ThreadPoolExecutor

import requests
import yfinance as yf

from ...utils.logging_config import get_logger
from ...config.settings import FMP_API_KEY

logger = get_logger(__name__)

# UK timezone offset (GMT/BST)
UK_TIMEZONE_OFFSET = 0  # GMT (adjust to 1 for BST if needed)


class MetadataCache:
    """
    Two-tier metadata cache for instant ticker lookups.

    Tier 1 (Permanent): sector, industry, exchange
    - Never changes for a ticker
    - Persisted to JSON file
    - Seeded from historical data

    Tier 2 (Daily): market_cap_millions
    - Refreshed daily at 4am UK time
    - Persisted to separate JSON file
    - Fetched fresh if stale
    """

    def __init__(
        self,
        cache_dir: str = "data/cache",
        permanent_file: str = "permanent_metadata.json",
        daily_file: str = "daily_metadata.json"
    ):
        """
        Initialize metadata cache.

        Args:
            cache_dir: Directory for cache files
            permanent_file: Filename for permanent cache
            daily_file: Filename for daily cache
        """
        self.cache_dir = Path(cache_dir)
        self.permanent_path = self.cache_dir / permanent_file
        self.daily_path = self.cache_dir / daily_file

        # In-memory caches
        self._permanent: Dict[str, Dict[str, Any]] = {}
        self._daily: Dict[str, Dict[str, Any]] = {}
        self._daily_date: Optional[str] = None  # Date of daily cache

        # Locks for thread safety
        self._permanent_lock = asyncio.Lock()
        self._daily_lock = asyncio.Lock()

        # Thread pool for yfinance calls
        self._executor = ThreadPoolExecutor(max_workers=5)

        # Scheduler task
        self._scheduler_task: Optional[asyncio.Task] = None

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        logger.info("MetadataCache initialized", cache_dir=str(self.cache_dir))

    async def start(self) -> None:
        """Start the cache - load from disk and start scheduler."""
        await self._load_caches()
        self._scheduler_task = asyncio.create_task(self._daily_refresh_scheduler())
        logger.info(
            "MetadataCache started",
            permanent_tickers=len(self._permanent),
            daily_tickers=len(self._daily)
        )

    async def stop(self) -> None:
        """Stop the cache - save to disk and stop scheduler."""
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        await self._save_caches()
        self._executor.shutdown(wait=False)
        logger.info("MetadataCache stopped")

    # ==================== Public API ====================

    async def get(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get cached metadata for a ticker (instant, ~0ms).

        Returns combined permanent + daily data, or None if not cached.
        """
        permanent = await self.get_permanent(ticker)
        daily = await self.get_daily(ticker)

        if not permanent and not daily:
            return None

        # Combine both caches
        result = {}
        if permanent:
            result.update(permanent)
        if daily:
            result.update(daily)

        return result if result else None

    async def get_permanent(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get permanent metadata (sector, industry, exchange)."""
        async with self._permanent_lock:
            return self._permanent.get(ticker.upper())

    async def get_daily(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get daily metadata (market_cap_millions)."""
        async with self._daily_lock:
            return self._daily.get(ticker.upper())

    async def set_permanent(self, ticker: str, data: Dict[str, Any]) -> None:
        """
        Set permanent metadata for a ticker.

        Only stores: sector, industry, exchange

        IMPORTANT: Only sets fields if they don't already exist.
        This prevents Yahoo Finance from overwriting correct FMP data.
        """
        ticker = ticker.upper()
        permanent_data = {
            k: v for k, v in data.items()
            if k in ("sector", "industry", "exchange") and v is not None
        }

        if not permanent_data:
            return

        async with self._permanent_lock:
            existing = self._permanent.get(ticker, {})

            # Only set fields that don't already exist (preserve FMP data)
            fields_set = []
            for key, value in permanent_data.items():
                if not existing.get(key):
                    existing[key] = value
                    fields_set.append(key)

            if fields_set:
                self._permanent[ticker] = existing
                logger.debug("Cached permanent metadata", ticker=ticker, fields=fields_set)
            # else: all fields already exist, skip silently

    async def set_daily(self, ticker: str, data: Dict[str, Any]) -> None:
        """
        Set daily metadata for a ticker.

        Stores: market_cap_millions, float_shares
        """
        ticker = ticker.upper()
        daily_data = {
            k: v for k, v in data.items()
            if k in ("market_cap_millions", "float_shares") and v is not None
        }

        if not daily_data:
            return

        async with self._daily_lock:
            # Merge with existing data (don't overwrite fields not in new data)
            existing = self._daily.get(ticker, {})
            existing.update(daily_data)
            self._daily[ticker] = existing

    async def set_from_full_metadata(self, ticker: str, metadata: Dict[str, Any]) -> None:
        """
        Set both permanent and daily data from a full metadata dict.

        Call this when fetching fresh data to populate both caches.
        """
        await self.set_permanent(ticker, metadata)
        await self.set_daily(ticker, metadata)

    async def has_permanent(self, ticker: str) -> bool:
        """Check if ticker has permanent metadata cached."""
        async with self._permanent_lock:
            return ticker.upper() in self._permanent

    async def get_all_tickers(self) -> Set[str]:
        """Get all cached ticker symbols."""
        async with self._permanent_lock:
            permanent_tickers = set(self._permanent.keys())
        async with self._daily_lock:
            daily_tickers = set(self._daily.keys())
        return permanent_tickers | daily_tickers

    # ==================== Bulk Operations ====================

    async def seed_from_recall_data(self, recall_data_dir: str = "tmp/statistics/recall") -> int:
        """
        Seed permanent cache from historical recall data.

        Extracts ticker_metadata from all recall JSON files.
        Returns number of tickers added.
        """
        recall_path = Path(recall_data_dir)
        if not recall_path.exists():
            logger.warning("Recall data directory not found", path=str(recall_path))
            return 0

        tickers_added = 0
        json_files = list(recall_path.rglob("*.json"))

        logger.info("Seeding cache from recall data", files=len(json_files))

        for json_file in json_files:
            try:
                with open(json_file, "r") as f:
                    data = json.load(f)

                records = data.get("records", [])
                for record in records:
                    ticker_metadata = record.get("ticker_metadata", {})
                    for ticker, metadata in ticker_metadata.items():
                        if metadata and isinstance(metadata, dict):
                            # Check if we already have this ticker
                            if not await self.has_permanent(ticker):
                                await self.set_from_full_metadata(ticker, metadata)
                                tickers_added += 1
                            else:
                                # Update with any new fields
                                await self.set_from_full_metadata(ticker, metadata)

            except Exception as e:
                logger.debug("Error reading recall file", file=str(json_file), error=str(e))
                continue

        # Save after seeding
        await self._save_caches()

        logger.info(
            "Cache seeding complete",
            tickers_added=tickers_added,
            total_permanent=len(self._permanent),
            total_daily=len(self._daily)
        )

        return tickers_added

    async def refresh_daily_cache(self) -> int:
        """
        Refresh daily cache (market_cap, float_shares) for all known tickers.

        Called at 4am UK time daily.
        Uses FMP as primary source (faster), falls back to yfinance.
        Returns number of tickers refreshed.
        """
        tickers = await self.get_all_tickers()
        if not tickers:
            return 0

        logger.info("Refreshing daily cache", tickers=len(tickers), use_fmp=bool(FMP_API_KEY))

        refreshed = 0
        ticker_list = list(tickers)

        if FMP_API_KEY:
            # FMP supports batch requests - much faster
            batch_size = 100  # FMP allows multiple symbols per request
            for i in range(0, len(ticker_list), batch_size):
                batch = ticker_list[i:i + batch_size]
                results = await self._fetch_daily_data_fmp_batch(batch)

                for ticker, data in results.items():
                    if data:
                        await self.set_daily(ticker, data)
                        refreshed += 1

                # Small delay between batches
                await asyncio.sleep(0.3)
        else:
            # Fallback to yfinance (slower)
            batch_size = 50
            for i in range(0, len(ticker_list), batch_size):
                batch = ticker_list[i:i + batch_size]
                tasks = [self._fetch_daily_data_yfinance(t) for t in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for ticker, result in zip(batch, results):
                    if isinstance(result, Exception):
                        continue
                    if result:
                        await self.set_daily(ticker, result)
                        refreshed += 1

                await asyncio.sleep(0.5)

        # Update daily date and save
        self._daily_date = datetime.now().strftime("%Y-%m-%d")
        await self._save_caches()

        logger.info("Daily cache refresh complete", refreshed=refreshed, total=len(tickers))
        return refreshed

    async def _fetch_daily_data_fmp_batch(self, tickers: list) -> Dict[str, Dict[str, Any]]:
        """
        Fetch market_cap and float_shares for multiple tickers from FMP.

        FMP profile endpoint returns both marketCap and floatShares.
        Much faster than yfinance for bulk operations.
        """
        if not tickers or not FMP_API_KEY:
            return {}

        loop = asyncio.get_event_loop()

        def fetch_sync():
            FMP_PROFILE_URL = "https://financialmodelingprep.com/stable/profile"
            symbols_str = ",".join(tickers)

            try:
                params = {
                    "apikey": FMP_API_KEY,
                    "symbol": symbols_str,
                }
                response = requests.get(FMP_PROFILE_URL, params=params, timeout=60)

                if response.status_code != 200:
                    logger.warning("FMP API error", status=response.status_code)
                    return {}

                data = response.json()

                results = {}
                if isinstance(data, list):
                    for item in data:
                        symbol = item.get("symbol")
                        if symbol:
                            market_cap = item.get("mktCap") or item.get("marketCap")
                            float_shares = item.get("floatShares")

                            daily_data = {}
                            if market_cap is not None:
                                daily_data["market_cap_millions"] = market_cap / 1_000_000
                            if float_shares is not None:
                                daily_data["float_shares"] = int(float_shares)

                            if daily_data:
                                results[symbol.upper()] = daily_data

                return results

            except Exception as e:
                logger.warning("FMP batch fetch failed", error=str(e), tickers=len(tickers))
                return {}

        return await loop.run_in_executor(self._executor, fetch_sync)

    async def _fetch_daily_data_yfinance(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch market_cap and float_shares from yfinance (fallback)."""
        loop = asyncio.get_event_loop()
        try:
            def fetch_sync():
                try:
                    info = yf.Ticker(ticker).info
                    result = {}

                    market_cap = info.get("marketCap")
                    if market_cap is not None:
                        result["market_cap_millions"] = market_cap / 1_000_000

                    float_shares = info.get("floatShares")
                    if float_shares is not None:
                        result["float_shares"] = int(float_shares)

                    return result if result else None
                except Exception:
                    pass
                return None

            return await loop.run_in_executor(self._executor, fetch_sync)
        except Exception:
            return None

    async def get_float(self, ticker: str) -> Optional[int]:
        """
        Get cached float shares for a ticker.

        Returns None if not cached.
        """
        daily = await self.get_daily(ticker)
        if daily:
            return daily.get("float_shares")
        return None

    # ==================== Persistence ====================

    async def _load_caches(self) -> None:
        """Load caches from disk."""
        # Load permanent cache
        if self.permanent_path.exists():
            try:
                with open(self.permanent_path, "r") as f:
                    self._permanent = json.load(f)
                logger.info("Loaded permanent cache", tickers=len(self._permanent))
            except Exception as e:
                logger.error("Error loading permanent cache", error=str(e))
                self._permanent = {}

        # Load daily cache
        if self.daily_path.exists():
            try:
                with open(self.daily_path, "r") as f:
                    data = json.load(f)
                    self._daily = data.get("data", {})
                    self._daily_date = data.get("date")

                # Check if daily cache is stale (not from today)
                today = datetime.now().strftime("%Y-%m-%d")
                if self._daily_date != today:
                    logger.info(
                        "Daily cache is stale, will refresh",
                        cached_date=self._daily_date,
                        today=today
                    )
                else:
                    logger.info("Loaded daily cache", tickers=len(self._daily))
            except Exception as e:
                logger.error("Error loading daily cache", error=str(e))
                self._daily = {}

    async def _save_caches(self) -> None:
        """Save caches to disk."""
        # Save permanent cache
        try:
            async with self._permanent_lock:
                with open(self.permanent_path, "w") as f:
                    json.dump(self._permanent, f, indent=2)
            logger.debug("Saved permanent cache", tickers=len(self._permanent))
        except Exception as e:
            logger.error("Error saving permanent cache", error=str(e))

        # Save daily cache with date
        try:
            async with self._daily_lock:
                data = {
                    "date": self._daily_date or datetime.now().strftime("%Y-%m-%d"),
                    "data": self._daily
                }
                with open(self.daily_path, "w") as f:
                    json.dump(data, f, indent=2)
            logger.debug("Saved daily cache", tickers=len(self._daily))
        except Exception as e:
            logger.error("Error saving daily cache", error=str(e))

    # ==================== Scheduler ====================

    async def _daily_refresh_scheduler(self) -> None:
        """
        Schedule daily cache refresh at 4am UK time.

        4am UK = 11pm ET (after market close, before premarket)
        """
        while True:
            try:
                # Calculate time until next 4am UK
                now = datetime.now(timezone.utc)

                # 4am UK in UTC (GMT = UTC, so 4am UK = 4am UTC)
                target_hour = 4
                target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)

                # If we've passed 4am today, schedule for tomorrow
                if now >= target:
                    target += timedelta(days=1)

                seconds_until_refresh = (target - now).total_seconds()

                logger.info(
                    "Daily refresh scheduled",
                    target=target.isoformat(),
                    hours_until=round(seconds_until_refresh / 3600, 1)
                )

                await asyncio.sleep(seconds_until_refresh)

                # Perform refresh
                logger.info("Starting scheduled daily cache refresh")
                await self.refresh_daily_cache()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in daily refresh scheduler", error=str(e))
                await asyncio.sleep(3600)  # Wait an hour on error

    # ==================== Stats ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "permanent_tickers": len(self._permanent),
            "daily_tickers": len(self._daily),
            "daily_date": self._daily_date,
            "cache_dir": str(self.cache_dir)
        }
