#!/usr/bin/env python3
"""
Simulate trade filter pipeline for specific headlines.
Tests whether a headline would pass all filters and be traded.

Usage:
    python scripts/simulate_trade_filters.py --ticker OGEN --headline "..."
    python scripts/simulate_trade_filters.py --from-recall  # Test from recall records
"""

import asyncio
import json
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
from decimal import Decimal

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load environment
from dotenv import load_dotenv
load_dotenv()

from newsflash.infra.classification.sector_classifier import SectorClassifier


class MockMetadataCache:
    """Mock metadata cache for simulation that stores industry data."""

    def __init__(self):
        self.data = {}

    def set(self, ticker: str, industry: str, sector: str):
        self.data[ticker] = {"industry": industry, "sector": sector}

    async def get_permanent(self, ticker: str):
        return self.data.get(ticker)


class FilterSimulator:
    """Simulates the full trade filter pipeline."""

    # Current filter thresholds (from auto_trade.py and classification service)
    MIN_MARKET_CAP_MILLIONS = 2.0
    MAX_SPREAD_PCT = 10.0
    MIN_PRICE = 0.25  # Sub-penny manipulation filter
    # MAX_PRICE removed - AI classification handles headline quality

    # Confluence thresholds
    VOLUME_SURGE_THRESHOLD = 2000
    PRICE_EXCURSION_THRESHOLD = 0.01  # 1%
    BUYING_PRESSURE_THRESHOLD = 0.80  # 80%

    def __init__(self, use_ai: bool = True):
        self.use_ai = use_ai
        self.classifier = None
        self.metadata_cache = MockMetadataCache()
        if use_ai:
            api_key = os.getenv("GROQ_API_KEY")
            if api_key:
                self.classifier = SectorClassifier(api_key=api_key, metadata_cache=self.metadata_cache)
            else:
                print("Warning: GROQ_API_KEY not found, AI classification disabled")
                self.use_ai = False

    def set_ticker_metadata(self, ticker: str, industry: str, sector: str):
        """Set metadata for a ticker before classification."""
        self.metadata_cache.set(ticker, industry, sector)

    def check_prefilters(self, ticker: str, price: float, market_cap: float,
                         spread_pct: float, industry: str = None) -> tuple[bool, str]:
        """
        Check pre-classification filters.
        Returns (passed, reason)
        """
        filters_passed = []
        filters_failed = []

        # 1. Market cap filter
        if market_cap < self.MIN_MARKET_CAP_MILLIONS:
            filters_failed.append(f"market_cap_too_low (${market_cap:.1f}M < ${self.MIN_MARKET_CAP_MILLIONS}M)")
        else:
            filters_passed.append(f"market_cap_ok (${market_cap:.1f}M)")

        # 2. Spread filter
        if spread_pct > self.MAX_SPREAD_PCT:
            filters_failed.append(f"spread_too_wide ({spread_pct:.1f}% > {self.MAX_SPREAD_PCT}%)")
        else:
            filters_passed.append(f"spread_ok ({spread_pct:.1f}%)")

        # 3. Min price filter (sub-penny manipulation)
        if price < self.MIN_PRICE:
            filters_failed.append(f"price_too_low (${price:.2f} < ${self.MIN_PRICE})")
        else:
            filters_passed.append(f"price_ok (${price:.2f})")

        passed = len(filters_failed) == 0

        if passed:
            return True, f"PASSED prefilters: {', '.join(filters_passed)}"
        else:
            return False, f"FAILED prefilters: {', '.join(filters_failed)}"

    async def check_ai_classification(self, ticker: str, headline: str,
                                       industry: str, price: float,
                                       market_cap: float) -> tuple[bool, str, str]:
        """
        Check AI classification.
        Returns (should_trade, classification, reason)
        """
        if not self.use_ai or not self.classifier:
            return True, "SKIPPED", "AI classification disabled"

        try:
            # SectorClassifier.classify returns (classification, sector, industry, confidence)
            classification, sector, detected_industry, confidence = await self.classifier.classify(
                headline=headline,
                ticker=ticker,
            )

            # Only TRADE classifications trigger trades
            should_trade = classification.upper() == "TRADE"

            return should_trade, classification, f"AI: {classification} (conf: {confidence:.2f}, industry: {detected_industry})"

        except Exception as e:
            return False, "ERROR", f"AI classification error: {str(e)}"

    def check_confluence(self, volume: int = 0, price_excursion_pct: float = 0,
                         buying_pressure: float = 0, imbalance_ratio: float = 0) -> tuple[bool, int, str]:
        """
        Check confluence signals (simulated - would need real market data).
        Returns (passed, score, reason)
        """
        score = 0
        signals = []

        # Volume surge
        if volume >= self.VOLUME_SURGE_THRESHOLD:
            score += 1
            signals.append(f"volume_surge ({volume:,} >= {self.VOLUME_SURGE_THRESHOLD:,})")
        else:
            signals.append(f"no_volume_surge ({volume:,} < {self.VOLUME_SURGE_THRESHOLD:,})")

        # Price excursion
        if price_excursion_pct >= self.PRICE_EXCURSION_THRESHOLD * 100:
            score += 1
            signals.append(f"price_excursion ({price_excursion_pct:.1f}% >= 1%)")
        else:
            signals.append(f"no_price_excursion ({price_excursion_pct:.1f}% < 1%)")

        # Buying pressure
        if buying_pressure >= self.BUYING_PRESSURE_THRESHOLD:
            score += 1
            signals.append(f"buying_pressure ({buying_pressure:.0%} >= 80%)")
        else:
            signals.append(f"no_buying_pressure ({buying_pressure:.0%} < 80%)")

        # Need at least 2 of 3 signals
        passed = score >= 2

        return passed, score, f"Confluence score {score}/3: {', '.join(signals)}"

    async def simulate_full_pipeline(self, ticker: str, headline: str,
                                     price: float, market_cap: float,
                                     spread_pct: float, industry: str,
                                     volume: int = 0, price_excursion_pct: float = 0,
                                     buying_pressure: float = 0, imbalance_ratio: float = 0) -> dict:
        """
        Simulate the full filter pipeline.
        Returns detailed results dict.
        """
        results = {
            "ticker": ticker,
            "headline": headline[:80] + "..." if len(headline) > 80 else headline,
            "price": price,
            "market_cap": market_cap,
            "spread_pct": spread_pct,
            "industry": industry,
            "steps": [],
            "would_trade": False,
            "blocked_at": None
        }

        # Step 1: Pre-filters
        passed, reason = self.check_prefilters(ticker, price, market_cap, spread_pct, industry)
        results["steps"].append({
            "step": "1. Pre-filters",
            "passed": passed,
            "reason": reason
        })

        if not passed:
            results["blocked_at"] = "pre-filters"
            return results

        # Step 2: AI Classification
        should_trade, classification, reason = await self.check_ai_classification(
            ticker, headline, industry, price, market_cap
        )
        results["ai_classification"] = classification
        results["steps"].append({
            "step": "2. AI Classification",
            "passed": should_trade,
            "reason": reason
        })

        if not should_trade:
            results["blocked_at"] = "ai_classification"
            return results

        # Step 3: Confluence check (simulated with provided data)
        passed, score, reason = self.check_confluence(
            volume, price_excursion_pct, buying_pressure, imbalance_ratio
        )
        results["confluence_score"] = score
        results["steps"].append({
            "step": "3. Confluence Check",
            "passed": passed,
            "reason": reason
        })

        if not passed:
            results["blocked_at"] = "confluence"
            # Note: In reality, surge window would be checked next
            results["steps"].append({
                "step": "4. Surge Window (8s)",
                "passed": "WOULD_CHECK",
                "reason": "Would check surge window if confluence fails"
            })
            return results

        # All passed
        results["would_trade"] = True
        return results


def print_simulation_results(results: dict):
    """Pretty print simulation results."""
    print("\n" + "=" * 80)
    print(f"FILTER SIMULATION: {results['ticker']}")
    print("=" * 80)
    print(f"Headline: {results['headline']}")
    print(f"Price: ${results['price']:.2f} | Market Cap: ${results['market_cap']:.1f}M | Spread: {results['spread_pct']:.1f}%")
    print(f"Industry: {results['industry']}")
    print()

    for step in results["steps"]:
        status = "✅ PASS" if step["passed"] == True else ("⏳ CHECK" if step["passed"] == "WOULD_CHECK" else "❌ FAIL")
        print(f"{step['step']}: {status}")
        print(f"   {step['reason']}")

    print()
    if results["would_trade"]:
        print("🚨 RESULT: WOULD TRADE")
    else:
        print(f"🛑 RESULT: BLOCKED at {results['blocked_at']}")
    print()


async def test_from_recall_records(tickers: list[str], date_filter: str = None,
                                   use_ai: bool = True):
    """Test specific tickers from recall records."""
    base_path = Path("/Users/seb/dev/newsflash/tmp/statistics/recall")

    # Find records for these tickers
    records_to_test = []

    for json_file in base_path.rglob("*.json"):
        if date_filter and date_filter not in str(json_file):
            continue

        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
        except:
            continue

        for record in data.get('records', []):
            record_tickers = record.get('tickers', [])
            for ticker in tickers:
                if ticker in record_tickers:
                    records_to_test.append(record)

    if not records_to_test:
        print(f"No records found for tickers: {tickers}")
        return

    # Test each record
    print(f"\nFound {len(records_to_test)} records to test")

    # Test with configurable filters (max price filter removed from system)
    simulator = FilterSimulator(use_ai=use_ai)

    for record in records_to_test:
        ticker = record.get('tickers', ['UNKNOWN'])[0]
        headline = record.get('title', '')

        meta = record.get('ticker_metadata', {}).get(ticker, {})
        price = meta.get('price', 0)
        market_cap = meta.get('market_cap_millions', 0)
        industry = meta.get('industry', 'Unknown')

        nbbo = record.get('initial_nbbo', {})
        spread_pct = (nbbo.get('spread', 0) / nbbo.get('mid', 1) * 100) if nbbo.get('mid') else 0

        vol_stats = record.get('volume_stats', {}).get(ticker, {})
        volume = vol_stats.get('window_volume', 0)
        price_excursion = vol_stats.get('max_excursion_pct', 0)
        imbalance = vol_stats.get('imbalance_ratio', 0)
        # Convert imbalance to buying pressure approximation
        buying_pressure = (imbalance + 1) / 2 if imbalance is not None else 0

        # Set ticker metadata for AI classification
        sector = meta.get('sector', 'Healthcare')
        simulator.set_ticker_metadata(ticker, industry, sector)

        results = await simulator.simulate_full_pipeline(
            ticker=ticker,
            headline=headline,
            price=price,
            market_cap=market_cap,
            spread_pct=spread_pct,
            industry=industry,
            volume=volume,
            price_excursion_pct=price_excursion,
            buying_pressure=buying_pressure,
            imbalance_ratio=imbalance or 0
        )

        # Add actual outcome if available
        highest = record.get('highest_price_during_hold', {})
        if highest:
            results["actual_max_excursion"] = highest.get('percent_gain_from_entry', 0)
            results["actual_outcome"] = "WIN" if highest.get('percent_gain_from_entry', 0) >= 10 else "LOSS"

        print_simulation_results(results)

        if highest:
            print(f"📊 ACTUAL OUTCOME: {results.get('actual_outcome', 'N/A')} (max +{results.get('actual_max_excursion', 0):.1f}%)")
            print()


async def main():
    parser = argparse.ArgumentParser(description="Simulate trade filter pipeline")
    parser.add_argument('--ticker', type=str, help='Ticker to test')
    parser.add_argument('--headline', type=str, help='Headline to test')
    parser.add_argument('--from-recall', action='store_true', help='Test from recall records')
    parser.add_argument('--tickers', type=str, nargs='+', default=['OGEN', 'GTBP'],
                        help='Tickers to find in recall records')
    parser.add_argument('--date', type=str, default='2026/02', help='Date filter for recall records')
    parser.add_argument('--no-ai', action='store_true', help='Skip AI classification')

    args = parser.parse_args()

    if args.from_recall:
        await test_from_recall_records(args.tickers, args.date,
                                       use_ai=not args.no_ai)
    elif args.ticker and args.headline:
        simulator = FilterSimulator(use_ai=not args.no_ai)

        # Would need additional params for full test
        results = await simulator.simulate_full_pipeline(
            ticker=args.ticker,
            headline=args.headline,
            price=1.0,  # Would need real data
            market_cap=10.0,
            spread_pct=5.0,
            industry="Biotechnology"
        )
        print_simulation_results(results)
    else:
        # Default: test OGEN and GTBP from Feb 3rd
        await test_from_recall_records(['OGEN', 'GTBP'], '2026/02/week_6/03',
                                       use_ai=not args.no_ai)


if __name__ == "__main__":
    asyncio.run(main())
