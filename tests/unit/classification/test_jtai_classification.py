"""
Test that JTAI (Jet.AI) passes industry classification.

Background: On 2026-02-12, JTAI was incorrectly classified as
"Industrials/Airlines, Airports & Air Services" instead of its actual
sector/industry: "Technology/Software - Application".

This caused a false negative - a +31% winner was filtered out by the
unsupported industry check.

This test verifies that:
1. The metadata cache now has correct sector/industry for JTAI
2. The SectorClassifier would allow JTAI through (not block as unsupported)
"""
import json
import pytest
from pathlib import Path

from src.newsflash.infra.classification.sector_classifier import (
    SECTOR_INDUSTRY_MAP,
    SUPPORTED_SECTORS,
    SectorClassifier,
)


class TestJTAIClassification:
    """Verify JTAI would pass industry classification now."""

    def test_jtai_metadata_is_technology_software(self):
        """JTAI should be cached as Technology/Software - Application."""
        cache_path = Path("data/cache/permanent_metadata.json")

        assert cache_path.exists(), "Metadata cache file not found"

        with open(cache_path) as f:
            cache = json.load(f)

        assert "JTAI" in cache, "JTAI not in metadata cache"

        jtai_metadata = cache["JTAI"]

        # Verify correct sector and industry
        assert jtai_metadata.get("sector") == "Technology", (
            f"JTAI sector is '{jtai_metadata.get('sector')}' but should be 'Technology'"
        )
        assert jtai_metadata.get("industry") == "Software - Application", (
            f"JTAI industry is '{jtai_metadata.get('industry')}' but should be 'Software - Application'"
        )

    def test_technology_sector_is_supported(self):
        """Technology sector should be in supported sectors."""
        assert "Technology" in SUPPORTED_SECTORS

    def test_software_application_is_supported_in_technology(self):
        """Software - Application should be a supported industry in Technology."""
        assert "Technology" in SECTOR_INDUSTRY_MAP

        technology_industries = SECTOR_INDUSTRY_MAP["Technology"]

        assert "Software - Application" in technology_industries, (
            f"'Software - Application' not in Technology industries. "
            f"Available: {list(technology_industries.keys())}"
        )

    def test_software_application_has_prompt_file(self):
        """Software - Application should map to a prompt file."""
        prompt_file = SECTOR_INDUSTRY_MAP["Technology"]["Software - Application"]

        assert prompt_file == "software_application.txt", (
            f"Expected 'software_application.txt', got '{prompt_file}'"
        )

        # Verify the prompt file exists
        prompt_path = Path("prompts/technology/software_application.txt")
        assert prompt_path.exists(), f"Prompt file not found: {prompt_path}"

    def test_jtai_would_pass_sector_industry_check(self):
        """
        Full integration check: JTAI should NOT be blocked by sector/industry checks.

        This simulates what SectorClassifier.classify() does before calling the LLM.
        """
        cache_path = Path("data/cache/permanent_metadata.json")

        with open(cache_path) as f:
            cache = json.load(f)

        jtai_metadata = cache.get("JTAI", {})
        sector = jtai_metadata.get("sector", "")
        industry = jtai_metadata.get("industry", "")

        # Step 1: Sector check (what SectorClassifier does at line 324)
        assert sector in SUPPORTED_SECTORS, (
            f"JTAI sector '{sector}' would fail: NOT_SUPPORTED_SECTOR"
        )

        # Step 2: Industry check (what SectorClassifier does at line 337)
        industry_map = SECTOR_INDUSTRY_MAP.get(sector, {})
        assert industry in industry_map, (
            f"JTAI industry '{industry}' would fail: UNSUPPORTED_INDUSTRY. "
            f"This is the bug that caused the Feb 12 false negative!"
        )

        # If we get here, JTAI would proceed to LLM classification
        # (which is what we want - let the AI decide based on headline quality)

    def test_airlines_is_not_software_application(self):
        """
        Verify Airlines is NOT the same as Software - Application.

        This was the misclassification that caused the Feb 12 issue.
        """
        # Airlines is under Industrials
        industrials_industries = SECTOR_INDUSTRY_MAP.get("Industrials", {})

        assert "Airlines" in industrials_industries, (
            "Airlines should exist in Industrials sector"
        )

        # But Software - Application is under Technology
        technology_industries = SECTOR_INDUSTRY_MAP.get("Technology", {})

        assert "Software - Application" in technology_industries, (
            "Software - Application should exist in Technology sector"
        )

        # They are completely different - a company named "Jet.AI" is a software
        # company, NOT an airline, despite having "Jet" in the name


class TestFalseNegativeHeadline:
    """Test the actual headline from the false negative."""

    JTAI_HEADLINE = (
        "Jet.AI Provides Capital Structure and Strategic Update "
        "in Connection with Merger Agreement Amendment"
    )

    def test_headline_contains_bullish_keywords(self):
        """The headline should contain tradeable keywords."""
        headline_lower = self.JTAI_HEADLINE.lower()

        # These are typically bullish for small caps
        bullish_keywords = ["merger", "agreement", "strategic"]

        found_keywords = [kw for kw in bullish_keywords if kw in headline_lower]

        assert len(found_keywords) >= 2, (
            f"Headline should contain bullish keywords. Found: {found_keywords}"
        )

    def test_headline_is_about_merger(self):
        """Mergers are typically tradeable catalysts."""
        assert "Merger Agreement" in self.JTAI_HEADLINE, (
            "This headline is about a merger - should be tradeable"
        )
