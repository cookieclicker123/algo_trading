"""Bin recall data by time-to-peak and report outcome quality per bin.

Settles the question: would extending the surge-monitoring window beyond the
current 2 minutes catch profitable trades, or would it mostly add losers?

Methodology:
  - Walk all recall JSON files.
  - For untraded articles with ai_classification == "imminent" (the population
    we'd plausibly trade if surge fires), compute time_to_peak_seconds:
        peak_timestamp - published_at
  - Bin by time_to_peak:  0-30s, 30-120s, 120-300s, 300-600s, 600+s, no_move
  - Per bin: count, median peak gain, median MAE, median 10-min PnL,
    % "stop survivors" (peak >= 5% AND MAE > -5% = would have caught move
    with a 5% stop), % positive at 10-min check.
  - Repeat filtered to HC types only (ai_breakthrough, government_contract,
    military_contract, defense_order, major_contract, merger_agreement,
    stock_buyback, ai_rebranding).

If 120-600s bins have similar stop-survivor rates and median 10-min PnL to
0-120s bins, extending the monitoring window is safe. If they're worse,
the late-surge population is a different (lower-quality) population and
extending costs more than it earns.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

RECALL_ROOT = Path("/Users/seb/dev/newsflash/tmp/statistics/recall")
LOOKBACK_DAYS = 60  # widen the window to get a meaningful sample
HC_TYPES = {
    "ai_breakthrough",
    "government_contract",
    "military_contract",
    "defense_order",
    "major_contract",
    "merger_agreement",
    "stock_buyback",
    "ai_rebranding",
}
BINS = [
    ("0-30s", 0, 30),
    ("30-120s", 30, 120),
    ("120-300s", 120, 300),
    ("300-600s", 300, 600),
    ("600s+", 600, 10_000_000),
]


def parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iter_recall_files(cutoff: datetime) -> Iterable[Path]:
    for p in RECALL_ROOT.rglob("*.json"):
        # recall/YYYY/MM/week_N/DD/session/session.json
        # parents[0]=session, [1]=DD, [2]=week_N, [3]=MM, [4]=YYYY
        try:
            day = p.parents[1].name
            month = p.parents[3].name
            year = p.parents[4].name
            d = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue
        if d >= cutoff:
            yield p


def extract_rows(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    rows = []
    for art in data.get("records", []):
        if art.get("is_traded"):
            continue
        if art.get("ai_classification") != "imminent":
            continue
        peak = art.get("highest_price_during_hold") or {}
        mae = art.get("max_adverse_excursion") or {}
        chk = art.get("price_check_10min") or {}
        pub = art.get("published_at")
        if not pub:
            continue
        peak_ts = peak.get("timestamp")
        peak_gain = peak.get("percent_gain_from_entry")
        mae_loss = mae.get("percent_loss_from_entry")
        ten_min = chk.get("percent_change")
        try:
            time_to_peak = (
                (parse_iso(peak_ts) - parse_iso(pub)).total_seconds()
                if peak_ts
                else None
            )
        except Exception:
            time_to_peak = None
        rows.append(
            {
                "ticker": (art.get("tickers") or [None])[0],
                "headline_type": art.get("headline_type"),
                "time_to_peak": time_to_peak,
                "peak_gain": peak_gain,
                "mae": mae_loss,
                "ten_min_pnl": ten_min,
                "monitoring_status": art.get("monitoring_status"),
                "surge_detected": art.get("surge_detected_at") is not None,
            }
        )
    return rows


def summarize(rows: list[dict], label: str) -> None:
    print(f"\n=== {label}  (n={len(rows)}) ===")
    print(
        f"{'bin':<10} {'n':>5} {'med_peak':>9} {'med_mae':>9} "
        f"{'med_10m':>9} {'stop_surv%':>11} {'10m_pos%':>9} {'surge_caught%':>14}"
    )
    no_move = []
    bins: dict[str, list[dict]] = {b[0]: [] for b in BINS}
    for r in rows:
        ttp = r.get("time_to_peak")
        peak = r.get("peak_gain")
        if ttp is None or peak is None or peak < 1.0:
            no_move.append(r)
            continue
        for name, lo, hi in BINS:
            if lo <= ttp < hi:
                bins[name].append(r)
                break

    def med(vals: list[float | None]) -> str:
        clean = [v for v in vals if v is not None]
        return f"{statistics.median(clean):.2f}" if clean else "  n/a"

    def pct(num: int, denom: int) -> str:
        return f"{(100 * num / denom):.1f}%" if denom else "  n/a"

    for name, _, _ in BINS:
        b = bins[name]
        n = len(b)
        peak_vals = [r["peak_gain"] for r in b]
        mae_vals = [r["mae"] for r in b]
        tm_vals = [r["ten_min_pnl"] for r in b]
        stop_survivors = sum(
            1
            for r in b
            if (r.get("peak_gain") or 0) >= 5.0
            and (r.get("mae") or -100) > -5.0
        )
        ten_min_pos = sum(1 for r in b if (r.get("ten_min_pnl") or 0) > 0)
        surge_caught = sum(1 for r in b if r.get("surge_detected"))
        print(
            f"{name:<10} {n:>5} {med(peak_vals):>9} {med(mae_vals):>9} "
            f"{med(tm_vals):>9} {pct(stop_survivors, n):>11} "
            f"{pct(ten_min_pos, n):>9} {pct(surge_caught, n):>14}"
        )
    print(f"{'no_move':<10} {len(no_move):>5}  (peak < 1% or missing timestamp)")


def main() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    print(f"Recall data since {cutoff.date()} ({LOOKBACK_DAYS}-day window)")

    all_rows: list[dict] = []
    for p in iter_recall_files(cutoff):
        all_rows.extend(extract_rows(p))

    summarize(all_rows, "ALL imminent (untraded)")

    hc_rows = [r for r in all_rows if r["headline_type"] in HC_TYPES]
    summarize(hc_rows, "HC types only")

    ai_rows = [r for r in all_rows if r["headline_type"] == "ai_breakthrough"]
    summarize(ai_rows, "ai_breakthrough only")

    # Sanity: show per-headline-type sample sizes
    print("\nSample sizes by headline_type (top 15):")
    counts: dict[str, int] = {}
    for r in all_rows:
        counts[r["headline_type"] or "_none"] = counts.get(r["headline_type"] or "_none", 0) + 1
    for ht, c in sorted(counts.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {ht:<35} {c}")


if __name__ == "__main__":
    main()
