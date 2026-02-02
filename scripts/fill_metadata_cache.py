#!/usr/bin/env python3
"""
One-time script to fill gaps in permanent_metadata.json using FMP API.

Uses FMP Stock Screener to get all tickers from NYSE, NASDAQ, AMEX
with their sector and industry, then merges with existing cache.

Usage:
    python scripts/fill_metadata_cache.py

Requires:
    FMP_API_KEY in .env file
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, Set, Any, Optional
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

# Configuration
FMP_API_KEY = os.getenv("FMP_API_KEY")
CACHE_PATH = Path("data/cache/permanent_metadata.json")
EXCHANGES = ["NYSE", "NASDAQ", "AMEX"]
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# FMP endpoints
FMP_SCREENER_URL = "https://financialmodelingprep.com/stable/company-screener"
FMP_PROFILE_URL = "https://financialmodelingprep.com/stable/profile"


def load_existing_cache() -> Dict[str, Dict[str, Any]]:
    """Load existing permanent metadata cache."""
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    return {}


def save_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    """Save cache to disk."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"Saved cache with {len(cache)} tickers to {CACHE_PATH}")


def fetch_exchange_tickers(exchange: str) -> list:
    """
    Fetch all tickers from an exchange using FMP Stock Screener.

    Returns list of dicts with symbol, sector, industry.
    """
    print(f"\nFetching tickers from {exchange}...")

    all_results = []
    offset = 0
    limit = 10000  # FMP's max limit per request

    while True:
        params = {
            "apikey": FMP_API_KEY,
            "exchange": exchange,
            "limit": limit,
            "offset": offset,
            "isActivelyTrading": "true",
        }

        try:
            response = requests.get(FMP_SCREENER_URL, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()

            if not data:
                break

            all_results.extend(data)
            print(f"  Fetched {len(data)} tickers (offset={offset}, total so far={len(all_results)})")

            # If we got fewer than limit, we've reached the end
            if len(data) < limit:
                break

            offset += limit
            time.sleep(1)  # Rate limit protection

        except requests.exceptions.RequestException as e:
            print(f"  Error fetching {exchange} at offset {offset}: {e}")
            break

    print(f"  Total from {exchange}: {len(all_results)} tickers")
    return all_results


def fetch_profile_batch(symbols: list) -> Dict[str, Dict[str, Any]]:
    """
    Fetch profiles for multiple symbols using FMP Profile endpoint.

    FMP allows comma-separated symbols in one request.
    """
    if not symbols:
        return {}

    # FMP profile endpoint accepts comma-separated symbols
    symbols_str = ",".join(symbols)

    params = {
        "apikey": FMP_API_KEY,
        "symbol": symbols_str,
    }

    try:
        response = requests.get(FMP_PROFILE_URL, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()

        # Convert list to dict keyed by symbol
        result = {}
        if isinstance(data, list):
            for item in data:
                symbol = item.get("symbol")
                if symbol:
                    result[symbol] = {
                        "sector": item.get("sector"),
                        "industry": item.get("industry"),
                    }

        return result

    except requests.exceptions.RequestException as e:
        print(f"  Error fetching profiles: {e}")
        return {}


def process_screener_results(results: list) -> Dict[str, Dict[str, Any]]:
    """
    Process screener results into cache format.

    Returns dict of {symbol: {sector, industry}}
    """
    processed = {}

    for item in results:
        symbol = item.get("symbol")
        if not symbol:
            continue

        sector = item.get("sector")
        industry = item.get("industry")

        # Only add if we have at least sector or industry
        if sector or industry:
            processed[symbol.upper()] = {}
            if sector:
                processed[symbol.upper()]["sector"] = sector
            if industry:
                processed[symbol.upper()]["industry"] = industry

    return processed


def fill_missing_with_profile(
    missing_tickers: Set[str],
    existing_cache: Dict[str, Dict[str, Any]],
    max_retries: int = MAX_RETRIES
) -> Dict[str, Dict[str, Any]]:
    """
    Try to fill missing tickers using FMP Profile endpoint.

    Batches requests and retries failures.
    """
    if not missing_tickers:
        return {}

    print(f"\nAttempting to fill {len(missing_tickers)} tickers via Profile API...")

    filled = {}
    failed = set(missing_tickers)
    batch_size = 100  # FMP allows multiple symbols per request

    for attempt in range(max_retries):
        if not failed:
            break

        print(f"  Attempt {attempt + 1}/{max_retries} for {len(failed)} tickers...")

        still_failed = set()
        ticker_list = list(failed)

        for i in range(0, len(ticker_list), batch_size):
            batch = ticker_list[i:i + batch_size]
            results = fetch_profile_batch(batch)

            for symbol in batch:
                if symbol in results and results[symbol].get("sector"):
                    filled[symbol] = results[symbol]
                else:
                    still_failed.add(symbol)

            time.sleep(0.5)  # Rate limit protection

        failed = still_failed

        if failed and attempt < max_retries - 1:
            print(f"  {len(failed)} tickers still missing, retrying in {RETRY_DELAY_SECONDS}s...")
            time.sleep(RETRY_DELAY_SECONDS)

    if failed:
        print(f"  Could not fetch {len(failed)} tickers after {max_retries} attempts:")
        # Log a sample of failed tickers
        sample = list(failed)[:20]
        print(f"    Sample: {sample}")

    return filled


def main():
    if not FMP_API_KEY:
        print("ERROR: FMP_API_KEY not found in environment")
        print("Please add FMP_API_KEY=your_key to .env file")
        return

    print("=" * 60)
    print("METADATA CACHE FILLER")
    print("=" * 60)

    # Load existing cache
    existing_cache = load_existing_cache()
    print(f"Existing cache has {len(existing_cache)} tickers")
    existing_tickers = set(existing_cache.keys())

    # Fetch from all exchanges
    all_new_tickers = {}

    for exchange in EXCHANGES:
        results = fetch_exchange_tickers(exchange)
        processed = process_screener_results(results)

        # Count how many are new
        new_count = sum(1 for t in processed if t not in existing_tickers)
        print(f"  {exchange}: {len(processed)} total, {new_count} new")

        # Merge (new tickers only, don't overwrite existing)
        for symbol, data in processed.items():
            if symbol not in existing_tickers:
                all_new_tickers[symbol] = data

    print(f"\nTotal new tickers from screener: {len(all_new_tickers)}")

    # Find tickers that still don't have sector/industry
    missing_sector = {
        t for t, data in all_new_tickers.items()
        if not data.get("sector")
    }

    if missing_sector:
        print(f"\n{len(missing_sector)} tickers missing sector, trying Profile API...")
        profile_results = fill_missing_with_profile(missing_sector, existing_cache)

        # Update with profile results
        for symbol, data in profile_results.items():
            if symbol in all_new_tickers:
                all_new_tickers[symbol].update(data)
            else:
                all_new_tickers[symbol] = data

    # Merge with existing cache
    updated_cache = {**existing_cache}
    added_count = 0

    for symbol, data in all_new_tickers.items():
        if symbol not in updated_cache:
            updated_cache[symbol] = data
            added_count += 1
        else:
            # Update existing with any new fields
            for key, value in data.items():
                if value and key not in updated_cache[symbol]:
                    updated_cache[symbol][key] = value

    # Save updated cache
    save_cache(updated_cache)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Previous cache size: {len(existing_cache)}")
    print(f"New tickers added:   {added_count}")
    print(f"Final cache size:    {len(updated_cache)}")

    # Stats by sector
    sector_counts = defaultdict(int)
    for data in updated_cache.values():
        sector = data.get("sector", "Unknown")
        sector_counts[sector] += 1

    print("\nTickers by sector:")
    for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
        print(f"  {sector}: {count}")


if __name__ == "__main__":
    main()
