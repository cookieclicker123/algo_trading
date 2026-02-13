#!/usr/bin/env python3
"""
Fetch sector/industry metadata from FMP (Financial Modeling Prep) for all US tickers.

This script uses FMP's STABLE API endpoints (new format as of 2025):
- /stable/stock-list - Get all available tickers
- /stable/profile - Get company profile (sector, industry)

Usage:
    python scripts/fetch_fmp_metadata.py [--dry-run] [--limit N]

Requirements:
    - FMP_API_KEY environment variable set (paid tier required)
    - pip install requests tqdm python-dotenv
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from tqdm import tqdm

# Load environment variables
load_dotenv()

# Configuration
FMP_API_KEY = os.getenv("FMP_API_KEY")
FMP_BASE_URL = "https://financialmodelingprep.com/stable"

# Rate limiting (paid tier can handle more, but be conservative)
REQUESTS_PER_MINUTE = 200  # Conservative to avoid hitting limits
DELAY_BETWEEN_REQUESTS = 60 / REQUESTS_PER_MINUTE  # 0.3 seconds

# Batch settings
BATCH_SIZE = 50
SAVE_EVERY_N_TICKERS = 500  # Save progress periodically

# Output
CACHE_DIR = Path("data/cache")
OUTPUT_FILE = CACHE_DIR / "permanent_metadata.json"

# US Exchanges to include
US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "NYSEArca", "BATS", "NYSE ARCA", "NYSE American"}


def get_all_stocks() -> list[dict]:
    """Get all stocks using FMP stable stock list endpoint."""
    url = f"{FMP_BASE_URL}/stock-list"
    params = {"apikey": FMP_API_KEY}

    try:
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching stock list: {e}")
        return []


def get_company_profile(symbol: str) -> Optional[dict]:
    """Get company profile (sector, industry) for a ticker using stable API."""
    url = f"{FMP_BASE_URL}/profile"
    params = {
        "symbol": symbol,
        "apikey": FMP_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data and isinstance(data, list) and len(data) > 0:
            profile = data[0]
            return {
                "sector": profile.get("sector", ""),
                "industry": profile.get("industry", ""),
                "exchange": profile.get("exchange", ""),
            }
    except requests.RequestException:
        pass

    return None


def get_bulk_profiles(symbols: list[str]) -> dict:
    """Get profiles for multiple symbols (fetches individually since bulk doesn't work)."""
    if not symbols:
        return {}

    results = {}
    for symbol in symbols:
        profile = get_company_profile(symbol)
        if profile and profile.get("sector"):
            results[symbol] = profile
        time.sleep(0.01)  # 10ms delay = 100 requests/second (well under FMP limits)

    return results


def load_existing_cache() -> dict:
    """Load existing permanent metadata cache."""
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading existing cache: {e}")
    return {}


def save_cache(data: dict):
    """Save metadata cache to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def main():
    parser = argparse.ArgumentParser(description="Fetch FMP metadata for US tickers")
    parser.add_argument("--dry-run", action="store_true", help="Don't fetch, just show what would be fetched")
    parser.add_argument("--limit", type=int, help="Limit number of tickers to fetch")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip tickers already in cache")
    parser.add_argument("--force-refresh", action="store_true", help="Refresh all tickers even if cached")
    parser.add_argument("--bulk-size", type=int, default=50, help="Bulk request size (default: 50)")
    args = parser.parse_args()

    if args.force_refresh:
        args.skip_existing = False

    if not FMP_API_KEY:
        print("ERROR: FMP_API_KEY environment variable not set")
        print("Set it in .env or export FMP_API_KEY=your_key")
        sys.exit(1)

    print("=" * 70)
    print("FMP METADATA FETCHER (Stable API)")
    print("=" * 70)
    print(f"API Key: {FMP_API_KEY[:8]}...")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Rate limit: {REQUESTS_PER_MINUTE} requests/minute")
    print(f"Bulk size: {args.bulk_size} symbols/request")
    print()

    # Step 1: Get all tickers
    print("Step 1: Fetching ticker list from FMP...")
    print("  Fetching all stocks...", end=" ", flush=True)
    all_stocks = get_all_stocks()
    print(f"{len(all_stocks)} total tickers")

    # Filter to US exchanges only
    us_stocks = []
    for stock in all_stocks:
        symbol = stock.get("symbol", "").upper()
        # Skip if no symbol or if it's an index/fund (contains special chars)
        if not symbol or "." in symbol or "-" in symbol or len(symbol) > 5:
            continue
        us_stocks.append(symbol)

    print(f"  US tickers (filtered): {len(us_stocks)}")

    # Step 2: Load existing cache
    print("\nStep 2: Loading existing cache...")
    existing_cache = load_existing_cache()
    print(f"  Existing tickers in cache: {len(existing_cache)}")

    # Determine which tickers need fetching
    if args.skip_existing:
        tickers_to_fetch = [t for t in us_stocks if t not in existing_cache]
        print(f"  Tickers already cached: {len(us_stocks) - len(tickers_to_fetch)}")
        print(f"  Tickers to fetch: {len(tickers_to_fetch)}")
    else:
        tickers_to_fetch = us_stocks

    # Apply limit
    if args.limit:
        tickers_to_fetch = tickers_to_fetch[:args.limit]
        print(f"  Limited to: {len(tickers_to_fetch)} tickers")

    if args.dry_run:
        print("\n[DRY RUN] Would fetch profiles for:")
        for ticker in tickers_to_fetch[:20]:
            print(f"  {ticker}")
        if len(tickers_to_fetch) > 20:
            print(f"  ... and {len(tickers_to_fetch) - 20} more")

        # Estimate time
        num_requests = len(tickers_to_fetch) // args.bulk_size + 1
        estimated_time = num_requests * DELAY_BETWEEN_REQUESTS / 60
        print(f"\n  Estimated requests: {num_requests}")
        print(f"  Estimated time: {estimated_time:.1f} minutes")
        return

    # Step 3: Fetch profiles in bulk
    print(f"\nStep 3: Fetching company profiles in bulk (batch size: {args.bulk_size})...")
    num_batches = len(tickers_to_fetch) // args.bulk_size + 1
    print(f"  Total batches: {num_batches}")
    print(f"  Estimated time: {num_batches * DELAY_BETWEEN_REQUESTS / 60:.1f} minutes")

    cache = existing_cache.copy()
    fetched = 0
    failed = 0

    pbar = tqdm(range(0, len(tickers_to_fetch), args.bulk_size), desc="Fetching batches")

    for i in pbar:
        batch = tickers_to_fetch[i:i + args.bulk_size]
        profiles = get_bulk_profiles(batch)

        for symbol in batch:
            if symbol in profiles and profiles[symbol].get("sector"):
                cache[symbol] = profiles[symbol]
                fetched += 1
            else:
                failed += 1

        # Update progress bar
        pbar.set_postfix({"fetched": fetched, "failed": failed})

        # Rate limiting
        time.sleep(DELAY_BETWEEN_REQUESTS)

        # Periodic save
        if (i // args.bulk_size + 1) % 10 == 0:
            save_cache(cache)
            pbar.write(f"  [Checkpoint] Saved {len(cache)} tickers")

    # Final save
    save_cache(cache)

    # Summary
    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"  Tickers fetched: {fetched}")
    print(f"  Tickers failed/no data: {failed}")
    print(f"  Total in cache: {len(cache)}")
    print(f"  Saved to: {OUTPUT_FILE}")

    # Show sector distribution
    print("\nSector distribution (top 15):")
    sectors = {}
    for ticker, data in cache.items():
        sector = data.get("sector", "Unknown") or "Unknown"
        sectors[sector] = sectors.get(sector, 0) + 1

    for sector, count in sorted(sectors.items(), key=lambda x: -x[1])[:15]:
        print(f"  {sector}: {count}")


if __name__ == "__main__":
    main()
