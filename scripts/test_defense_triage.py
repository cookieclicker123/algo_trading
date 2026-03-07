#!/usr/bin/env python3
"""
Test universal triage classifier on defense headlines (winners + losers).

Top 15 winners by MFE + 15 random losers.
Reports whether triage identifies them as HIGH_CONVICTION defense types.
"""

import asyncio
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from groq import AsyncGroq

# HIGH_CONVICTION defense types that would trigger filter relaxation
HIGH_CONVICTION_TYPES = {"government_contract", "military_contract", "defense_order"}

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "headline_types" / "universal_triage.txt"


async def classify_headline(client: AsyncGroq, prompt_template: str, headline: str) -> str:
    """Run a single headline through the triage prompt."""
    prompt = prompt_template.replace("{headline}", headline)
    try:
        response = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=20,
            timeout=10.0,
        )
        if response.choices and response.choices[0].message.content:
            result = response.choices[0].message.content.strip().lower()
            result = result.split()[0] if result else "ERROR"
            result = result.replace(".", "").replace(",", "")
            return result
        return "NO_RESPONSE"
    except Exception as e:
        return f"ERROR:{e}"


async def main():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set. Source .env first.")
        sys.exit(1)

    # Load prompt
    prompt_template = PROMPT_PATH.read_text()

    # Load data
    with open("/tmp/defense_winners.json") as f:
        winners = json.load(f)
    with open("/tmp/defense_losers.json") as f:
        losers = json.load(f)

    # Top 15 winners by MFE (already sorted descending)
    top_winners = winners[:15]

    # 15 random losers
    random.seed(42)  # reproducible
    sample_losers = random.sample(losers, min(15, len(losers)))

    client = AsyncGroq(api_key=api_key)

    # --- WINNERS ---
    print("=" * 120)
    print("TOP 15 WINNERS (by MFE) — expecting defense types")
    print("=" * 120)
    print(f"{'Ticker':<8} {'MFE':>7} {'Triage Result':<25} {'Defense?':<10} Headline")
    print("-" * 120)

    winner_defense_count = 0
    for item in top_winners:
        result = await classify_headline(client, prompt_template, item["headline"])
        is_defense = result in HIGH_CONVICTION_TYPES
        if is_defense:
            winner_defense_count += 1
        marker = "YES" if is_defense else f"NO  ({result})"
        mfe = item["mfe_pct"]
        mfe_str = f"{mfe:.1f}%" if mfe is not None else "N/A"
        headline_short = item["headline"][:60]
        print(f"{item['ticker']:<8} {mfe_str:>7} {result:<25} {marker:<10} {headline_short}")
        await asyncio.sleep(0.15)  # rate limit

    print(f"\nWinners identified as defense: {winner_defense_count}/{len(top_winners)} ({winner_defense_count/len(top_winners)*100:.0f}%)")

    # --- LOSERS ---
    print()
    print("=" * 120)
    print("15 RANDOM LOSERS — checking triage classification")
    print("=" * 120)
    print(f"{'Ticker':<8} {'MFE':>7} {'Triage Result':<25} {'Defense?':<10} Headline")
    print("-" * 120)

    loser_defense_count = 0
    for item in sample_losers:
        result = await classify_headline(client, prompt_template, item["headline"])
        is_defense = result in HIGH_CONVICTION_TYPES
        if is_defense:
            loser_defense_count += 1
        marker = "YES" if is_defense else f"NO  ({result})"
        mfe = item["mfe_pct"]
        mfe_str = f"{mfe:.1f}%" if mfe is not None else "N/A"
        headline_short = item["headline"][:60]
        print(f"{item['ticker']:<8} {mfe_str:>7} {result:<25} {marker:<10} {headline_short}")
        await asyncio.sleep(0.15)  # rate limit

    print(f"\nLosers identified as defense: {loser_defense_count}/{len(sample_losers)} ({loser_defense_count/len(sample_losers)*100:.0f}%)")

    # --- SUMMARY ---
    print()
    print("=" * 120)
    print("SUMMARY")
    print("=" * 120)
    print(f"Winners correctly triaged as defense: {winner_defense_count}/{len(top_winners)} ({winner_defense_count/len(top_winners)*100:.0f}%)")
    print(f"Losers triaged as defense:           {loser_defense_count}/{len(sample_losers)} ({loser_defense_count/len(sample_losers)*100:.0f}%)")
    print(f"HIGH_CONVICTION types checked: {sorted(HIGH_CONVICTION_TYPES)}")


if __name__ == "__main__":
    asyncio.run(main())
