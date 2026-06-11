"""
Postfilter engine — pure, side-effect-free evaluation of the microstructure
postfilters that decide whether an IMMINENT article actually trades.

WHY THIS EXISTS
---------------
The postfilter chain used to live inline inside `process_imminent_article`
(auto_trade.py), interleaved with live I/O (NBBO fetches, event publishing,
order submission). That made it impossible to backtest faithfully — any replay
had to re-implement the logic, which drifts from production.

This module is the SINGLE SOURCE OF TRUTH for the postfilter *decision*:
  - Production gathers the live inputs (NBBO samples, prices) into `PostfilterInputs`
    and calls `evaluate_microstructure_postfilters(...)`, then acts on the verdict.
  - The 45-day backtest builds `PostfilterInputs` from recorded recall/signal data
    and calls the SAME function — guaranteeing the backtest sees exactly what
    production would decide.

SCOPE: this engine covers the *microstructure* postfilters that run AFTER the
STRENGTH/SURGE entry gate and are pure functions of recorded data:
  spread → selling_pressure → fill_spread(median) → pub_to_recv → recv_to_fill
  → pump_and_dump → momentum_exhaustion.
The entry gate (STRENGTH/SURGE/late), market_cap (needs a metadata fetch), and the
environmental guards (cooldown, active-position, circuit-breaker, blacklist) are
NOT here — they are either upstream signal logic or runtime state, not part of the
microstructure decision the backtest needs to replay.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


# ============================================================
# Shared strong-signal runup bypass (front-run filter family)
# ============================================================
# Single home for the bypass that was previously duplicated (and drifted) across
# pub_to_recv / pump_and_dump / pre_news_runup. auto_trade.py imports this.
# 2026-06-11: the ceiling is now a HARD CAP for the entire front-run family —
# NO bypass (high-conviction included) survives a runup above 15%. TGL entered
# at +18.6% pub→recv via the unconditional HC bypass and was instant exit
# liquidity (-16% stop in 21s). Above the ceiling the move already happened.
STRONG_SIGNAL_RUNUP_CEILING_PCT = 15.0


def strong_signal_runup_bypass(
    confluence_score: int,
    runup_pct: float,
    ceiling_pct: float = STRONG_SIGNAL_RUNUP_CEILING_PCT,
) -> bool:
    """Allow a strong-signal IMMINENT trade (any STRENGTH or SURGE entry,
    confluence >= 4) to tolerate runup up to ``ceiling_pct`` — real momentum being
    priced in live, not an exhausted blow-off. The 15% ceiling blocks blow-offs
    (DGNX 27% / ARAI 15.8% were both deep-MAE stop-outs). Validated 2026-06-09."""
    return confluence_score >= 4 and runup_pct <= ceiling_pct


# ============================================================
# Config / Inputs / Decision
# ============================================================
@dataclass(frozen=True)
class PostfilterConfig:
    """Thresholds for the microstructure postfilters (production defaults 2026-06-09)."""
    max_spread_pct: float = 5.0                 # entry spread hard cap
    max_fill_spread_pct: float = 8.0            # fill spread cap (median of samples)
    fill_spread_tight_initial_pct: float = 3.0  # carve-out: initial considered "tight"
    fill_spread_widening_tolerance_pp: float = 3.0
    selling_pressure_threshold: float = -0.3    # imbalance < this = heavy selling
    leg_max_pct: float = 7.5                    # pub->recv / recv->fill ask move
    leg_max_pct_mega: float = 8.0
    leg_min_absolute_move: float = 0.05         # $ floor for the leg filters
    pump_max_pct: float = 5.5                   # fill vs pub premium
    pump_max_pct_ai_breakthrough: float = 12.0
    pump_min_absolute: float = 0.08
    momentum_max_runup_pct: float = 5.0         # confluence_max vs entry ask
    strong_signal_runup_ceiling_pct: float = STRONG_SIGNAL_RUNUP_CEILING_PCT


@dataclass
class PostfilterInputs:
    """Everything the microstructure filters need — gathered live in production,
    reconstructed from records in the backtest. Missing optional fields cause the
    corresponding filter to be skipped (same as production when data is absent)."""
    # signal / context
    confluence_score: int = 0
    is_high_conviction: bool = False
    is_mega_trade: bool = False
    is_ai_breakthrough: bool = False
    confluence_imbalance_ratio: Optional[float] = None
    # spread
    initial_spread_pct: Optional[float] = None          # entry spread % of mid
    fill_spread_samples_pct: List[float] = field(default_factory=list)  # >=1 sampled fill spreads
    # two-leg ask prices
    pub_time_ask: Optional[float] = None
    recv_ask: Optional[float] = None                    # ask at reception (a.k.a. initial_ask)
    fill_ask: Optional[float] = None                    # ask at the pre-entry snapshot
    # momentum
    confluence_max_price: Optional[float] = None
    entry_reference_price: Optional[float] = None       # initial_ask or confluence_first_price


@dataclass
class PostfilterDecision:
    passed: bool
    reason: Optional[str] = None                # postfilter_<x> reason if blocked
    computed: Dict[str, Any] = field(default_factory=dict)


def _block(reason: str, computed: Dict[str, Any]) -> PostfilterDecision:
    return PostfilterDecision(passed=False, reason=reason, computed=computed)


def evaluate_microstructure_postfilters(
    inp: PostfilterInputs,
    cfg: PostfilterConfig = PostfilterConfig(),
) -> PostfilterDecision:
    """Run the microstructure postfilter chain in production order and return the
    first failure (or pass). Pure — no I/O, no logging, no side effects. The caller
    (production or backtest) is responsible for gathering inputs and acting on the
    verdict (record skip + return, or proceed to order)."""
    computed: Dict[str, Any] = {}

    # 1) SPREAD — entry spread hard cap (no headline-type relaxation)
    if inp.initial_spread_pct is not None:
        computed["initial_spread_pct"] = round(inp.initial_spread_pct, 2)
        if inp.initial_spread_pct > cfg.max_spread_pct:
            return _block(f"postfilter_spread_too_wide:{inp.initial_spread_pct:.0f}%", computed)

    # 2) SELLING PRESSURE — heavy net selling (HC bypass)
    if inp.confluence_imbalance_ratio is not None and not inp.is_high_conviction:
        if inp.confluence_imbalance_ratio < cfg.selling_pressure_threshold:
            return _block(f"postfilter_selling_pressure:{inp.confluence_imbalance_ratio:.2f}", computed)

    # 3) FILL SPREAD — median of samples vs 8% cap (+ tight-initial/modest-widening carve-out)
    samples = [s for s in inp.fill_spread_samples_pct if s is not None]
    if samples:
        # Upper-middle element of the sorted samples — byte-identical to the original
        # inline `samples[len(samples)//2]` (NOT an averaging median, which would drift
        # for even sample counts).
        _sorted = sorted(samples)
        median_fill = _sorted[len(_sorted) // 2]
        computed["fill_spread_pct"] = round(median_fill, 2)
        if median_fill > cfg.max_fill_spread_pct:
            init = inp.initial_spread_pct or 0.0
            widening = median_fill - init if init > 0 else median_fill
            initial_was_tight = 0 < init < cfg.fill_spread_tight_initial_pct
            widening_modest = widening < cfg.fill_spread_widening_tolerance_pp
            if not (initial_was_tight and widening_modest):
                return _block(f"postfilter_fill_spread_too_wide:{median_fill:.1f}%", computed)

    leg_max = cfg.leg_max_pct_mega if inp.is_mega_trade else cfg.leg_max_pct

    # 4) PUB -> RECV (leg 1) — front-running. The 15% ceiling is a HARD CAP:
    # high-conviction and strong-signal bypasses only apply BELOW it (TGL lesson).
    if inp.pub_time_ask and inp.recv_ask and inp.pub_time_ask > 0:
        pub_to_recv_pct = (inp.recv_ask - inp.pub_time_ask) / inp.pub_time_ask * 100
        absolute_move = abs(inp.recv_ask - inp.pub_time_ask)
        computed["pub_to_recv_pct"] = round(pub_to_recv_pct, 2)
        if pub_to_recv_pct > leg_max and absolute_move >= cfg.leg_min_absolute_move:
            within_ceiling = pub_to_recv_pct <= cfg.strong_signal_runup_ceiling_pct
            bypass = within_ceiling and (
                inp.is_high_conviction or strong_signal_runup_bypass(
                    inp.confluence_score, pub_to_recv_pct, cfg.strong_signal_runup_ceiling_pct
                )
            )
            if not bypass:
                return _block(f"postfilter_pub_to_recv:{pub_to_recv_pct:.1f}%", computed)

    # 5) RECV -> FILL (leg 2) — chase during our checks. HC bypass, also capped at the ceiling.
    if inp.recv_ask and inp.fill_ask and inp.recv_ask > 0:
        recv_to_fill_pct = (inp.fill_ask - inp.recv_ask) / inp.recv_ask * 100
        absolute_move_leg2 = abs(inp.fill_ask - inp.recv_ask)
        computed["recv_to_fill_pct"] = round(recv_to_fill_pct, 2)
        if recv_to_fill_pct > leg_max and absolute_move_leg2 >= cfg.leg_min_absolute_move:
            within_ceiling = recv_to_fill_pct <= cfg.strong_signal_runup_ceiling_pct
            if not (within_ceiling and inp.is_high_conviction):
                return _block(f"postfilter_recv_to_fill:{recv_to_fill_pct:.1f}%", computed)

    # 6) PUMP-AND-DUMP — fill premium vs publication ask. Bypasses capped at the ceiling.
    pump_max = cfg.pump_max_pct_ai_breakthrough if inp.is_ai_breakthrough else cfg.pump_max_pct
    if inp.pub_time_ask and inp.fill_ask and inp.pub_time_ask > 0:
        pump_pct = (inp.fill_ask - inp.pub_time_ask) / inp.pub_time_ask * 100
        absolute_gap = abs(inp.fill_ask - inp.pub_time_ask)
        computed["ask_pub_to_fill_pct"] = round(pump_pct, 2)
        if pump_pct > pump_max and absolute_gap >= cfg.pump_min_absolute:
            within_ceiling = pump_pct <= cfg.strong_signal_runup_ceiling_pct
            bypass = within_ceiling and (
                inp.is_high_conviction or strong_signal_runup_bypass(
                    inp.confluence_score, pump_pct, cfg.strong_signal_runup_ceiling_pct
                )
            )
            if not bypass:
                return _block(f"postfilter_pump_and_dump:{pump_pct:.1f}%", computed)

    # 7) MOMENTUM EXHAUSTION — confluence max price already ran above our entry (HC bypass)
    if inp.entry_reference_price and inp.confluence_max_price and inp.entry_reference_price > 0:
        runup_pct = (inp.confluence_max_price - inp.entry_reference_price) / inp.entry_reference_price * 100
        computed["confluence_runup_pct"] = round(runup_pct, 2)
        if runup_pct > cfg.momentum_max_runup_pct and not inp.is_high_conviction:
            return _block(f"postfilter_momentum_exhausted:{runup_pct:.1f}%", computed)

    return PostfilterDecision(passed=True, reason=None, computed=computed)
