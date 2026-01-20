"""
Integration test for Electrical Equipment prompt - verifies permits/regulatory approvals
are correctly classified as TRADE signals.

This test uses REAL Groq API calls to verify classification behavior.

Test case: SDST "Stardust Power Secures Air Permit" headline
- OLD prompt (without permits section): Should classify as SKIP
- NEW prompt (with permits section): Should classify as TRADE

The SDST headline moved +43.18% but was missed because permits weren't recognized.
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


# The SDST headline that moved +43.18%
SDST_HEADLINE = "Stardust Power Secures Air Permit; Muskogee Lithium Refinery Now Permitted For Construction and Commissioning"
SDST_TICKER = "SDST"

# Original prompt WITHOUT permits section
OLD_PROMPT_WITHOUT_PERMITS = """ELECTRICAL EQUIPMENT & PARTS HEADLINE CLASSIFIER

You classify Electrical Equipment news headlines for high-frequency trading.
Output TRADE (expect 10%+ move) or SKIP (no significant move expected).

INDUSTRY CONTEXT:
- 100 historical winners analyzed, avg move +20.3%
- Top catalysts: Earnings (31%), Contracts (18%), Offerings (13%), Partnerships (13%)
- Includes power equipment, transformers, motors, EV charging
- Contracts and partnerships drive non-earnings moves
- NOTE: We do NOT trade earnings announcements

===============================================================================
TRADE SIGNALS
===============================================================================

CONTRACTS (18% of winners)
- Utility contracts
- EV infrastructure contracts
- Industrial equipment orders
- Government contracts
- Examples:
   "Wins Major Utility Grid Contract" -> TRADE
   "Secures EV Charging Infrastructure Deal" -> TRADE
   "Awarded DOE Energy Contract" -> TRADE

PARTNERSHIPS (13% of winners)
- EV/automotive partnerships
- Utility partnerships
- Technology partnerships
- Examples:
   "Partners With Major Automaker For EV Components" -> TRADE
   "Announces Grid Partnership With Utility" -> TRADE

M&A ACTIVITY
- Acquisition announcements
- Strategic acquisitions
- Examples:
   "To Be Acquired By Electrical Giant" -> TRADE
   "Acquires EV Charging Company" -> TRADE

PRODUCT LAUNCHES
- New technology launches
- EV-related products
- Examples:
   "Launches Next-Gen EV Charger" -> TRADE
   "Announces Breakthrough In Grid Storage" -> TRADE

===============================================================================
SKIP SIGNALS
===============================================================================

EARNINGS (Do NOT trade - too unpredictable)
- EPS beats/misses, Revenue beats/misses
- Order trends, Backlog
- ANY earnings-related headline -> SKIP
- Examples:
   "Q4 EPS Beats Estimate" -> SKIP
   "Reports Strong Order Growth" -> SKIP

CONFERENCES
- Industry conferences
- Trade shows
- Examples:
   "To Present at Energy Conference" -> SKIP
   "Announces Trade Show Participation" -> SKIP

POLICY/REGULATORY
- Grid policy commentary
- EV incentive discussions
- Examples:
   "Comments On Infrastructure Bill Impact" -> SKIP
   "Responds To EV Policy Changes" -> SKIP

ADMINISTRATIVE
- Stock offerings
- Executive changes
- Examples:
   "Announces Secondary Offering" -> SKIP
   "Names New CEO" -> SKIP

===============================================================================
DECISION RULES
===============================================================================

1. EV infrastructure contracts = strong TRADE signal
2. Utility contracts = TRADE
3. Automaker partnerships = TRADE
4. Earnings = always SKIP
5. Policy commentary = always SKIP
6. Conference attendance = always SKIP

RESPOND: TRADE or SKIP (no explanation)
"""


@pytest.fixture
def groq_client():
    """Create Groq client from environment."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        pytest.skip("GROQ_API_KEY must be set to run this integration test")
    return Groq(api_key=api_key)


@pytest.fixture
def new_prompt_with_permits():
    """Load the current (new) prompt with permits section."""
    prompt_path = PROJECT_ROOT / "prompts" / "industrials" / "electrical_equipment.txt"
    if not prompt_path.exists():
        pytest.skip(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text()


def classify_headline(client: Groq, prompt: str, headline: str) -> str:
    """
    Classify a headline using the given prompt.

    Returns: "TRADE" or "SKIP"
    """
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": headline}
        ],
        temperature=0.0,  # Deterministic
        max_tokens=10,
    )

    result = response.choices[0].message.content.strip().upper()

    if "TRADE" in result:
        return "TRADE"
    return "SKIP"


def test_old_prompt_classifies_sdst_as_skip(groq_client):
    """
    Test that the OLD prompt (without permits) classifies SDST headline as SKIP.

    This confirms our hypothesis that permits were not recognized as TRADE signals.
    """
    print("\n" + "=" * 80)
    print("TEST: OLD PROMPT (without permits) -> SDST headline")
    print("=" * 80)
    print(f"Headline: {SDST_HEADLINE}")

    classification = classify_headline(
        groq_client,
        OLD_PROMPT_WITHOUT_PERMITS,
        SDST_HEADLINE
    )

    print(f"Classification: {classification}")
    print(f"Expected: SKIP (permits not recognized)")

    # The old prompt should classify permits as SKIP since they're not in TRADE signals
    assert classification == "SKIP", (
        f"Expected OLD prompt to classify SDST as SKIP, got {classification}. "
        "This means the old prompt already recognized permits as TRADE signals."
    )
    print("✅ PASSED: Old prompt correctly classified as SKIP (missed opportunity)")


def test_new_prompt_classifies_sdst_as_trade(groq_client, new_prompt_with_permits):
    """
    Test that the NEW prompt (with permits) classifies SDST headline as TRADE.

    This confirms our fix works - permits are now recognized as TRADE signals.
    """
    print("\n" + "=" * 80)
    print("TEST: NEW PROMPT (with permits) -> SDST headline")
    print("=" * 80)
    print(f"Headline: {SDST_HEADLINE}")

    # Verify the new prompt has permits section
    assert "PERMITS & REGULATORY APPROVALS" in new_prompt_with_permits, (
        "New prompt is missing PERMITS & REGULATORY APPROVALS section!"
    )

    classification = classify_headline(
        groq_client,
        new_prompt_with_permits,
        SDST_HEADLINE
    )

    print(f"Classification: {classification}")
    print(f"Expected: TRADE (permits now recognized)")

    assert classification == "TRADE", (
        f"Expected NEW prompt to classify SDST as TRADE, got {classification}. "
        "The permits section may need adjustment."
    )
    print("✅ PASSED: New prompt correctly classified as TRADE")


def test_both_prompts_comparison(groq_client, new_prompt_with_permits):
    """
    Side-by-side comparison of old vs new prompt on SDST headline.

    Expected:
    - OLD prompt: SKIP (permits not recognized)
    - NEW prompt: TRADE (permits recognized as strong signal)

    This test validates the prompt improvement works as intended.
    """
    print("\n" + "=" * 80)
    print("TEST: SIDE-BY-SIDE COMPARISON")
    print("=" * 80)
    print(f"Headline: {SDST_HEADLINE}")
    print(f"Actual move: +43.18% (missed opportunity)")
    print()

    # Classify with both prompts
    old_result = classify_headline(groq_client, OLD_PROMPT_WITHOUT_PERMITS, SDST_HEADLINE)
    new_result = classify_headline(groq_client, new_prompt_with_permits, SDST_HEADLINE)

    print(f"OLD prompt (without permits): {old_result}")
    print(f"NEW prompt (with permits):    {new_result}")
    print()

    # Validate expected behavior
    if old_result == "SKIP" and new_result == "TRADE":
        print("✅ PERFECT: Prompt improvement works as expected!")
        print("   - Old prompt missed the signal (SKIP)")
        print("   - New prompt catches the signal (TRADE)")
    elif old_result == "TRADE" and new_result == "TRADE":
        print("⚠️  INTERESTING: Both prompts classified as TRADE")
        print("   - The old prompt may have already caught this")
        print("   - New prompt still works correctly")
    elif old_result == "SKIP" and new_result == "SKIP":
        print("❌ PROBLEM: New prompt still classifies as SKIP")
        print("   - Permits section may need strengthening")
        pytest.fail("New prompt should classify permits as TRADE")
    else:
        print(f"❓ UNEXPECTED: Old={old_result}, New={new_result}")

    # The key assertion: new prompt must classify as TRADE
    assert new_result == "TRADE", f"New prompt must classify SDST as TRADE, got {new_result}"


def test_additional_permit_headlines(groq_client, new_prompt_with_permits):
    """
    Test additional permit-related headlines to ensure robust classification.
    """
    print("\n" + "=" * 80)
    print("TEST: ADDITIONAL PERMIT HEADLINES")
    print("=" * 80)

    permit_headlines = [
        ("Receives EPA Approval For Battery Manufacturing Plant", "TRADE"),
        ("Granted Environmental Permit For EV Factory", "TRADE"),
        ("Secures Construction Permit For Lithium Processing Facility", "TRADE"),
        ("Receives Air Quality Permit For Clean Energy Project", "TRADE"),
        ("Comments On EPA Permit Process Delays", "SKIP"),  # Commentary, not approval
        ("Q4 EPS Beats, Mentions Permit Progress", "SKIP"),  # Earnings
    ]

    results = []
    for headline, expected in permit_headlines:
        classification = classify_headline(groq_client, new_prompt_with_permits, headline)
        passed = classification == expected
        status = "✅" if passed else "❌"
        results.append((headline, expected, classification, passed))
        print(f"{status} '{headline[:60]}...' -> {classification} (expected {expected})")

    # Check all passed
    failed = [r for r in results if not r[3]]
    if failed:
        print(f"\n{len(failed)} headlines classified incorrectly:")
        for headline, expected, actual, _ in failed:
            print(f"  - '{headline}': expected {expected}, got {actual}")

    # At least 80% should pass (allow some flexibility for LLM interpretation)
    pass_rate = sum(1 for r in results if r[3]) / len(results)
    print(f"\nPass rate: {pass_rate:.0%}")
    assert pass_rate >= 0.8, f"Expected at least 80% pass rate, got {pass_rate:.0%}"


if __name__ == "__main__":
    # Allow running directly
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    prompt = (PROJECT_ROOT / "prompts" / "industrials" / "electrical_equipment.txt").read_text()

    print("Running side-by-side comparison...")
    old_result = classify_headline(client, OLD_PROMPT_WITHOUT_PERMITS, SDST_HEADLINE)
    new_result = classify_headline(client, prompt, SDST_HEADLINE)

    print(f"\nHeadline: {SDST_HEADLINE}")
    print(f"OLD prompt: {old_result}")
    print(f"NEW prompt: {new_result}")
