#!/usr/bin/env python3
"""
Add DILUTION/OFFERINGS section to UNIVERSAL SKIP SIGNALS in all prompts.

TURB pattern: "Enters into securities purchase agreement" = dilution = SKIP
"""

import os
from pathlib import Path

PROMPT_DIR = Path(__file__).parent.parent / "prompts"

# Section to add after the existing UNIVERSAL SKIP SIGNALS patterns
DILUTION_SECTION = '''
DILUTION/OFFERINGS (Company raising cash = stock goes DOWN)
├─ "Securities purchase agreement" = private placement (PIPE) = dilution
├─ "Prices offering", "Closes offering", "Underwritten offering" = dilution
├─ "Registered direct offering", "At-the-market offering" = dilution
├─ "Private placement", "PIPE", "Gross proceeds", "Net proceeds" = dilution
├─ Even "at market" offerings signal the company needs cash (weakness)
├─ The stock almost ALWAYS drops on offerings - sellers front-run dilution
└─ Examples:
   "Enters Into Securities Purchase Agreement" → SKIP (dilution)
   "Prices $50M Public Offering" → SKIP (dilution)
   "Announces Registered Direct Offering" → SKIP (dilution)
   "Closes Private Placement" → SKIP (dilution)
'''

def update_prompt_file(filepath: Path) -> bool:
    """Update a single prompt file with dilution section."""
    try:
        content = filepath.read_text()

        # Skip non-industry prompts
        if "catalyst_identification" in str(filepath):
            return False
        if "headline_types" in str(filepath):
            return False

        # Check if already has dilution section
        if "DILUTION/OFFERINGS" in content:
            print(f"  Already has dilution section: {filepath.name}")
            return False

        # Find where to insert - after CASH OUTFLOWS section in UNIVERSAL SKIP SIGNALS
        # Look for the CASH OUTFLOWS section
        cash_outflow_marker = "CASH OUTFLOWS (Spending money, not making it)"

        if cash_outflow_marker not in content:
            # Try alternate markers
            if "UNIVERSAL SKIP SIGNALS" not in content:
                print(f"  WARNING: No UNIVERSAL SKIP SIGNALS in {filepath.name}")
                return False
            # Insert before KEY RULE or DECISION RULES
            for marker in ["KEY RULE:", "POSITION SIZING"]:
                if marker in content:
                    insert_pos = content.find(marker)
                    content = content[:insert_pos] + DILUTION_SECTION + "\n" + content[insert_pos:]
                    filepath.write_text(content)
                    print(f"  Updated (before {marker}): {filepath.name}")
                    return True
            print(f"  WARNING: Could not find insert point in {filepath.name}")
            return False

        # Find the end of CASH OUTFLOWS section (next blank line after examples)
        cash_pos = content.find(cash_outflow_marker)
        # Find the next section or KEY RULE after CASH OUTFLOWS
        search_start = cash_pos + len(cash_outflow_marker)

        # Look for next section marker or KEY RULE
        next_markers = ["\nKEY RULE:", "\nPOSITION SIZING", "\n===", "\n═══"]
        insert_pos = len(content)

        for marker in next_markers:
            pos = content.find(marker, search_start)
            if pos != -1 and pos < insert_pos:
                insert_pos = pos

        # Insert dilution section before the next marker
        content = content[:insert_pos] + "\n" + DILUTION_SECTION + content[insert_pos:]

        filepath.write_text(content)
        print(f"  Updated: {filepath.name}")
        return True

    except Exception as e:
        print(f"  ERROR updating {filepath.name}: {e}")
        return False


def main():
    """Update all industry prompts."""
    print("Adding DILUTION/OFFERINGS section to all prompts...\n")

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
