#!/usr/bin/env python3
"""Show exactly what prompt goes to Groq and what comes back."""

import asyncio
import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
ALPACA_API_KEY = os.getenv("ALPACA_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET", "")

# Use the exact same samples that worked
SAMPLES = [
    {"ticker": "TVGN", "move_start_time": "2024-02-15T14:53:00+00:00", "max_excursion_pct": 297},
    {"ticker": "ATYR", "move_start_time": "2025-09-15T12:30:00+00:00", "max_excursion_pct": 368},
    {"ticker": "PRFX", "move_start_time": "2024-08-20T13:30:00+00:00", "max_excursion_pct": 239},
    {"ticker": "BON", "move_start_time": "2025-03-14T12:00:00+00:00", "max_excursion_pct": 1016},
    {"ticker": "AIM", "move_start_time": "2025-07-28T13:30:00+00:00", "max_excursion_pct": 313},
    {"ticker": "IBG", "move_start_time": "2025-09-23T13:30:00+00:00", "max_excursion_pct": 293},
    {"ticker": "GALT", "move_start_time": "2024-12-20T14:30:00+00:00", "max_excursion_pct": 283},
    {"ticker": "ACCL", "move_start_time": "2024-08-23T08:00:00+00:00", "max_excursion_pct": 202},
]


def fetch_news(client, ticker, move_start):
    move_dt = datetime.fromisoformat(move_start.replace("Z", "+00:00"))
    start_time = move_dt - timedelta(hours=24)
    end_time = move_dt + timedelta(minutes=5)

    try:
        request = NewsRequest(symbols=ticker, start=start_time, end=end_time, limit=15)
        response = client.get_news(request)
        news_list = response.data.get("news", [])
        return [{"headline": art.headline, "time": art.created_at.isoformat(), "source": art.source} for art in news_list]
    except:
        return []


async def main():
    news_client = NewsClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY)

    # Fetch news for all samples
    batch_data = []
    for s in SAMPLES:
        articles = fetch_news(news_client, s["ticker"], s["move_start_time"])
        if articles:
            batch_data.append({
                "ticker": s["ticker"],
                "move_time": s["move_start_time"],
                "excursion": s["max_excursion_pct"],
                "articles": articles,
            })

    # Build the prompt exactly as in test script
    lines = []
    for i, item in enumerate(batch_data, 1):
        ticker = item["ticker"]
        move_time = item["move_time"]
        excursion = item["excursion"]
        articles = item["articles"]

        art_strs = []
        for j, a in enumerate(articles, 1):
            headline = a['headline'][:120]
            art_strs.append(f"  [{j}] {a['time'][-14:-6]}: {headline}")

        lines.append(f"{i}. {ticker} +{excursion:.0f}% @ {move_time[-14:-6]}\n" + "\n".join(art_strs))

    tickers_news = "\n".join(lines)

    prompt = f"""Identify the CATALYST headline for each stock move.

RULES:
- Catalyst = press release announcing NEW information (offering, FDA, contract, merger, earnings)
- NOT catalyst = headlines mentioning stock "surging/soaring/up X%" (these REPORT the effect, not CAUSE it)
- The catalyst comes BEFORE the price move, not after
- If multiple valid catalysts, pick the one closest to the move time

STOCKS WITH NEWS:
{tickers_news}

Respond with ONLY valid JSON - map each ticker to the article number (1-indexed) that is the catalyst, or null if none:
{{"TICKER": {{"n": 1, "t": "offering"}}, "TICKER2": {{"n": null, "t": "none"}}, ...}}

Types: offering, fda, contract, merger, earnings, partnership, other, none"""

    print("=" * 80)
    print("INPUT PROMPT TO GROQ:")
    print("=" * 80)
    print(prompt)
    print()
    print(f"PROMPT LENGTH: {len(prompt)} chars, ~{len(prompt)//4} tokens")
    print("=" * 80)

    # Send to Groq
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
    response = await groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": "Financial analyst. JSON only. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=800,
    )

    result = response.choices[0].message.content.strip()

    print("\nOUTPUT FROM GROQ:")
    print("=" * 80)
    print(result)
    print()
    print(f"OUTPUT LENGTH: {len(result)} chars, ~{len(result)//4} tokens")
    print("=" * 80)

    # Show usage
    print(f"\nUSAGE STATS:")
    print(f"  Prompt tokens: {response.usage.prompt_tokens}")
    print(f"  Completion tokens: {response.usage.completion_tokens}")
    print(f"  Total tokens: {response.usage.total_tokens}")


if __name__ == "__main__":
    asyncio.run(main())
