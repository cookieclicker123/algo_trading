"""
Golden-master / contract tests for the postfilter engine.

This is the regression net that lets us refactor `process_imminent_article`
safely: the engine is the single source of truth for the microstructure
postfilter decision, and these tests pin its CONTRACT — the exact (passed, reason)
verdict — for:
  1. Real recorded production cases (FEED, ADTX, DGNX, and the 2026-06-09 losers).
  2. Every filter's threshold boundary + bypass path.
  3. A simulated news event end-to-end through the engine.
  4. Latency (the engine is pure arithmetic — must stay microsecond-cheap).

If any of these change, the trade contract changed — that must be deliberate.
"""
import time
import pytest

from newsflash.services.brokerage.postfilter_engine import (
    PostfilterInputs,
    PostfilterConfig,
    evaluate_microstructure_postfilters as evaluate,
    strong_signal_runup_bypass,
)


def _inp(**kw) -> PostfilterInputs:
    return PostfilterInputs(**kw)


# ─────────────────────────────────────────────────────────────────────────────
# 1. REAL RECORDED CASES (the golden master from production, 2026-06-08/09)
#    Each: (label, inputs, expected_passed, expected_reason_prefix_or_None)
# ─────────────────────────────────────────────────────────────────────────────
def _runup_ask(pub: float, pct: float) -> float:
    return pub * (1 + pct / 100.0)

REAL_CASES = [
    # FEED +104%: conf4, tight spread, pub->recv 9.2%, ai_breakthrough -> TRADE
    ("FEED", _inp(confluence_score=4, is_ai_breakthrough=True, initial_spread_pct=0.96,
                  fill_spread_samples_pct=[0.9, 1.0, 0.95],
                  pub_time_ask=0.87, recv_ask=_runup_ask(0.87, 9.2), fill_ask=_runup_ask(0.87, 9.2)),
     True, None),
    # ADTX +60%: conf4, spread 2.22, oscillating fill median ~4% -> TRADE
    ("ADTX", _inp(confluence_score=4, initial_spread_pct=2.22,
                  fill_spread_samples_pct=[2.76, 4.05, 5.6],
                  pub_time_ask=0.05, recv_ask=0.05, fill_ask=0.05),
     True, None),
    # GXAI +59%: pub->recv 10.7% (<15) conf4 -> TRADE
    ("GXAI", _inp(confluence_score=4, initial_spread_pct=0.77,
                  pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 10.7), fill_ask=_runup_ask(1.0, 10.7)),
     True, None),
    # OMEX +80%: pub->recv 6% conf4 -> TRADE
    ("OMEX", _inp(confluence_score=4, initial_spread_pct=1.8,
                  pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 6.0), fill_ask=_runup_ask(1.0, 6.0)),
     True, None),
    # ARAI: pub->recv 15.8% (>15 ceiling) -> BLOCK pub_to_recv (was a stop-out)
    ("ARAI", _inp(confluence_score=4, initial_spread_pct=0.86,
                  pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 15.8), fill_ask=_runup_ask(1.0, 15.8)),
     False, "postfilter_pub_to_recv"),
    # DGNX: pub->recv 27% -> BLOCK pub_to_recv ("+111% peak" was a -18.6% MAE stop-out)
    ("DGNX", _inp(confluence_score=6, initial_spread_pct=3.25,
                  pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 27.1), fill_ask=_runup_ask(1.0, 27.1)),
     False, "postfilter_pub_to_recv"),
    # FRSX: pub->recv 10.7%, conf4 -> TRADE (accepted fader; recall over precision)
    ("FRSX", _inp(confluence_score=4, initial_spread_pct=5.36,
                  pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 10.7), fill_ask=_runup_ask(1.0, 10.7)),
     False, "postfilter_spread_too_wide"),  # NB: FRSX spread 5.36 > 5 entry cap -> blocked on spread, not pub_recv
    # 2026-06-09 small losers — all clean setups that traded (passed every filter)
    ("RYET", _inp(confluence_score=4, initial_spread_pct=2.66, fill_spread_samples_pct=[2.63, 1.32, 0.38]),
     True, None),
    ("JFB",  _inp(confluence_score=4, initial_spread_pct=0.89, fill_spread_samples_pct=[0.88, 6.34, 6.34]),
     True, None),
    ("BKSY", _inp(confluence_score=4, initial_spread_pct=2.25, fill_spread_samples_pct=[2.23, 2.23, 2.23]),
     True, None),
    ("OPTX", _inp(confluence_score=4, initial_spread_pct=1.86, fill_spread_samples_pct=[1.85, 1.85, 1.85]),
     True, None),
]


@pytest.mark.parametrize("label,inp,exp_pass,exp_reason", REAL_CASES,
                         ids=[c[0] for c in REAL_CASES])
def test_real_recorded_cases(label, inp, exp_pass, exp_reason):
    d = evaluate(inp)
    assert d.passed == exp_pass, f"{label}: passed={d.passed} reason={d.reason}"
    if exp_reason is None:
        assert d.reason is None, f"{label}: unexpected reason {d.reason}"
    else:
        assert d.reason and d.reason.startswith(exp_reason), f"{label}: reason={d.reason}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. PER-FILTER THRESHOLD CONTRACTS
# ─────────────────────────────────────────────────────────────────────────────
def test_spread_cap_boundary():
    assert evaluate(_inp(initial_spread_pct=5.0)).passed is True          # exactly at cap
    d = evaluate(_inp(initial_spread_pct=5.01))
    assert d.passed is False and d.reason.startswith("postfilter_spread_too_wide")

def test_selling_pressure_and_hc_bypass():
    assert evaluate(_inp(confluence_imbalance_ratio=-0.30)).passed is True  # at threshold, not below
    assert evaluate(_inp(confluence_imbalance_ratio=-0.31)).reason.startswith("postfilter_selling_pressure")
    # HC bypasses selling pressure
    assert evaluate(_inp(confluence_imbalance_ratio=-0.9, is_high_conviction=True)).passed is True

def test_fill_spread_median_not_single_flicker():
    # ADTX oscillation: one 5.6% flicker, median ~4% -> PASS
    assert evaluate(_inp(initial_spread_pct=2.22, fill_spread_samples_pct=[2.76, 4.05, 5.6])).passed is True
    # genuinely wide book -> median 9.5% > 8% -> BLOCK
    d = evaluate(_inp(initial_spread_pct=4.0, fill_spread_samples_pct=[9.0, 9.5, 10.0]))
    assert d.passed is False and d.reason.startswith("postfilter_fill_spread_too_wide")
    # tight-initial transient carve-out: initial 2% widening to 5.5% (<3pp? 3.5pp -> no), 4.5% (<3pp yes) -> allow
    assert evaluate(_inp(initial_spread_pct=2.0, fill_spread_samples_pct=[4.5, 4.5, 4.5])).passed is True

def test_pub_to_recv_ceiling_and_bypass():
    base = dict(confluence_score=4, pub_time_ask=1.0)
    assert evaluate(_inp(recv_ask=_runup_ask(1.0, 9.0), **base)).passed is True    # 9% < 15 ceiling
    assert evaluate(_inp(recv_ask=_runup_ask(1.0, 15.0), **base)).passed is True   # exactly 15
    d = evaluate(_inp(recv_ask=_runup_ask(1.0, 16.0), **base))
    assert d.passed is False and d.reason.startswith("postfilter_pub_to_recv")     # >15
    # conf < 4: no strong-signal bypass -> blocked at the 7.5% leg cap
    d = evaluate(_inp(confluence_score=3, pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 9.0)))
    assert d.passed is False and d.reason.startswith("postfilter_pub_to_recv")
    # HC bypasses below the ceiling...
    assert evaluate(_inp(confluence_score=3, is_high_conviction=True,
                         pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 12.0))).passed is True
    # ...but the 15% ceiling is a HARD CAP even for HC (TGL +18.6% lesson, 2026-06-11)
    d = evaluate(_inp(confluence_score=3, is_high_conviction=True,
                      pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 20.0)))
    assert d.passed is False and d.reason.startswith("postfilter_pub_to_recv")

def test_pump_and_dump_ai_threshold():
    # non-AI: 6% premium > 5.5% (abs move $0.60 clears the $0.08 floor), conf<4 -> block
    d = evaluate(_inp(confluence_score=3, pub_time_ask=10.0, fill_ask=_runup_ask(10.0, 6.0)))
    assert d.passed is False and d.reason.startswith("postfilter_pump_and_dump")
    # AI breakthrough: 6% premium < 12% threshold -> pass
    assert evaluate(_inp(confluence_score=3, is_ai_breakthrough=True,
                         pub_time_ask=10.0, fill_ask=_runup_ask(10.0, 6.0))).passed is True
    # the $0.08 absolute floor: 6% on a $1 stock = $0.06 < floor -> does NOT fire
    assert evaluate(_inp(confluence_score=3, pub_time_ask=1.0, fill_ask=_runup_ask(1.0, 6.0))).passed is True

def test_momentum_exhaustion():
    d = evaluate(_inp(entry_reference_price=1.0, confluence_max_price=1.06))  # +6% > 5%
    assert d.passed is False and d.reason.startswith("postfilter_momentum_exhausted")
    assert evaluate(_inp(entry_reference_price=1.0, confluence_max_price=1.04)).passed is True  # +4%

def test_filter_order_is_preserved():
    # a record failing BOTH spread and pub_to_recv must report SPREAD (checked first)
    d = evaluate(_inp(confluence_score=3, initial_spread_pct=9.0,
                      pub_time_ask=1.0, recv_ask=_runup_ask(1.0, 30.0)))
    assert d.reason.startswith("postfilter_spread_too_wide")


def test_strong_signal_helper_contract():
    assert strong_signal_runup_bypass(4, 9.2) is True
    assert strong_signal_runup_bypass(4, 15.0) is True
    assert strong_signal_runup_bypass(4, 15.1) is False
    assert strong_signal_runup_bypass(3, 1.0) is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. NEWS-EVENT SIMULATION (stateless end-to-end through the engine)
# ─────────────────────────────────────────────────────────────────────────────
def test_simulate_feed_class_news_event():
    """A biotech AI-breakthrough breaks: tight book, surges, runs 9% before we buy.
    The engine should let it trade (the FEED lesson)."""
    decision = evaluate(_inp(
        confluence_score=4, is_ai_breakthrough=True,
        initial_spread_pct=0.96,
        fill_spread_samples_pct=[0.9, 1.1, 1.0],
        pub_time_ask=0.87, recv_ask=0.95, fill_ask=0.95,
        entry_reference_price=0.95, confluence_max_price=0.97,
    ))
    assert decision.passed is True
    assert "pub_to_recv_pct" in decision.computed


def test_clean_pass_populates_computed_metrics():
    d = evaluate(_inp(confluence_score=4, initial_spread_pct=1.0,
                      fill_spread_samples_pct=[1.5, 2.0, 1.8],
                      pub_time_ask=1.0, recv_ask=1.01, fill_ask=1.01,
                      entry_reference_price=1.01, confluence_max_price=1.02))
    assert d.passed is True
    for k in ("initial_spread_pct", "fill_spread_pct", "pub_to_recv_pct",
              "recv_to_fill_pct", "ask_pub_to_fill_pct", "confluence_runup_pct"):
        assert k in d.computed, f"missing computed metric {k}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. LATENCY — pure arithmetic, must stay microsecond-cheap (no regression vs inline)
# ─────────────────────────────────────────────────────────────────────────────
def test_engine_latency_is_negligible():
    inp = _inp(confluence_score=4, initial_spread_pct=1.0,
               fill_spread_samples_pct=[1.5, 2.0, 1.8],
               pub_time_ask=1.0, recv_ask=1.05, fill_ask=1.05,
               entry_reference_price=1.05, confluence_max_price=1.06)
    n = 10_000
    t0 = time.perf_counter()
    for _ in range(n):
        evaluate(inp)
    per_call_us = (time.perf_counter() - t0) / n * 1e6
    # generous ceiling — the inline version was the same arithmetic; assert no I/O crept in
    assert per_call_us < 50, f"engine too slow: {per_call_us:.1f}us/call"
