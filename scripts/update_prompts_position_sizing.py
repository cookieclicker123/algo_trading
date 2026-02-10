#!/usr/bin/env python3
"""
Update all industry prompts to include position sizing guidance.

Changes:
1. Output now: TRADE SMALL / TRADE MODERATE / TRADE LARGE / TRADE MAX / SKIP
2. Add position sizing section explaining what determines size
3. Add context about dollar amounts relative to company size
4. Emphasize concrete headlines are better
"""

import os
from pathlib import Path

# Directory containing prompts
PROMPT_DIR = Path(__file__).parent.parent / "prompts"

# Position sizing section to add before DECISION RULES
POSITION_SIZING_SECTION = '''
===============================================================================
POSITION SIZING (Output: TRADE SMALL, TRADE MODERATE, TRADE LARGE, TRADE MAX, or SKIP)
===============================================================================

You must classify BOTH whether to trade AND the position size.
Position size is based on headline strength and conviction level.

SIZE FACTORS - Consider ALL of the following:

1. HEADLINE CONCRETENESS (Most important)
   - MAX: Specific dollar amounts, named counterparties, definitive terms
   - LARGE: Specific details but missing some elements (e.g., no $ but named partner)
   - MODERATE: Reasonably specific but some vagueness
   - SMALL: Tradeable but lacks specificity

2. DOLLAR AMOUNT RELATIVE TO COMPANY (When applicable)
   You will receive company market cap in the CONTEXT section.
   - MAX: Deal value > 50% of market cap (transformational)
   - LARGE: Deal value 25-50% of market cap (major)
   - MODERATE: Deal value 10-25% of market cap (significant)
   - SMALL: Deal value < 10% of market cap (modest)
   Note: Not all headline types have dollar amounts - that's OK. Use other factors.

3. CATALYST STRENGTH (Industry-specific)
   - MAX: Strongest catalysts for this industry (see TRADE SIGNALS above)
   - LARGE: Strong catalysts
   - MODERATE: Moderate catalysts
   - SMALL: Weaker but still tradeable catalysts

4. COUNTERPARTY QUALITY (When applicable)
   - MAX: Fortune 100, major government (DOD, DOE, NASA), big pharma, big tech
   - LARGE: Fortune 500, major industry players
   - MODERATE: Known companies, mid-tier partners
   - SMALL: Unknown or smaller counterparties

EXAMPLES OF SIZE DETERMINATION:
- "$140M contract with DOD" for $50M market cap company → TRADE MAX (280% of mkt cap, DOD)
- "$20M partnership with Microsoft" for $100M company → TRADE LARGE (20% + big tech)
- "Partners with regional distributor" (no $) → TRADE SMALL (vague, unknown partner)
- "FDA Approval for new drug" → TRADE MAX (strongest biotech catalyst)
- "Wins contract" (no $ amount, no customer name) → TRADE SMALL (vague)
- "Signs $5M deal with Fortune 500" for $200M company → TRADE MODERATE (2.5%, good partner)

CONCRETE > VAGUE:
Headlines with specific numbers, names, and terms are more reliable.
When in doubt about size, ask: "How concrete is this headline?"

'''

# New response instruction
NEW_RESPONSE_INSTRUCTION = '''RESPOND: TRADE MAX, TRADE LARGE, TRADE MODERATE, TRADE SMALL, or SKIP (no explanation)
'''

def update_prompt_file(filepath: Path) -> bool:
    """Update a single prompt file with position sizing guidance."""
    try:
        content = filepath.read_text()
        original_content = content

        # Skip non-industry prompts
        if "catalyst_identification" in str(filepath):
            print(f"  Skipping {filepath.name} (not industry prompt)")
            return False
        if "headline_types" in str(filepath):
            print(f"  Skipping {filepath.name} (headline_types folder)")
            return False

        # Check if already updated
        if "POSITION SIZING" in content:
            print(f"  Already updated: {filepath.name}")
            return False

        # Find where to insert position sizing section
        # Insert before DECISION RULES section
        decision_rules_markers = [
            "===============================================================================\nDECISION RULES",
            "═══════════════════════════════════════════════════════════════════════════════\nDECISION RULES",
        ]

        insert_pos = -1
        for marker in decision_rules_markers:
            if marker in content:
                insert_pos = content.find(marker)
                break

        if insert_pos == -1:
            print(f"  WARNING: No DECISION RULES section found in {filepath.name}")
            return False

        # Insert position sizing section before DECISION RULES
        content = content[:insert_pos] + POSITION_SIZING_SECTION + "\n" + content[insert_pos:]

        # Update the response instruction at the end
        old_responses = [
            "RESPOND: TRADE or SKIP (no explanation)",
            "RESPOND: TRADE or SKIP",
            "Respond: TRADE or SKIP (no explanation)",
            "Respond: TRADE or SKIP",
        ]

        for old_response in old_responses:
            if old_response in content:
                content = content.replace(old_response, NEW_RESPONSE_INSTRUCTION.strip())
                break

        # Also update the header instruction if present
        old_headers = [
            "Output TRADE (expect 10%+ move) or SKIP (no significant move expected).",
            "Output TRADE (expect 10%+ move) or SKIP.",
        ]

        new_header = "Output TRADE SMALL/MODERATE/LARGE/MAX (with position size) or SKIP."

        for old_header in old_headers:
            if old_header in content:
                content = content.replace(old_header, new_header)
                break

        # Write updated content
        filepath.write_text(content)
        print(f"  Updated: {filepath.name}")
        return True

    except Exception as e:
        print(f"  ERROR updating {filepath.name}: {e}")
        return False


def main():
    """Update all industry prompts."""
    print("Updating all industry prompts with position sizing guidance...\n")

    # Get all sector directories
    sector_dirs = [
        "healthcare",
        "technology",
        "industrials",
        "consumer_cyclical",
        "consumer_defensive",
        "financial_services",
        "basic_materials",
        "communication_services",
    ]

    updated_count = 0
    total_count = 0

    for sector_dir in sector_dirs:
        sector_path = PROMPT_DIR / sector_dir
        if not sector_path.exists():
            continue

        print(f"\n{sector_dir.upper()}:")

        for prompt_file in sector_path.glob("*.txt"):
            total_count += 1
            if update_prompt_file(prompt_file):
                updated_count += 1

    print(f"\n{'='*60}")
    print(f"Updated {updated_count}/{total_count} prompt files")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
