"""Backfill late_trade_candidate flags on historical recall files.

Walks recall JSONs and re-stamps `late_trade_candidate` using the new logic:
  - postfilter starts with activity_gate OR no_strength_or_surge_or_late
  - peak gain (highest_price_during_hold.percent_gain_from_entry) >= 5%
Existing flags are overwritten so the new schema (peak_gain_pct,
time_to_peak_seconds, block_reason, etc.) replaces the old shape.

Usage:
  python scripts/backfill_late_trade_candidates.py           # all files
  python scripts/backfill_late_trade_candidates.py 2026-05-22  # one date
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RECALL_ROOT = Path("/Users/seb/dev/newsflash/tmp/statistics/recall")
MIN_PEAK_PCT = 5.0
BLOCK_PREFIXES = (
    "postfilter_activity_gate",
    "postfilter_no_strength_or_surge_or_late",
)


def parse_iso(s: str) -> datetime | None:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def build_ltc(record: dict) -> dict | None:
    postfilter = record.get("postfilter_reason") or ""
    if not any(postfilter.startswith(p) for p in BLOCK_PREFIXES):
        return None
    highest = record.get("highest_price_during_hold") or {}
    peak_gain = highest.get("percent_gain_from_entry")
    if peak_gain is None or peak_gain < MIN_PEAK_PCT:
        return None

    block_reason = next(
        (p.replace("postfilter_", "") for p in BLOCK_PREFIXES if postfilter.startswith(p)),
        "unknown",
    )
    telemetry = postfilter.split(":", 1)[1].strip() if ":" in postfilter else ""

    pub = parse_iso(record.get("published_at") or "")
    recv = parse_iso(record.get("received_at") or "")
    peak_ts = parse_iso(highest.get("timestamp") or "")

    pub_to_recv_s = round((recv - pub).total_seconds(), 2) if pub and recv else None
    time_to_peak_s = round((peak_ts - pub).total_seconds(), 1) if pub and peak_ts else None

    ten_min = (record.get("price_check_10min") or {}).get("percent_change")

    return {
        "peak_gain_pct": round(peak_gain, 2),
        "time_to_peak_seconds": time_to_peak_s,
        "ten_min_gain_pct": round(ten_min, 2) if ten_min is not None else None,
        "block_reason": block_reason,
        "block_telemetry": telemetry,
        "monitoring_status": record.get("monitoring_status"),
        "headline_type": record.get("headline_type"),
        "pub_to_recv_seconds": pub_to_recv_s,
    }


def backfill_file(path: Path) -> tuple[int, int]:
    """Returns (flagged_count, total_records)."""
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(f"  skip {path}: {e}")
        return (0, 0)

    records = data.get("records", [])
    flagged = 0
    for r in records:
        ltc = build_ltc(r)
        if ltc is not None:
            r["late_trade_candidate"] = ltc
            flagged += 1
        elif r.get("late_trade_candidate"):
            r["late_trade_candidate"] = None

    if "summary" in data:
        data["summary"]["late_trade_candidates"] = flagged

    path.write_text(json.dumps(data, indent=2, default=str))
    return (flagged, len(records))


def main() -> None:
    date_filter = sys.argv[1] if len(sys.argv) > 1 else None
    total_flagged = 0
    files_touched = 0
    for p in sorted(RECALL_ROOT.rglob("*.json")):
        if date_filter and date_filter not in str(p):
            continue
        flagged, total = backfill_file(p)
        if flagged:
            print(f"{p.relative_to(RECALL_ROOT)}: {flagged}/{total} flagged")
            total_flagged += flagged
        files_touched += 1
    print(f"\nDone. {files_touched} files, {total_flagged} late-trade candidates flagged.")


if __name__ == "__main__":
    main()
