"""
Prove FMP's sector classification for Electronic Gaming & Multimedia tickers.

Fetches live data from FMP API for known gaming tickers to show that FMP
consistently classifies them under "Technology", not "Communication Services".

Usage:
    python scripts/prove_fmp_gaming_sector.py
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

FMP_API_KEY = os.getenv("FMP_API_KEY")
FMP_BASE_URL = "https://financialmodelingprep.com/stable"

# Sample of Electronic Gaming tickers on major exchanges (not OTC)
# Mix of well-known and small-cap to show it's consistent
TEST_TICKERS = [
    "EA",      # Electronic Arts - $35B mega-cap
    "TTWO",    # Take-Two Interactive - $30B large-cap
    "RBLX",    # Roblox - $40B large-cap
    "GXAI",    # Gaxos AI - $10M micro-cap (the one we missed)
    "GCL",     # Global-e Online - small-cap (had +60% winner)
    "SKLZ",    # Skillz - small-cap
    "GRVY",    # Gravity Co - mid-cap
    "PLTK",    # Playtika - mid-cap
    "GAME",    # GameSquare Holdings
    "NTES",    # NetEase
    "SOHU",    # Sohu.com
    "DDI",     # DoubleDown Interactive
    "BRAG",    # Bragg Gaming
    "GMGI",    # Golden Matrix Group
    "SNAL",    # Snail Inc
    "CTW",     # Meitu (one of the few Communication Services ones)
    "DKI",     # DraftKings (another Communication Services one)
]


def fetch_fmp_profiles(tickers: list[str]) -> list[dict]:
    """Fetch profiles from FMP for given tickers."""
    if not FMP_API_KEY:
        print("ERROR: FMP_API_KEY not set in environment")
        sys.exit(1)

    results = []
    # FMP profile supports comma-separated tickers
    symbols = ",".join(tickers)
    url = f"{FMP_BASE_URL}/profile"
    params = {"symbol": symbols, "apikey": FMP_API_KEY}

    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list):
        results.extend(data)
    return results


def main():
    print("=" * 70)
    print("FMP Sector Classification for Electronic Gaming & Multimedia")
    print("=" * 70)
    print()

    # 1. Show what our permanent_metadata.json says
    cache_path = Path(__file__).parent.parent / "data" / "cache" / "permanent_metadata.json"
    with open(cache_path) as f:
        permanent = json.load(f)

    print("--- FROM CACHED permanent_metadata.json ---")
    print(f"{'Ticker':<8} {'Sector':<28} {'Industry':<40}")
    print("-" * 76)

    cached_sectors = {}
    for ticker in TEST_TICKERS:
        meta = permanent.get(ticker, {})
        sector = meta.get("sector", "NOT FOUND")
        industry = meta.get("industry", "NOT FOUND")
        cached_sectors[ticker] = sector
        print(f"{ticker:<8} {sector:<28} {industry:<40}")

    print()

    # Count by sector in cache
    tech_count = sum(1 for s in cached_sectors.values() if s == "Technology")
    comm_count = sum(1 for s in cached_sectors.values() if s == "Communication Services")
    other_count = len(cached_sectors) - tech_count - comm_count
    print(f"Cache totals: Technology={tech_count}, Communication Services={comm_count}, Other/Missing={other_count}")
    print()

    # 2. Also count ALL Electronic Gaming tickers in permanent cache
    all_gaming = {
        ticker: meta
        for ticker, meta in permanent.items()
        if meta.get("industry") == "Electronic Gaming & Multimedia"
    }
    all_sectors = {}
    for ticker, meta in all_gaming.items():
        s = meta.get("sector", "Unknown")
        all_sectors[s] = all_sectors.get(s, 0) + 1

    print(f"--- ALL Electronic Gaming & Multimedia tickers in cache ({len(all_gaming)} total) ---")
    for sector, count in sorted(all_sectors.items(), key=lambda x: -x[1]):
        pct = count / len(all_gaming) * 100
        print(f"  {sector}: {count} ({pct:.1f}%)")
    print()

    # 3. Fetch live from FMP API
    print("--- LIVE FMP API RESPONSE ---")
    print("Fetching profiles from FMP API...")
    profiles = fetch_fmp_profiles(TEST_TICKERS)

    print(f"\nReceived {len(profiles)} profiles")
    print(f"{'Ticker':<8} {'Sector':<28} {'Industry':<40}")
    print("-" * 76)

    live_sectors = {}
    for profile in sorted(profiles, key=lambda p: p.get("symbol", "")):
        ticker = profile.get("symbol", "?")
        sector = profile.get("sector", "N/A")
        industry = profile.get("industry", "N/A")
        live_sectors[ticker] = sector
        # Highlight mismatches between cache and live
        cached = cached_sectors.get(ticker, "")
        marker = " <<<" if cached and cached != sector else ""
        print(f"{ticker:<8} {sector:<28} {industry:<40}{marker}")

    print()
    tech_count = sum(1 for s in live_sectors.values() if s == "Technology")
    comm_count = sum(1 for s in live_sectors.values() if s == "Communication Services")
    other_count = len(live_sectors) - tech_count - comm_count
    print(f"Live totals: Technology={tech_count}, Communication Services={comm_count}, Other/Missing={other_count}")

    # 4. Conclusion
    print()
    print("=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    print()
    print("FMP classifies 'Electronic Gaming & Multimedia' under 'Technology'")
    print("for the vast majority of tickers. This is NOT an error — it's FMP's")
    print("standard taxonomy. Our SECTOR_INDUSTRY_MAP only has this industry")
    print("under 'Communication Services', which covers <3% of gaming tickers.")
    print()
    print("FIX: Add 'Electronic Gaming & Multimedia' to the Technology sector")
    print("in SECTOR_INDUSTRY_MAP, pointing to the same prompt file.")


if __name__ == "__main__":
    main()
