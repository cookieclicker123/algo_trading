#!/usr/bin/env python3
"""
Fetch historical headlines from Alpaca (Benzinga) for collected movers.

For each mover in the CSVs, fetches news from the day before through
the move date to capture pre-market catalysts.

Usage:
    arch -arm64 .venv/bin/python scripts/fetch_headlines_for_movers.py

Output:
    tmp/alpaca_movers/5_to_10_pct_winners_with_headlines.csv
    tmp/alpaca_movers/10_plus_pct_winners_with_headlines.csv
"""

import csv
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

# Paths
INPUT_DIR = Path("tmp/alpaca_movers")
FILE_5_TO_10 = INPUT_DIR / "5_to_10_pct_winners.csv"
FILE_10_PLUS = INPUT_DIR / "10_plus_pct_winners.csv"

# Rate limiting - Algo Trader Plus = 10,000/min, but be conservative
BATCH_SIZE = 100
PAUSE_BETWEEN_BATCHES_SECS = 1

# How far back to look for news before the move
LOOKBACK_DAYS = 1


def load_movers(file_path: Path) -> list[dict]:
    """Load movers from CSV."""
    movers = []
    if not file_path.exists():
        return movers

    with open(file_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            movers.append(row)

    return movers


def fetch_headlines_for_ticker(
    client: NewsClient,
    ticker: str,
    move_date: datetime,
    move_start_time: str | None = None,
) -> list[dict]:
    """
    Fetch news headlines for a ticker around the move date.

    Returns list of headline dicts with: headline, summary, created_at, source
    """
    # Look from day before through move date
    start_date = move_date - timedelta(days=LOOKBACK_DAYS)
    end_date = move_date + timedelta(days=1)

    try:
        request = NewsRequest(
            symbols=ticker,
            start=start_date,
            end=end_date,
            limit=50,  # Max headlines per ticker/date
        )
        response = client.get_news(request)

        headlines = []
        for article in response.news:
            # Filter to articles before or around the move time
            article_time = article.created_at

            headlines.append({
                "headline": article.headline,
                "summary": getattr(article, "summary", "") or "",
                "created_at": article_time.isoformat() if article_time else "",
                "source": getattr(article, "source", "") or "",
                "url": getattr(article, "url", "") or "",
            })

        return headlines

    except Exception as e:
        # Rate limit or other error
        return []


def find_best_headline(headlines: list[dict], move_start_time: str | None) -> dict:
    """
    Find the most relevant headline - closest before the move time.

    Returns the best headline dict, or empty dict if none found.
    """
    if not headlines:
        return {}

    # If we have move start time, find headline closest before it
    if move_start_time:
        try:
            move_dt = datetime.fromisoformat(move_start_time.replace("Z", "+00:00"))

            # Filter to headlines before the move
            before_move = [
                h for h in headlines
                if h.get("created_at") and
                datetime.fromisoformat(h["created_at"].replace("Z", "+00:00")) <= move_dt
            ]

            if before_move:
                # Sort by time, get most recent before move
                before_move.sort(
                    key=lambda x: datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")),
                    reverse=True
                )
                return before_move[0]

        except (ValueError, TypeError):
            pass

    # Fallback: return first headline (usually most recent)
    return headlines[0] if headlines else {}


def process_movers_file(file_path: Path, output_path: Path):
    """Process a movers file and add headlines."""
    movers = load_movers(file_path)
    if not movers:
        print(f"No movers found in {file_path}")
        return

    print(f"\nProcessing {len(movers)} movers from {file_path.name}")

    # Initialize news client (no keys needed for news data)
    client = NewsClient()

    # Track unique ticker+date combos to avoid duplicate fetches
    fetched_cache: dict[str, list[dict]] = {}

    # Add headline columns to existing columns
    existing_columns = list(movers[0].keys())
    new_columns = existing_columns + [
        "catalyst_headline",
        "catalyst_summary",
        "catalyst_time",
        "catalyst_source",
        "catalyst_url",
        "headlines_found",
    ]

    results = []
    pbar = tqdm(total=len(movers), desc=f"Fetching headlines")

    for i, mover in enumerate(movers):
        ticker = mover["ticker"]
        date_str = mover["date"]
        move_start = mover.get("move_start_time", "")

        # Create cache key
        cache_key = f"{ticker}_{date_str}"

        # Check cache first
        if cache_key in fetched_cache:
            headlines = fetched_cache[cache_key]
        else:
            # Parse date
            try:
                move_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                headlines = []
            else:
                headlines = fetch_headlines_for_ticker(client, ticker, move_date, move_start)
                fetched_cache[cache_key] = headlines

        # Find best headline
        best = find_best_headline(headlines, move_start)

        # Add to result
        result = dict(mover)
        result["catalyst_headline"] = best.get("headline", "")
        result["catalyst_summary"] = best.get("summary", "")
        result["catalyst_time"] = best.get("created_at", "")
        result["catalyst_source"] = best.get("source", "")
        result["catalyst_url"] = best.get("url", "")
        result["headlines_found"] = len(headlines)
        results.append(result)

        pbar.update(1)

        # Rate limiting - pause every batch
        if (i + 1) % BATCH_SIZE == 0:
            time.sleep(PAUSE_BETWEEN_BATCHES_SECS)

    pbar.close()

    # Write output
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=new_columns)
        writer.writeheader()
        writer.writerows(results)

    # Stats
    with_headlines = sum(1 for r in results if r["catalyst_headline"])
    print(f"  Wrote {len(results)} rows to {output_path.name}")
    print(f"  Headlines found: {with_headlines}/{len(results)} ({with_headlines/len(results)*100:.1f}%)")


def main():
    print("=" * 60)
    print("FETCH HEADLINES FOR MOVERS (via Alpaca/Benzinga)")
    print("=" * 60)

    # Process both files
    if FILE_5_TO_10.exists():
        output_5_to_10 = INPUT_DIR / "5_to_10_pct_winners_with_headlines.csv"
        process_movers_file(FILE_5_TO_10, output_5_to_10)
    else:
        print(f"File not found: {FILE_5_TO_10}")

    if FILE_10_PLUS.exists():
        output_10_plus = INPUT_DIR / "10_plus_pct_winners_with_headlines.csv"
        process_movers_file(FILE_10_PLUS, output_10_plus)
    else:
        print(f"File not found: {FILE_10_PLUS}")

    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
