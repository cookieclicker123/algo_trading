#!/usr/bin/env python3
"""
Collect catalyst headlines for 10%+ movers only.

Usage:
    arch -arm64 .venv/bin/python scripts/collect_catalysts_10_plus.py
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
INPUT_FILE = Path("tmp/alpaca_movers/10_plus_pct_winners.csv")
OUTPUT_FILE = Path("tmp/alpaca_movers/10_plus_pct_with_catalysts.csv")
CHECKPOINT_FILE = Path("tmp/alpaca_movers/checkpoint_10_plus.json")
PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "catalyst_identification.txt"

# Load prompt
PROMPT_TEMPLATE = PROMPT_FILE.read_text()
SYSTEM_PROMPT = PROMPT_TEMPLATE.split("USER:")[0].replace("SYSTEM:", "").strip()
USER_PROMPT_TEMPLATE = PROMPT_TEMPLATE.split("USER:")[1].strip()

# Processing settings
BATCH_SIZE = 100
GROQ_BATCH_SIZE = 20
GROQ_CONCURRENT = 5
GROQ_RETRY_MAX = 10
GROQ_BACKOFF_BASE = 2
LOOKBACK_HOURS = 24
ALPACA_CONCURRENT = 100


def load_checkpoint() -> int:
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return json.load(f).get("index", 0)
    return 0


def save_checkpoint(index: int):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"index": index}, f)


def load_movers() -> list[dict]:
    movers = []
    with open(INPUT_FILE, newline="") as f:
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
    move_dt = parse_ts(move_start)
    if not move_dt:
        return ticker, []

    start_time = move_dt - timedelta(hours=LOOKBACK_HOURS)
    end_time = move_dt + timedelta(minutes=5)

    async with semaphore:
        try:
            loop = asyncio.get_event_loop()
            request = NewsRequest(symbols=ticker, start=start_time, end=end_time, limit=15)
            response = await loop.run_in_executor(None, client.get_news, request)
            news_list = response.data.get("news", [])
            return ticker, [
                {"headline": art.headline, "time": art.created_at.isoformat(), "source": art.source}
                for art in news_list
            ]
        except:
            return ticker, []


async def call_groq_with_retry(groq_client: AsyncGroq, batch: list[dict], semaphore: asyncio.Semaphore, pbar: tqdm) -> dict:
    lines = []
    for item in batch:
        if not item["articles"]:
            continue
        ticker = item["ticker"]
        art_strs = []
        for j, a in enumerate(item["articles"], 1):
            headline = a["headline"][:120]
            art_strs.append(f"  [{j}] {a['time'][-14:-6]}: {headline}")
        lines.append(f"{len(lines)+1}. {ticker} +{item['excursion']:.0f}% @ {item['move_time'][-14:-6]}\n" + "\n".join(art_strs))

    if not lines:
        return {}

    prompt = USER_PROMPT_TEMPLATE.replace("{stocks_with_news}", "\n".join(lines))

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
                pbar.write(f"    Rate limit, waiting {wait_time}s (attempt {attempt+1})")
                await asyncio.sleep(wait_time)
            else:
                wait_time = GROQ_BACKOFF_BASE * (attempt + 1)
                pbar.write(f"    Error: {str(e)[:50]}, retrying...")
                await asyncio.sleep(wait_time)

    raise RuntimeError(f"Failed after {GROQ_RETRY_MAX} attempts")


def get_output_columns() -> list[str]:
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


def append_to_csv(rows: list[dict]):
    columns = get_output_columns()
    file_exists = OUTPUT_FILE.exists()
    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


async def main():
    print("=" * 60)
    print("CATALYST COLLECTION - 10%+ MOVERS")
    print("=" * 60)

    movers = load_movers()
    start_idx = load_checkpoint()

    if start_idx > 0:
        print(f"Resuming from {start_idx}/{len(movers)}")
    else:
        print(f"Processing {len(movers)} movers")
        if OUTPUT_FILE.exists():
            OUTPUT_FILE.unlink()

    news_client = NewsClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY)
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
    groq_semaphore = asyncio.Semaphore(GROQ_CONCURRENT)

    total_news = 0
    total_catalysts = 0

    pbar = tqdm(total=len(movers), initial=start_idx, desc="10%+ movers")

    i = start_idx
    while i < len(movers):
        batch_end = min(i + BATCH_SIZE, len(movers))
        batch_movers = movers[i:batch_end]

        # Fetch news in parallel
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
        total_news += len(with_news)

        # Groq calls in parallel
        results = {}
        if with_news:
            groq_chunks = [with_news[j:j+GROQ_BATCH_SIZE] for j in range(0, len(with_news), GROQ_BATCH_SIZE)]
            groq_tasks = [call_groq_with_retry(groq_client, chunk, groq_semaphore, pbar) for chunk in groq_chunks]
            chunk_results = await asyncio.gather(*groq_tasks)
            for cr in chunk_results:
                results.update(cr)

        # Enrich and save
        enriched = []
        for item in batch_data:
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
                total_catalysts += 1
            else:
                row["catalyst_found"] = False
                row["catalyst_headline"] = ""
                row["catalyst_time"] = ""
                row["catalyst_source"] = ""
                row["catalyst_type"] = result.get("t", "none")

            enriched.append(row)

        append_to_csv(enriched)

        i = batch_end
        save_checkpoint(i)
        pbar.update(len(batch_movers))
        pbar.set_postfix({"news": total_news, "catalysts": total_catalysts})

    pbar.close()

    print(f"\n{'=' * 60}")
    print(f"COMPLETE: {total_news} with news, {total_catalysts} catalysts")
    print(f"Output: {OUTPUT_FILE}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
