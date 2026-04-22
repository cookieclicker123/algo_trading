"""
Headline type classifier - AI-based classification for statistical analysis.

Runs in BACKGROUND only - never blocks trade execution.
Returns ONLY the type, no explanation.
"""
import asyncio
import os
import random
from pathlib import Path
from typing import Optional
from anthropic import AsyncAnthropic, RateLimitError

try:
    from groq import AsyncGroq
except ImportError:
    AsyncGroq = None

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class HeadlineTypeClassifier:
    """
    Lightweight AI classifier for headline types.

    - Uses Claude Haiku for accurate triage via the universal triage prompt
    - Falls back to Groq Llama-3.3-70B on 429 rate limits (higher throughput)
    - Returns ONLY the type (no explanation)
    - For background statistical collection only
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        groq_api_key: Optional[str] = None,
        groq_fallback_model: str = "llama-3.3-70b-versatile",
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.groq_api_key = groq_api_key or os.environ.get("GROQ_API_KEY")
        self.groq_fallback_model = groq_fallback_model
        self._prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts" / "headline_types"
        self._triage_prompt: Optional[str] = None

    @staticmethod
    def _parse_type(text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        result = text.strip().lower()
        result = result.split()[0] if result else None
        return result.replace(".", "").replace(",", "") if result else None

    async def triage(
        self,
        headline: str,
        timeout: float = 5.0,
        max_retries: int = 4,
        prefer_groq: bool = False,
    ) -> Optional[str]:
        """
        Universal headline triage — sector-agnostic classification.

        Uses Claude Haiku on Anthropic with retry-on-429 (exponential backoff
        + jitter). If Anthropic stays rate-limited past all retries AND a
        Groq API key is available, falls back to Groq Llama-3.3-70B on the
        same prompt. The universal triage prompt is sector-agnostic, so the
        fallback produces compatible output.

        Args:
            headline: The article headline
            timeout: (Unused — per-call timeout is set by the underlying client)
            max_retries: How many times to retry Anthropic on 429 before falling back

        Returns:
            Headline type string or None if both providers failed.
        """
        if not self.api_key and not self.groq_api_key:
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

        # Split the triage prompt into a CACHED static prefix (the rules + type list)
        # and a VARIABLE suffix (the headline). Ephemeral prompt caching on Anthropic
        # means the 14K-token prefix is billed once per 5-min window, and every
        # subsequent call pays for only ~50 headline tokens against the 50K TPM cap.
        # This preserves Claude Haiku's quality while making bulk backfills feasible.
        marker = "Headline: {headline}"
        if marker in self._triage_prompt:
            static_part, _, trailing = self._triage_prompt.partition(marker)
            # `trailing` is typically "\n\nReturn ONLY the type, nothing else."
            user_message = f"Headline: {headline}{trailing}"
        else:
            # Prompt format drift — fall back to full interpolation (uncached).
            static_part = self._triage_prompt.replace("{headline}", headline)
            user_message = "Return ONLY the type, nothing else."

        # Groq-first path (opt-in, for callers that explicitly prefer throughput
        # over Claude quality). Default is Anthropic + prompt cache.
        if prefer_groq and self.groq_api_key and AsyncGroq is not None:
            try:
                groq_client = AsyncGroq(api_key=self.groq_api_key)
                response = await groq_client.chat.completions.create(
                    model=self.groq_fallback_model,
                    messages=[
                        {"role": "system", "content": static_part},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.0,
                    max_tokens=20,
                )
                if response.choices and response.choices[0].message.content:
                    return self._parse_type(response.choices[0].message.content)
            except Exception as e:
                logger.debug(f"Triage Groq primary failed, falling back to Anthropic: {e}")
                # fall through to Anthropic below

        # Anthropic with prompt cache + retry-on-429
        if self.api_key:
            for attempt in range(max_retries):
                try:
                    client = AsyncAnthropic(api_key=self.api_key)
                    response = await client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        system=[
                            {
                                "type": "text",
                                "text": static_part,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                        messages=[{"role": "user", "content": user_message}],
                        temperature=0.0,
                        max_tokens=20,
                    )
                    if response.content and response.content[0].text:
                        return self._parse_type(response.content[0].text)
                    return None
                except RateLimitError as e:
                    # Exponential backoff with jitter: 2^attempt + [0, 1)s
                    delay = (2 ** attempt) + random.random()
                    if attempt < max_retries - 1:
                        logger.debug(
                            f"Triage 429, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.debug(f"Triage exhausted Anthropic retries: {e}")
                    break
                except Exception as e:
                    logger.debug(f"Triage failed (non-429): {e}")
                    break

        # Groq fallback
        if self.groq_api_key and AsyncGroq is not None:
            try:
                groq_client = AsyncGroq(api_key=self.groq_api_key)
                response = await groq_client.chat.completions.create(
                    model=self.groq_fallback_model,
                    messages=[
                        {"role": "system", "content": static_part},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.0,
                    max_tokens=20,
                )
                if response.choices and response.choices[0].message.content:
                    return self._parse_type(response.choices[0].message.content)
            except Exception as e:
                logger.debug(f"Triage Groq fallback failed: {e}")

        return None


# Singleton instance for reuse
_classifier: Optional[HeadlineTypeClassifier] = None


def get_headline_classifier() -> HeadlineTypeClassifier:
    """Get or create singleton classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = HeadlineTypeClassifier()
    return _classifier
