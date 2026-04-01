"""
Headline type classifier - AI-based classification for statistical analysis.

Runs in BACKGROUND only - never blocks trade execution.
Uses industry-specific type lists for accurate classification.
Returns ONLY the type, no explanation.
"""
import os
from pathlib import Path
from typing import Optional, Dict
from groq import AsyncGroq

from ...utils.logging_config import get_logger

logger = get_logger(__name__)

# Map sector/industry to prompt file
INDUSTRY_PROMPT_MAP: Dict[str, str] = {
    # Healthcare
    "Biotechnology": "healthcare_biotechnology.txt",
    "Medical Devices": "healthcare_biotechnology.txt",
    "Drug Manufacturers - Specialty & Generic": "healthcare_biotechnology.txt",
    "Diagnostics & Research": "healthcare_biotechnology.txt",
    "Health Information Services": "technology_software.txt",
    "Medical Instruments & Supplies": "healthcare_biotechnology.txt",
    "Medical Care Facilities": "healthcare_biotechnology.txt",

    # Technology
    "Semiconductors": "technology_semiconductors.txt",
    "Semiconductor Equipment & Materials": "technology_semiconductors.txt",
    "Software - Application": "technology_software.txt",
    "Software - Infrastructure": "technology_software.txt",
    "Information Technology Services": "technology_software.txt",
    "Communication Equipment": "technology_software.txt",
    "Computer Hardware": "technology_software.txt",
    "Electronic Components": "technology_semiconductors.txt",
    "Solar": "technology_software.txt",
    "Consumer Electronics": "consumer_cyclical_general.txt",
    "Scientific & Technical Instruments": "technology_software.txt",

    # Industrials
    "Aerospace & Defense": "industrials_aerospace_defense.txt",
    "Specialty Industrial Machinery": "industrials_general.txt",
    "Electrical Equipment & Parts": "industrials_general.txt",
    "Specialty Business Services": "industrials_general.txt",
    "Engineering & Construction": "industrials_general.txt",
    "Security & Protection Services": "industrials_general.txt",
    "Pollution & Treatment Controls": "industrials_general.txt",
    "Consulting Services": "industrials_general.txt",
    "Building Products & Equipment": "industrials_general.txt",
    "Integrated Freight & Logistics": "industrials_general.txt",
    "Waste Management": "industrials_general.txt",
    "Metal Fabrication": "industrials_general.txt",
    "Staffing & Employment Services": "industrials_general.txt",
    "Marine Shipping": "industrials_general.txt",
    "Airlines": "industrials_general.txt",
    "Railroads": "industrials_general.txt",
    "Trucking": "industrials_general.txt",
    "Farm & Heavy Construction Machinery": "industrials_general.txt",

    # Consumer Cyclical
    "Auto Parts": "consumer_cyclical_general.txt",
    "Auto Manufacturers": "consumer_cyclical_general.txt",
    "Specialty Retail": "consumer_cyclical_general.txt",
    "Internet Retail": "consumer_cyclical_general.txt",
    "Apparel Retail": "consumer_cyclical_general.txt",
    "Restaurants": "consumer_cyclical_general.txt",
    "Leisure": "consumer_cyclical_general.txt",
    "Recreational Vehicles": "consumer_cyclical_general.txt",
    "Residential Construction": "consumer_cyclical_general.txt",

    # Basic Materials
    "Specialty Chemicals": "basic_materials_mining.txt",
    "Agricultural Inputs": "basic_materials_mining.txt",
    "Gold": "basic_materials_mining.txt",
    "Other Precious Metals & Mining": "basic_materials_mining.txt",
    "Other Industrial Metals & Mining": "basic_materials_mining.txt",

    # Financial Services
    "Capital Markets": "financial_services_general.txt",
    "Asset Management": "financial_services_general.txt",
    "Banks - Regional": "financial_services_general.txt",
    "Banks - Diversified": "financial_services_general.txt",
    "Insurance - Property & Casualty": "financial_services_general.txt",
    "Credit Services": "financial_services_general.txt",

    # Communication Services
    "Electronic Gaming & Multimedia": "consumer_cyclical_general.txt",

    # Consumer Defensive
    "Education & Training Services": "industrials_general.txt",
    "Food Distribution": "consumer_cyclical_general.txt",
    "Household & Personal Products": "consumer_cyclical_general.txt",
}


class HeadlineTypeClassifier:
    """
    Lightweight AI classifier for headline types.

    - Uses Groq for fast inference
    - Industry-specific type lists
    - Returns ONLY the type (no explanation)
    - For background statistical collection only
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self._prompt_cache: Dict[str, str] = {}
        self._prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts" / "headline_types"
        self._triage_prompt: Optional[str] = None

    def _load_prompt(self, industry: str) -> Optional[str]:
        """Load industry-specific prompt template."""
        if industry in self._prompt_cache:
            return self._prompt_cache[industry]

        prompt_file = INDUSTRY_PROMPT_MAP.get(industry)
        if not prompt_file:
            # Fallback to general industrials
            prompt_file = "industrials_general.txt"

        prompt_path = self._prompts_dir / prompt_file

        if not prompt_path.exists():
            logger.debug(f"No headline type prompt for {industry}")
            return None

        try:
            with open(prompt_path, "r") as f:
                prompt = f.read()
                self._prompt_cache[industry] = prompt
                return prompt
        except Exception as e:
            logger.debug(f"Failed to load headline type prompt: {e}")
            return None

    async def classify(
        self,
        headline: str,
        industry: str,
        timeout: float = 5.0,
    ) -> Optional[str]:
        """
        Classify headline into a type.

        Args:
            headline: The article headline
            industry: Industry for type list selection
            timeout: Max seconds to wait

        Returns:
            Headline type string or None if failed
        """
        if not self.api_key:
            return None

        prompt_template = self._load_prompt(industry)
        if not prompt_template:
            return None

        # Build the prompt
        prompt = prompt_template.replace("{headline}", headline)

        try:
            client = AsyncGroq(api_key=self.api_key)

            response = await client.chat.completions.create(
                model="llama-3.1-8b-instant",  # Fast, cheap model for simple classification
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,  # Deterministic
                max_tokens=20,  # Only need the type word
                timeout=timeout,
            )

            if response.choices and response.choices[0].message.content:
                # Clean up response - just the type, lowercase, no whitespace
                result = response.choices[0].message.content.strip().lower()
                # Remove any punctuation or extra words
                result = result.split()[0] if result else None
                result = result.replace(".", "").replace(",", "") if result else None
                return result

            return None

        except Exception as e:
            logger.debug(f"Headline classification failed: {e}")
            return None

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
            client = AsyncGroq(api_key=self.api_key)

            response = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=20,
                timeout=timeout,
            )

            if response.choices and response.choices[0].message.content:
                result = response.choices[0].message.content.strip().lower()
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
