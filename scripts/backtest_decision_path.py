"""
45-day backtest of the CURRENT decision path (entry_gate + postfilter_engine),
scored with a stop simulated on the recorded price PATH (peak vs MAE timing).

Method
------
- Population: recall records over the window with ai_classification == "imminent"
  and no prefilter block (i.e. the AI said trade-worthy and it reached the decision).
  We use the classification the system ACTUALLY made — the new HC entity prompts
  would only ADD winners (AIM-class), so this is a conservative floor.
- For each, reconstruct inputs and run the REAL engine + gate (single source of
  truth that production runs). recv_to_fill / pump / fill_spread are evaluated when
  recorded, else treated as pass (noted).
- P&L: stop_pct = 12% for high-signal (HC / ai_breakthrough / clinical) else 5%.
  Conservative stop model: if the recorded max-adverse-excursion ever breached the
  stop, the trade is a stop-out at -stop_pct (we never see the later peak — the
  DGNX lesson). Otherwise the trade exits at the 10-minute mark (percent_change).
"""
import json, glob, datetime as dt
from statistics import mean

import newsflash.services.brokerage.auto_trade as at
from newsflash.services.brokerage.entry_gate import evaluate_strength_gate
from newsflash.services.brokerage.postfilter_engine import (
    PostfilterInputs, evaluate_microstructure_postfilters,
)

HC = at.HIGH_CONVICTION_HEADLINE_TYPES
AIB = at.AI_BREAKTHROUGH_HEADLINE_TYPES
CLIN = at.CLINICAL_BREAKTHROUGH_HEADLINE_TYPES
WINDOW_START = dt.date(2026, 4, 26)   # ~45 days before 2026-06-10


def _get(r, k, default=None):
    v = r.get(k)
    return v if v is not None else default


def would_trade(r):
    """Return (trade: bool, block_reason: str|None) under the CURRENT pipeline."""
    htype = r.get("headline_type")
    is_hc = htype in HC
    is_aib = htype in AIB
    is_clin = htype in CLIN

    score = _get(r, "confluence_score", 0) or 0
    exc = _get(r, "confluence_price_excursion_pct", 0.0) or 0.0
    trades = _get(r, "confluence_trade_count", 0) or 0
    nbbo = r.get("initial_nbbo") or {}
    initial_spread = nbbo.get("spread")
    initial_ask = nbbo.get("ask")

    # ENTRY GATE (pure) + surge fallback (recorded monitoring_status)
    g = evaluate_strength_gate(score, exc, trades, is_hc, initial_spread, initial_ask)
    surged = r.get("monitoring_status") == "surge_detected"
    if g.entry_reason is None and not surged:
        return False, "gate_no_strength_or_surge"

    # MICROSTRUCTURE ENGINE — reconstruct what we can; recompute pub_to_recv from the
    # recorded value at the real price scale so the $0.05 absolute floor is respected.
    # The TRUE pub_to_recv lives in the postfilter_reason string for blocked records
    # (the pub_to_recv_pct field is the confluence-window value ≈0). For records not
    # blocked by it, it passed -> None (skip).
    pubrcv = None
    _pf = r.get("postfilter_reason") or ""
    if _pf.startswith("postfilter_pub_to_recv"):
        import re as _re
        m = _re.search(r"([\d.]+)%", _pf)
        if m:
            pubrcv = float(m.group(1))
    pub_ask = _get(r, "pub_time_ask") or initial_ask or 1.0
    recv_ask = pub_ask * (1 + (pubrcv or 0) / 100.0) if pubrcv is not None else None
    fill_pct = _get(r, "fill_spread_pct")
    inp = PostfilterInputs(
        confluence_score=score,
        is_high_conviction=is_hc,
        is_ai_breakthrough=is_aib,
        confluence_imbalance_ratio=_get(r, "confluence_imbalance_ratio"),
        initial_spread_pct=(nbbo.get("spread_pct")),
        fill_spread_samples_pct=[fill_pct] if fill_pct is not None else [],
        pub_time_ask=pub_ask if pubrcv is not None else None,
        recv_ask=recv_ask,
        entry_reference_price=initial_ask,
        confluence_max_price=_get(r, "confluence_max_price"),
    )
    d = evaluate_microstructure_postfilters(inp)
    return d.passed, d.reason


def _ts(o):
    try:
        return dt.datetime.fromisoformat((o or {}).get("timestamp", "").replace("Z", "+00:00"))
    except Exception:
        return None


def pnl_models(r):
    """Return dict of realized P&L under 3 exit/stop models, or None if no outcome.
      conservative : stop on ANY MAE breach (no wick tolerance) -> exit 10min      (lower bound)
      path         : stop only if MAE breached BEFORE the peak  -> exit 10min       (mid)
      peak_capture : stop only if breached-before-peak, else capture the full peak  (upper bound)
    """
    htype = r.get("headline_type")
    stop = 12.0 if (htype in HC or htype in AIB or htype in CLIN) else 5.0
    h = r.get("highest_price_during_hold") or {}
    a = r.get("max_adverse_excursion") or {}
    peak = h.get("percent_gain_from_entry")
    mae = a.get("percent_loss_from_entry")
    tenmin = (r.get("price_check_10min") or {}).get("percent_change")
    if tenmin is None and peak is None:
        return None
    tenmin = tenmin if tenmin is not None else 0.0
    peak = peak if peak is not None else 0.0
    breached = mae is not None and mae <= -stop
    pt, mt = _ts(h), _ts(a)
    breached_before_peak = breached and (pt is None or mt is None or mt < pt)
    return {
        "conservative": -stop if breached else tenmin,
        "path": -stop if breached_before_peak else tenmin,
        "peak_capture": -stop if breached_before_peak else peak,
    }


def main():
    files = [f for f in glob.glob("tmp/statistics/recall/**/*.json", recursive=True) if "backup" not in f]
    taken, models = [], {"conservative": [], "path": [], "peak_capture": []}
    pub_rule = {"conservative": [], "path": [], "peak_capture": []}
    n_records = 0
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        try:
            fdate = dt.date.fromisoformat(d.get("date"))
        except Exception:
            continue
        if fdate < WINDOW_START:
            continue
        for r in d.get("records", []):
            if (r.get("ai_classification") != "imminent") or (r.get("filter_reason") is not None):
                continue
            n_records += 1
            trade, _ = would_trade(r)
            if not trade:
                continue
            m = pnl_models(r)
            if m is None:
                continue
            taken.append(r)
            for k in models:
                models[k].append(m[k])
            # pub_to_recv 15% rule marginal set: newly traded because runup>7.5% & conf>=4 & <=15%
            pf = r.get("postfilter_reason") or ""
            if pf.startswith("postfilter_pub_to_recv") and (r.get("confluence_score") or 0) >= 4:
                for k in pub_rule:
                    pub_rule[k].append(m[k])

    days = (dt.date(2026, 6, 9) - WINDOW_START).days + 1
    print(f"WINDOW: {WINDOW_START} .. 2026-06-09  ({days} days)")
    print(f"imminent records reaching decision: {n_records}")
    print(f"TRADES TAKEN (current pipeline): {len(taken)}  ({len(taken)/days:.2f}/day)\n")
    print(f"{'EXIT/STOP MODEL':<16}{'win%':>6}{'avgWin':>8}{'avgLoss':>9}{'EXPECT':>8}{'TOTAL':>8}")
    for k in ("conservative", "path", "peak_capture"):
        p = models[k]
        w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
        print(f"{k:<16}{100*len(w)/len(p):>5.0f}%{(mean(w) if w else 0):>+7.1f}%{(mean(l) if l else 0):>+8.1f}%{mean(p):>+7.2f}%{sum(p):>+7.0f}%")
    print("\n  (truth lies between 'path' and 'peak_capture' depending on the real dynamic-exit logic;")
    print("   'conservative' assumes the stop fires on any wick — pessimistic, no 1.25s confirmation)")
    print(f"\npub_to_recv 15% RULE — newly-traded records (runup>7.5%, conf>=4): n={len(pub_rule['path'])}")
    for k in ("conservative", "path", "peak_capture"):
        p = pub_rule[k]
        if p:
            print(f"  {k:<14} expectancy {mean(p):+.1f}%  sum {sum(p):+.0f}%  detail {[round(x,0) for x in sorted(p, reverse=True)]}")


if __name__ == "__main__":
    main()
