"""
Headline type classifier - AI-based classification for statistical analysis.

Runs in BACKGROUND only - never blocks trade execution.
Returns ONLY the type, no explanation.
"""
import os
from pathlib import Path
from typing import Optional
from anthropic import AsyncAnthropic

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class HeadlineTypeClassifier:
    """
    Lightweight AI classifier for headline types.

    - Uses Claude Haiku for accurate triage via the universal triage prompt
    - Returns ONLY the type (no explanation)
    - For background statistical collection only
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts" / "headline_types"
        self._triage_prompt: Optional[str] = None

    async def triage(
        self,
        headline: str,
        timeout: float = 5.0,
    ) -> Optional[str]:
        """
        Universal headline triage — sector-agnostic classification.

        Uses a broad list of general headline types. Called at prefilter time
        (before industry is known) to determine headline nature for filter relaxation.
        The result is reused downstream as headline_type for postfilter bypass.

        Args:
            headline: The article headline
            timeout: Max seconds to wait (tight — this is in the hot path)

        Returns:
            Headline type string or None if failed
        """
        if not self.api_key:
            return None

        # Load and cache the universal triage prompt
        if self._triage_prompt is None:
            triage_path = self._prompts_dir / "universal_triage.txt"
            if not triage_path.exists():
                logger.warning("Universal triage prompt not found")
                return None
            try:
                with open(triage_path, "r") as f:
                    self._triage_prompt = f.read()
            except Exception as e:
                logger.debug(f"Failed to load triage prompt: {e}")
                return None

        prompt = self._triage_prompt.replace("{headline}", headline)

        try:
            client = AsyncAnthropic(api_key=self.api_key)

            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=20,
            )

            if response.content and response.content[0].text:
                result = response.content[0].text.strip().lower()
                result = result.split()[0] if result else None
                result = result.replace(".", "").replace(",", "") if result else None
                return result

            return None

        except Exception as e:
            logger.debug(f"Headline triage failed: {e}")
            return None


# Singleton instance for reuse
_classifier: Optional[HeadlineTypeClassifier] = None


def get_headline_classifier() -> HeadlineTypeClassifier:
    """Get or create singleton classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = HeadlineTypeClassifier()
    return _classifier
