"""Golden-master / contract tests for the pure STRENGTH-family entry gate."""
from newsflash.services.brokerage.entry_gate import (
    evaluate_strength_gate as gate,
    StrengthGateDecision,
)


def test_strength_basic():
    d = gate(confluence_score=1, max_excursion_pct=0.5, confluence_trade_count=5,
             is_high_conviction=False, initial_spread=None, initial_ask=None)
    assert d.has_strength and d.entry_reason == "STRENGTH"

def test_excursion_below_threshold_is_no_strength():
    d = gate(confluence_score=2, max_excursion_pct=0.49, confluence_trade_count=5,
             is_high_conviction=False, initial_spread=None, initial_ask=None)
    assert not d.has_strength and d.entry_reason is None  # -> falls through to surge/late

def test_high_confluence_priority_is_strength_first():
    # score 3 + excursion -> has_strength True too; STRENGTH wins the priority
    d = gate(confluence_score=3, max_excursion_pct=1.0, confluence_trade_count=5,
             is_high_conviction=False, initial_spread=None, initial_ask=None)
    assert d.has_high_confluence and d.has_strength and d.entry_reason == "STRENGTH"

def test_too_few_trades_zeroes_score_non_hc():
    d = gate(confluence_score=5, max_excursion_pct=2.0, confluence_trade_count=2,
             is_high_conviction=False, initial_spread=None, initial_ask=None)
    assert d.too_few_trades_non_hc and d.effective_score == 0
    assert not d.has_strength and d.entry_reason is None

def test_too_few_trades_kept_for_hc():
    d = gate(confluence_score=2, max_excursion_pct=0.0, confluence_trade_count=1,
             is_high_conviction=True, initial_spread=None, initial_ask=None)
    assert not d.too_few_trades_non_hc and d.effective_score == 2
    assert d.has_hc_bypass and d.entry_reason == "HC_BYPASS"  # HC + score>=1, no excursion needed

def test_hc_bypass_requires_score_ge_1():
    d = gate(confluence_score=0, max_excursion_pct=0.0, confluence_trade_count=5,
             is_high_conviction=True, initial_spread=None, initial_ask=None)
    assert not d.has_hc_bypass and d.entry_reason is None

def test_tight_spread_bypass_hc_only():
    # HC, score 0, but tight book (ask-based mid): spread 0.02 on ask 1.0 -> mid 0.99 -> 2.02% <= 2.5%
    d = gate(confluence_score=0, max_excursion_pct=0.0, confluence_trade_count=5,
             is_high_conviction=True, initial_spread=0.02, initial_ask=1.0)
    assert d.has_tight_spread_bypass and d.entry_reason == "TIGHT_SPREAD_BYPASS"
    assert round(d.tight_spread_pct, 2) == 2.02
    # wide book -> no bypass
    d2 = gate(confluence_score=0, max_excursion_pct=0.0, confluence_trade_count=5,
              is_high_conviction=True, initial_spread=0.10, initial_ask=1.0)
    assert not d2.has_tight_spread_bypass and d2.entry_reason is None
    # not HC -> tight spread bypass never applies
    d3 = gate(confluence_score=0, max_excursion_pct=0.0, confluence_trade_count=5,
              is_high_conviction=False, initial_spread=0.02, initial_ask=1.0)
    assert not d3.has_tight_spread_bypass and d3.entry_reason is None

def test_entry_reason_priority_order():
    # HC + strength present -> STRENGTH wins over HC_BYPASS
    d = gate(confluence_score=4, max_excursion_pct=1.0, confluence_trade_count=10,
             is_high_conviction=True, initial_spread=0.01, initial_ask=1.0)
    assert d.entry_reason == "STRENGTH"
