"""
Retrospective classifier — runs triage + HC-bypass / sector-classification on
recall records that got rejected by prefilter but ended up moving >=10% (mid).

Purpose: capture what the AI would have done on articles we filtered out live.
Used for false-negative analysis and to enrich headline_type coverage in
analytics engines (e.g. headline_exit_profiles).

Called from:
- Live: PriceMonitor after the 10-min window closes (if excursion >= 10%)
- Batch: scripts/backfill_retrospective_classification.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


# Mirrors HC_BYPASS_TYPES in infra/classification/service.py:1080 —
# headline types that skip the sector LLM and go straight to trade
# based on headline signal + activity confirmation.
HC_BYPASS_TYPES = frozenset({
    "government_contract",
    "military_contract",
    "defense_order",
    "major_contract",
    "stock_buyback",
    "ai_breakthrough",
    "ai_rebranding",
})

# Minimum mid-price excursion (%) to trigger retrospective classification.
# Below this, the article didn't move enough to be a meaningful false negative.
DEFAULT_MIN_EXCURSION_PCT = 10.0


def compute_mid_excursion_pct(
    initial_nbbo: Optional[Dict[str, Any]],
    highest_price_during_hold: Optional[Dict[str, Any]],
) -> Optional[float]:
    """
    Estimate mid-price max excursion from initial NBBO and the peak trade price
    observed during the 10-min hold. Matches the formula used in
    headline_exit_profiles._estimate_mid_excursion so sample inclusion is
    consistent across the live hook, the backfill script, and the analytics job.
    """
    if not initial_nbbo or not highest_price_during_hold:
        return None

    initial_mid = initial_nbbo.get("mid")
    peak_price = highest_price_during_hold.get("price")
    initial_spread = initial_nbbo.get("spread", 0) or 0

    if not initial_mid or not peak_price or initial_mid <= 0:
        return None

    estimated_peak_mid = peak_price - (initial_spread / 2)
    return ((estimated_peak_mid - initial_mid) / initial_mid) * 100


class RetrospectiveClassifier:
    """
    Post-hoc classifier for recall records that moved >=10% after being
    rejected by prefilter (or otherwise never classified live).

    Wraps: HeadlineTypeClassifier.triage() + (optionally) SectorClassifier.classify().
    """

    def __init__(self, headline_classifier, sector_classifier=None, prefer_groq: bool = False):
        """
        Args:
            headline_classifier: HeadlineTypeClassifier — provides triage()
            sector_classifier: SectorClassifier or None. If None, only triage
                               is run (no sector decision is captured).
            prefer_groq: If True, route triage + sector calls through Groq
                         primarily (much higher rate limits — needed for bulk
                         backfill). Default False keeps Anthropic primary for
                         the live path.
        """
        self.headline_classifier = headline_classifier
        self.sector_classifier = sector_classifier
        self.prefer_groq = prefer_groq

    async def classify(
        self,
        headline: str,
        ticker: str,
    ) -> Dict[str, Any]:
        """
        Run retrospective triage, then either record HC bypass size or run sector.

        Returns a dict with shape:
            {
              "applied_at": "ISO-8601",
              "triage_type": "major_contract" | None,
              "hc_bypass": {"is_hc": True, "size": "MODERATE"}
                           | {"is_hc": False}
                           | None,
              "sector_decision": {
                  "classification": "TRADE"|"SKIP"|"NOT_SUPPORTED_SECTOR"|"UNSUPPORTED_INDUSTRY",
                  "size": "SMALL"|"MODERATE"|"LARGE"|"MAX"|None,
                  "sector": str | None,
                  "industry": str | None,
              } | None,
            }

        hc_bypass semantics (unambiguous):
        - None  → triage did not run or failed (can't say whether HC-bypass applies)
        - {"is_hc": True, "size": ...}  → triage ran, type IS HC-bypass, sector skipped
        - {"is_hc": False}              → triage ran, type is NOT HC-bypass, sector ran

        - If triage returns a HC_BYPASS type, hc_bypass={"is_hc": True, ...}
          and sector_decision stays None (mirrors live HC bypass behavior).
        - Otherwise, hc_bypass={"is_hc": False} and sector_decision is populated.
        - If triage fails, hc_bypass=None. applied_at and triage_type=None
          are still returned so callers can distinguish "tried and failed"
          from "never tried".
        """
        result: Dict[str, Any] = {
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "triage_type": None,
            "hc_bypass": None,
            "sector_decision": None,
        }

        if not headline:
            return result

        # Triage — universal, sector-agnostic
        try:
            triage_type = await self.headline_classifier.triage(
                headline,
                prefer_groq=self.prefer_groq,
            )
        except Exception as e:
            logger.debug(f"Retrospective triage failed: {e}", ticker=ticker)
            triage_type = None

        if not triage_type:
            return result

        result["triage_type"] = triage_type

        # HC bypass — skip sector, record what HC would have sized
        if triage_type in HC_BYPASS_TYPES:
            # Mirror live HC bypass size logic (service.py:1095): buybacks LARGE, others MODERATE
            size = "LARGE" if triage_type == "stock_buyback" else "MODERATE"
            result["hc_bypass"] = {"is_hc": True, "size": size}
            return result

        # Non-HC path — triage succeeded but this type doesn't bypass sector.
        # Explicitly record is_hc=False so downstream can't confuse this with a
        # "retro never ran / triage failed" case.
        result["hc_bypass"] = {"is_hc": False}

        if self.sector_classifier is None:
            return result

        try:
            classification, sector, industry, _latency_ms, position_size = (
                await self.sector_classifier.classify(headline, ticker)
            )
            result["sector_decision"] = {
                "classification": classification,
                "size": position_size,
                "sector": sector,
                "industry": industry,
            }
        except Exception as e:
            logger.debug(f"Retrospective sector classification failed: {e}", ticker=ticker)

        return result
