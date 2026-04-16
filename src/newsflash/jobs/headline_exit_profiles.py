"""
Headline Exit Profile Job - Analyzes premarket recall data to build per-headline-type exit profiles.

Scans premarket recall records for articles with 10%+ mid-price excursion,
groups by headline_type, and computes statistical profiles showing typical
peak timing, peak magnitude, 10-minute outcome, and fade-from-peak.

Output is used for Telegram notifications during active trades — no automated exit changes.

Schedule: Nightly at 1:05 AM UK time (after postmarket close, after daily analytics at 1:01 AM)
Can also be run manually: python -m src.newsflash.jobs.headline_exit_profiles
"""
import json
import statistics
from dataclasses import dataclass, asdict, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Only use data from April 7, 2026 onward (post-triage-overhaul: all 38 headline types have criteria)
DEFAULT_DATA_START = date(2026, 4, 7)
DEFAULT_LOOKBACK_DAYS = 14
MIN_MID_EXCURSION_PCT = 10.0
MIN_SAMPLES_FOR_PROFILE = 2


@dataclass(frozen=True)
class HeadlineExitProfile:
    """Statistical exit profile for a single headline type, derived from premarket recall data."""

    headline_type: str
    sample_count: int

    # Peak behavior (mid-price based)
    median_peak_gain_pct: float  # typical peak from mid entry
    mean_peak_gain_pct: float
    min_peak_gain_pct: float
    max_peak_gain_pct: float

    # Timing
    median_time_to_peak_seconds: int  # when the peak usually happens
    mean_time_to_peak_seconds: int
    peak_timing_bucket: str  # "fast" (<30s), "medium" (30s-2m), "slow" (2-10m)

    # 10-minute outcome (mid-price based)
    median_10min_outcome_pct: float  # where mid price ends up at 10min
    mean_10min_outcome_pct: float

    # Fade analysis
    median_fade_from_peak_pct: float  # peak minus 10-min outcome (how much given back)
    mean_fade_from_peak_pct: float

    # Timing distribution (percentage of samples in each bucket)
    pct_peak_under_30s: float
    pct_peak_30s_to_2m: float
    pct_peak_2m_to_5m: float
    pct_peak_5m_to_10m: float

    # When this profile was last computed
    computed_at: str
    data_start_date: str
    data_end_date: str


@dataclass
class _RawSample:
    """Internal: one 10%+ mid excursion observation."""

    ticker: str
    headline_type: str
    mid_excursion_pct: float
    time_to_peak_seconds: int
    ten_min_mid_change_pct: float
    fade_from_peak_pct: float
    article_title: str
    date: str


def _estimate_mid_excursion(record: Dict[str, Any]) -> Optional[float]:
    """
    Estimate mid-price max excursion from recall record.

    Uses highest_price_during_hold.price adjusted by half the initial spread
    vs initial_nbbo.mid as the base.
    """
    nbbo = record.get("initial_nbbo")
    highest = record.get("highest_price_during_hold")

    if not nbbo or not highest:
        return None

    initial_mid = nbbo.get("mid")
    peak_price = highest.get("price")
    initial_spread = nbbo.get("spread", 0)

    if not initial_mid or not peak_price or initial_mid <= 0:
        return None

    # Approximate mid at peak: peak trade price minus half-spread
    # (peak price is typically the ask-side, so mid is lower)
    estimated_peak_mid = peak_price - (initial_spread / 2)

    return ((estimated_peak_mid - initial_mid) / initial_mid) * 100


def _time_to_peak_seconds(record: Dict[str, Any]) -> Optional[int]:
    """Extract time-to-peak in seconds from the highest_price_during_hold timestamps."""
    highest = record.get("highest_price_during_hold")
    if not highest:
        return None

    # Use minute/second fields if available
    minute = highest.get("minute")
    second = highest.get("second")
    if minute is not None and second is not None:
        # These are clock minute/second, need delta from entry
        # Fall back to timestamp comparison
        pass

    # Use timestamp delta from received_at
    peak_ts = highest.get("timestamp")
    received_at = record.get("received_at")
    if not peak_ts or not received_at:
        return None

    try:
        # Handle timezone-aware timestamps
        if isinstance(peak_ts, str):
            peak_dt = datetime.fromisoformat(peak_ts.replace("Z", "+00:00"))
        else:
            peak_dt = peak_ts

        if isinstance(received_at, str):
            recv_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        else:
            recv_dt = received_at

        delta = (peak_dt - recv_dt).total_seconds()
        # Clamp to 0-600 (10 minutes)
        return max(0, min(int(delta), 600))
    except (ValueError, TypeError):
        return None


def _peak_timing_bucket(seconds: int) -> str:
    if seconds < 30:
        return "fast"
    elif seconds < 120:
        return "medium"
    else:
        return "slow"


def _classify_timing_distribution(times: List[int]) -> dict:
    """Compute percentage of samples in each timing bucket."""
    n = len(times)
    if n == 0:
        return {"under_30s": 0, "30s_to_2m": 0, "2m_to_5m": 0, "5m_to_10m": 0}

    under_30 = sum(1 for t in times if t < 30)
    s30_to_2m = sum(1 for t in times if 30 <= t < 120)
    m2_to_5m = sum(1 for t in times if 120 <= t < 300)
    m5_to_10m = sum(1 for t in times if t >= 300)

    return {
        "under_30s": round(under_30 / n * 100, 1),
        "30s_to_2m": round(s30_to_2m / n * 100, 1),
        "2m_to_5m": round(m2_to_5m / n * 100, 1),
        "5m_to_10m": round(m5_to_10m / n * 100, 1),
    }


def collect_premarket_samples(
    recall_base_path: Path,
    data_start: date,
    data_end: date,
    min_excursion_pct: float = MIN_MID_EXCURSION_PCT,
) -> List[_RawSample]:
    """
    Walk premarket recall files and collect samples with 10%+ mid excursion.

    Only reads premarket session files (97% headline_type coverage).
    Only reads data from data_start onward (post-triage-overhaul accuracy).
    """
    samples: List[_RawSample] = []

    # Walk year/month/week/day structure
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

                    # Parse the day from directory name
                    try:
                        day_num = int(day_dir.name)
                        year_num = int(year_dir.name)
                        month_num = int(month_dir.name)
                        record_date = date(year_num, month_num, day_num)
                    except (ValueError, TypeError):
                        continue

                    # Filter to date range
                    if record_date < data_start or record_date > data_end:
                        continue

                    # Only premarket
                    premarket_file = day_dir / "premarket" / "premarket.json"
                    if not premarket_file.exists():
                        continue

                    try:
                        with open(premarket_file) as f:
                            data = json.load(f)
                    except (json.JSONDecodeError, OSError) as e:
                        logger.warning(f"Failed to read {premarket_file}: {e}")
                        continue

                    records = data.get("records", [])
                    for rec in records:
                        headline_type = rec.get("headline_type")
                        if not headline_type:
                            continue

                        # Compute mid excursion
                        mid_excursion = _estimate_mid_excursion(rec)
                        if mid_excursion is None or mid_excursion < min_excursion_pct:
                            continue

                        # Time to peak
                        ttp = _time_to_peak_seconds(rec)
                        if ttp is None:
                            continue

                        # 10-min outcome (mid-based)
                        price_check = rec.get("price_check_10min")
                        ten_min_mid = None
                        if price_check:
                            ten_min_mid = price_check.get("mid_price_change")

                        if ten_min_mid is None:
                            # Fall back to percent_change if mid_price_change missing
                            if price_check:
                                ten_min_mid = price_check.get("percent_change")
                        if ten_min_mid is None:
                            continue

                        fade = mid_excursion - ten_min_mid

                        samples.append(
                            _RawSample(
                                ticker=rec.get("tickers", ["?"])[0],
                                headline_type=headline_type,
                                mid_excursion_pct=round(mid_excursion, 2),
                                time_to_peak_seconds=ttp,
                                ten_min_mid_change_pct=round(ten_min_mid, 2),
                                fade_from_peak_pct=round(fade, 2),
                                article_title=rec.get("title", ""),
                                date=record_date.isoformat(),
                            )
                        )

    logger.info(
        f"Collected {len(samples)} samples with {min_excursion_pct}%+ mid excursion "
        f"from {data_start} to {data_end}"
    )
    return samples


def build_profiles(
    samples: List[_RawSample],
    min_samples: int = MIN_SAMPLES_FOR_PROFILE,
    data_start: date = None,
    data_end: date = None,
) -> Dict[str, HeadlineExitProfile]:
    """Group samples by headline_type and compute statistical profiles."""
    from collections import defaultdict

    by_type: Dict[str, List[_RawSample]] = defaultdict(list)
    for s in samples:
        by_type[s.headline_type].append(s)

    profiles: Dict[str, HeadlineExitProfile] = {}
    now_str = datetime.utcnow().isoformat()

    for ht, type_samples in sorted(by_type.items()):
        if len(type_samples) < min_samples:
            logger.info(
                f"Skipping {ht}: only {len(type_samples)} samples (need {min_samples})"
            )
            continue

        peaks = [s.mid_excursion_pct for s in type_samples]
        times = [s.time_to_peak_seconds for s in type_samples]
        outcomes = [s.ten_min_mid_change_pct for s in type_samples]
        fades = [s.fade_from_peak_pct for s in type_samples]

        median_ttp = int(statistics.median(times))
        timing_dist = _classify_timing_distribution(times)

        profiles[ht] = HeadlineExitProfile(
            headline_type=ht,
            sample_count=len(type_samples),
            median_peak_gain_pct=round(statistics.median(peaks), 2),
            mean_peak_gain_pct=round(statistics.mean(peaks), 2),
            min_peak_gain_pct=round(min(peaks), 2),
            max_peak_gain_pct=round(max(peaks), 2),
            median_time_to_peak_seconds=median_ttp,
            mean_time_to_peak_seconds=int(statistics.mean(times)),
            peak_timing_bucket=_peak_timing_bucket(median_ttp),
            median_10min_outcome_pct=round(statistics.median(outcomes), 2),
            mean_10min_outcome_pct=round(statistics.mean(outcomes), 2),
            median_fade_from_peak_pct=round(statistics.median(fades), 2),
            mean_fade_from_peak_pct=round(statistics.mean(fades), 2),
            pct_peak_under_30s=timing_dist["under_30s"],
            pct_peak_30s_to_2m=timing_dist["30s_to_2m"],
            pct_peak_2m_to_5m=timing_dist["2m_to_5m"],
            pct_peak_5m_to_10m=timing_dist["5m_to_10m"],
            computed_at=now_str,
            data_start_date=data_start.isoformat() if data_start else "",
            data_end_date=data_end.isoformat() if data_end else "",
        )

    return profiles


def save_profiles(
    profiles: Dict[str, HeadlineExitProfile],
    output_path: Path,
) -> Path:
    """Save profiles to JSON file."""
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / "headline_exit_profiles.json"

    data = {
        "generated_at": datetime.utcnow().isoformat(),
        "profile_count": len(profiles),
        "profiles": {ht: asdict(p) for ht, p in profiles.items()},
    }

    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)

    logger.info(f"Saved {len(profiles)} headline exit profiles to {output_file}")
    return output_file


def load_profiles(output_path: Path) -> Dict[str, HeadlineExitProfile]:
    """Load profiles from JSON file. Returns empty dict if file doesn't exist."""
    output_file = output_path / "headline_exit_profiles.json"
    if not output_file.exists():
        return {}

    with open(output_file) as f:
        data = json.load(f)

    profiles = {}
    for ht, p in data.get("profiles", {}).items():
        profiles[ht] = HeadlineExitProfile(**p)

    return profiles


def print_profiles_table(profiles: Dict[str, HeadlineExitProfile]) -> str:
    """Format profiles as a readable table for logging/Telegram."""
    if not profiles:
        return "No headline exit profiles available."

    lines = [
        f"{'Type':<28} {'N':>3} {'Peak':>6} {'@Time':>6} {'10min':>6} {'Fade':>6} {'Bucket':<7}",
        "-" * 75,
    ]

    for ht, p in sorted(profiles.items(), key=lambda x: -x[1].median_peak_gain_pct):
        time_str = (
            f"{p.median_time_to_peak_seconds}s"
            if p.median_time_to_peak_seconds < 60
            else f"{p.median_time_to_peak_seconds // 60}m{p.median_time_to_peak_seconds % 60:02d}s"
        )
        lines.append(
            f"{ht:<28} {p.sample_count:>3} "
            f"{p.median_peak_gain_pct:>5.1f}% "
            f"{time_str:>6} "
            f"{p.median_10min_outcome_pct:>5.1f}% "
            f"{p.median_fade_from_peak_pct:>5.1f}% "
            f"{p.peak_timing_bucket:<7}"
        )

    return "\n".join(lines)


def run_headline_exit_profiles(
    recall_base_path: Path = Path("tmp/statistics/recall"),
    output_path: Path = Path("tmp/statistics/headline_exit_profiles"),
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    data_start_override: Optional[date] = None,
) -> Dict[str, HeadlineExitProfile]:
    """
    Main entry point. Collects premarket recall data, builds profiles, saves to disk.

    Args:
        recall_base_path: Root of recall statistics tree
        output_path: Where to save the profiles JSON
        lookback_days: How many days back to scan (from today)
        data_start_override: Override the start date (default: max of DEFAULT_DATA_START and today - lookback_days)
    """
    today = date.today()

    if data_start_override:
        data_start = data_start_override
    else:
        # Expanding window: always start from DEFAULT_DATA_START (April 7)
        # so samples accumulate over time toward 150+ per headline type
        data_start = DEFAULT_DATA_START

    data_end = today

    logger.info(
        f"Building headline exit profiles from premarket data: {data_start} to {data_end}"
    )

    samples = collect_premarket_samples(
        recall_base_path=recall_base_path,
        data_start=data_start,
        data_end=data_end,
    )

    if not samples:
        logger.warning("No samples found with 10%+ mid excursion in date range")
        return {}

    profiles = build_profiles(
        samples=samples,
        data_start=data_start,
        data_end=data_end,
    )

    save_profiles(profiles, output_path)

    table = print_profiles_table(profiles)
    logger.info(f"Headline Exit Profiles:\n{table}")

    return profiles


# CLI entry point
if __name__ == "__main__":
    import sys

    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOOKBACK_DAYS
    profiles = run_headline_exit_profiles(lookback_days=lookback)

    if profiles:
        print(f"\n{print_profiles_table(profiles)}")
        print(f"\nTotal profiles: {len(profiles)}")
    else:
        print("No profiles generated. Check data availability.")
