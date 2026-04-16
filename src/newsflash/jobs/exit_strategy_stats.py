"""
Exit Strategy Stats Job - Builds detailed and summary JSON files for exit strategy analysis.

Two output files in tmp/exit_strategy_stats/:
  - detailed.json: All individual 10%+ mid excursion samples organized by sector → industry → headline_type
    with headline text, strength score, return, MAE, market cap, etc. Grows over time.
  - summary.json: Aggregated stats by headline_type → strength_score → industry.
    Stays concise. Updated daily.

Headline strength scoring (1-10) via Claude Haiku runs nightly on new headlines.

Schedule: Nightly at 8 PM ET (after postmarket, part of _run_daily_analytics chain)
Can also be run manually: python -m src.newsflash.jobs.exit_strategy_stats
"""
import asyncio
import json
import os
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Only use data from April 7, 2026 onward (post-triage-overhaul)
DEFAULT_DATA_START = date(2026, 4, 7)
DEFAULT_LOOKBACK_DAYS = 14
MIN_MID_EXCURSION_PCT = 10.0

OUTPUT_DIR = Path("tmp/exit_strategy_stats")
DETAILED_FILE = OUTPUT_DIR / "detailed.json"
SUMMARY_FILE = OUTPUT_DIR / "summary.json"

# Haiku model for headline strength scoring
HAIKU_MODEL = "claude-haiku-4-5-20251001"

STRENGTH_SYSTEM_PROMPT = """\
You are a financial news headline strength scorer for a news trading system.

Given a headline and its classified type, rate the headline's MATERIAL IMPACT strength from 1-10.

Scoring criteria:
- 10: Transformative, massive dollar amounts, tier-1 government/defense contracts, blockbuster FDA approvals, mega-mergers
- 8-9: Very strong material impact, significant dollar amounts mentioned, major named partners, concrete deliverables
- 6-7: Solid material impact, moderate dollar amounts or meaningful partnerships, clear growth catalyst
- 4-5: Moderate impact, vague terms, no dollar amounts, generic partnerships, incremental progress
- 2-3: Weak impact, routine announcements dressed up as news, no concrete details
- 1: Minimal impact, filler headline, no trading relevance

Key factors that INCREASE score:
- Specific dollar amounts (especially large ones relative to market cap)
- Named tier-1 partners (government agencies, Fortune 500 companies)
- Concrete deliverables (not "exploring" or "planning")
- Regulatory milestones (FDA approval vs FDA submission)
- Exclusivity or competitive moat implications

Key factors that DECREASE score:
- Vague language ("strategic partnership", "exploring opportunities")
- No dollar amounts on contracts
- Small/unknown counterparties
- "Potential" or "expected" vs confirmed
- Routine operational updates

Respond with ONLY a single integer 1-10. Nothing else."""


async def score_headline_strength(
    headline: str,
    headline_type: str,
    api_key: Optional[str] = None,
) -> Optional[int]:
    """
    Score a headline's material impact strength (1-10) using Claude Haiku.

    Returns None if API key not available or call fails.
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None

    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=key, timeout=10.0)
        response = await client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=4,
            system=STRENGTH_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Headline type: {headline_type}\nHeadline: {headline}",
                }
            ],
        )

        text = response.content[0].text.strip()
        score = int(text)
        return max(1, min(10, score))
    except Exception as e:
        logger.warning(f"Headline strength scoring failed: {e}")
        return None


def _estimate_mid_excursion(record: Dict[str, Any]) -> Optional[float]:
    """Estimate mid-price max excursion from recall record."""
    nbbo = record.get("initial_nbbo")
    highest = record.get("highest_price_during_hold")
    if not nbbo or not highest:
        return None

    initial_mid = nbbo.get("mid")
    peak_price = highest.get("price")
    initial_spread = nbbo.get("spread", 0)
    if not initial_mid or not peak_price or initial_mid <= 0:
        return None

    estimated_peak_mid = peak_price - (initial_spread / 2)
    return ((estimated_peak_mid - initial_mid) / initial_mid) * 100


def _compute_mae_after_5s(record: Dict[str, Any]) -> Optional[float]:
    """
    Compute MAE (Max Adverse Excursion) that occurred after first 5 seconds.

    Returns the percent loss from entry, or None if MAE was within first 5s or missing.
    """
    mae = record.get("max_adverse_excursion")
    received_at = record.get("received_at")
    if not mae or not received_at:
        return None

    mae_ts = mae.get("timestamp")
    mae_pct = mae.get("percent_loss_from_entry")
    if not mae_ts or mae_pct is None:
        return None

    try:
        if isinstance(mae_ts, str):
            mae_dt = datetime.fromisoformat(mae_ts.replace("Z", "+00:00"))
        else:
            mae_dt = mae_ts

        if isinstance(received_at, str):
            recv_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        else:
            recv_dt = received_at

        delta_seconds = (mae_dt - recv_dt).total_seconds()

        if delta_seconds < 5:
            return None  # MAE was in first 5 seconds, ignore

        return round(mae_pct, 2)
    except (ValueError, TypeError):
        return None


def _time_to_peak_seconds(record: Dict[str, Any]) -> Optional[int]:
    """Extract time-to-peak in seconds."""
    highest = record.get("highest_price_during_hold")
    received_at = record.get("received_at")
    if not highest or not received_at:
        return None

    peak_ts = highest.get("timestamp")
    if not peak_ts:
        return None

    try:
        if isinstance(peak_ts, str):
            peak_dt = datetime.fromisoformat(peak_ts.replace("Z", "+00:00"))
        else:
            peak_dt = peak_ts

        if isinstance(received_at, str):
            recv_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        else:
            recv_dt = received_at

        delta = (peak_dt - recv_dt).total_seconds()
        return max(0, min(int(delta), 600))
    except (ValueError, TypeError):
        return None


def collect_samples(
    recall_base_path: Path,
    data_start: date,
    data_end: date,
) -> List[Dict[str, Any]]:
    """Collect premarket recall samples with 10%+ mid excursion."""
    samples = []

    for year_dir in sorted(recall_base_path.iterdir()):
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
                        day_num = int(day_dir.name)
                        year_num = int(year_dir.name)
                        month_num = int(month_dir.name)
                        record_date = date(year_num, month_num, day_num)
                    except (ValueError, TypeError):
                        continue

                    if record_date < data_start or record_date > data_end:
                        continue

                    premarket_file = day_dir / "premarket" / "premarket.json"
                    if not premarket_file.exists():
                        continue

                    try:
                        with open(premarket_file) as f:
                            data = json.load(f)
                    except (json.JSONDecodeError, OSError) as e:
                        logger.warning(f"Failed to read {premarket_file}: {e}")
                        continue

                    for rec in data.get("records", []):
                        headline_type = rec.get("headline_type")
                        if not headline_type:
                            continue

                        mid_excursion = _estimate_mid_excursion(rec)
                        if mid_excursion is None or mid_excursion < MIN_MID_EXCURSION_PCT:
                            continue

                        ttp = _time_to_peak_seconds(rec)
                        if ttp is None:
                            continue

                        price_check = rec.get("price_check_10min")
                        ten_min_mid = None
                        if price_check:
                            ten_min_mid = price_check.get("mid_price_change")
                            if ten_min_mid is None:
                                ten_min_mid = price_check.get("percent_change")
                        if ten_min_mid is None:
                            continue

                        mae_after_5s = _compute_mae_after_5s(rec)

                        # Extract metadata
                        tickers = rec.get("tickers", [])
                        ticker = tickers[0] if tickers else "?"
                        metadata = rec.get("ticker_metadata", {}).get(ticker, {})

                        samples.append({
                            "date": record_date.isoformat(),
                            "ticker": ticker,
                            "sector": metadata.get("sector", "Unknown"),
                            "industry": metadata.get("industry", "Unknown"),
                            "headline_type": headline_type,
                            "headline": rec.get("title", ""),
                            "strength_score": None,  # filled by Haiku
                            "market_cap_millions": metadata.get("market_cap_millions"),
                            "peak_gain_pct": round(mid_excursion, 2),
                            "time_to_peak_seconds": ttp,
                            "ten_min_outcome_pct": round(ten_min_mid, 2),
                            "fade_from_peak_pct": round(mid_excursion - ten_min_mid, 2),
                            "mae_after_5s_pct": mae_after_5s,
                            "initial_spread_pct": rec.get("initial_nbbo", {}).get("spread_pct"),
                            "article_id": rec.get("article_id", ""),
                        })

    logger.info(f"Collected {len(samples)} samples from {data_start} to {data_end}")
    return samples


async def score_samples(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score all samples that don't have a strength_score yet."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("No ANTHROPIC_API_KEY set, skipping headline strength scoring")
        return samples

    unscored = [s for s in samples if s.get("strength_score") is None]
    if not unscored:
        return samples

    logger.info(f"Scoring {len(unscored)} headlines with Claude Haiku")

    for s in unscored:
        score = await score_headline_strength(
            headline=s["headline"],
            headline_type=s["headline_type"],
            api_key=api_key,
        )
        s["strength_score"] = score
        # Small delay to avoid rate limits
        await asyncio.sleep(0.1)

    scored = sum(1 for s in unscored if s["strength_score"] is not None)
    logger.info(f"Scored {scored}/{len(unscored)} headlines")
    return samples


def build_detailed(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build detailed JSON organized: sector → industry → headline_type → records.
    """
    tree: Dict[str, Dict[str, Dict[str, List]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for s in samples:
        tree[s["sector"]][s["industry"]][s["headline_type"]].append({
            "date": s["date"],
            "ticker": s["ticker"],
            "headline": s["headline"],
            "strength_score": s["strength_score"],
            "market_cap_millions": s["market_cap_millions"],
            "peak_gain_pct": s["peak_gain_pct"],
            "time_to_peak_seconds": s["time_to_peak_seconds"],
            "ten_min_outcome_pct": s["ten_min_outcome_pct"],
            "fade_from_peak_pct": s["fade_from_peak_pct"],
            "mae_after_5s_pct": s["mae_after_5s_pct"],
            "initial_spread_pct": s["initial_spread_pct"],
        })

    # Convert defaultdicts to regular dicts for JSON
    result = {}
    for sector in sorted(tree):
        result[sector] = {}
        for industry in sorted(tree[sector]):
            result[sector][industry] = {}
            for ht in sorted(tree[sector][industry]):
                records = tree[sector][industry][ht]
                # Sort by date descending
                records.sort(key=lambda r: r["date"], reverse=True)
                result[sector][industry][ht] = records

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "total_samples": len(samples),
        "data": result,
    }


def _avg(values: List[float]) -> Optional[float]:
    """Average of non-None values, or None."""
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 2) if clean else None


def build_summary(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build summary JSON: headline_type → avg stats → by_strength → by_industry triangle.
    """
    # Group by headline_type
    by_type: Dict[str, List[Dict]] = defaultdict(list)
    for s in samples:
        by_type[s["headline_type"]].append(s)

    summary = {}

    for ht, type_samples in sorted(by_type.items()):
        peaks = [s["peak_gain_pct"] for s in type_samples]
        outcomes = [s["ten_min_outcome_pct"] for s in type_samples]
        fades = [s["fade_from_peak_pct"] for s in type_samples]
        maes = [s["mae_after_5s_pct"] for s in type_samples]
        times = [s["time_to_peak_seconds"] for s in type_samples]

        # By strength score
        by_strength: Dict[int, List[Dict]] = defaultdict(list)
        for s in type_samples:
            if s.get("strength_score") is not None:
                by_strength[s["strength_score"]].append(s)

        strength_breakdown = {}
        for score in sorted(by_strength):
            ss = by_strength[score]
            strength_breakdown[str(score)] = {
                "count": len(ss),
                "avg_peak_gain_pct": _avg([s["peak_gain_pct"] for s in ss]),
                "avg_ten_min_outcome_pct": _avg([s["ten_min_outcome_pct"] for s in ss]),
                "avg_mae_after_5s_pct": _avg([s["mae_after_5s_pct"] for s in ss]),
                "avg_fade_pct": _avg([s["fade_from_peak_pct"] for s in ss]),
                "avg_time_to_peak_seconds": _avg([s["time_to_peak_seconds"] for s in ss]),
            }

        # By industry (within this headline type)
        by_industry: Dict[str, List[Dict]] = defaultdict(list)
        for s in type_samples:
            by_industry[s["industry"]].append(s)

        industry_breakdown = {}
        for ind in sorted(by_industry):
            ind_samples = by_industry[ind]

            # Industry × strength triangle
            ind_by_strength: Dict[int, List[Dict]] = defaultdict(list)
            for s in ind_samples:
                if s.get("strength_score") is not None:
                    ind_by_strength[s["strength_score"]].append(s)

            ind_strength = {}
            for score in sorted(ind_by_strength):
                iss = ind_by_strength[score]
                ind_strength[str(score)] = {
                    "count": len(iss),
                    "avg_peak_gain_pct": _avg([s["peak_gain_pct"] for s in iss]),
                    "avg_mae_after_5s_pct": _avg([s["mae_after_5s_pct"] for s in iss]),
                }

            industry_breakdown[ind] = {
                "count": len(ind_samples),
                "avg_peak_gain_pct": _avg([s["peak_gain_pct"] for s in ind_samples]),
                "avg_ten_min_outcome_pct": _avg([s["ten_min_outcome_pct"] for s in ind_samples]),
                "avg_mae_after_5s_pct": _avg([s["mae_after_5s_pct"] for s in ind_samples]),
                "by_strength": ind_strength if ind_strength else None,
            }

        summary[ht] = {
            "sample_count": len(type_samples),
            "avg_peak_gain_pct": _avg(peaks),
            "avg_ten_min_outcome_pct": _avg(outcomes),
            "avg_fade_pct": _avg(fades),
            "avg_mae_after_5s_pct": _avg(maes),
            "avg_time_to_peak_seconds": _avg(times),
            "by_strength": strength_breakdown if strength_breakdown else None,
            "by_industry": industry_breakdown,
        }

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "total_samples": len(samples),
        "headline_types": summary,
    }


def save_files(
    detailed: Dict[str, Any],
    summary_data: Dict[str, Any],
    output_dir: Path = OUTPUT_DIR,
) -> tuple:
    """Save both JSON files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    detailed_file = output_dir / "detailed.json"
    summary_file = output_dir / "summary.json"

    with open(detailed_file, "w") as f:
        json.dump(detailed, f, indent=2)

    with open(summary_file, "w") as f:
        json.dump(summary_data, f, indent=2)

    logger.info(
        f"Saved exit strategy stats: {detailed_file} ({detailed['total_samples']} samples), "
        f"{summary_file} ({len(summary_data['headline_types'])} types)"
    )
    return detailed_file, summary_file


async def run_exit_strategy_stats(
    recall_base_path: Path = Path("tmp/statistics/recall"),
    output_dir: Path = OUTPUT_DIR,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    data_start_override: Optional[date] = None,
    skip_scoring: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Main entry point. Collects samples, scores headlines, builds both files.

    Args:
        recall_base_path: Root of recall statistics tree
        output_dir: Where to save the output files
        lookback_days: How many days back to scan
        data_start_override: Override start date
        skip_scoring: Skip Claude Haiku scoring (for testing)
    """
    today = date.today()
    if data_start_override:
        data_start = data_start_override
    else:
        # Expanding window: always start from DEFAULT_DATA_START (April 7)
        # so samples accumulate over time rather than dropping off
        data_start = DEFAULT_DATA_START
    data_end = today

    logger.info(f"Building exit strategy stats from {data_start} to {data_end}")

    # Collect raw samples
    samples = collect_samples(recall_base_path, data_start, data_end)
    if not samples:
        logger.warning("No samples found")
        return None

    # Load existing detailed file to preserve old strength scores
    existing_scores: Dict[str, int] = {}
    detailed_file = output_dir / "detailed.json"
    if detailed_file.exists():
        try:
            with open(detailed_file) as f:
                existing = json.load(f)
            for sector_data in existing.get("data", {}).values():
                for industry_data in sector_data.values():
                    for ht_records in industry_data.values():
                        for rec in ht_records:
                            # Key by headline text for matching
                            if rec.get("strength_score") is not None:
                                existing_scores[rec.get("headline", "")] = rec["strength_score"]
        except (json.JSONDecodeError, OSError):
            pass

    # Apply existing scores to avoid re-scoring
    unscored_count = 0
    for s in samples:
        if s["headline"] in existing_scores:
            s["strength_score"] = existing_scores[s["headline"]]
        else:
            unscored_count += 1

    logger.info(
        f"Samples: {len(samples)} total, {len(samples) - unscored_count} pre-scored, "
        f"{unscored_count} need scoring"
    )

    # Score new headlines
    if not skip_scoring and unscored_count > 0:
        samples = await score_samples(samples)

    # Build outputs
    detailed = build_detailed(samples)
    summary_data = build_summary(samples)

    save_files(detailed, summary_data, output_dir)

    return summary_data


# CLI entry point
if __name__ == "__main__":
    import sys

    skip = "--skip-scoring" in sys.argv
    lookback = DEFAULT_LOOKBACK_DAYS
    for arg in sys.argv[1:]:
        if arg.isdigit():
            lookback = int(arg)

    result = asyncio.run(
        run_exit_strategy_stats(lookback_days=lookback, skip_scoring=skip)
    )

    if result:
        print(f"\nSummary: {len(result['headline_types'])} headline types, {result['total_samples']} samples")
        for ht, stats in result["headline_types"].items():
            print(
                f"  {ht}: {stats['sample_count']}x | "
                f"peak {stats['avg_peak_gain_pct']}% | "
                f"10min {stats['avg_ten_min_outcome_pct']}% | "
                f"MAE {stats['avg_mae_after_5s_pct']}%"
            )
    else:
        print("No data generated.")
