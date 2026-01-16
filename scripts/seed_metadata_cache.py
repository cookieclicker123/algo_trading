#!/usr/bin/env python3
"""
Seed the metadata cache from historical recall data.

Standalone script - no external dependencies except json/pathlib.
Extracts ticker metadata from all recall JSON files.

Usage:
    python scripts/seed_metadata_cache.py
"""
import json
from pathlib import Path
from typing import Dict, Any


def seed_cache_from_recall_data(
    recall_dir: str = "tmp/statistics/recall",
    cache_dir: str = "data/cache"
) -> tuple[int, Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Extract ticker metadata from recall data and save to cache files.

    Returns:
        (tickers_found, permanent_cache, daily_cache)
    """
    recall_path = Path(recall_dir)
    cache_path = Path(cache_dir)

    if not recall_path.exists():
        print(f"Error: Recall directory not found: {recall_path}")
        return 0, {}, {}

    # Create cache directory
    cache_path.mkdir(parents=True, exist_ok=True)

    # Collect metadata from all recall files
    permanent_cache: Dict[str, Dict[str, Any]] = {}  # sector, industry, exchange
    daily_cache: Dict[str, Dict[str, Any]] = {}  # market_cap_millions

    json_files = list(recall_path.rglob("*.json"))
    print(f"Found {len(json_files)} JSON files to process...")

    for json_file in json_files:
        try:
            with open(json_file, "r") as f:
                data = json.load(f)

            records = data.get("records", [])
            for record in records:
                ticker_metadata = record.get("ticker_metadata", {})
                for ticker, metadata in ticker_metadata.items():
                    if not metadata or not isinstance(metadata, dict):
                        continue

                    ticker = ticker.upper()

                    # Extract permanent fields
                    permanent_fields = {}
                    if metadata.get("sector"):
                        permanent_fields["sector"] = metadata["sector"]
                    if metadata.get("industry"):
                        permanent_fields["industry"] = metadata["industry"]
                    if metadata.get("exchange"):
                        permanent_fields["exchange"] = metadata["exchange"]

                    if permanent_fields:
                        if ticker not in permanent_cache:
                            permanent_cache[ticker] = permanent_fields
                        else:
                            # Update with any new fields
                            permanent_cache[ticker].update(permanent_fields)

                    # Extract daily fields
                    if metadata.get("market_cap_millions") is not None:
                        daily_cache[ticker] = {
                            "market_cap_millions": metadata["market_cap_millions"]
                        }

        except Exception as e:
            # Skip files that can't be parsed
            continue

    return len(permanent_cache), permanent_cache, daily_cache


def main():
    print("=" * 60)
    print("Metadata Cache Seeding")
    print("=" * 60)

    # Load existing caches if any
    cache_dir = Path("data/cache")
    permanent_path = cache_dir / "permanent_metadata.json"
    daily_path = cache_dir / "daily_metadata.json"

    existing_permanent = {}
    existing_daily = {}

    if permanent_path.exists():
        with open(permanent_path, "r") as f:
            existing_permanent = json.load(f)
        print(f"Existing permanent cache: {len(existing_permanent)} tickers")

    if daily_path.exists():
        with open(daily_path, "r") as f:
            data = json.load(f)
            existing_daily = data.get("data", {})
        print(f"Existing daily cache: {len(existing_daily)} tickers")

    # Extract metadata from recall data
    print("\nExtracting metadata from recall data...")
    count, permanent, daily = seed_cache_from_recall_data()

    # Merge with existing
    for ticker, data in permanent.items():
        if ticker not in existing_permanent:
            existing_permanent[ticker] = data
        else:
            existing_permanent[ticker].update(data)

    for ticker, data in daily.items():
        existing_daily[ticker] = data

    # Save caches
    cache_dir.mkdir(parents=True, exist_ok=True)

    with open(permanent_path, "w") as f:
        json.dump(existing_permanent, f, indent=2)

    from datetime import datetime
    daily_data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "data": existing_daily
    }
    with open(daily_path, "w") as f:
        json.dump(daily_data, f, indent=2)

    print(f"\nSeeding complete!")
    print(f"  Unique tickers from recall data: {count}")
    print(f"  Total permanent cache: {len(existing_permanent)} tickers")
    print(f"  Total daily cache: {len(existing_daily)} tickers")

    # Show sample data
    print(f"\nSample cached data:")
    for ticker, data in list(existing_permanent.items())[:10]:
        sector = data.get("sector", "N/A")
        industry = data.get("industry", "N/A")
        exchange = data.get("exchange", "N/A")
        print(f"  {ticker}: sector={sector}, industry={industry}, exchange={exchange}")

    # Show sectors distribution
    sectors = {}
    for ticker, data in existing_permanent.items():
        sector = data.get("sector", "Unknown")
        sectors[sector] = sectors.get(sector, 0) + 1

    print(f"\nSector distribution:")
    for sector, count in sorted(sectors.items(), key=lambda x: -x[1])[:10]:
        print(f"  {sector}: {count} tickers")

    print(f"\nCache saved to: {cache_dir}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
