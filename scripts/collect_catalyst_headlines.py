#!/usr/bin/env python3
"""
Production pipeline: Collect catalyst headlines for all movers.

Phase 1: Filter movers by news availability (Alpaca)
Phase 2: Identify catalysts with AI (Groq)

Features:
- Never skips batches - retries until success
- Saves progress after each batch
- Resumes from checkpoint on restart
- Exponential backoff on rate limits
- Parallel Groq requests with semaphore

Usage:
    arch -arm64 .venv/bin/python scripts/collect_catalyst_headlines.py

Output:
    tmp/alpaca_movers/10_plus_pct_with_catalysts.csv
    tmp/alpaca_movers/5_to_10_pct_with_catalysts.csv
"""

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
from tqdm import tqdm

load_dotenv()

# === CONFIG ===
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
ALPACA_API_KEY = os.getenv("ALPACA_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET", "")

# Paths
INPUT_DIR = Path("tmp/alpaca_movers")
CHECKPOINT_FILE = INPUT_DIR / "catalyst_checkpoint.json"
PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "catalyst_identification.txt"

# Load prompt
PROMPT_TEMPLATE = PROMPT_FILE.read_text()
SYSTEM_PROMPT = PROMPT_TEMPLATE.split("USER:")[0].replace("SYSTEM:", "").strip()
USER_PROMPT_TEMPLATE = PROMPT_TEMPLATE.split("USER:")[1].strip()

# Processing settings
BATCH_SIZE = 100  # Tickers per processing chunk
GROQ_BATCH_SIZE = 20  # Tickers per Groq call
GROQ_CONCURRENT = 5  # Max concurrent Groq requests
GROQ_RETRY_MAX = 10  # Max retries per batch
GROQ_BACKOFF_BASE = 2  # Base seconds for exponential backoff
LOOKBACK_HOURS = 24  # News lookback window
ALPACA_CONCURRENT = 100  # Concurrent Alpaca news fetches


def load_checkpoint() -> dict:
    """Load progress checkpoint."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {"10_plus": 0, "5_to_10": 0, "completed_batches": []}


def save_checkpoint(checkpoint: dict):
    """Save progress checkpoint."""
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f, indent=2)


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


def parse_ts(ts_str: str) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except:
        return None


async def fetch_news_async(client: NewsClient, ticker: str, move_start: str, semaphore: asyncio.Semaphore) -> tuple[str, list[dict]]:
    """Fetch news 24h before move (async with semaphore)."""
    move_dt = parse_ts(move_start)
    if not move_dt:
        return ticker, []

    start_time = move_dt - timedelta(hours=LOOKBACK_HOURS)
    end_time = move_dt + timedelta(minutes=5)

    async with semaphore:
        try:
            # Run sync call in executor
            loop = asyncio.get_event_loop()
            request = NewsRequest(symbols=ticker, start=start_time, end=end_time, limit=15)
            response = await loop.run_in_executor(None, client.get_news, request)
            news_list = response.data.get("news", [])
            return ticker, [
                {"headline": art.headline, "time": art.created_at.isoformat(), "source": art.source}
                for art in news_list
            ]
        except Exception:
            return ticker, []


async def call_groq_with_retry(
    groq_client: AsyncGroq,
    batch: list[dict],
    semaphore: asyncio.Semaphore,
    pbar: tqdm,
) -> dict:
    """Call Groq with exponential backoff retry. Never skips."""

    # Build prompt
    lines = []
    ticker_map = {}
    for item in batch:
        if not item["articles"]:
            continue
        ticker = item["ticker"]
        ticker_map[ticker] = item
        art_strs = []
        for j, a in enumerate(item["articles"], 1):
            headline = a["headline"][:120]
            art_strs.append(f"  [{j}] {a['time'][-14:-6]}: {headline}")
        lines.append(f"{len(lines)+1}. {ticker} +{item['excursion']:.0f}% @ {item['move_time'][-14:-6]}\n" + "\n".join(art_strs))

    if not lines:
        return {}

    prompt = USER_PROMPT_TEMPLATE.replace("{stocks_with_news}", "\n".join(lines))

    # Retry loop - never skip
    for attempt in range(GROQ_RETRY_MAX):
        try:
            async with semaphore:
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

            # Clean markdown
            if "```" in result_text:
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()

            return json.loads(result_text)

        except Exception as e:
            error_str = str(e).lower()

            if "429" in error_str or "rate" in error_str:
                wait_time = GROQ_BACKOFF_BASE * (2 ** attempt)
                pbar.write(f"    Rate limit hit, waiting {wait_time}s (attempt {attempt+1}/{GROQ_RETRY_MAX})")
                await asyncio.sleep(wait_time)
            else:
                wait_time = GROQ_BACKOFF_BASE * (attempt + 1)
                pbar.write(f"    Error: {str(e)[:50]}, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)

    # Should never reach here, but if we do, raise to prevent skipping
    raise RuntimeError(f"Failed after {GROQ_RETRY_MAX} attempts - batch must not be skipped")


def enrich_batch_with_results(batch: list[dict], results: dict) -> list[dict]:
    """Add catalyst info to batch items."""
    enriched = []
    for item in batch:
        ticker = item["ticker"]
        result = results.get(ticker, {})
        catalyst_idx = result.get("n")

        row = dict(item["raw"])
        row["news_count"] = len(item["articles"])

        if catalyst_idx and item["articles"] and 1 <= catalyst_idx <= len(item["articles"]):
            art = item["articles"][catalyst_idx - 1]
            row["catalyst_found"] = True
            row["catalyst_headline"] = art["headline"]
            row["catalyst_time"] = art["time"]
            row["catalyst_source"] = art["source"]
            row["catalyst_type"] = result.get("t", "")
        else:
            row["catalyst_found"] = False
            row["catalyst_headline"] = ""
            row["catalyst_time"] = ""
            row["catalyst_source"] = ""
            row["catalyst_type"] = result.get("t", "none")

        enriched.append(row)
    return enriched


def get_output_columns() -> list[str]:
    """Define output CSV columns."""
    return [
        "ticker", "date", "sector", "industry",
        "daily_open", "daily_high", "daily_low", "daily_close",
        "daily_volume", "daily_move_pct",
        "move_start_time", "move_peak_time", "move_end_time",
        "move_start_price", "move_peak_price", "move_end_price",
        "max_excursion_pct",
        "news_count", "catalyst_found", "catalyst_headline",
        "catalyst_time", "catalyst_source", "catalyst_type",
    ]


def append_to_csv(file_path: Path, rows: list[dict]):
    """Append rows to CSV, creating with header if needed."""
    columns = get_output_columns()
    file_exists = file_path.exists()

    with open(file_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


async def process_file(
    file_path: Path,
    output_path: Path,
    file_key: str,
    checkpoint: dict,
    news_client: NewsClient,
    groq_client: AsyncGroq,
):
    """Process one mover file (5-10% or 10%+)."""

    movers = load_movers(file_path)
    if not movers:
        print(f"No movers in {file_path}")
        return

    start_idx = checkpoint.get(file_key, 0)

    if start_idx > 0:
        print(f"\n[{file_key}] Resuming from index {start_idx}/{len(movers)}")
    else:
        print(f"\n[{file_key}] Processing {len(movers)} movers")
        # Clear output file if starting fresh
        if output_path.exists():
            output_path.unlink()

    # Stats
    total_with_news = 0
    total_catalysts = 0

    # Progress bars
    pbar_global = tqdm(total=len(movers), initial=start_idx, desc=f"{file_key} total", position=0)

    semaphore = asyncio.Semaphore(GROQ_CONCURRENT)

    # Process in batches
    i = start_idx
    while i < len(movers):
        batch_end = min(i + BATCH_SIZE, len(movers))
        batch_movers = movers[i:batch_end]

        # Phase 1: Fetch news for batch (parallel)
        alpaca_semaphore = asyncio.Semaphore(ALPACA_CONCURRENT)
        news_tasks = [
            fetch_news_async(news_client, m["ticker"], m["move_start_time"], alpaca_semaphore)
            for m in batch_movers
        ]
        news_results = await asyncio.gather(*news_tasks)
        news_map = {ticker: articles for ticker, articles in news_results}

        batch_data = []
        for m in batch_movers:
            ticker = m["ticker"]
            batch_data.append({
                "ticker": ticker,
                "move_time": m["move_start_time"],
                "excursion": float(m.get("max_excursion_pct", 0)),
                "articles": news_map.get(ticker, []),
                "raw": m,
            })

        with_news = [b for b in batch_data if b["articles"]]
        total_with_news += len(with_news)

        # Phase 2: Identify catalysts (parallel Groq calls for chunks of 20)
        results = {}
        if with_news:
            groq_chunks = [with_news[j:j+GROQ_BATCH_SIZE] for j in range(0, len(with_news), GROQ_BATCH_SIZE)]
            groq_tasks = [call_groq_with_retry(groq_client, chunk, semaphore, pbar_global) for chunk in groq_chunks]
            chunk_results = await asyncio.gather(*groq_tasks)
            for cr in chunk_results:
                results.update(cr)

        # Enrich and save
        enriched = enrich_batch_with_results(batch_data, results)
        catalysts_in_batch = sum(1 for r in enriched if r["catalyst_found"])
        total_catalysts += catalysts_in_batch

        append_to_csv(output_path, enriched)

        # Update checkpoint
        i = batch_end
        checkpoint[file_key] = i
        save_checkpoint(checkpoint)

        # Update progress
        pbar_global.update(len(batch_movers))
        pbar_global.set_postfix({
            "news": total_with_news,
            "catalysts": total_catalysts,
        })

    pbar_global.close()

    print(f"\n[{file_key}] Complete: {total_with_news} with news, {total_catalysts} catalysts found")


async def main():
    print("=" * 70)
    print("CATALYST HEADLINE COLLECTION - PRODUCTION")
    print("=" * 70)
    print(f"Prompt: {PROMPT_FILE}")
    print(f"Checkpoint: {CHECKPOINT_FILE}")
    print()

    # Load checkpoint
    checkpoint = load_checkpoint()

    # Initialize clients
    news_client = NewsClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY)
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

    # Process 10%+ movers first (higher priority)
    file_10_plus = INPUT_DIR / "10_plus_pct_winners.csv"
    output_10_plus = INPUT_DIR / "10_plus_pct_with_catalysts.csv"

    if file_10_plus.exists():
        await process_file(
            file_10_plus, output_10_plus, "10_plus",
            checkpoint, news_client, groq_client
        )

    # Process 5-10% movers
    file_5_to_10 = INPUT_DIR / "5_to_10_pct_winners.csv"
    output_5_to_10 = INPUT_DIR / "5_to_10_pct_with_catalysts.csv"

    if file_5_to_10.exists():
        await process_file(
            file_5_to_10, output_5_to_10, "5_to_10",
            checkpoint, news_client, groq_client
        )

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Output files:")
    print(f"  {output_10_plus}")
    print(f"  {output_5_to_10}")


if __name__ == "__main__":
    asyncio.run(main())
