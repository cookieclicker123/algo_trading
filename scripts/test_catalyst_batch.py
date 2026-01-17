#!/usr/bin/env python3
"""
Test catalyst identification with batched Groq calls.

Step 1: Fetch news for 20 sample movers (24h lookback - news often overnight)
Step 2: Show what news exists
Step 3: Send ONE batch of 20 to Groq for catalyst identification

Usage:
    arch -arm64 .venv/bin/python scripts/test_catalyst_batch.py
"""

import asyncio
import csv
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from alpaca.data.historical.news import NewsClient

# Load prompt template
PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "catalyst_identification.txt"
PROMPT_TEMPLATE = PROMPT_FILE.read_text()
SYSTEM_PROMPT = PROMPT_TEMPLATE.split("USER:")[0].replace("SYSTEM:", "").strip()
USER_PROMPT_TEMPLATE = PROMPT_TEMPLATE.split("USER:")[1].strip()
from alpaca.data.requests import NewsRequest
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()

# Config
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
ALPACA_API_KEY = os.getenv("ALPACA_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET", "")

SAMPLE_SIZE = 20
LOOKBACK_HOURS = 24  # Look back 24 hours - news often comes overnight

# Paths
INPUT_FILE = Path("tmp/alpaca_movers/10_plus_pct_winners.csv")


def parse_ts(ts_str: str) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except:
        return None


def load_samples(n: int = 20) -> list[dict]:
    """Load n random samples from CSV, preferring larger moves."""
    movers = []
    with open(INPUT_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            movers.append(row)

    # Take top 200 by excursion, then random sample from those
    movers.sort(key=lambda x: float(x.get("max_excursion_pct", 0)), reverse=True)
    top_movers = movers[:200]
    return random.sample(top_movers, min(n, len(top_movers)))


def fetch_news(client: NewsClient, ticker: str, move_start: str) -> list[dict]:
    """Fetch news 24h before move start."""
    move_dt = parse_ts(move_start)
    if not move_dt:
        return []

    # Look back 24 hours before move
    start_time = move_dt - timedelta(hours=LOOKBACK_HOURS)
    end_time = move_dt + timedelta(minutes=5)  # tiny buffer past move start

    try:
        request = NewsRequest(
            symbols=ticker,
            start=start_time,
            end=end_time,
            limit=15,
        )
        response = client.get_news(request)

        articles = []
        news_list = response.data.get("news", [])
        for art in news_list:
            articles.append({
                "headline": art.headline or "",
                "time": art.created_at.isoformat() if art.created_at else "",
                "source": art.source or "",
            })
        return articles
    except Exception as e:
        return []


async def identify_catalysts_batch(
    groq_client: AsyncGroq,
    batch: list[dict],
) -> dict:
    """Send ONE prompt with all tickers, get back catalyst for each."""

    # Build concise input - only tickers with news
    lines = []
    ticker_order = []
    for i, item in enumerate(batch, 1):
        ticker = item["ticker"]
        move_time = item["move_time"]
        excursion = item["excursion"]
        articles = item["articles"]

        if not articles:
            continue  # Skip tickers with no news

        ticker_order.append(ticker)
        art_strs = []
        for j, a in enumerate(articles, 1):
            # Truncate headline for token efficiency
            headline = a['headline'][:120]
            art_strs.append(f"  [{j}] {a['time'][-14:-6]}: {headline}")

        lines.append(f"{len(ticker_order)}. {ticker} +{excursion:.0f}% @ {move_time[-14:-6]}\n" + "\n".join(art_strs))

    if not lines:
        return {}

    tickers_news = "\n".join(lines)
    prompt = USER_PROMPT_TEMPLATE.replace("{stocks_with_news}", tickers_news)

    try:
        response = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=800,
        )

        result_text = response.choices[0].message.content.strip()

        # Clean up any markdown formatting
        if "```" in result_text:
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        return json.loads(result_text)

    except Exception as e:
        print(f"Groq error: {e}")
        print(f"Raw response: {result_text[:500] if 'result_text' in dir() else 'N/A'}")
        return {}


async def main():
    print("=" * 70)
    print("CATALYST BATCH TEST - 20 SAMPLES (24h lookback)")
    print("=" * 70)

    # Step 1: Load samples
    print("\n[1] Loading 20 random samples from top 200 movers...")
    samples = load_samples(SAMPLE_SIZE)
    print(f"    Loaded {len(samples)} samples")

    # Step 2: Fetch news for each
    print(f"\n[2] Fetching news ({LOOKBACK_HOURS}h lookback)...")
    news_client = NewsClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY)

    batch_data = []
    for s in samples:
        ticker = s["ticker"]
        move_start = s["move_start_time"]
        excursion = float(s["max_excursion_pct"])

        articles = fetch_news(news_client, ticker, move_start)

        batch_data.append({
            "ticker": ticker,
            "move_time": move_start,
            "excursion": excursion,
            "articles": articles,
            "raw": s,
        })

        # Show what we found
        if articles:
            print(f"    {ticker:6} +{excursion:6.0f}% | {len(articles):2} articles")
            for a in articles[:2]:
                hl = a['headline'][:60]
                print(f"           [{a['source'][:4]}] {hl}...")
        else:
            print(f"    {ticker:6} +{excursion:6.0f}% | NO NEWS")

    # Stats
    with_news = sum(1 for b in batch_data if b["articles"])
    total_articles = sum(len(b["articles"]) for b in batch_data)
    print(f"\n    Summary: {with_news}/{len(batch_data)} have news ({total_articles} total articles)")

    if with_news == 0:
        print("\n    No news found for any ticker. Cannot proceed with AI step.")
        return

    # Step 3: Send to Groq
    if not GROQ_API_KEY:
        print("\n[3] GROQ_API_KEY not set - skipping AI step")
        return

    print("\n[3] Sending batch to Groq for catalyst identification...")
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

    results = await identify_catalysts_batch(groq_client, batch_data)

    print("\n[4] RESULTS:")
    print("=" * 70)

    found = 0
    for b in batch_data:
        ticker = b["ticker"]
        excursion = b["excursion"]
        articles = b["articles"]

        if not articles:
            print(f"{ticker:6} +{excursion:5.0f}% | NO NEWS AVAILABLE")
            continue

        result = results.get(ticker, {})
        catalyst_idx = result.get("n")
        catalyst_type = result.get("t", "none")

        if catalyst_idx and 1 <= catalyst_idx <= len(articles):
            found += 1
            art = articles[catalyst_idx - 1]
            print(f"\n{ticker:6} +{excursion:5.0f}% | CATALYST FOUND [{catalyst_type}]")
            print(f"        Headline: {art['headline'][:80]}")
            print(f"        Time: {art['time']}")
        else:
            print(f"{ticker:6} +{excursion:5.0f}% | no catalyst identified ({catalyst_type})")

    print("\n" + "=" * 70)
    print(f"SUMMARY: Found catalysts for {found}/{with_news} movers with news")
    print(f"         ({len(batch_data) - with_news} had no news coverage)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
