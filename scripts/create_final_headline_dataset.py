#!/usr/bin/env python3
"""
Create final headline dataset with sector/industry from yfinance.

Takes the cleaned catalyst file and:
1. Extracts only rows with valid catalysts
2. Fetches sector/industry for all unique tickers
3. Outputs final training dataset

Usage:
    python scripts/create_final_headline_dataset.py
"""

import csv
import json
import time
from pathlib import Path

import yfinance as yf
from tqdm import tqdm

# Paths
INPUT_FILE = Path("tmp/alpaca_movers/10_plus_pct_with_catalysts_CLEAN.csv")
OUTPUT_FILE = Path("tmp/alpaca_movers/10_plus_pct_final_headlines.csv")
CACHE_DIR = Path("tmp/yahoo_finance_cache")

# Rate limiting
BATCH_SIZE = 50
PAUSE_SECS = 2


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
        with open(cache_file, "w") as f:
            json.dump(data, f, indent=2)
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
        save_to_cache(ticker, result)
        return result
    except Exception:
        return {"sector": "", "industry": ""}


def main():
    print("=" * 70)
    print("CREATE FINAL HEADLINE DATASET")
    print("=" * 70)

    # Load cleaned file and extract catalyst rows
    print("\nLoading cleaned catalyst file...")
    catalyst_rows = []
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row.get("catalyst_found") == "True":
                catalyst_rows.append(row)

    print(f"Catalyst rows: {len(catalyst_rows)}")

    # Get unique tickers
    unique_tickers = set(row["ticker"] for row in catalyst_rows)
    print(f"Unique tickers: {len(unique_tickers)}")

    # Load cache
    print("\nLoading yfinance cache...")
    cache = load_cache()
    print(f"Cached tickers: {len(cache)}")

    # Find uncached
    cached_set = set(cache.keys())
    uncached = unique_tickers - cached_set
    print(f"Need to fetch: {len(uncached)}")

    # Build sector/industry map
    sector_map = dict(cache)

    # Fetch uncached
    if uncached:
        print(f"\nFetching {len(uncached)} tickers from yfinance...")
        uncached_list = sorted(uncached)
        pbar = tqdm(total=len(uncached_list), desc="Fetching")

        for i in range(0, len(uncached_list), BATCH_SIZE):
            batch = uncached_list[i:i + BATCH_SIZE]
            for ticker in batch:
                result = fetch_from_yfinance(ticker)
                sector_map[ticker] = result
                pbar.update(1)

            if i + BATCH_SIZE < len(uncached_list):
                time.sleep(PAUSE_SECS)

        pbar.close()

    # Update rows with sector/industry
    print("\nUpdating rows with sector/industry...")
    for row in catalyst_rows:
        ticker = row["ticker"]
        if ticker in sector_map:
            row["sector"] = sector_map[ticker].get("sector", "")
            row["industry"] = sector_map[ticker].get("industry", "")

    # Define output columns
    output_columns = [
        "ticker",
        "date",
        "sector",
        "industry",
        "max_excursion_pct",
        "move_start_time",
        "catalyst_headline",
        "catalyst_time",
        "catalyst_source",
        "catalyst_type",
    ]

    # Write output file
    print(f"\nWriting output to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(catalyst_rows)

    # Summary stats
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    # Count by sector
    sector_counts = {}
    industry_counts = {}
    for row in catalyst_rows:
        sector = row.get("sector") or "Unknown"
        industry = row.get("industry") or "Unknown"
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        industry_counts[industry] = industry_counts.get(industry, 0) + 1

    print(f"\nTotal samples: {len(catalyst_rows)}")
    print(f"With sector: {sum(1 for r in catalyst_rows if r.get('sector'))}")
    print(f"Without sector: {sum(1 for r in catalyst_rows if not r.get('sector'))}")

    print("\n--- SAMPLES BY SECTOR ---")
    for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
        bar = "█" * (count // 50)
        print(f"  {sector:30} {count:5} {bar}")

    print("\n--- TOP 20 INDUSTRIES ---")
    for industry, count in sorted(industry_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {industry:45} {count:5}")

    print(f"\nOutput file: {OUTPUT_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()
