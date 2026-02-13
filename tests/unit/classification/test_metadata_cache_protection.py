"""
Test that permanent metadata (sector/industry from FMP) cannot be overwritten.

Background: On 2026-02-12, JTAI was incorrectly classified because
Yahoo Finance data overwrote the correct FMP data in the cache.

This test verifies that:
1. set_permanent() does NOT overwrite existing sector/industry values
2. Only new/empty fields get populated
3. FMP data is protected from Yahoo Finance updates
"""
import asyncio
import pytest
from pathlib import Path

from src.newsflash.infra.cache.metadata_cache import MetadataCache


class TestMetadataCacheProtection:
    """Verify that permanent metadata cannot be overwritten."""

    def _make_cache(self, tmp_path):
        """Create a temporary metadata cache."""
        return MetadataCache(
            cache_dir=str(tmp_path),
            permanent_file="test_permanent.json",
            daily_file="test_daily.json"
        )

    def test_set_permanent_does_not_overwrite_existing_sector(self, tmp_path):
        """Setting sector should not overwrite existing sector."""
        cache = self._make_cache(tmp_path)

        async def run_test():
            # Simulate FMP data (correct)
            await cache.set_permanent("JTAI", {
                "sector": "Technology",
                "industry": "Software - Application",
                "exchange": "NASDAQ"
            })

            # Simulate Yahoo Finance trying to overwrite with wrong data
            await cache.set_permanent("JTAI", {
                "sector": "Industrials",  # WRONG - should be ignored
                "industry": "Airlines",   # WRONG - should be ignored
                "exchange": "NYSE"        # WRONG - should be ignored
            })

            # Verify FMP data is preserved
            metadata = await cache.get_permanent("JTAI")
            return metadata

        metadata = asyncio.run(run_test())

        assert metadata["sector"] == "Technology", (
            "Yahoo Finance overwrote FMP sector data!"
        )
        assert metadata["industry"] == "Software - Application", (
            "Yahoo Finance overwrote FMP industry data!"
        )
        assert metadata["exchange"] == "NASDAQ", (
            "Yahoo Finance overwrote FMP exchange data!"
        )

    def test_set_permanent_allows_new_ticker(self, tmp_path):
        """Setting permanent data for new ticker should work."""
        cache = self._make_cache(tmp_path)

        async def run_test():
            # New ticker not in cache
            await cache.set_permanent("NEWT", {
                "sector": "Healthcare",
                "industry": "Biotechnology",
                "exchange": "NASDAQ"
            })
            return await cache.get_permanent("NEWT")

        metadata = asyncio.run(run_test())

        assert metadata is not None, "New ticker should be cached"
        assert metadata["sector"] == "Healthcare"
        assert metadata["industry"] == "Biotechnology"
        assert metadata["exchange"] == "NASDAQ"

    def test_set_permanent_allows_filling_empty_fields(self, tmp_path):
        """Setting permanent data should fill in missing fields only."""
        cache = self._make_cache(tmp_path)

        async def run_test():
            # Initial data with only sector
            await cache.set_permanent("PART", {
                "sector": "Technology"
            })

            # Later call adds industry and exchange
            await cache.set_permanent("PART", {
                "sector": "Industrials",  # Should be ignored (already set)
                "industry": "Software - Application",  # Should be added (was empty)
                "exchange": "NASDAQ"  # Should be added (was empty)
            })

            return await cache.get_permanent("PART")

        metadata = asyncio.run(run_test())

        assert metadata["sector"] == "Technology", (
            "Existing sector should not be overwritten"
        )
        assert metadata["industry"] == "Software - Application", (
            "Empty industry should be filled"
        )
        assert metadata["exchange"] == "NASDAQ", (
            "Empty exchange should be filled"
        )

    def test_yahoo_finance_simulation(self, tmp_path):
        """
        Simulate the exact scenario that caused the Feb 12 bug.

        1. FMP populates cache with correct JTAI data
        2. Yahoo Finance tries to update with wrong data
        3. Verify FMP data is preserved
        """
        cache = self._make_cache(tmp_path)

        async def run_test():
            # Step 1: FMP populates cache (via script)
            await cache.set_permanent("JTAI", {
                "sector": "Technology",
                "industry": "Software - Application",
                "exchange": "NASDAQ"
            })

            # Step 2: Yahoo Finance coordinator calls set_from_full_metadata
            # which internally calls set_permanent
            await cache.set_from_full_metadata("JTAI", {
                "sector": "Industrials",
                "industry": "Airlines, Airports & Air Services",
                "exchange": "NYSE",
                "market_cap_millions": 6.82  # This should go to daily cache
            })

            permanent = await cache.get_permanent("JTAI")
            daily = await cache.get_daily("JTAI")
            return permanent, daily

        permanent, daily = asyncio.run(run_test())

        # Permanent data should be FMP (unchanged)
        assert permanent["sector"] == "Technology", "FMP sector overwritten!"
        assert permanent["industry"] == "Software - Application", "FMP industry overwritten!"

        # Daily data should have market cap (from Yahoo)
        assert daily["market_cap_millions"] == 6.82, "Daily market cap should be updated"


class TestFMPScriptExists:
    """Verify FMP infrastructure is in place."""

    def test_fmp_script_exists(self):
        """The FMP fetch script should exist."""
        script_path = Path("scripts/fetch_fmp_metadata.py")
        assert script_path.exists(), "FMP script not found - needed for cache population"

    def test_permanent_cache_file_exists(self):
        """The permanent cache file should exist and have data."""
        import json

        cache_path = Path("data/cache/permanent_metadata.json")
        assert cache_path.exists(), "Permanent cache not found"

        with open(cache_path) as f:
            cache = json.load(f)

        assert len(cache) > 1000, (
            f"Cache only has {len(cache)} tickers - expected 1000+ from FMP"
        )
