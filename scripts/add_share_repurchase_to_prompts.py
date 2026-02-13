#!/usr/bin/env python3
"""
Add SHARE REPURCHASE section to all industry prompts.

Share repurchases are bullish signals:
- Company buying back shares = confidence in undervaluation
- Reduces float, increases EPS
- Size relative to market cap determines trade size

Usage:
    python scripts/add_share_repurchase_to_prompts.py
"""

import re
from pathlib import Path

PROMPTS_DIR = Path("prompts")

SHARE_REPURCHASE_SECTION = """
SHARE REPURCHASE (Bullish signal - size based on % of market cap)
├─ Share repurchase/buyback announcements = TRADE
├─ Company buying back shares = confidence in undervaluation
├─ Reduces float, increases EPS = bullish for price
├─ SIZE BY MARKET CAP PERCENTAGE (context provided):
│   - >25% of market cap = TRADE MAX (transformational)
│   - 10-25% of market cap = TRADE LARGE
│   - 5-10% of market cap = TRADE MODERATE
│   - <5% of market cap = TRADE SMALL
└─ Examples:
   "$10M Repurchase" for $30M company (33%) → TRADE MAX
   "$5M Buyback" for $40M company (12.5%) → TRADE LARGE
   "Announces Share Repurchase Program" (no $ amount) → TRADE SMALL
   "Board Authorizes Stock Buyback" → TRADE SMALL

"""


def add_share_repurchase_to_prompt(filepath: Path) -> bool:
    """Add share repurchase section to a prompt file if not already present."""
    try:
        content = filepath.read_text()

        # Skip if already has share repurchase section
        if "SHARE REPURCHASE" in content:
            print(f"  SKIP (already has): {filepath.name}")
            return False

        # Find the SKIP SIGNALS section and insert before it
        skip_pattern = r'(═+\nSKIP SIGNALS\n═+)'
        match = re.search(skip_pattern, content)

        if match:
            # Insert share repurchase section before SKIP SIGNALS
            insert_pos = match.start()
            new_content = content[:insert_pos] + SHARE_REPURCHASE_SECTION + content[insert_pos:]
            filepath.write_text(new_content)
            print(f"  ADDED: {filepath.name}")
            return True
        else:
            # Try alternative pattern (single line separator)
            skip_pattern2 = r'(SKIP SIGNALS\n=+)'
            match2 = re.search(skip_pattern2, content)
            if match2:
                insert_pos = match2.start()
                new_content = content[:insert_pos] + SHARE_REPURCHASE_SECTION + content[insert_pos:]
                filepath.write_text(new_content)
                print(f"  ADDED: {filepath.name}")
                return True
            else:
                print(f"  SKIP (no SKIP SIGNALS section): {filepath.name}")
                return False

    except Exception as e:
        print(f"  ERROR: {filepath.name} - {e}")
        return False


def main():
    print("=" * 60)
    print("ADD SHARE REPURCHASE TO ALL PROMPTS")
    print("=" * 60)

    # Find all .txt prompt files
    prompt_files = list(PROMPTS_DIR.rglob("*.txt"))
    prompt_files = [f for f in prompt_files if f.name != "README.md"]

    print(f"\nFound {len(prompt_files)} prompt files\n")

    added = 0
    skipped = 0

    for filepath in sorted(prompt_files):
        if add_share_repurchase_to_prompt(filepath):
            added += 1
        else:
            skipped += 1

    print(f"\n{'=' * 60}")
    print(f"COMPLETE: Added to {added} files, skipped {skipped} files")
    print("=" * 60)


if __name__ == "__main__":
    main()
