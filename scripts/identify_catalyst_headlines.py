#!/usr/bin/env python3
"""
Identify catalyst headlines for price movers using Alpaca News + Groq AI.

This script:
1. Fetches news from Alpaca within ±30 min of each move
2. Uses Groq (Llama) to identify the ORIGINAL catalyst (press release)
   vs aggregator headlines that just report on the surge
3. Outputs enriched CSV with catalyst details

Key insight: Real catalysts are press releases that DON'T mention stock movement.
- CATALYST: "XYZ Announces FDA Approval of Drug ABC"
- NOT CATALYST: "XYZ Shares Surge 200% After FDA Approval" (this reports the effect)

Usage:
    arch -arm64 .venv/bin/python scripts/identify_catalyst_headlines.py

    # Test mode with sample tickers:
    arch -arm64 .venv/bin/python scripts/identify_catalyst_headlines.py --test

Output:
    tmp/alpaca_movers/10_plus_pct_with_catalysts.csv
    tmp/alpaca_movers/5_to_10_pct_with_catalysts.csv
"""

import argparse
import asyncio
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
from dotenv import load_dotenv
from groq import AsyncGroq
from tqdm import tqdm

load_dotenv()

# Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"

# Paths
INPUT_DIR = Path("tmp/alpaca_movers")
FILE_5_TO_10 = INPUT_DIR / "5_to_10_pct_winners.csv"
FILE_10_PLUS = INPUT_DIR / "10_plus_pct_winners.csv"

# News window around move
WINDOW_MINUTES_BEFORE = 30
WINDOW_MINUTES_AFTER = 30

# Rate limiting
GROQ_BATCH_SIZE = 5  # Concurrent Groq calls
GROQ_PAUSE_SECS = 1  # Pause between batches
ALPACA_BATCH_SIZE = 20  # News fetches before pause
ALPACA_PAUSE_SECS = 0.5

# Test data - top 10 movers provided by user
TEST_MOVERS = [
    {"ticker": "TNON", "date": "2024-09-13", "move_start_time": "2024-09-13T08:04:00+00:00", "move_peak_time": "2024-09-13T08:12:00+00:00", "max_excursion_pct": 2504.12},
    {"ticker": "BKKT", "date": "2024-04-29", "move_start_time": "2024-04-29T08:00:00+00:00", "move_peak_time": "2024-04-29T12:25:00+00:00", "max_excursion_pct": 1182.05},
    {"ticker": "UBXG", "date": "2024-08-23", "move_start_time": "2024-08-23T12:01:00+00:00", "move_peak_time": "2024-08-23T12:10:00+00:00", "max_excursion_pct": 570.94},
    {"ticker": "CCG", "date": "2024-02-14", "move_start_time": "2024-02-14T09:31:00+00:00", "move_peak_time": "2024-02-14T09:41:00+00:00", "max_excursion_pct": 495.24},
    {"ticker": "IBACR", "date": "2024-06-26", "move_start_time": "2024-06-26T16:27:00+00:00", "move_peak_time": "2024-06-26T16:27:00+00:00", "max_excursion_pct": 486.2},
    {"ticker": "MI", "date": "2024-04-12", "move_start_time": "2024-04-12T08:00:00+00:00", "move_peak_time": "2024-04-12T13:41:00+00:00", "max_excursion_pct": 480.0},
    {"ticker": "GXAI", "date": "2024-02-16", "move_start_time": "2024-02-16T13:30:00+00:00", "move_peak_time": "2024-02-16T13:39:00+00:00", "max_excursion_pct": 476.92},
    {"ticker": "RZLV", "date": "2024-09-19", "move_start_time": "2024-09-19T12:25:00+00:00", "move_peak_time": "2024-09-19T12:36:00+00:00", "max_excursion_pct": 474.53},
    {"ticker": "BRFH", "date": "2024-04-25", "move_start_time": "2024-04-25T08:14:00+00:00", "move_peak_time": "2024-04-25T11:20:00+00:00", "max_excursion_pct": 390.2},
    {"ticker": "CBIO", "date": "2024-05-06", "move_start_time": "2024-05-06T12:04:00+00:00", "move_peak_time": "2024-05-06T12:04:00+00:00", "max_excursion_pct": 385.11},
]


def parse_timestamp(ts_str: str) -> datetime | None:
    """Parse ISO timestamp string to datetime."""
    if not ts_str:
        return None
    try:
        # Handle various formats
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def fetch_news_for_mover(
    client: NewsClient,
    ticker: str,
    move_start_time: str,
) -> list[dict]:
    """
    Fetch news headlines within ±30 min window of move.

    Returns list of article dicts with: headline, summary, created_at, source, url
    """
    move_dt = parse_timestamp(move_start_time)
    if not move_dt:
        return []

    # Window: 30 min before to 30 min after move start
    start_time = move_dt - timedelta(minutes=WINDOW_MINUTES_BEFORE)
    end_time = move_dt + timedelta(minutes=WINDOW_MINUTES_AFTER)

    try:
        request = NewsRequest(
            symbols=ticker,
            start=start_time,
            end=end_time,
            limit=50,
        )
        response = client.get_news(request)

        articles = []
        for article in response.news:
            created_at = article.created_at
            if created_at:
                created_str = created_at.isoformat()
            else:
                created_str = ""

            articles.append({
                "headline": article.headline or "",
                "summary": getattr(article, "summary", "") or "",
                "created_at": created_str,
                "source": getattr(article, "source", "") or "",
                "url": getattr(article, "url", "") or "",
                "author": getattr(article, "author", "") or "",
            })

        return articles

    except Exception as e:
        print(f"  Error fetching news for {ticker}: {e}")
        return []


async def identify_catalyst_with_ai(
    groq_client: AsyncGroq,
    ticker: str,
    move_start_time: str,
    max_excursion_pct: float,
    articles: list[dict],
) -> dict:
    """
    Use Groq/Llama to identify which headline is the original catalyst.

    Returns dict with catalyst details or empty dict if none found.
    """
    if not articles:
        return {"catalyst_found": False, "reason": "no_articles_in_window"}

    # Format articles for AI
    articles_text = ""
    for i, art in enumerate(articles, 1):
        articles_text += f"""
Article {i}:
  Headline: {art['headline']}
  Summary: {art['summary'][:200] if art['summary'] else 'N/A'}
  Time: {art['created_at']}
  Source: {art['source']}
"""

    prompt = f"""You are analyzing news headlines to identify the ORIGINAL CATALYST that caused a stock price surge.

STOCK: {ticker}
MOVE START TIME: {move_start_time}
PRICE SURGE: +{max_excursion_pct:.1f}% within 10 minutes

ARTICLES IN WINDOW (±30 min of move):
{articles_text}

CRITICAL RULES FOR IDENTIFYING THE CATALYST:

1. The CATALYST is a press release or breaking news that contains SURPRISE information:
   - FDA approvals, drug trial results
   - Contract wins, partnership announcements
   - Acquisition offers, merger news
   - Earnings surprises, guidance raises
   - New product launches, patent grants

2. The catalyst DOES NOT mention the stock is moving/surging/soaring. It's the CAUSE, not the EFFECT.
   - CATALYST: "ABC Corp Announces FDA Approval of Cancer Drug"
   - NOT CATALYST: "ABC Corp Shares Surge 200% on FDA Approval" (this reports the effect)

3. Look for press release language:
   - "announces", "reports", "enters agreement", "receives approval"
   - Company-issued statements without stock price commentary

4. REJECT articles that:
   - Mention stock price movement (surge, soar, spike, jump, explode, rally)
   - Are aggregator summaries of the move
   - Are analyst opinions or ratings
   - Are general market commentary

5. The catalyst should be BEFORE or AT the move start time, not after.

6. If multiple articles could be the catalyst, pick the EARLIEST one that fits.

7. If NO article is clearly the original catalyst (all are aggregator reports), say so.

Respond with ONLY valid JSON in this exact format:
{{
    "catalyst_found": true or false,
    "article_number": 1-N or null,
    "confidence": "high", "medium", or "low",
    "reason": "brief explanation of why this is/isn't the catalyst",
    "catalyst_type": "fda_approval", "contract", "acquisition", "earnings", "partnership", "other", or null
}}"""

    try:
        response = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "You are a financial news analyst. Respond only with valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        result_text = response.choices[0].message.content.strip()

        # Extract JSON from response (handle markdown code blocks)
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        result = json.loads(result_text)

        # If catalyst found, attach the article details
        if result.get("catalyst_found") and result.get("article_number"):
            idx = result["article_number"] - 1
            if 0 <= idx < len(articles):
                art = articles[idx]
                result["catalyst_headline"] = art["headline"]
                result["catalyst_summary"] = art["summary"]
                result["catalyst_time"] = art["created_at"]
                result["catalyst_source"] = art["source"]
                result["catalyst_url"] = art["url"]
                result["catalyst_author"] = art["author"]

        result["articles_in_window"] = len(articles)
        return result

    except json.JSONDecodeError as e:
        return {
            "catalyst_found": False,
            "reason": f"json_parse_error: {str(e)}",
            "articles_in_window": len(articles),
        }
    except Exception as e:
        return {
            "catalyst_found": False,
            "reason": f"ai_error: {str(e)}",
            "articles_in_window": len(articles),
        }


async def process_mover(
    news_client: NewsClient,
    groq_client: AsyncGroq,
    mover: dict,
) -> dict:
    """Process a single mover: fetch news and identify catalyst."""
    ticker = mover["ticker"]
    move_start_time = mover.get("move_start_time", "")
    max_excursion = float(mover.get("max_excursion_pct", 0))

    # Fetch news
    articles = fetch_news_for_mover(news_client, ticker, move_start_time)

    # Identify catalyst with AI
    catalyst_result = await identify_catalyst_with_ai(
        groq_client, ticker, move_start_time, max_excursion, articles
    )

    # Merge mover data with catalyst result
    result = dict(mover)
    result.update({
        "catalyst_found": catalyst_result.get("catalyst_found", False),
        "catalyst_headline": catalyst_result.get("catalyst_headline", ""),
        "catalyst_summary": catalyst_result.get("catalyst_summary", ""),
        "catalyst_time": catalyst_result.get("catalyst_time", ""),
        "catalyst_source": catalyst_result.get("catalyst_source", ""),
        "catalyst_url": catalyst_result.get("catalyst_url", ""),
        "catalyst_author": catalyst_result.get("catalyst_author", ""),
        "catalyst_type": catalyst_result.get("catalyst_type", ""),
        "catalyst_confidence": catalyst_result.get("confidence", ""),
        "catalyst_reason": catalyst_result.get("reason", ""),
        "articles_in_window": catalyst_result.get("articles_in_window", 0),
    })

    return result


async def process_movers_batch(
    news_client: NewsClient,
    groq_client: AsyncGroq,
    movers: list[dict],
    pbar: tqdm,
) -> list[dict]:
    """Process a batch of movers with rate limiting."""
    results = []

    for i, mover in enumerate(movers):
        result = await process_mover(news_client, groq_client, mover)
        results.append(result)
        pbar.update(1)

        # Show progress
        ticker = mover["ticker"]
        found = "YES" if result["catalyst_found"] else "no"
        pbar.set_postfix({"last": f"{ticker}={found}"})

        # Rate limiting for Groq
        if (i + 1) % GROQ_BATCH_SIZE == 0:
            await asyncio.sleep(GROQ_PAUSE_SECS)

    return results


def load_movers_from_csv(file_path: Path) -> list[dict]:
    """Load movers from CSV file."""
    movers = []
    if not file_path.exists():
        return movers

    with open(file_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            movers.append(row)

    return movers


def save_results_to_csv(results: list[dict], output_path: Path):
    """Save results to CSV with catalyst columns."""
    if not results:
        return

    # Define column order
    base_columns = [
        "ticker", "date", "sector", "industry",
        "daily_open", "daily_high", "daily_low", "daily_close",
        "daily_volume", "daily_move_pct",
        "move_start_time", "move_peak_time", "move_end_time",
        "move_start_price", "move_peak_price", "move_end_price",
        "max_excursion_pct",
    ]

    catalyst_columns = [
        "catalyst_found", "catalyst_headline", "catalyst_summary",
        "catalyst_time", "catalyst_source", "catalyst_url", "catalyst_author",
        "catalyst_type", "catalyst_confidence", "catalyst_reason",
        "articles_in_window",
    ]

    # Use all columns from first result, maintaining order
    all_columns = []
    for col in base_columns + catalyst_columns:
        if col in results[0]:
            all_columns.append(col)

    # Add any remaining columns
    for col in results[0].keys():
        if col not in all_columns:
            all_columns.append(col)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


async def run_test_mode():
    """Run on test data (10 sample movers)."""
    print("=" * 70)
    print("CATALYST IDENTIFICATION - TEST MODE")
    print("=" * 70)
    print(f"\nTesting with {len(TEST_MOVERS)} sample movers...")
    print(f"Window: ±{WINDOW_MINUTES_BEFORE} min around move start")
    print(f"AI Model: {GROQ_MODEL}")
    print()

    news_client = NewsClient()
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

    pbar = tqdm(total=len(TEST_MOVERS), desc="Processing")

    results = await process_movers_batch(
        news_client, groq_client, TEST_MOVERS, pbar
    )

    pbar.close()

    # Print results
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)

    found_count = 0
    for r in results:
        ticker = r["ticker"]
        excursion = r["max_excursion_pct"]
        found = r["catalyst_found"]

        if found:
            found_count += 1
            print(f"\n{ticker} (+{excursion}%)")
            print(f"  CATALYST: {r['catalyst_headline']}")
            print(f"  Source: {r['catalyst_source']}")
            print(f"  Time: {r['catalyst_time']}")
            print(f"  Type: {r['catalyst_type']} (confidence: {r['catalyst_confidence']})")
            if r['catalyst_url']:
                print(f"  URL: {r['catalyst_url']}")
        else:
            print(f"\n{ticker} (+{excursion}%)")
            print(f"  NO CATALYST FOUND - {r['catalyst_reason']}")
            print(f"  Articles in window: {r['articles_in_window']}")

    print("\n" + "=" * 70)
    print(f"SUMMARY: Found catalysts for {found_count}/{len(results)} movers")
    print("=" * 70)

    # Save test results
    output_path = INPUT_DIR / "test_catalysts.csv"
    save_results_to_csv(results, output_path)
    print(f"\nResults saved to: {output_path}")


async def run_full_mode():
    """Run on full dataset."""
    print("=" * 70)
    print("CATALYST IDENTIFICATION - FULL MODE")
    print("=" * 70)

    news_client = NewsClient()
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

    # Process 10%+ movers first (higher priority)
    if FILE_10_PLUS.exists():
        movers_10_plus = load_movers_from_csv(FILE_10_PLUS)
        print(f"\nProcessing {len(movers_10_plus)} 10%+ movers...")

        pbar = tqdm(total=len(movers_10_plus), desc="10%+ movers")
        results_10_plus = await process_movers_batch(
            news_client, groq_client, movers_10_plus, pbar
        )
        pbar.close()

        output_path = INPUT_DIR / "10_plus_pct_with_catalysts.csv"
        save_results_to_csv(results_10_plus, output_path)

        found = sum(1 for r in results_10_plus if r["catalyst_found"])
        print(f"  Saved to: {output_path}")
        print(f"  Catalysts found: {found}/{len(results_10_plus)}")

    # Process 5-10% movers
    if FILE_5_TO_10.exists():
        movers_5_to_10 = load_movers_from_csv(FILE_5_TO_10)
        print(f"\nProcessing {len(movers_5_to_10)} 5-10% movers...")

        pbar = tqdm(total=len(movers_5_to_10), desc="5-10% movers")
        results_5_to_10 = await process_movers_batch(
            news_client, groq_client, movers_5_to_10, pbar
        )
        pbar.close()

        output_path = INPUT_DIR / "5_to_10_pct_with_catalysts.csv"
        save_results_to_csv(results_5_to_10, output_path)

        found = sum(1 for r in results_5_to_10 if r["catalyst_found"])
        print(f"  Saved to: {output_path}")
        print(f"  Catalysts found: {found}/{len(results_5_to_10)}")

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Identify catalyst headlines for price movers")
    parser.add_argument("--test", action="store_true", help="Run on 10 sample movers only")
    args = parser.parse_args()

    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set in environment")
        return

    if args.test:
        asyncio.run(run_test_mode())
    else:
        asyncio.run(run_full_mode())


if __name__ == "__main__":
    main()
