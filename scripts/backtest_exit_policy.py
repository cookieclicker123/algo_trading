"""
Phase-0 EXIT-policy backtest. The entry is validated; this asks: given the trades
the pipeline takes, which EXIT policy captures the most of the move?

Compares, on the recorded trajectories (peak%+time, MAE%+time, 10-min%):
  hold_10min     : do nothing, exit at the 10-minute mark (the 'greedy hold' baseline)
  exit_peak      : exit exactly at the high (unrealizable CEILING — the most you could get)
  trail_5 / _10  : trailing stop — give back N% from the high, lock the rest
  profile_zone   : take profit at the headline TYPE's median peak (the statistical zone)
  quality_cond   : the user's rule — FAST-FADE types trail tight & take fast; FORGIVING
                   types trail loose & give room. (Quality proxied by the type's fade
                   profile; within-type 'great vs average' needs the strength score later.)

Stop model (consistent across policies): stop = 12% high-signal / 5% else; if drawdown
breached the stop BEFORE the peak, it's a stop-out at -stop.
"""
import json, glob, datetime as dt
from collections import defaultdict
from statistics import mean, median

import newsflash.services.brokerage.auto_trade as at

HC, AIB, CLIN = at.HIGH_CONVICTION_HEADLINE_TYPES, at.AI_BREAKTHROUGH_HEADLINE_TYPES, at.CLINICAL_BREAKTHROUGH_HEADLINE_TYPES
WINDOW_START = dt.date(2026, 4, 26)


def _ts(o):
    try:
        return dt.datetime.fromisoformat((o or {}).get("timestamp", "").replace("Z", "+00:00"))
    except Exception:
        return None


def load():
    rows = []
    for f in glob.glob("tmp/statistics/recall/**/*.json", recursive=True):
        if "backup" in f:
            continue
        try:
            d = json.load(open(f)); fdate = dt.date.fromisoformat(d.get("date"))
        except Exception:
            continue
        if fdate < WINDOW_START:
            continue
        for r in d.get("records", []):
            if r.get("ai_classification") != "imminent" or r.get("filter_reason") is not None:
                continue
            h = r.get("highest_price_during_hold") or {}
            a = r.get("max_adverse_excursion") or {}
            pc = r.get("price_check_10min") or {}
            peak = h.get("percent_gain_from_entry")
            tenmin = pc.get("percent_change")
            if peak is None or tenmin is None:
                continue
            rows.append({
                "type": r.get("headline_type") or "other",
                "peak": peak, "peak_t": _ts(h),
                "mae": a.get("percent_loss_from_entry"), "mae_t": _ts(a),
                "tenmin": tenmin,
                "stop": 12.0 if (r.get("headline_type") in HC or r.get("headline_type") in AIB
                                 or r.get("headline_type") in CLIN) else 5.0,
            })
    return rows


def type_fade_profiles(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["type"]].append(r)
    prof = {}
    for t, rs in by.items():
        peaks = [x["peak"] for x in rs]
        fades = [x["peak"] - x["tenmin"] for x in rs]
        prof[t] = {"n": len(rs), "med_peak": median(peaks), "med_fade": median(fades)}
    return prof


def capture(r, policy, prof, trail=None):
    p, m, tm, stop = r["peak"], r["mae"], r["tenmin"], r["stop"]
    stopped_first = m is not None and m <= -stop and (r["mae_t"] and r["peak_t"] and r["mae_t"] < r["peak_t"])
    if stopped_first:
        return -stop
    breached = m is not None and m <= -stop  # breached after the peak (a held trade stops on the fade)

    def held():
        return -stop if breached else tm

    if policy == "hold_10min":
        return held()
    if policy == "exit_peak":
        return p  # ceiling
    if policy == "trail":
        if p - tm >= trail:           # price fell >= trail from the high -> trailing stop hit
            return max(p - trail, -stop)
        return held()
    if policy == "profile_zone":
        z = prof.get(r["type"], {}).get("med_peak", p)
        return min(p, z) if p >= z else held()   # take profit at the type's typical peak
    if policy == "quality_cond":
        pr = prof.get(r["type"], {})
        fade = pr.get("med_fade", 12)
        t = 4.0 if fade >= 15 else (12.0 if fade <= 8 else 7.0)  # fast-fade tight, forgiving loose
        if p - tm >= t:
            return max(p - t, -stop)
        return held()
    raise ValueError(policy)


def stats(name, vals):
    w = [v for v in vals if v > 0]
    print(f"  {name:<16}{mean(vals):>+7.2f}%  total {sum(vals):>+6.0f}%  win {100*len(w)/len(vals):>3.0f}%")


def main():
    rows = load()
    prof = type_fade_profiles(rows)
    print(f"window {WINDOW_START}..2026-06-09   trades with trajectory: {len(rows)}\n")
    print("AVG CAPTURE per trade by exit policy (higher = better; vs the 'greedy hold' baseline):")
    stats("hold_10min", [capture(r, "hold_10min", prof) for r in rows])
    stats("trail_5", [capture(r, "trail", prof, 5) for r in rows])
    stats("trail_10", [capture(r, "trail", prof, 10) for r in rows])
    stats("profile_zone", [capture(r, "profile_zone", prof) for r in rows])
    stats("quality_cond", [capture(r, "quality_cond", prof) for r in rows])
    stats("exit_peak*", [capture(r, "exit_peak", prof) for r in rows])
    print("  (* exit_peak is the unrealizable ceiling — the gap to it is the cost of imperfect exits)")

    faders = [r for r in rows if r["peak"] >= 10 and r["tenmin"] <= 0]
    print(f"\nFADED WINNERS (peak>=10% but RED by 10-min — your 'should've taken it' trades): {len(faders)}")
    if faders:
        print(f"  held-to-10min on these: {mean([capture(r,'hold_10min',prof) for r in faders]):+.1f}%/trade")
        print(f"  trail_5 on these:       {mean([capture(r,'trail',prof,5) for r in faders]):+.1f}%/trade")
        print(f"  their median peak was:  +{median([r['peak'] for r in faders]):.0f}%  (this is what early-taking harvests)")


if __name__ == "__main__":
    main()
