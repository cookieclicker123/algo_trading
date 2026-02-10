"""
Update all industry prompts with universal skip signal patterns.

Adds a common "UNIVERSAL SKIP SIGNALS" section to each prompt that
catches bad headline patterns identified from Feb 9, 2026 backtesting.
"""
from pathlib import Path

# The universal section to add (must be inserted before DECISION RULES)
UNIVERSAL_SECTION = '''
===============================================================================
UNIVERSAL SKIP SIGNALS (Bad language patterns - applies to all headlines)
===============================================================================

NON-COMMITTAL LANGUAGE (100% losers in backtesting)
├─ "Non-binding" = not a real deal, frequently falls through
├─ "Letter of Intent" without dollar amounts = vague, uncommitted
├─ "Enters Into" without specifics = meaningless corporate speak
├─ "Memorandum of Understanding" without $ = preliminary, no commitment
└─ Examples:
   "Signs Non-binding Letter of Intent" → SKIP
   "Enters Into Strategic Agreement" → SKIP (no details)
   "Signs MOU for Future Cooperation" → SKIP (vague)

DEFENSIVE/DISTRESSED LANGUAGE (Often bad news disguised as good)
├─ "Restructuring" (debt, financing) = company in trouble
├─ "Strengthens Financial Position" = defensive language
├─ "Improves Financial Flexibility" = euphemism for distress
├─ These phrases often mean: "we were in trouble, now less so"
└─ Examples:
   "Restructures Debt Through Bank Financing" → SKIP
   "Strengthens Financial Position Through Agreement" → SKIP

PROVISIONAL/LIMITED (Not final or material)
├─ "Allowance" (patent/regulatory) = not final grant
├─ "Canadian" only = limited geography (unless US/global also mentioned)
├─ "Preliminary" approval/results = can change
└─ Examples:
   "Receives Canadian Patent Allowance" → SKIP
   "Preliminary Approval Granted" → SKIP

SECTOR MISMATCH (Company doing something outside their business)
├─ If a company announces news completely unrelated to their industry
├─ This often signals desperation or pump scheme
├─ E.g., Internet Retail company doing submarines, Solar company doing AI
└─ Examples:
   "E-commerce Company Enters Defense Sector" → SKIP (mismatch)

CASH OUTFLOWS (Spending money, not making it)
├─ "Acquires" / "To Acquire" = company SPENDING money
├─ Acquisitions often dilute or overextend the acquirer
├─ EXCEPTION: If the company is BEING acquired (target) → TRADE
└─ Examples:
   "To Acquire AI Startup" → SKIP (spending money)
   "To Be Acquired For $X Per Share" → TRADE (being acquired)

KEY RULE: Headlines should show CONCRETE VALUE CREATION
✓ TRADE: Specific dollar amounts, named customers, definitive agreements
✗ SKIP: Vague "strategic" language, non-binding, restructuring

'''

def update_prompt(filepath: Path) -> bool:
    """Update a single prompt file with universal section."""
    content = filepath.read_text()

    # Skip if already has universal section
    if "UNIVERSAL SKIP SIGNALS" in content:
        print(f"  Skipped (already has section): {filepath.name}")
        return False

    # Find the DECISION RULES marker
    marker = "===============================================================================\nDECISION RULES"

    if marker not in content:
        # Try alternate format (different number of =)
        for line in content.split('\n'):
            if 'DECISION RULES' in line:
                idx = content.find(line)
                if idx > 0:
                    # Find the line before (the separator)
                    prev_newline = content.rfind('\n', 0, idx)
                    if prev_newline > 0:
                        prev_prev_newline = content.rfind('\n', 0, prev_newline)
                        marker_line = content[prev_prev_newline+1:idx].strip()
                        if '=' in marker_line:
                            marker = marker_line + '\n' + line
                            break

    if marker not in content:
        print(f"  Warning: No DECISION RULES section found in {filepath.name}")
        return False

    # Insert the universal section before DECISION RULES
    new_content = content.replace(marker, UNIVERSAL_SECTION + marker)

    # Write back
    filepath.write_text(new_content)
    print(f"  Updated: {filepath.name}")
    return True


def main():
    prompts_dir = Path(__file__).parent.parent / "prompts"

    # Find all industry-specific prompts (not headline_types)
    prompt_dirs = [
        "healthcare",
        "technology",
        "industrials",
        "consumer_cyclical",
        "financial_services",
        "consumer_defensive",
        "basic_materials",
        "communication_services",
    ]

    updated = 0
    skipped = 0

    for subdir in prompt_dirs:
        subdir_path = prompts_dir / subdir
        if not subdir_path.exists():
            continue

        print(f"\n{subdir.upper()}:")
        for filepath in subdir_path.glob("*.txt"):
            if update_prompt(filepath):
                updated += 1
            else:
                skipped += 1

    print(f"\n{'='*60}")
    print(f"Updated {updated} prompts, skipped {skipped}")


if __name__ == "__main__":
    main()
