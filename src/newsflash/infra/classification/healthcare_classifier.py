"""
Healthcare headline classifier using industry-specific Anthropic Claude prompts.

Simple flow: headline → sector check → industry check → LLM classification → TRADE/SKIP

This is the primary trading decision maker - no microstructure filters.
Speed is critical - classify as fast as possible for immediate entry.
"""
import asyncio
from pathlib import Path
from typing import Optional, Dict, Tuple
from datetime import datetime

from anthropic import AsyncAnthropic

from ...utils.logging_config import get_logger

logger = get_logger(__name__)

# Supported Healthcare industries (must match prompt filenames)
SUPPORTED_INDUSTRIES = {
    "Biotechnology": "biotechnology.txt",
    "Medical Devices": "medical_devices.txt",
    "Drug Manufacturers - Specialty & Generic": "drug_manufacturers.txt",
    "Diagnostics & Research": "diagnostics_research.txt",
    "Health Information Services": "health_information_services.txt",
    "Medical Instruments & Supplies": "medical_instruments_supplies.txt",
    "Medical Care Facilities": "medical_care_facilities.txt",
}


class HealthcareClassifier:
    """
    Fast Healthcare headline classifier using industry-specific prompts.

    Design principles:
    - Speed over everything - one API call per headline
    - Industry-specific prompts for better accuracy
    - Simple TRADE/SKIP output
    - No microstructure checks - pure language-based decision
    """

    def __init__(
        self,
        api_key: str,
        metadata_cache,  # MetadataCache instance for sector/industry lookup
        model: str = "claude-haiku-4-5-20251001",
    ):
        """
        Initialize Healthcare classifier.

        Args:
            api_key: Anthropic API key
            metadata_cache: MetadataCache instance for instant sector/industry lookup
            model: Anthropic model to use (default: Claude Sonnet 4.6)
        """
        self.api_key = api_key
        self.metadata_cache = metadata_cache
        self.model = model

        # Anthropic client
        self.client = AsyncAnthropic(api_key=api_key, timeout=15.0) if api_key else None

        # Cache loaded prompts (load once, reuse)
        self._prompts: Dict[str, str] = {}

        # Prompt directory (relative to project root)
        self._prompt_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts" / "healthcare"

        # Stats
        self._stats = {
            "total_classified": 0,
            "trade_signals": 0,
            "skip_signals": 0,
            "not_healthcare": 0,
            "unsupported_industry": 0,
            "errors": 0,
            "avg_latency_ms": 0.0,
        }

        logger.info(
            "HealthcareClassifier initialized",
            model=model,
            prompt_dir=str(self._prompt_dir),
            supported_industries=list(SUPPORTED_INDUSTRIES.keys())
        )

    def _load_prompt(self, industry: str) -> Optional[str]:
        """
        Load industry-specific prompt (cached after first load).

        Args:
            industry: Industry name (must be in SUPPORTED_INDUSTRIES)

        Returns:
            Prompt text or None if not found
        """
        if industry in self._prompts:
            return self._prompts[industry]

        prompt_file = SUPPORTED_INDUSTRIES.get(industry)
        if not prompt_file:
            return None

        prompt_path = self._prompt_dir / prompt_file

        try:
            with open(prompt_path, "r") as f:
                prompt = f.read()
            self._prompts[industry] = prompt
            logger.debug("Loaded prompt", industry=industry, path=str(prompt_path))
            return prompt
        except Exception as e:
            logger.error("Failed to load prompt", industry=industry, error=str(e))
            return None

    async def classify(
        self,
        headline: str,
        ticker: str,
    ) -> Tuple[str, Optional[str], float]:
        """
        Classify a Healthcare headline.

        Args:
            headline: News headline text
            ticker: Primary ticker symbol

        Returns:
            Tuple of (classification, industry, latency_ms):
            - classification: "TRADE", "SKIP", "NOT_HEALTHCARE", or "UNSUPPORTED_INDUSTRY"
            - industry: Industry name if Healthcare, else None
            - latency_ms: Classification latency in milliseconds
        """
        start_time = datetime.now()

        # Step 1: Check sector/industry from cache (instant, ~0ms)
        metadata = await self.metadata_cache.get_permanent(ticker)

        if not metadata:
            # Unknown ticker - can't classify without sector/industry
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.debug(
                "Classification skipped - no metadata",
                ticker=ticker,
                headline=headline[:50]
            )
            self._stats["not_healthcare"] += 1
            return "NOT_HEALTHCARE", None, latency_ms

        sector = metadata.get("sector", "")
        industry = metadata.get("industry", "")

        # Step 2: Check if Healthcare sector
        if sector != "Healthcare":
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.debug(
                "Classification skipped - not Healthcare",
                ticker=ticker,
                sector=sector,
                headline=headline[:50]
            )
            self._stats["not_healthcare"] += 1
            return "NOT_HEALTHCARE", None, latency_ms

        # Step 3: Check if supported industry
        if industry not in SUPPORTED_INDUSTRIES:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(
                "Classification skipped - unsupported Healthcare industry",
                ticker=ticker,
                industry=industry,
                headline=headline[:50]
            )
            self._stats["unsupported_industry"] += 1
            return "UNSUPPORTED_INDUSTRY", industry, latency_ms

        # Step 4: Load industry-specific prompt
        prompt = self._load_prompt(industry)
        if not prompt:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error("Failed to load prompt for industry", industry=industry)
            self._stats["errors"] += 1
            return "SKIP", industry, latency_ms

        # Step 5: Call Anthropic Claude for classification
        if not self.client:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error("Anthropic client not initialized")
            self._stats["errors"] += 1
            return "SKIP", industry, latency_ms

        try:
            # Simple prompt: just the headline
            response = await self.client.messages.create(
                model=self.model,
                system=prompt,
                messages=[
                    {"role": "user", "content": headline}
                ],
                temperature=0.0,  # Deterministic for consistency
                max_tokens=10,    # Only need "TRADE" or "SKIP"
            )

            # Parse response
            result = response.content[0].text.strip().upper()

            # Normalize to TRADE or SKIP
            if "TRADE" in result:
                classification = "TRADE"
                self._stats["trade_signals"] += 1
            else:
                classification = "SKIP"
                self._stats["skip_signals"] += 1

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            self._stats["total_classified"] += 1

            # Update average latency
            n = self._stats["total_classified"]
            self._stats["avg_latency_ms"] = (
                (self._stats["avg_latency_ms"] * (n - 1) + latency_ms) / n
            )

            logger.info(
                f"🎯 Healthcare classification: {classification}",
                ticker=ticker,
                industry=industry,
                headline=headline[:60],
                latency_ms=round(latency_ms, 1)
            )

            return classification, industry, latency_ms

        except Exception as e:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(
                "Anthropic API error",
                ticker=ticker,
                error=str(e),
                headline=headline[:50]
            )
            self._stats["errors"] += 1
            return "SKIP", industry, latency_ms

    async def classify_batch(
        self,
        headlines: list[Tuple[str, str]],  # List of (headline, ticker) tuples
    ) -> list[Tuple[str, Optional[str], float]]:
        """
        Classify multiple headlines in parallel.

        Args:
            headlines: List of (headline, ticker) tuples

        Returns:
            List of (classification, industry, latency_ms) tuples
        """
        tasks = [self.classify(headline, ticker) for headline, ticker in headlines]
        return await asyncio.gather(*tasks)

    def get_stats(self) -> Dict:
        """Get classifier statistics."""
        return {
            **self._stats,
            "supported_industries": list(SUPPORTED_INDUSTRIES.keys()),
            "model": self.model,
            "prompts_loaded": list(self._prompts.keys()),
        }
