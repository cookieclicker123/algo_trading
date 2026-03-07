#!/usr/bin/env python3
"""
Defense Trading Pipeline Simulation
====================================
Searches all recall records for defense/military headlines, applies prefilters,
makes REAL Groq LLM calls for triage + sector classification, and simulates P&L.
"""

import json
import glob
import os
import re
import math
import time
import html
from pathlib import Path

from groq import Groq

# ============================================================================
# CONFIGURATION
# ============================================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not set. Source .env first.")

client = Groq(api_key=GROQ_API_KEY)

RECALL_DIR = Path("tmp/statistics/recall")
PROMPTS_DIR = Path("prompts")
TRIAGE_PROMPT_PATH = PROMPTS_DIR / "headline_types" / "universal_triage.txt"

POSITION_SIZE_DOLLARS = 10_000
STOP_LOSS_PCT = -12.0
TRAILING_STOP_PP = 15.0  # percentage points below MFE peak

# Take-profit tiers (HIGH_CONVICTION)
TIER1_PCT = 25.0   # sell 34% at +25%
TIER2_PCT = 40.0   # sell 50% of remaining at +40%
TIER3_PCT = 60.0   # sell 100% of remaining at +60%
TIER1_SELL_FRAC = 0.34
TIER2_SELL_FRAC = 0.50
# Tier3 sells everything remaining

HIGH_CONVICTION_TYPES = {"government_contract", "military_contract", "defense_order"}

# Defense keyword patterns (case-insensitive)
DEFENSE_KEYWORDS = [
    r'\bmilitary\b', r'\barmy\b', r'\bnavy\b', r'\bair\s+force\b',
    r'\bdefense\b', r'\bdefence\b', r'\bpentagon\b', r'\bdod\b',
    r'\bmunition', r'\bdrone', r'\bcounter[\-\s]?uas\b', r'\bdarpa\b',
    r'\btactical\b', r'\bmissile', r'\bcombat\b', r'\bwarfighter',
    r'\bgovernment\s+contract', r'\bgovernment\s+award', r'\bgovernment\s+order',
    r'\bidiq\b', r'\bforeign\s+military\s+sale', r'\bspace\s+force\b',
    r'\bweapon', r'\bloitering\b', r'\bcounter[\-\s]?drone', r'\btorpedo',
    r'\bradar\b', r'\bsatellite\s+defense', r'\bnasa\s+contract',
    r'\bnasa\s+award', r'\bmarine\s+corps\b', r'\bcoast\s+guard\b',
]

DEFENSE_PATTERN = re.compile('|'.join(DEFENSE_KEYWORDS), re.IGNORECASE)

# Headline skip patterns
SKIP_HEADLINE_PATTERNS = [
    re.compile(r'\bconference\b', re.IGNORECASE),
    re.compile(r'\bpresents?\s+at\b', re.IGNORECASE),
    re.compile(r'\blaw\s+(firm|group|office)\b', re.IGNORECASE),
    re.compile(r'\binvestors?\s+have\s+opportunity\b', re.IGNORECASE),
    re.compile(r'\bsecurities\s+(fraud|law|class\s+action)\b', re.IGNORECASE),
    re.compile(r'\bsued\s+for\b', re.IGNORECASE),
]

# SECTOR_INDUSTRY_MAP (copied from sector_classifier.py for prompt lookup)
SECTOR_INDUSTRY_MAP = {
    "Healthcare": {
        "Biotechnology": "biotechnology.txt",
        "Medical Devices": "medical_devices.txt",
        "Drug Manufacturers - Specialty & Generic": "drug_manufacturers.txt",
        "Diagnostics & Research": "diagnostics_research.txt",
        "Health Information Services": "health_information_services.txt",
        "Medical Instruments & Supplies": "medical_instruments_supplies.txt",
        "Medical Care Facilities": "medical_care_facilities.txt",
    },
    "Technology": {
        "Software - Application": "software_application.txt",
        "Software - Infrastructure": "software_infrastructure.txt",
        "Semiconductors": "semiconductors.txt",
        "Communication Equipment": "communication_equipment.txt",
        "Computer Hardware": "computer_hardware.txt",
        "Information Technology Services": "it_services.txt",
        "Electronic Components": "electronic_components.txt",
        "Solar": "solar.txt",
        "Consumer Electronics": "consumer_electronics.txt",
        "Semiconductor Equipment & Materials": "semiconductor_equipment.txt",
        "Scientific & Technical Instruments": "scientific_instruments.txt",
        "Electronic Gaming & Multimedia": "electronic_gaming_multimedia.txt",
    },
    "Industrials": {
        "Aerospace & Defense": "aerospace_defense.txt",
        "Specialty Industrial Machinery": "specialty_machinery.txt",
        "Electrical Equipment & Parts": "electrical_equipment.txt",
        "Specialty Business Services": "business_services.txt",
        "Engineering & Construction": "engineering_construction.txt",
        "Security & Protection Services": "security_services.txt",
        "Pollution & Treatment Controls": "pollution_controls.txt",
        "Consulting Services": "consulting_services.txt",
        "Building Products & Equipment": "building_products.txt",
        "Integrated Freight & Logistics": "freight_logistics.txt",
        "Waste Management": "waste_management.txt",
        "Metal Fabrication": "metal_fabrication.txt",
        "Staffing & Employment Services": "staffing_services.txt",
        "Marine Shipping": "transportation_other.txt",
        "Airlines": "transportation_other.txt",
        "Railroads": "transportation_other.txt",
        "Trucking": "transportation_other.txt",
        "Rental & Leasing Services": "transportation_other.txt",
        "Farm & Heavy Construction Machinery": "transportation_other.txt",
        "Industrial Distribution": "transportation_other.txt",
        "Tools & Accessories": "transportation_other.txt",
        "Conglomerates": "transportation_other.txt",
    },
    "Consumer Cyclical": {
        "Auto Parts": "auto_parts.txt",
        "Specialty Retail": "specialty_retail.txt",
        "Auto Manufacturers": "auto_manufacturers.txt",
        "Internet Retail": "internet_retail.txt",
        "Apparel Retail": "apparel_retail.txt",
        "Restaurants": "consumer_services.txt",
        "Leisure": "consumer_services.txt",
        "Footwear & Accessories": "consumer_services.txt",
        "Residential Construction": "residential_construction.txt",
        "Home Improvement Retail": "consumer_services.txt",
        "Travel Services": "consumer_services.txt",
        "Apparel Manufacturing": "consumer_services.txt",
        "Furnishings, Fixtures & Appliances": "consumer_services.txt",
        "Gambling": "consumer_services.txt",
        "Grocery Stores": "grocery_stores.txt",
        "Packaging & Containers": "consumer_services.txt",
        "Personal Services": "consumer_services.txt",
        "Lodging": "consumer_services.txt",
        "Textile Manufacturing": "consumer_services.txt",
        "Department Stores": "consumer_services.txt",
        "Luxury Goods": "consumer_services.txt",
        "Recreational Vehicles": "consumer_services.txt",
        "Auto & Truck Dealerships": "consumer_services.txt",
    },
    "Financial Services": {
        "Capital Markets": "capital_markets.txt",
        "Asset Management": "asset_management.txt",
        "Insurance - Property & Casualty": "banking_insurance.txt",
        "Insurance - Life": "banking_insurance.txt",
        "Insurance - Specialty": "banking_insurance.txt",
        "Banks - Regional": "banking_insurance.txt",
        "Banks - Diversified": "banking_insurance.txt",
        "Credit Services": "banking_insurance.txt",
        "Mortgage Finance": "banking_insurance.txt",
        "Insurance Brokers": "banking_insurance.txt",
        "Financial Data & Stock Exchanges": "banking_insurance.txt",
        "Insurance - Diversified": "banking_insurance.txt",
        "Financial Conglomerates": "banking_insurance.txt",
        "Shell Companies": "banking_insurance.txt",
    },
    "Communication Services": {
        "Electronic Gaming & Multimedia": "electronic_gaming_multimedia.txt",
    },
    "Consumer Defensive": {
        "Education & Training Services": "education_training.txt",
        "Food Distribution": "food_distribution.txt",
        "Household & Personal Products": "household_personal.txt",
    },
    "Basic Materials": {
        "Specialty Chemicals": "specialty_chemicals.txt",
        "Agricultural Inputs": "agricultural_inputs.txt",
        "Gold": "precious_metals_mining.txt",
        "Other Precious Metals & Mining": "precious_metals_mining.txt",
        "Other Industrial Metals & Mining": "other_industrial_metals_mining.txt",
    },
    "Energy": {
        "Solar": "solar.txt",
        "Oil & Gas Equipment & Services": "oil_gas_equipment_services.txt",
        "Oil & Gas E&P": "oil_gas_ep.txt",
        "Oil & Gas Midstream": "oil_gas_midstream.txt",
        "Oil & Gas Drilling": "oil_gas_other.txt",
        "Oil & Gas Integrated": "oil_gas_other.txt",
        "Oil & Gas Refining & Marketing": "oil_gas_other.txt",
        "Thermal Coal": "oil_gas_other.txt",
        "Uranium": "oil_gas_other.txt",
    },
}

INDUSTRY_ALIASES = {
    "Medical - Devices": "Medical Devices",
    "Medical - Instruments & Supplies": "Medical Instruments & Supplies",
    "Medical - Care Facilities": "Medical Care Facilities",
    "Medical - Distribution": "Medical Devices",
    "Medical Distribution": "Medical Devices",
    "Medical - Diagnostics & Research": "Diagnostics & Research",
    "Medical - Healthcare Plans": "Medical Care Facilities",
    "Medical - Healthcare Information Services": "Health Information Services",
    "Medical - Pharmaceuticals": "Drug Manufacturers - Specialty & Generic",
    "Medical - Equipment & Services": "Medical Instruments & Supplies",
    "Drug Manufacturers - General": "Drug Manufacturers - Specialty & Generic",
    "Health Care": "Medical Care Facilities",
    "Financial - Data & Stock Exchanges": "Financial Data & Stock Exchanges",
    "Financial - Conglomerates": "Financial Conglomerates",
    "Financial - Credit Services": "Credit Services",
    "Financial - Mortgages": "Mortgage Finance",
    "Financial - Capital Markets": "Capital Markets",
    "Insurance - Reinsurance": "Insurance - Diversified",
    "Asset Management - Global": "Asset Management",
    "Asset Management - Income": "Asset Management",
    "Asset Management - Leveraged": "Asset Management",
    "Asset Management - Cryptocurrency": "Asset Management",
    "Capital Markets - Independent": "Capital Markets",
    "Capital Markets - Institutional": "Capital Markets",
    "Auto - Parts": "Auto Parts",
    "Auto - Manufacturers": "Auto Manufacturers",
    "Auto - Dealerships": "Auto & Truck Dealerships",
    "Auto - Recreational Vehicles": "Recreational Vehicles",
    "Apparel - Footwear & Accessories": "Footwear & Accessories",
    "Apparel - Manufacturers": "Apparel Manufacturing",
    "Apparel - Retail": "Apparel Retail",
    "Gambling, Resorts & Casinos": "Gambling",
    "Home Improvement": "Home Improvement Retail",
    "Personal Products & Services": "Personal Services",
    "Industrial - Distribution": "Industrial Distribution",
    "Industrial - Machinery": "Specialty Industrial Machinery",
    "Industrial - Pollution & Treatment Controls": "Pollution & Treatment Controls",
    "Manufacturing - Metal Fabrication": "Metal Fabrication",
    "Airlines, Airports & Air Services": "Airlines",
    "Airports & Air Services": "Airlines",
    "Agricultural - Machinery": "Farm & Heavy Construction Machinery",
    "Chemicals - Specialty": "Specialty Chemicals",
    "Hardware, Equipment & Parts": "Computer Hardware",
    "Oil & Gas - Equipment & Services": "Oil & Gas Equipment & Services",
    "Oil & Gas - E&P": "Oil & Gas E&P",
    "Oil & Gas - Midstream": "Oil & Gas Midstream",
    "Oil & Gas - Drilling": "Oil & Gas Drilling",
    "Oil & Gas - Integrated": "Oil & Gas Integrated",
    "Oil & Gas - Refining & Marketing": "Oil & Gas Refining & Marketing",
    "Oil & Gas - Services": "Oil & Gas Equipment & Services",
    "Thermal Coal": "Thermal Coal",
}


# ============================================================================
# HELPERS
# ============================================================================

def clean_html(text: str) -> str:
    """Unescape HTML entities."""
    return html.unescape(text) if text else text


def get_prompt_directory(sector: str) -> str:
    return sector.lower().replace(" ", "_")


def load_prompt(sector: str, industry: str) -> str | None:
    """Load sector/industry prompt file."""
    industry = INDUSTRY_ALIASES.get(industry, industry)
    industry_map = SECTOR_INDUSTRY_MAP.get(sector, {})
    prompt_file = industry_map.get(industry)
    if not prompt_file:
        return None
    sector_dir = get_prompt_directory(sector)
    prompt_path = PROMPTS_DIR / sector_dir / prompt_file
    if prompt_path.exists():
        return prompt_path.read_text()
    return None


def is_defense_headline(title: str) -> bool:
    """Check if headline matches defense keywords."""
    return bool(DEFENSE_PATTERN.search(title))


def should_skip_headline(title: str) -> str | None:
    """Check headline skip patterns. Returns reason or None."""
    for pat in SKIP_HEADLINE_PATTERNS:
        if pat.search(title):
            return f"headline_skip:{pat.pattern}"
    return None


def compute_latency_seconds(rec: dict) -> float | None:
    """Compute pub-to-reception latency in seconds."""
    # Try direct field first
    latency_ms = rec.get("pub_to_recv_latency_ms")
    if latency_ms is not None:
        return latency_ms / 1000.0
    # Try computing from timestamps
    pub = rec.get("published_at")
    recv = rec.get("received_at")
    if pub and recv:
        from datetime import datetime, timezone
        try:
            # Parse ISO format
            if pub.endswith("Z"):
                pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            else:
                pub_dt = datetime.fromisoformat(pub)
            if recv.endswith("Z"):
                recv_dt = datetime.fromisoformat(recv.replace("Z", "+00:00"))
            else:
                recv_dt = datetime.fromisoformat(recv)
            # Make both offset-aware for comparison
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if recv_dt.tzinfo is None:
                recv_dt = recv_dt.replace(tzinfo=timezone.utc)
            return (recv_dt - pub_dt).total_seconds()
        except Exception:
            pass
    return None


def compute_spread_pct(nbbo: dict) -> float | None:
    """Compute spread percentage from NBBO."""
    if not nbbo:
        return None
    # If spread_pct is already there, use it
    if "spread_pct" in nbbo and nbbo["spread_pct"] is not None:
        return nbbo["spread_pct"]
    bid = nbbo.get("bid")
    ask = nbbo.get("ask")
    if bid and ask and ask > 0:
        return ((ask - bid) / ask) * 100.0
    return None


def get_market_cap(rec: dict) -> float | None:
    """Extract market cap in millions from record."""
    meta = rec.get("ticker_metadata", {})
    if not meta:
        return None
    for ticker, info in meta.items():
        if isinstance(info, dict):
            mc = info.get("market_cap_millions")
            if mc is not None:
                return mc
    return None


def get_sector_industry(rec: dict) -> tuple[str | None, str | None]:
    """Extract sector and industry from record metadata."""
    meta = rec.get("ticker_metadata", {})
    if not meta:
        return None, None
    for ticker, info in meta.items():
        if isinstance(info, dict):
            sector = info.get("sector")
            industry = info.get("industry")
            return sector, industry
    return None, None


def get_entry_price(rec: dict) -> float | None:
    """Get entry price (initial ask)."""
    nbbo = rec.get("initial_nbbo", {})
    if not nbbo:
        return None
    return nbbo.get("ask")


def get_10min_price(rec: dict) -> float | None:
    """Get 10-min exit price. Use ask from price_check_10min."""
    pc = rec.get("price_check_10min")
    if not pc:
        # Fallback to 5min
        pc = rec.get("price_check_5min")
    if not pc:
        return None
    return pc.get("ask") or pc.get("mid")


def get_mfe_pct(rec: dict) -> float | None:
    """Get maximum favorable excursion in percent."""
    mfe = rec.get("highest_price_during_hold")
    if not mfe or not isinstance(mfe, dict):
        return None
    return mfe.get("percent_gain_from_entry")


def get_mae_pct(rec: dict) -> float | None:
    """Get maximum adverse excursion in percent (negative number)."""
    mae = rec.get("max_adverse_excursion")
    if not mae or not isinstance(mae, dict):
        return None
    return mae.get("percent_loss_from_entry")


def get_ticker(rec: dict) -> str:
    """Get primary ticker."""
    tickers = rec.get("tickers", [])
    return tickers[0] if tickers else "???"


# ============================================================================
# P&L SIMULATION
# ============================================================================

def simulate_pnl(entry_price: float, mfe_pct: float | None, mae_pct: float | None,
                 price_10min: float | None) -> dict:
    """
    Simulate P&L for a single trade using HIGH_CONVICTION tier system.

    Returns dict with: total_pnl, total_pnl_pct, shares, details
    """
    if entry_price <= 0:
        return {"total_pnl": 0.0, "total_pnl_pct": 0.0, "shares": 0, "details": "invalid entry"}

    total_shares = math.floor(POSITION_SIZE_DOLLARS / entry_price)
    if total_shares <= 0:
        return {"total_pnl": 0.0, "total_pnl_pct": 0.0, "shares": 0, "details": "price too high"}

    actual_investment = total_shares * entry_price

    # If we don't have MFE/MAE data, use 10min price only
    if mfe_pct is None and mae_pct is None:
        if price_10min is None:
            return {
                "total_pnl": None, "total_pnl_pct": None,
                "shares": total_shares, "details": "no price data"
            }
        pnl = (price_10min - entry_price) * total_shares
        pnl_pct = ((price_10min / entry_price) - 1) * 100
        return {
            "total_pnl": pnl, "total_pnl_pct": pnl_pct,
            "shares": total_shares, "details": "10min_exit_only"
        }

    mfe = mfe_pct if mfe_pct is not None else 0.0
    mae = mae_pct if mae_pct is not None else 0.0

    remaining_shares = total_shares
    total_revenue = 0.0
    details_parts = []

    # Check stop loss first
    if mae <= STOP_LOSS_PCT:
        # Stopped out - ALL shares at stop price
        stop_price = entry_price * (1 + STOP_LOSS_PCT / 100)
        total_revenue = remaining_shares * stop_price
        total_pnl = total_revenue - actual_investment
        total_pnl_pct = (total_pnl / actual_investment) * 100
        return {
            "total_pnl": total_pnl, "total_pnl_pct": total_pnl_pct,
            "shares": total_shares, "details": f"STOPPED_OUT@{STOP_LOSS_PCT}%"
        }

    # Tier 1: +25%
    if mfe >= TIER1_PCT:
        t1_shares = math.floor(total_shares * TIER1_SELL_FRAC)
        t1_price = entry_price * (1 + TIER1_PCT / 100)
        total_revenue += t1_shares * t1_price
        remaining_shares -= t1_shares
        details_parts.append(f"T1:{t1_shares}@+{TIER1_PCT}%")

    # Tier 2: +40%
    if mfe >= TIER2_PCT and remaining_shares > 0:
        t2_shares = math.floor(remaining_shares * TIER2_SELL_FRAC)
        t2_price = entry_price * (1 + TIER2_PCT / 100)
        total_revenue += t2_shares * t2_price
        remaining_shares -= t2_shares
        details_parts.append(f"T2:{t2_shares}@+{TIER2_PCT}%")

    # Tier 3: +60%
    if mfe >= TIER3_PCT and remaining_shares > 0:
        t3_shares = remaining_shares
        t3_price = entry_price * (1 + TIER3_PCT / 100)
        total_revenue += t3_shares * t3_price
        remaining_shares = 0
        details_parts.append(f"T3:{t3_shares}@+{TIER3_PCT}%")

    # Handle remaining shares (not all tiers fired)
    if remaining_shares > 0:
        # Check trailing stop: if 10min price is more than 15pp below MFE
        exit_price = None
        exit_reason = ""

        if price_10min is not None:
            price_10min_pct = ((price_10min / entry_price) - 1) * 100
            trailing_stop_level = mfe - TRAILING_STOP_PP

            if trailing_stop_level > 0 and price_10min_pct < trailing_stop_level:
                # Trailing stop fires
                exit_price = entry_price * (1 + trailing_stop_level / 100)
                exit_reason = f"trailing_stop@+{trailing_stop_level:.1f}%"
            else:
                # Exit at 10min price
                exit_price = price_10min
                exit_reason = "10min_exit"
        elif mfe > TRAILING_STOP_PP:
            # No 10min price but high MFE — assume trailing stop
            trailing_stop_level = mfe - TRAILING_STOP_PP
            exit_price = entry_price * (1 + trailing_stop_level / 100)
            exit_reason = f"trailing_stop@+{trailing_stop_level:.1f}%(no10min)"
        else:
            # No 10min price, low MFE — assume flat exit
            exit_price = entry_price
            exit_reason = "flat_exit(no10min)"

        total_revenue += remaining_shares * exit_price
        details_parts.append(f"remain:{remaining_shares}={exit_reason}")

    total_pnl = total_revenue - actual_investment
    total_pnl_pct = (total_pnl / actual_investment) * 100

    return {
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "shares": total_shares,
        "details": " | ".join(details_parts) if details_parts else "no_tiers"
    }


# ============================================================================
# LLM CALLS
# ============================================================================

def call_triage(headline: str) -> str:
    """Call Groq triage classifier. Returns the headline type string."""
    triage_prompt = TRIAGE_PROMPT_PATH.read_text()
    # Replace placeholder
    user_msg = triage_prompt.replace("{headline}", headline)

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "user", "content": user_msg}
            ],
            temperature=0.0,
            max_tokens=20,
        )
        result = response.choices[0].message.content.strip().lower()
        # Clean up: sometimes model adds extra text
        # Take first word/token that looks like a type
        result = result.split('\n')[0].strip()
        result = result.strip('"\'., ')
        return result
    except Exception as e:
        return f"error:{e}"


def call_sector_classifier(headline: str, sector: str, industry: str,
                           market_cap: float | None, price: float | None) -> str:
    """Call Groq sector classifier. Returns TRADE or SKIP (with optional size)."""
    # Resolve industry alias
    resolved_industry = INDUSTRY_ALIASES.get(industry, industry) if industry else industry

    prompt = load_prompt(sector, resolved_industry) if sector and resolved_industry else None
    if not prompt:
        # Fallback to aerospace_defense for defense headlines
        prompt = (PROMPTS_DIR / "industrials" / "aerospace_defense.txt").read_text()

    # Build user message (matching real system format)
    mc = market_cap if market_cap else 0
    pr = price if price else 0

    if mc > 0 and pr > 0:
        user_message = f"""HEADLINE: {headline}

CONTEXT:
- Sector: {sector or 'Unknown'}
- Industry: {resolved_industry or 'Unknown'}
- Price: ${pr:.2f}
- Market Cap: ${mc:.1f}M

IMPORTANT: When the headline contains dollar figures (investments, contracts, partnerships),
assess them RELATIVE to the company's market cap:
- Investment/contract > 10% of market cap = significant, worth noting
- Investment/contract > 25% of market cap = major deal, likely TRADE
- Investment/contract > 50% of market cap = transformational, very likely TRADE
- Investment/contract > 100% of market cap = massive, almost certainly TRADE
- A "$40M investment" means very different things for a $2M vs $500M company

Also consider sector norms:
- Industrials contracts with specific $ values are usually real deals
- Biotech: Phase 3 results matter more than deal size
- Tech: Large enterprise contracts relative to market cap are significant

Respond: TRADE or SKIP"""
    else:
        user_message = f"""HEADLINE: {headline}

CONTEXT:
- Sector: {sector or 'Unknown'}
- Industry: {resolved_industry or 'Unknown'}

Respond: TRADE or SKIP"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.0,
            max_tokens=10,
        )
        result = response.choices[0].message.content.strip().upper()
        return result
    except Exception as e:
        return f"ERROR:{e}"


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def main():
    print("=" * 120)
    print("DEFENSE TRADING PIPELINE SIMULATION")
    print("=" * 120)
    print()

    # Step 1: Find all recall files and search for defense headlines
    print("[STEP 1] Scanning all recall files for defense/military headlines...")
    recall_files = sorted(glob.glob(str(RECALL_DIR / "**/*.json"), recursive=True))
    print(f"  Found {len(recall_files)} recall session files")

    defense_records = []
    total_records_scanned = 0
    seen_article_ids = set()  # Deduplicate

    for fpath in recall_files:
        try:
            with open(fpath) as f:
                data = json.load(f)
            records = data.get("records", [])
            total_records_scanned += len(records)
            for rec in records:
                title = clean_html(rec.get("title", ""))
                article_id = rec.get("article_id", "")
                if article_id in seen_article_ids:
                    continue
                if is_defense_headline(title):
                    seen_article_ids.add(article_id)
                    rec["_clean_title"] = title
                    rec["_source_file"] = fpath
                    defense_records.append(rec)
        except Exception as e:
            print(f"  ERROR reading {fpath}: {e}")

    print(f"  Scanned {total_records_scanned} total records")
    print(f"  Found {len(defense_records)} defense/military headlines (deduplicated)")
    print()

    # Step 2: Apply prefilters
    print("[STEP 2] Applying prefilters...")
    prefilter_results = []

    for rec in defense_records:
        title = rec["_clean_title"]
        ticker = get_ticker(rec)
        reasons = []

        # 2a. Market cap check
        mc = get_market_cap(rec)
        if mc is not None and mc > 500:
            reasons.append(f"market_cap_too_high:{mc:.0f}M")

        # 2b. Latency check
        latency_s = compute_latency_seconds(rec)
        if latency_s is not None and latency_s > 15:
            reasons.append(f"latency_too_high:{latency_s:.0f}s")

        # 2c. Headline pattern check
        skip_reason = should_skip_headline(title)
        if skip_reason:
            reasons.append(skip_reason)

        # 2d. Spread check (> 10% skip)
        nbbo = rec.get("initial_nbbo", {})
        spread_pct = compute_spread_pct(nbbo)
        if spread_pct is not None and spread_pct > 10:
            reasons.append(f"spread_too_wide:{spread_pct:.1f}%")

        passed = len(reasons) == 0
        prefilter_results.append({
            "rec": rec,
            "passed": passed,
            "prefilter_reasons": reasons,
            "ticker": ticker,
            "title": title,
            "market_cap": mc,
            "latency_s": latency_s,
            "spread_pct": spread_pct,
        })

    passed_prefilter = [r for r in prefilter_results if r["passed"]]
    failed_prefilter = [r for r in prefilter_results if not r["passed"]]

    print(f"  Passed prefilters: {len(passed_prefilter)}")
    print(f"  Failed prefilters: {len(failed_prefilter)}")
    for fr in failed_prefilter:
        print(f"    SKIP {fr['ticker']:6s} | {fr['title'][:70]:70s} | {', '.join(fr['prefilter_reasons'])}")
    print()

    # Step 3: Triage LLM calls
    print("[STEP 3] Running REAL triage LLM calls (Groq llama-3.1-8b-instant)...")
    high_conviction = []
    normal_path = []

    for i, item in enumerate(passed_prefilter):
        title = item["title"]
        ticker = item["ticker"]
        print(f"  [{i+1}/{len(passed_prefilter)}] Triage: {ticker:6s} | {title[:70]}")

        triage_result = call_triage(title)
        item["triage_result"] = triage_result

        if triage_result in HIGH_CONVICTION_TYPES:
            print(f"    -> HIGH CONVICTION: {triage_result}")
            high_conviction.append(item)
        else:
            print(f"    -> normal path: {triage_result}")
            normal_path.append(item)

        time.sleep(0.5)  # Rate limiting

    print()
    print(f"  High-conviction (defense types): {len(high_conviction)}")
    print(f"  Normal path (other types): {len(normal_path)}")
    print()

    # Step 4: Sector classification LLM calls (high conviction only)
    print("[STEP 4] Running REAL sector classification LLM calls (Groq llama-3.3-70b-versatile)...")
    trades = []
    skips = []

    for i, item in enumerate(high_conviction):
        rec = item["rec"]
        title = item["title"]
        ticker = item["ticker"]
        mc = item["market_cap"]
        sector, industry = get_sector_industry(rec)

        # Get price from metadata
        meta = rec.get("ticker_metadata", {})
        price = None
        for t, info in meta.items():
            if isinstance(info, dict):
                price = info.get("price")
                break

        print(f"  [{i+1}/{len(high_conviction)}] Sector LLM: {ticker:6s} | {sector or 'N/A'}/{industry or 'N/A'} | {title[:60]}")

        sector_result = call_sector_classifier(title, sector, industry, mc, price)
        item["sector_result"] = sector_result

        if "TRADE" in sector_result:
            print(f"    -> {sector_result}")
            trades.append(item)
        else:
            print(f"    -> {sector_result}")
            skips.append(item)

        time.sleep(0.5)  # Rate limiting

    print()
    print(f"  Sector LLM TRADE: {len(trades)}")
    print(f"  Sector LLM SKIP:  {len(skips)}")
    print()

    # Step 5: P&L simulation
    print("[STEP 5] Simulating P&L for trades...")
    trade_results = []

    for item in trades:
        rec = item["rec"]
        entry = get_entry_price(rec)
        mfe = get_mfe_pct(rec)
        mae = get_mae_pct(rec)
        p10 = get_10min_price(rec)

        if entry and entry > 0:
            pnl_result = simulate_pnl(entry, mfe, mae, p10)
        else:
            pnl_result = {
                "total_pnl": None, "total_pnl_pct": None,
                "shares": 0, "details": "no_entry_price"
            }

        item["entry_price"] = entry
        item["mfe_pct"] = mfe
        item["mae_pct"] = mae
        item["price_10min"] = p10
        item["pnl"] = pnl_result
        trade_results.append(item)

    # ============================================================================
    # Step 6: OUTPUT
    # ============================================================================

    print()
    print("=" * 120)
    print("DETAILED RESULTS — ALL DEFENSE HEADLINES")
    print("=" * 120)
    print()

    header = f"{'Ticker':<8}{'Headline':<82}{'Prefilter':<12}{'Triage':<22}{'Sector LLM':<16}{'Trade?':<8}{'Entry':>8}{'MFE%':>8}{'MAE%':>8}{'10min':>8}{'P&L$':>10}{'P&L%':>8}"
    print(header)
    print("-" * len(header))

    for item in prefilter_results:
        ticker = item["ticker"]
        title = item["title"][:80]
        rec = item["rec"]

        if not item["passed"]:
            # Failed prefilter
            reason_short = item["prefilter_reasons"][0][:20] if item["prefilter_reasons"] else "?"
            print(f"{ticker:<8}{title:<82}{'FAIL':12}{'—':22}{'—':16}{'NO':8}{'—':>8}{'—':>8}{'—':>8}{'—':>8}{'—':>10}{'—':>8}")
            continue

        triage = item.get("triage_result", "—")
        is_hc = triage in HIGH_CONVICTION_TYPES

        if not is_hc:
            print(f"{ticker:<8}{title:<82}{'PASS':12}{triage:22}{'(normal)':16}{'NO':8}{'—':>8}{'—':>8}{'—':>8}{'—':>8}{'—':>10}{'—':>8}")
            continue

        sector_res = item.get("sector_result", "—")
        is_trade = "TRADE" in sector_res

        if not is_trade:
            print(f"{ticker:<8}{title:<82}{'PASS':12}{triage:22}{sector_res:16}{'NO':8}{'—':>8}{'—':>8}{'—':>8}{'—':>8}{'—':>10}{'—':>8}")
            continue

        # This is a trade
        entry = item.get("entry_price")
        mfe = item.get("mfe_pct")
        mae = item.get("mae_pct")
        p10 = item.get("price_10min")
        pnl = item.get("pnl", {})

        entry_s = f"${entry:.2f}" if entry else "—"
        mfe_s = f"{mfe:+.1f}%" if mfe is not None else "—"
        mae_s = f"{mae:+.1f}%" if mae is not None else "—"
        p10_s = f"${p10:.2f}" if p10 else "—"

        pnl_val = pnl.get("total_pnl")
        pnl_pct = pnl.get("total_pnl_pct")
        pnl_s = f"${pnl_val:+,.0f}" if pnl_val is not None else "—"
        pnl_pct_s = f"{pnl_pct:+.1f}%" if pnl_pct is not None else "—"

        print(f"{ticker:<8}{title:<82}{'PASS':12}{triage:22}{sector_res:16}{'YES':8}{entry_s:>8}{mfe_s:>8}{mae_s:>8}{p10_s:>8}{pnl_s:>10}{pnl_pct_s:>8}")

    # Print trade details
    print()
    print("=" * 120)
    print("TRADE DETAIL BREAKDOWN")
    print("=" * 120)
    for item in trade_results:
        pnl = item.get("pnl", {})
        print(f"  {item['ticker']:6s} | Entry: ${item.get('entry_price', 0):.2f} | "
              f"MFE: {item.get('mfe_pct', 'N/A')}% | MAE: {item.get('mae_pct', 'N/A')}% | "
              f"Shares: {pnl.get('shares', 0)} | "
              f"P&L: ${pnl.get('total_pnl', 'N/A')} ({pnl.get('total_pnl_pct', 'N/A')}%) | "
              f"{pnl.get('details', '')}")

    # ============================================================================
    # SUMMARY
    # ============================================================================

    print()
    print("=" * 120)
    print("SUMMARY")
    print("=" * 120)

    total_headlines = len(defense_records)
    passed_pf = len(passed_prefilter)
    hc_count = len(high_conviction)
    trade_count = len(trades)
    skip_count = len(skips)

    # P&L stats
    valid_pnls = [t["pnl"]["total_pnl"] for t in trade_results
                  if t["pnl"].get("total_pnl") is not None]
    wins = [p for p in valid_pnls if p > 0]
    losses = [p for p in valid_pnls if p <= 0]

    total_pnl = sum(valid_pnls) if valid_pnls else 0
    win_rate = (len(wins) / len(valid_pnls) * 100) if valid_pnls else 0
    avg_pnl = (total_pnl / len(valid_pnls)) if valid_pnls else 0

    best_trade = max(valid_pnls) if valid_pnls else 0
    worst_trade = min(valid_pnls) if valid_pnls else 0
    best_ticker = ""
    worst_ticker = ""
    for t in trade_results:
        if t["pnl"].get("total_pnl") == best_trade:
            best_ticker = t["ticker"]
        if t["pnl"].get("total_pnl") == worst_trade:
            worst_ticker = t["ticker"]

    total_capital = trade_count * POSITION_SIZE_DOLLARS

    print(f"  Total defense headlines found:    {total_headlines}")
    print(f"  Passed prefilters:                {passed_pf}")
    print(f"  Triage high-conviction:           {hc_count}")
    print(f"  Sector LLM → TRADE:              {trade_count}")
    print(f"  Sector LLM → SKIP:               {skip_count}")
    print(f"  Normal path (non-defense triage): {len(normal_path)}")
    print()
    print(f"  Total trades taken:               {trade_count}")
    print(f"  Trades with P&L data:             {len(valid_pnls)}")
    print(f"  Total P&L:                        ${total_pnl:+,.2f}")
    print(f"  Win rate:                         {win_rate:.1f}%")
    print(f"  Average P&L per trade:            ${avg_pnl:+,.2f}")
    print(f"  Best trade:                       ${best_trade:+,.2f} ({best_ticker})")
    print(f"  Worst trade:                      ${worst_trade:+,.2f} ({worst_ticker})")
    print(f"  Total capital deployed:            ${total_capital:,}")
    if total_capital > 0:
        print(f"  Return on capital:                {(total_pnl / total_capital * 100):+.2f}%")
    print()
    print("=" * 120)


if __name__ == "__main__":
    main()
