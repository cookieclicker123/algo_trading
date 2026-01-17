#!/usr/bin/env python3
"""
Fill sector and industry fields in mover CSVs using yfinance.

Uses the existing YahooFinanceCoordinator cache where available.
For uncached tickers, fetches from yfinance with rate limiting.

Usage:
    python scripts/fill_sector_industry.py

Requirements:
    - pip install yfinance
"""

import csv
import json
import time
from pathlib import Path

import yfinance as yf
from tqdm import tqdm

# Paths
INPUT_DIR = Path("tmp/alpaca_movers")
CACHE_DIR = Path("tmp/yahoo_finance_cache")

# Files
FILE_5_TO_10 = INPUT_DIR / "5_to_10_pct_winners.csv"
FILE_10_PLUS = INPUT_DIR / "10_plus_pct_winners.csv"

# Rate limiting for yfinance
BATCH_SIZE = 50
PAUSE_BETWEEN_BATCHES_SECS = 2


def load_cache() -> dict[str, dict]:
    """Load existing yfinance cache."""
    cache = {}

    if not CACHE_DIR.exists():
        return cache

    for cache_file in CACHE_DIR.glob("*.json"):
        try:
            with open(cache_file) as f:
                data = json.load(f)
                ticker = cache_file.stem.upper()
                if data.get("sector") or data.get("industry"):
                    cache[ticker] = {
                        "sector": data.get("sector", ""),
                        "industry": data.get("industry", ""),
                    }
        except Exception:
            pass

    return cache


def save_to_cache(ticker: str, data: dict):
    """Save ticker data to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{ticker.upper()}.json"

    try:
        # Load existing data if any
        existing = {}
        if cache_file.exists():
            with open(cache_file) as f:
                existing = json.load(f)

        # Merge
        existing.update(data)

        with open(cache_file, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass


def fetch_from_yfinance(ticker: str) -> dict:
    """Fetch sector/industry from yfinance."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        result = {
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
        }

        # Cache the result
        save_to_cache(ticker, result)

        return result
    except Exception:
        return {"sector": "", "industry": ""}


def get_unique_tickers(file_path: Path) -> set[str]:
    """Get unique tickers from CSV."""
    tickers = set()

    if not file_path.exists():
        return tickers

    with open(file_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tickers.add(row["ticker"])

    return tickers


def update_csv(file_path: Path, sector_industry_map: dict[str, dict]):
    """Update CSV with sector/industry data."""
    if not file_path.exists():
        return 0

    # Read all rows
    rows = []
    with open(file_path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            ticker = row["ticker"]
            if ticker in sector_industry_map:
                row["sector"] = sector_industry_map[ticker].get("sector", "")
                row["industry"] = sector_industry_map[ticker].get("industry", "")
            rows.append(row)

    # Write back
    with open(file_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def main():
    print("=" * 60)
    print("FILL SECTOR/INDUSTRY FROM YFINANCE")
    print("=" * 60)

    # Get unique tickers from both files
    tickers_5_to_10 = get_unique_tickers(FILE_5_TO_10)
    tickers_10_plus = get_unique_tickers(FILE_10_PLUS)
    all_tickers = tickers_5_to_10 | tickers_10_plus

    print(f"\nUnique tickers in 5-10% file: {len(tickers_5_to_10)}")
    print(f"Unique tickers in 10%+ file: {len(tickers_10_plus)}")
    print(f"Total unique tickers: {len(all_tickers)}")

    # Load cache
    print("\nLoading yfinance cache...")
    cache = load_cache()
    print(f"Cached tickers: {len(cache)}")

    # Find uncached tickers
    cached_tickers = set(cache.keys())
    uncached_tickers = all_tickers - cached_tickers

    print(f"Already cached: {len(all_tickers & cached_tickers)}")
    print(f"Need to fetch: {len(uncached_tickers)}")

    # Build sector/industry map from cache first
    sector_industry_map = {}
    for ticker in all_tickers:
        if ticker in cache:
            sector_industry_map[ticker] = cache[ticker]

    # Fetch uncached tickers
    if uncached_tickers:
        print(f"\nFetching {len(uncached_tickers)} tickers from yfinance...")
        uncached_list = sorted(uncached_tickers)

        pbar = tqdm(total=len(uncached_list), desc="Fetching")

        for i in range(0, len(uncached_list), BATCH_SIZE):
            batch = uncached_list[i:i + BATCH_SIZE]

            for ticker in batch:
                result = fetch_from_yfinance(ticker)
                sector_industry_map[ticker] = result
                pbar.update(1)

            # Pause between batches
            if i + BATCH_SIZE < len(uncached_list):
                time.sleep(PAUSE_BETWEEN_BATCHES_SECS)

        pbar.close()

    # Update CSVs
    print("\nUpdating CSV files...")

    count_5_to_10 = update_csv(FILE_5_TO_10, sector_industry_map)
    print(f"  Updated {FILE_5_TO_10.name}: {count_5_to_10} rows")

    count_10_plus = update_csv(FILE_10_PLUS, sector_industry_map)
    print(f"  Updated {FILE_10_PLUS.name}: {count_10_plus} rows")

    # Summary
    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)

    # Count by sector
    sectors = {}
    for ticker, data in sector_industry_map.items():
        sector = data.get("sector", "Unknown") or "Unknown"
        sectors[sector] = sectors.get(sector, 0) + 1

    print("\nTickers by sector:")
    for sector, count in sorted(sectors.items(), key=lambda x: -x[1]):
        print(f"  {sector}: {count}")


if __name__ == "__main__":
    main()
