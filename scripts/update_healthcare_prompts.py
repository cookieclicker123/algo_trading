"""
Update healthcare prompts with universal skip signals (Unicode format).
"""
from pathlib import Path

UNIVERSAL_SECTION = '''═══════════════════════════════════════════════════════════════════════════════
UNIVERSAL SKIP SIGNALS (Bad language patterns - applies to all headlines)
═══════════════════════════════════════════════════════════════════════════════

NON-COMMITTAL LANGUAGE (100% losers in backtesting)
├─ "Non-binding" = not a real deal, frequently falls through
├─ "Letter of Intent" without dollar amounts = vague, uncommitted
├─ "Enters Into" without specifics = meaningless corporate speak
└─ Examples:
   "Signs Non-binding Letter of Intent" → SKIP
   "Enters Into Strategic Agreement" → SKIP (no details)

DEFENSIVE/DISTRESSED LANGUAGE (Often bad news disguised as good)
├─ "Restructuring" (debt, financing) = company in trouble
├─ "Strengthens Financial Position" = defensive language
└─ Examples:
   "Restructures Debt Through Bank Financing" → SKIP
   "Strengthens Financial Position" → SKIP

PROVISIONAL/LIMITED (Not final or material)
├─ "Allowance" (patent/regulatory) = not final grant
├─ "Canadian" only = limited geography
└─ Examples:
   "Receives Canadian Patent Allowance" → SKIP

CASH OUTFLOWS (Spending money, not making it)
├─ "Acquires" / "To Acquire" = company SPENDING money
├─ EXCEPTION: If being acquired (target) → TRADE
└─ Examples:
   "To Acquire Startup" → SKIP
   "To Be Acquired For $X Per Share" → TRADE

'''

def update_prompt(filepath: Path) -> bool:
    """Update a single prompt file."""
    content = filepath.read_text()

    if "UNIVERSAL SKIP SIGNALS" in content:
        print(f"  Skipped (already has section): {filepath.name}")
        return False

    # Find the DECISION RULES marker with Unicode separator
    marker = "═══════════════════════════════════════════════════════════════════════════════\nDECISION RULES"

    if marker not in content:
        print(f"  Warning: No DECISION RULES section found in {filepath.name}")
        return False

    # Insert before DECISION RULES
    new_content = content.replace(marker, UNIVERSAL_SECTION + marker)
    filepath.write_text(new_content)
    print(f"  Updated: {filepath.name}")
    return True


def main():
    prompts_dir = Path(__file__).parent.parent / "prompts" / "healthcare"

    updated = 0
    for filepath in prompts_dir.glob("*.txt"):
        if update_prompt(filepath):
            updated += 1

    print(f"\nUpdated {updated} healthcare prompts")


if __name__ == "__main__":
    main()
