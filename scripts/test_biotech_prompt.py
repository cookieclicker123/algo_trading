#!/usr/bin/env python3
"""Test biotech prompt with various headlines."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from newsflash.infra.classification.sector_classifier import SectorClassifier


class MockCache:
    def __init__(self):
        self.data = {}

    def set(self, ticker, industry, sector):
        self.data[ticker] = {"industry": industry, "sector": sector}

    async def get_permanent(self, ticker):
        return self.data.get(ticker)


async def test_headlines():
    cache = MockCache()
    classifier = SectorClassifier(api_key=os.getenv("GROQ_API_KEY"), metadata_cache=cache)

    # Test cases: (ticker, headline, expected) - winners should be TRADE
    test_cases = [
        # Should SKIP (IND related)
        ("GTBP", "GT Biopharma Announces FDA Clearance of Investigational New Drug (IND) Application", "SKIP"),
        ("OGEN", "Oragenics Partners with DUCK FLATS Pharma to Support FDA IND Readiness", "SKIP"),
        ("TEST1", "FDA Clears IND Application for Phase 1 Cancer Trial", "SKIP"),

        # Should TRADE (real catalysts from top winners)
        ("GRI", "GRI Bio Delivers Compelling New Phase 2a Gene Expression Data Demonstrating Improvements in Key Drivers", "TRADE"),
        ("CORT", "Overall Survival Primary Endpoint Met in Corcept's Pivotal Phase 3 ROSELLA Trial of Relacorilant", "TRADE"),
        ("TEST2", "FDA Approves Drug for Treatment of Advanced Cancer", "TRADE"),
        ("TEST3", "Receives Breakthrough Therapy Designation from FDA", "TRADE"),
        ("TEST4", "Partners with Pfizer on Gene Therapy Development", "TRADE"),
        ("TEST5", "Phase 3 Trial Meets Primary Endpoint with 45% Tumor Reduction", "TRADE"),

        # Should SKIP (other weak signals)
        ("PMN", "ProMIS Neurosciences Announces Up to $175 Million Private Placement Financing", "SKIP"),
        ("TEST6", "To Present at J.P. Morgan Healthcare Conference", "SKIP"),
        ("TEST7", "Reports Positive Interim Phase 2a Results", "SKIP"),
    ]

    print("Testing biotech headlines with updated prompt:\n")
    print(f"{'Ticker':<8} {'Expected':<8} {'Actual':<8} {'Match':<6} Headline")
    print("-" * 110)

    correct = 0
    total = 0

    for ticker, headline, expected in test_cases:
        cache.set(ticker, "Biotechnology", "Healthcare")
        try:
            classification, sector, industry, conf = await classifier.classify(headline, ticker)
            match = "✅" if classification == expected else "❌"
            if classification == expected:
                correct += 1
            total += 1
            print(f"{ticker:<8} {expected:<8} {classification:<8} {match:<6} {headline[:70]}...")
        except Exception as e:
            print(f"{ticker:<8} {expected:<8} {'ERROR':<8} ❌      {str(e)[:50]}")
        await asyncio.sleep(0.3)  # Rate limiting

    print(f"\n{'='*50}")
    print(f"Results: {correct}/{total} correct ({correct/total*100:.0f}%)")


if __name__ == "__main__":
    asyncio.run(test_headlines())
