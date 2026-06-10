"""
Entry-gate decision — the PURE part of the STRENGTH/SURGE entry gate.

`process_imminent_article` decides whether a confirmed-activity signal warrants a
trade. That decision has two halves:
  - PURE (this module): given the confluence-window stats (score, excursion, trade
    count) + headline conviction + the initial book, does the signal clear the
    STRENGTH / HIGH_CONFLUENCE / HC_BYPASS / TIGHT_SPREAD_BYPASS bar?
  - IMPURE (stays in auto_trade): the SURGE (8s) and LATE (≤Ns) fallbacks, which
    require live async monitoring and so cannot be pure.

Extracting the pure half makes it unit-testable and backtestable — the same
single-source-of-truth pattern as postfilter_engine.py. Production calls
`evaluate_strength_gate(...)`; the backtest calls it with recorded inputs.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StrengthGateConfig:
    min_confluence_trades: int = 3        # <3 trades (non-HC) = one person, not confluence
    min_strength_excursion_pct: float = 0.5
    high_confluence_score: int = 3
    tight_spread_bypass_pct: float = 2.5  # HC-only: tight book = limited downside, trade w/o activity


@dataclass
class StrengthGateDecision:
    # effective score AFTER the too-few-trades override (downstream uses this)
    effective_score: int
    has_strength: bool
    has_high_confluence: bool
    has_hc_bypass: bool
    has_tight_spread_bypass: bool
    # None means no STRENGTH-family confirmation -> caller falls through to SURGE/LATE
    entry_reason: Optional[str]
    too_few_trades_non_hc: bool           # score was overridden to 0
    tight_spread_pct: Optional[float] = None


def evaluate_strength_gate(
    confluence_score: int,
    max_excursion_pct: float,
    confluence_trade_count: int,
    is_high_conviction: bool,
    initial_spread: Optional[float],
    initial_ask: Optional[float],
    cfg: StrengthGateConfig = StrengthGateConfig(),
) -> StrengthGateDecision:
    """Pure STRENGTH-family entry decision. Byte-faithful port of the inline logic in
    process_imminent_article (the score-override + 4 booleans + entry_reason priority)."""
    # Too few independent trades (and not HC) → the score can't be trusted; zero it.
    too_few_trades_non_hc = confluence_trade_count < cfg.min_confluence_trades and not is_high_conviction
    score = 0 if too_few_trades_non_hc else confluence_score

    has_strength = score >= 1 and max_excursion_pct >= cfg.min_strength_excursion_pct
    has_high_confluence = score >= cfg.high_confluence_score and max_excursion_pct >= cfg.min_strength_excursion_pct
    has_hc_bypass = is_high_conviction and score >= 1

    has_tight_spread_bypass = False
    tight_spread_pct: Optional[float] = None
    if is_high_conviction and initial_spread and initial_ask and initial_ask > 0:
        # NB: this gate uses ask-based mid (ask - spread/2), matching the original inline
        # bypass — distinct from the postfilter spread filter's bid-based mid.
        mid = initial_ask - (initial_spread / 2)
        if mid > 0:
            tight_spread_pct = (initial_spread / mid) * 100
            if tight_spread_pct <= cfg.tight_spread_bypass_pct:
                has_tight_spread_bypass = True

    entry_reason: Optional[str] = None
    if has_strength:
        entry_reason = "STRENGTH"
    elif has_high_confluence:
        entry_reason = "HIGH_CONFLUENCE"
    elif has_hc_bypass:
        entry_reason = "HC_BYPASS"
    elif has_tight_spread_bypass:
        entry_reason = "TIGHT_SPREAD_BYPASS"

    return StrengthGateDecision(
        effective_score=score,
        has_strength=has_strength,
        has_high_confluence=has_high_confluence,
        has_hc_bypass=has_hc_bypass,
        has_tight_spread_bypass=has_tight_spread_bypass,
        entry_reason=entry_reason,
        too_few_trades_non_hc=too_few_trades_non_hc,
        tight_spread_pct=tight_spread_pct,
    )
