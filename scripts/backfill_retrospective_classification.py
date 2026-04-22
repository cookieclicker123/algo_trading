#!/usr/bin/env python3
"""
Backfill retrospective classification on historical recall records.

For every recall record with mid excursion >= 10% that does NOT already have
a `retrospective_classification` populated, this script:

1. Runs triage (Claude Haiku) on the headline to get a headline type.
2. If the type is in HC_BYPASS_TYPES, records HC bypass + size.
3. Otherwise, runs the sector classifier (needs metadata_cache) and records
   TRADE/SKIP + size + sector + industry.

This captures "what would the AI have done?" for articles that got rejected
by prefilter (spread too wide, market cap too low, latency, etc.) but ended
up being real movers — false negatives.

Usage:
    python scripts/backfill_retrospective_classification.py
    python scripts/backfill_retrospective_classification.py --start 2026-04-07
    python scripts/backfill_retrospective_classification.py --dry-run
    python scripts/backfill_retrospective_classification.py --session premarket  # premarket only

The script rewrites each session JSON file atomically (temp + rename).
Idempotent: records with `retrospective_classification` already populated
are skipped unless --force is set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Path setup
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.shared.statistics.retrospective_classifier import (  # noqa: E402
    RetrospectiveClassifier,
    compute_mid_excursion_pct,
    DEFAULT_MIN_EXCURSION_PCT,
)
from newsflash.shared.statistics.headline_classifier import get_headline_classifier  # noqa: E402


DEFAULT_START = date(2026, 4, 7)
SESSIONS = ("premarket", "market_hours", "postmarket")


class _MetadataCacheAdapter:
    """
    Minimal metadata cache adapter for the backfill script — SectorClassifier
    needs `.get_permanent(ticker)` → {sector, industry, market_cap_millions, ...}.

    Reads directly from data/cache/permanent_metadata.json and
    data/cache/daily_metadata.json so we don't need to bootstrap the full
    DI container (which would spin up the event bus, brokerage connection, etc.).
    """

    def __init__(self, permanent_path: Path, daily_path: Path):
        self._permanent: Dict[str, Dict[str, Any]] = {}
        self._daily: Dict[str, Dict[str, Any]] = {}
        if permanent_path.exists():
            with open(permanent_path) as f:
                data = json.load(f)
                # Flat {TICKER: {sector, industry, exchange, ...}}
                self._permanent = data if isinstance(data, dict) else {}
        if daily_path.exists():
            with open(daily_path) as f:
                data = json.load(f)
                # Nested: {"date": ..., "data": {TICKER: {market_cap_millions, price}}}
                self._daily = data.get("data", {}) if isinstance(data, dict) else {}

    async def get_permanent(self, ticker: str) -> Optional[Dict[str, Any]]:
        perm = self._permanent.get(ticker)
        if not perm:
            return None
        daily = self._daily.get(ticker, {})
        return {
            "sector": perm.get("sector"),
            "industry": perm.get("industry"),
            "exchange": perm.get("exchange"),
            "market_cap_millions": daily.get("market_cap_millions"),
            "price": daily.get("price"),
        }


def _walk_recall_files(
    base: Path,
    start: date,
    end: date,
    sessions: tuple,
) -> List[Path]:
    """Yield recall session files in the date range, sorted by date."""
    matched: List[Path] = []
    if not base.exists():
        return matched
    for year_dir in sorted(base.iterdir()):
        if not year_dir.is_dir():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for week_dir in sorted(month_dir.iterdir()):
                if not week_dir.is_dir():
                    continue
                for day_dir in sorted(week_dir.iterdir()):
                    if not day_dir.is_dir():
                        continue
                    try:
                        dt = date(int(year_dir.name), int(month_dir.name), int(day_dir.name))
                    except ValueError:
                        continue
                    if dt < start or dt > end:
                        continue
                    for sess in sessions:
                        f = day_dir / sess / f"{sess}.json"
                        if f.exists():
                            matched.append(f)
    return matched


async def _process_file(
    path: Path,
    classifier: RetrospectiveClassifier,
    *,
    dry_run: bool,
    force: bool,
    min_excursion_pct: float,
) -> Dict[str, int]:
    """Process one session JSON file; returns counters."""
    counters = {
        "records": 0,
        "eligible": 0,
        "already_classified": 0,
        "classified": 0,
        "failed": 0,
    }

    with open(path) as f:
        data = json.load(f)

    records = data.get("records", [])
    counters["records"] = len(records)
    modified = False

    for rec in records:
        excursion = compute_mid_excursion_pct(
            rec.get("initial_nbbo"),
            rec.get("highest_price_during_hold"),
        )
        if excursion is None or excursion < min_excursion_pct:
            continue
        counters["eligible"] += 1

        if rec.get("retrospective_classification") and not force:
            counters["already_classified"] += 1
            continue

        headline = rec.get("title") or rec.get("headline")
        tickers = rec.get("tickers") or []
        ticker = tickers[0] if tickers else None
        if not headline or not ticker:
            counters["failed"] += 1
            continue

        try:
            retro = await classifier.classify(headline, ticker)
        except Exception as e:
            print(f"  ! classifier error on {rec.get('article_id')}: {e}")
            counters["failed"] += 1
            continue

        retro["max_mid_excursion_pct"] = round(excursion, 2)
        rec["retrospective_classification"] = retro
        counters["classified"] += 1
        modified = True
        print(
            f"  + {rec.get('article_id')[:30]:<30} {ticker:<6} excursion={excursion:>5.1f}% "
            f"triage={retro.get('triage_type') or 'null':<28} "
            f"hc={'Y' if retro.get('hc_bypass') else 'N'} "
            f"sector={(retro.get('sector_decision') or {}).get('classification') or '-'}"
        )

    if modified and not dry_run:
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)

    return counters


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=str, default=DEFAULT_START.isoformat(),
                        help=f"Start date (YYYY-MM-DD). Default: {DEFAULT_START}")
    parser.add_argument("--end", type=str, default=None,
                        help="End date (YYYY-MM-DD). Default: today")
    parser.add_argument("--session", type=str, default=None,
                        choices=list(SESSIONS),
                        help="Only process one session (default: all three)")
    parser.add_argument("--min-excursion", type=float, default=DEFAULT_MIN_EXCURSION_PCT,
                        help=f"Minimum mid excursion %% to classify (default: {DEFAULT_MIN_EXCURSION_PCT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write files; just print what would happen")
    parser.add_argument("--force", action="store_true",
                        help="Reclassify even records that already have retrospective_classification")
    parser.add_argument("--recall-root", type=str, default="tmp/statistics/recall",
                        help="Root path for recall files")
    parser.add_argument("--permanent-cache", type=str, default="data/cache/permanent_metadata.json")
    parser.add_argument("--daily-cache", type=str, default="data/cache/daily_metadata.json")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    sessions = (args.session,) if args.session else SESSIONS

    # Build classifier with adapter metadata cache
    metadata_cache = _MetadataCacheAdapter(
        Path(args.permanent_cache),
        Path(args.daily_cache),
    )

    # Sector classifier — needs Anthropic + (optional) Groq keys
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    from newsflash.infra.classification.sector_classifier import SectorClassifier
    sector_classifier = SectorClassifier(
        api_key=anthropic_key,
        metadata_cache=metadata_cache,
        groq_api_key=groq_key,
    )

    headline_classifier = get_headline_classifier()
    # Ensure headline_classifier has an API key (it reads from env but let's be explicit)
    if not headline_classifier.api_key:
        headline_classifier.api_key = anthropic_key

    classifier = RetrospectiveClassifier(
        headline_classifier=headline_classifier,
        sector_classifier=sector_classifier,
    )

    files = _walk_recall_files(
        Path(args.recall_root),
        start=start,
        end=end,
        sessions=sessions,
    )
    print(f"Scanning {len(files)} session files from {start} to {end} (sessions={sessions})")
    print(f"Min excursion threshold: {args.min_excursion}%")
    print(f"Dry run: {args.dry_run}, force: {args.force}")
    print()

    totals = {k: 0 for k in ("records", "eligible", "already_classified", "classified", "failed")}
    for f in files:
        print(f"=== {f} ===")
        counters = await _process_file(
            f,
            classifier,
            dry_run=args.dry_run,
            force=args.force,
            min_excursion_pct=args.min_excursion,
        )
        for k, v in counters.items():
            totals[k] += v

    print()
    print("=== SUMMARY ===")
    print(f"  Files processed:       {len(files)}")
    print(f"  Records scanned:       {totals['records']}")
    print(f"  Eligible (>=10%):      {totals['eligible']}")
    print(f"  Already classified:    {totals['already_classified']}")
    print(f"  Newly classified:      {totals['classified']}")
    print(f"  Failed (no headline/error): {totals['failed']}")
    if args.dry_run:
        print(f"  (dry run — no files were modified)")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
