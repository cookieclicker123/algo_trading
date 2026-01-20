"""
Integration test for prompt classification - backtests prompts against historical winners.

Tests:
1. SDST (Electrical Equipment) - Air Permit headline
2. TWG (Food Distribution) - 9 historical winners + today's acquisition headline

Uses REAL Groq API calls to verify classification behavior.
"""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from groq import Groq

# Load .env file
load_dotenv()

# Ensure src is on path
import sys
PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


@pytest.fixture
def groq_client():
    """Create Groq client from environment."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        pytest.skip("GROQ_API_KEY must be set to run this integration test")
    return Groq(api_key=api_key)


def classify_headline(client: Groq, prompt: str, headline: str) -> str:
    """Classify a headline using the given prompt. Returns 'TRADE' or 'SKIP'."""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": headline}
        ],
        temperature=0.0,
        max_tokens=10,
    )
    result = response.choices[0].message.content.strip().upper()
    return "TRADE" if "TRADE" in result else "SKIP"


# =============================================================================
# ELECTRICAL EQUIPMENT - SDST Air Permit Test
# =============================================================================

SDST_HEADLINE = "Stardust Power Secures Air Permit; Muskogee Lithium Refinery Now Permitted For Construction and Commissioning"

OLD_ELECTRICAL_PROMPT_WITHOUT_PERMITS = """ELECTRICAL EQUIPMENT & PARTS HEADLINE CLASSIFIER

You classify Electrical Equipment news headlines for high-frequency trading.
Output TRADE (expect 10%+ move) or SKIP (no significant move expected).

TRADE SIGNALS:
- Contracts (utility, EV infrastructure, government)
- Partnerships (EV/automotive, utility, technology)
- M&A Activity (acquisitions)
- Product Launches (new technology, EV-related)

SKIP SIGNALS:
- Earnings (EPS, revenue, backlog)
- Conferences
- Policy/Regulatory commentary
- Administrative (offerings, executive changes)

RESPOND: TRADE or SKIP (no explanation)
"""


@pytest.fixture
def electrical_equipment_prompt():
    """Load current Electrical Equipment prompt."""
    prompt_path = PROJECT_ROOT / "prompts" / "industrials" / "electrical_equipment.txt"
    if not prompt_path.exists():
        pytest.skip(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text()


def test_sdst_old_vs_new_prompt(groq_client, electrical_equipment_prompt):
    """Test SDST Air Permit headline: old prompt SKIP, new prompt TRADE."""
    print("\n" + "=" * 80)
    print("SDST ELECTRICAL EQUIPMENT TEST")
    print("=" * 80)
    print(f"Headline: {SDST_HEADLINE}")
    print(f"Actual move: +43.18%")
    print()

    old_result = classify_headline(groq_client, OLD_ELECTRICAL_PROMPT_WITHOUT_PERMITS, SDST_HEADLINE)
    new_result = classify_headline(groq_client, electrical_equipment_prompt, SDST_HEADLINE)

    print(f"OLD prompt (no permits): {old_result}")
    print(f"NEW prompt (with permits): {new_result}")

    assert new_result == "TRADE", f"New prompt should classify SDST as TRADE, got {new_result}"
    print("\n✅ PASSED: New Electrical Equipment prompt correctly classifies permits as TRADE")


# =============================================================================
# FOOD DISTRIBUTION - TWG + Historical Winners Test
# =============================================================================

# 9 historical Food Distribution headlines from backtest data + today's TWG
FOOD_DISTRIBUTION_HEADLINES = [
    # Today's trade (should be TRADE - acquisition)
    {
        "headline": "TWG Announces Entry into of a Material Definitive Agreement for the Acquisition of Wine Authentication and Tracking System and Wine Trading Business",
        "ticker": "TWG",
        "move": "+44.85%",
        "expected": "TRADE",
        "reason": "Material Definitive Agreement + Acquisition = TRADE"
    },
    # Historical winner #1 - IPO completion (TRADE per prompt)
    {
        "headline": "Reported Earlier, Top Wealth Group Successfully Closes IPO, Raises $8M",
        "ticker": "TWG",
        "move": "+103.59%",
        "expected": "TRADE",
        "reason": "IPO completion with capital raised = TRADE"
    },
    # Historical winner #2 - Legally-binding MOU (TRADE per prompt)
    {
        "headline": "Top Wealth Group Holding Enters Into Legally-Binding MOU With Jilin Xiuzheng For Proposed Injection Of Animal-Related Pharmaceutical Products, Foods And Supplements Business",
        "ticker": "TWG",
        "move": "+10.69%",
        "expected": "TRADE",
        "reason": "Legally-binding MOU + Business injection = TRADE"
    },
    # Historical #3 - Compliance extension (SKIP per prompt)
    {
        "headline": "Top Wealth Group Granted 180-Day Extension By Nasdaq To Regain Compliance With $1 Minimum Bid Price",
        "ticker": "TWG",
        "move": "+52.4%",
        "expected": "SKIP",
        "reason": "Nasdaq compliance extension = SKIP"
    },
    # Historical #4 - Same compliance headline, different day (SKIP)
    {
        "headline": "Top Wealth Group Granted 180-Day Extension By Nasdaq To Regain Compliance With $1 Minimum Bid Price",
        "ticker": "TWG",
        "move": "+64.84%",
        "expected": "SKIP",
        "reason": "Nasdaq compliance extension = SKIP"
    },
    # Historical #5 - IPO pricing (SKIP per prompt)
    {
        "headline": "Top Wealth Group Prices Initial Public Offering Of 2M Ordinary Shares At $4/Share",
        "ticker": "TWG",
        "move": "+18.61%",
        "expected": "SKIP",
        "reason": "IPO pricing = SKIP (dilutive)"
    },
    # Historical #6 - Same IPO pricing, different day (SKIP)
    {
        "headline": "Top Wealth Group Prices Initial Public Offering Of 2M Ordinary Shares At $4/Share",
        "ticker": "TWG",
        "move": "+10.0%",
        "expected": "SKIP",
        "reason": "IPO pricing = SKIP (dilutive)"
    },
    # Historical #7 - Earnings (SKIP per prompt)
    {
        "headline": "CORRECTION: HF Foods Group Q4 EPS $0.11 Up From $0.05 YoY, Sales $305.28M Up From $280.87M YoY",
        "ticker": "HFFG",
        "move": "+24.18%",
        "expected": "SKIP",
        "reason": "Earnings = always SKIP"
    },
    # Historical #8 - Earnings (SKIP per prompt)
    {
        "headline": "United Natural Foods Beats Q1 Estimates On Positive Volume Trends, Lifts FY25 Guidance",
        "ticker": "UNFI",
        "move": "+14.18%",
        "expected": "SKIP",
        "reason": "Earnings = always SKIP"
    },
    # Historical #9 - Earnings (SKIP per prompt)
    {
        "headline": "G. Willi-Food Intl Q3 EPS $0.40 Up From $0.09 YoY, Sales $41.20M Up From $32.40M YoY",
        "ticker": "WILC",
        "move": "+22.44%",
        "expected": "SKIP",
        "reason": "Earnings = always SKIP"
    },
]


@pytest.fixture
def food_distribution_prompt():
    """Load current Food Distribution prompt."""
    prompt_path = PROJECT_ROOT / "prompts" / "consumer_defensive" / "food_distribution.txt"
    if not prompt_path.exists():
        pytest.skip(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text()


def test_food_distribution_all_headlines(groq_client, food_distribution_prompt):
    """
    Test Food Distribution prompt against all 10 headlines (9 historical + today's TWG).

    NOTE: We do NOT trade earnings, compliance extensions, or IPO pricing.
    These are correctly classified as SKIP even though the stocks moved.

    Expected results:
    - 3 TRADE: Today's acquisition, IPO completion, MOU
    - 7 SKIP: Compliance (2), IPO pricing (2), Earnings (3)
    """
    print("\n" + "=" * 80)
    print("FOOD DISTRIBUTION BACKTEST (10 headlines)")
    print("=" * 80)

    results = []
    for item in FOOD_DISTRIBUTION_HEADLINES:
        classification = classify_headline(groq_client, food_distribution_prompt, item["headline"])
        passed = classification == item["expected"]
        status = "✅" if passed else "❌"
        results.append({**item, "actual": classification, "passed": passed})

        headline_short = item["headline"][:70] + "..." if len(item["headline"]) > 70 else item["headline"]
        print(f"{status} {item['ticker']} ({item['move']}): {classification}")
        print(f"   Headline: {headline_short}")
        print(f"   Expected: {item['expected']} - {item['reason']}")
        print()

    # Summary
    passed_count = sum(1 for r in results if r["passed"])
    total = len(results)
    trade_count = sum(1 for r in results if r["actual"] == "TRADE")
    skip_count = sum(1 for r in results if r["actual"] == "SKIP")

    print("=" * 80)
    print(f"RESULTS: {passed_count}/{total} correct classifications")
    print(f"TRADE signals: {trade_count} | SKIP signals: {skip_count}")
    print("=" * 80)

    # Show failures
    failed = [r for r in results if not r["passed"]]
    if failed:
        print("\nFAILURES:")
        for r in failed:
            print(f"  - {r['ticker']}: Expected {r['expected']}, got {r['actual']}")
            print(f"    Headline: {r['headline'][:80]}...")

    assert passed_count == total, f"Expected {total}/{total}, got {passed_count}/{total}"


def test_food_distribution_tradeable_only(groq_client, food_distribution_prompt):
    """
    Test ONLY the tradeable Food Distribution headlines (acquisitions, MOUs, IPO completions).

    These are the headlines we WANT to trade - should be 3/3 TRADE.
    """
    print("\n" + "=" * 80)
    print("FOOD DISTRIBUTION - TRADEABLE HEADLINES ONLY")
    print("=" * 80)

    tradeable_headlines = [h for h in FOOD_DISTRIBUTION_HEADLINES if h["expected"] == "TRADE"]

    results = []
    for item in tradeable_headlines:
        classification = classify_headline(groq_client, food_distribution_prompt, item["headline"])
        passed = classification == "TRADE"
        results.append({**item, "actual": classification, "passed": passed})

        status = "✅" if passed else "❌"
        print(f"{status} {item['ticker']} ({item['move']}): {classification}")
        print(f"   {item['headline'][:80]}...")
        print()

    passed_count = sum(1 for r in results if r["passed"])
    print(f"\nTRADEABLE HEADLINES: {passed_count}/{len(tradeable_headlines)} classified as TRADE")

    assert passed_count == len(tradeable_headlines), (
        f"All tradeable headlines should classify as TRADE. "
        f"Got {passed_count}/{len(tradeable_headlines)}"
    )
    print("✅ PASSED: All tradeable headlines correctly classified as TRADE")


if __name__ == "__main__":
    # Allow running directly
    load_dotenv()
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    # Load prompts
    elec_prompt = (PROJECT_ROOT / "prompts" / "industrials" / "electrical_equipment.txt").read_text()
    food_prompt = (PROJECT_ROOT / "prompts" / "consumer_defensive" / "food_distribution.txt").read_text()

    print("\n" + "=" * 80)
    print("RUNNING BACKTEST DIRECTLY")
    print("=" * 80)

    # SDST test
    print("\n--- SDST (Electrical Equipment) ---")
    old_sdst = classify_headline(client, OLD_ELECTRICAL_PROMPT_WITHOUT_PERMITS, SDST_HEADLINE)
    new_sdst = classify_headline(client, elec_prompt, SDST_HEADLINE)
    print(f"Old prompt: {old_sdst}")
    print(f"New prompt: {new_sdst}")

    # Food Distribution test
    print("\n--- Food Distribution (10 headlines) ---")
    for item in FOOD_DISTRIBUTION_HEADLINES:
        result = classify_headline(client, food_prompt, item["headline"])
        status = "✅" if result == item["expected"] else "❌"
        print(f"{status} {item['ticker']}: {result} (expected {item['expected']})")
