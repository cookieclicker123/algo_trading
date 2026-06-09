"""
Multi-sector headline classifier using industry-specific Anthropic Claude prompts.

Simple flow: headline → sector check → industry check → LLM classification → TRADE/SKIP

This is the primary trading decision maker - no microstructure filters.
Speed is critical - classify as fast as possible for immediate entry.

Supported sectors:
- Healthcare (7 industries)
- Technology (12 industries)
- Industrials (14 industry groupings)
- Consumer Cyclical (6 industry groupings)
- Financial Services (3 industry groupings)
- Consumer Defensive (3 industries)
- Basic Materials (5 industries)
- Communication Services (1 industry - Electronic Gaming & Multimedia only)
- Energy (9 industries - Solar + Oil & Gas)
"""
import asyncio
from pathlib import Path
from typing import Optional, Dict, Tuple

from datetime import datetime

from anthropic import AsyncAnthropic, RateLimitError
from groq import AsyncGroq

from ...utils.logging_config import get_logger

logger = get_logger(__name__)

# Sector to industry mappings
# Each sector maps industry names to prompt filenames

SECTOR_INDUSTRY_MAP: Dict[str, Dict[str, str]] = {
    # =========================================================================
    # HEALTHCARE (7 industries)
    # =========================================================================
    "Healthcare": {
        "Biotechnology": "biotechnology.txt",
        "Medical Devices": "medical_devices.txt",
        "Drug Manufacturers - Specialty & Generic": "drug_manufacturers.txt",
        "Diagnostics & Research": "diagnostics_research.txt",
        "Health Information Services": "health_information_services.txt",
        "Medical Instruments & Supplies": "medical_instruments_supplies.txt",
        "Medical Care Facilities": "medical_care_facilities.txt",
    },

    # =========================================================================
    # TECHNOLOGY (12 industries)
    # FMP classifies 97% of Electronic Gaming tickers under Technology,
    # not Communication Services. Support it in both sectors.
    # =========================================================================
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

    # =========================================================================
    # INDUSTRIALS (14 industry groupings)
    # =========================================================================
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
        # Combined prompt for smaller transportation industries
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

    # =========================================================================
    # CONSUMER CYCLICAL (7 industry groupings)
    # =========================================================================
    "Consumer Cyclical": {
        "Auto Parts": "auto_parts.txt",
        "Specialty Retail": "specialty_retail.txt",
        "Auto Manufacturers": "auto_manufacturers.txt",
        "Internet Retail": "internet_retail.txt",
        "Apparel Retail": "apparel_retail.txt",
        # Combined prompt for smaller consumer industries
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

    # =========================================================================
    # FINANCIAL SERVICES (3 industry groupings)
    # =========================================================================
    "Financial Services": {
        "Capital Markets": "capital_markets.txt",
        "Asset Management": "asset_management.txt",
        # Combined prompt for banking/insurance industries
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

    # =========================================================================
    # COMMUNICATION SERVICES (1 industry - Electronic Gaming only)
    # Re-enabled Jan 2026 after GCL +60% winner analysis
    # Only Electronic Gaming & Multimedia - other industries remain blacklisted
    # =========================================================================
    "Communication Services": {
        "Electronic Gaming & Multimedia": "electronic_gaming_multimedia.txt",
        "Internet Content & Information": "internet_content_information.txt",
        # Blacklisted industries:
        # - Entertainment
        # - Telecom Services
    },

    # =========================================================================
    # CONSUMER DEFENSIVE (3 industries)
    # =========================================================================
    "Consumer Defensive": {
        "Education & Training Services": "education_training.txt",
        "Food Distribution": "food_distribution.txt",
        "Household & Personal Products": "household_personal.txt",
    },

    # =========================================================================
    # BASIC MATERIALS (5 industries)
    # =========================================================================
    "Basic Materials": {
        "Specialty Chemicals": "specialty_chemicals.txt",
        "Agricultural Inputs": "agricultural_inputs.txt",
        # Gold and Precious Metals - M&A, contracts, discoveries are key catalysts
        "Gold": "precious_metals_mining.txt",
        "Other Precious Metals & Mining": "precious_metals_mining.txt",
        # Industrial Metals - lithium, cobalt, graphite, uranium, rare earths, copper
        # DOD/DOE contracts, offtake agreements, strategic investments are key catalysts
        "Other Industrial Metals & Mining": "other_industrial_metals_mining.txt",
    },

    # =========================================================================
    # ENERGY (Solar + Oil & Gas industries)
    # Solar under Energy uses same prompt as Technology/Solar (FMP classifies
    # some solar tickers as Energy/Solar instead of Technology/Solar).
    # Oil & Gas industries added based on 83 historical winners analysis.
    # =========================================================================
    "Energy": {
        # Solar (FMP sometimes classifies solar tickers under Energy instead of Technology)
        "Solar": "solar.txt",
        # Oil & Gas Equipment & Services (28 winners, avg +14.0%, contracts strongest catalyst)
        "Oil & Gas Equipment & Services": "oil_gas_equipment_services.txt",
        # Oil & Gas E&P (25 winners, avg +22.9%, M&A/asset deals + well completions)
        "Oil & Gas E&P": "oil_gas_ep.txt",
        # Oil & Gas Midstream (18 winners, avg +31.4%, LNG contracts + spin-offs)
        "Oil & Gas Midstream": "oil_gas_midstream.txt",
        # Smaller O&G industries (combined prompt)
        "Oil & Gas Drilling": "oil_gas_other.txt",
        "Oil & Gas Integrated": "oil_gas_other.txt",
        "Oil & Gas Refining & Marketing": "oil_gas_other.txt",
        "Thermal Coal": "oil_gas_other.txt",
        "Uranium": "oil_gas_other.txt",
    },
}

# Aliases for FMP/yfinance industry names that differ from our SECTOR_INDUSTRY_MAP keys.
# FMP uses "Category - Subcategory" dash format. yfinance uses yet another format.
# This dict maps external names → our canonical SECTOR_INDUSTRY_MAP keys.
# Comprehensive audit performed against all recall data unsupported_industry blocks.
INDUSTRY_ALIASES: Dict[str, str] = {
    # =========================================================================
    # HEALTHCARE
    # =========================================================================
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
    # =========================================================================
    # FINANCIAL SERVICES
    # =========================================================================
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
    # =========================================================================
    # CONSUMER CYCLICAL
    # =========================================================================
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
    # =========================================================================
    # INDUSTRIALS
    # =========================================================================
    "Industrial - Distribution": "Industrial Distribution",
    "Industrial - Machinery": "Specialty Industrial Machinery",
    "Industrial - Pollution & Treatment Controls": "Pollution & Treatment Controls",
    "Manufacturing - Metal Fabrication": "Metal Fabrication",
    "Airlines, Airports & Air Services": "Airlines",
    "Airports & Air Services": "Airlines",
    "Agricultural - Machinery": "Farm & Heavy Construction Machinery",
    # =========================================================================
    # BASIC MATERIALS
    # =========================================================================
    "Chemicals - Specialty": "Specialty Chemicals",
    # =========================================================================
    # TECHNOLOGY
    # =========================================================================
    "Hardware, Equipment & Parts": "Computer Hardware",
    # =========================================================================
    # ENERGY
    # =========================================================================
    "Oil & Gas - Equipment & Services": "Oil & Gas Equipment & Services",
    "Oil & Gas - E&P": "Oil & Gas E&P",
    "Oil & Gas Exploration & Production": "Oil & Gas E&P",
    "Oil & Gas - Midstream": "Oil & Gas Midstream",
    "Oil & Gas - Drilling": "Oil & Gas Drilling",
    "Oil & Gas - Integrated": "Oil & Gas Integrated",
    "Oil & Gas - Refining & Marketing": "Oil & Gas Refining & Marketing",
    "Oil & Gas - Services": "Oil & Gas Equipment & Services",
    "Thermal Coal": "Thermal Coal",
}

# Set of all supported sectors
SUPPORTED_SECTORS = set(SECTOR_INDUSTRY_MAP.keys())


def get_prompt_directory(sector: str) -> str:
    """Convert sector name to prompt directory name."""
    return sector.lower().replace(" ", "_")


def parse_sector_response(raw_text: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Parse the sector LLM's two-line entity-CoT response.

    Expected format (healthcare prompts, 2026-06-09):
        ENTITIES: <terse entity extraction — the reasoning>
        DECISION: TRADE MODERATE | SKIP | ...

    Returns (classification, position_size, entities):
      - classification: "TRADE" or "SKIP"
      - position_size:  "MAX"/"LARGE"/"MODERATE"/"SMALL" or None
      - entities:       the extracted entity string, or None

    Robust to missing labels, extra prose, casing, and the legacy bare-token
    format ("TRADE MODERATE" / "SKIP") so non-CoT prompts still parse. The
    TRADE/SKIP decision is read ONLY from the DECISION segment to avoid
    misreading phrases like "would not TRADE" inside the entity line.
    """
    if not raw_text:
        return "SKIP", None, None
    text = raw_text.strip()
    low = text.lower()

    ent_idx = low.find("entities:")
    dec_idx = low.find("decision:")

    entities = None
    if ent_idx != -1:
        end = dec_idx if (dec_idx != -1 and dec_idx > ent_idx) else len(text)
        entities = text[ent_idx + len("entities:"):end].strip().strip("-—").strip()
        entities = " ".join(entities.split()) or None

    if dec_idx != -1:
        decision_seg = text[dec_idx + len("decision:"):]
    else:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        decision_seg = lines[-1] if lines else text
    decision_up = decision_seg.upper()

    if "TRADE" in decision_up:
        if "MAX" in decision_up:
            size = "MAX"
        elif "LARGE" in decision_up:
            size = "LARGE"
        elif "MODERATE" in decision_up:
            size = "MODERATE"
        elif "SMALL" in decision_up:
            size = "SMALL"
        else:
            size = "MODERATE"
        return "TRADE", size, entities
    return "SKIP", None, entities


class SectorClassifier:
    """
    Fast multi-sector headline classifier using industry-specific prompts.

    Design principles:
    - Speed over everything - one API call per headline
    - Industry-specific prompts for better accuracy
    - Simple TRADE/SKIP output
    - No microstructure checks - pure language-based decision
    """

    def __init__(
        self,
        api_key: str,
        metadata_cache,  # MetadataCache instance for sector/industry lookup
        model: str = "claude-haiku-4-5-20251001",
        groq_api_key: str = None,
        groq_fallback_model: str = "llama-3.3-70b-versatile",
    ):
        """
        Initialize multi-sector classifier.

        Args:
            api_key: Anthropic API key
            metadata_cache: MetadataCache instance for instant sector/industry lookup
            model: Anthropic model to use (default: Claude Haiku 4.5)
            groq_api_key: Groq API key for fallback on Anthropic 429 rate limits
            groq_fallback_model: Groq model for fallback classification
        """
        self.api_key = api_key
        self.metadata_cache = metadata_cache
        self.model = model
        self.groq_fallback_model = groq_fallback_model

        # Anthropic client (primary)
        self.client = AsyncAnthropic(api_key=api_key, timeout=15.0) if api_key else None

        # Groq client (fallback for Anthropic 429 rate limits)
        self.groq_client = AsyncGroq(api_key=groq_api_key) if groq_api_key else None

        # Cache loaded prompts (load once, reuse)
        # Key format: "{sector}/{industry}"
        self._prompts: Dict[str, str] = {}

        # Base prompt directory (relative to project root)
        self._prompt_base_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts"

        # Stats (per sector)
        self._stats = {
            "total_classified": 0,
            "trade_signals": 0,
            "skip_signals": 0,
            "not_supported_sector": 0,
            "unsupported_industry": 0,
            "errors": 0,
            "avg_latency_ms": 0.0,
            "by_sector": {sector: {"trade": 0, "skip": 0} for sector in SUPPORTED_SECTORS},
        }

        logger.info(
            "SectorClassifier initialized",
            model=model,
            prompt_base_dir=str(self._prompt_base_dir),
            supported_sectors=list(SUPPORTED_SECTORS),
            total_industries=sum(len(industries) for industries in SECTOR_INDUSTRY_MAP.values()),
        )

    def _load_prompt(self, sector: str, industry: str) -> Optional[str]:
        """
        Load industry-specific prompt (cached after first load).

        Args:
            sector: Sector name (must be in SUPPORTED_SECTORS)
            industry: Industry name (must be in sector's industry map)

        Returns:
            Prompt text or None if not found
        """
        cache_key = f"{sector}/{industry}"
        if cache_key in self._prompts:
            return self._prompts[cache_key]

        # Get industry map for sector
        industry_map = SECTOR_INDUSTRY_MAP.get(sector, {})
        prompt_file = industry_map.get(industry)
        if not prompt_file:
            return None

        # Build path: prompts/{sector_dir}/{prompt_file}
        sector_dir = get_prompt_directory(sector)
        prompt_path = self._prompt_base_dir / sector_dir / prompt_file

        try:
            with open(prompt_path, "r") as f:
                prompt = f.read()
            # Append universal cross-sector rule so Haiku evaluates catalysts
            # on their merits rather than anchoring on the company's industry
            prompt += """

===============================================================================
CROSS-SECTOR CATALYST OVERRIDE
===============================================================================

CRITICAL: Evaluate the CATALYST, not the company's industry. A restaurant company
announcing a military drone partnership is a MILITARY catalyst, not a restaurant catalyst.

If the headline describes ANY of these, it is a TRADE regardless of this company's sector:
- Military, defense, or government contracts/partnerships (DOD, DOE, NASA, any branch)
- Merger partner or subsidiary news that benefits the parent ticker
- Joint ventures with named partners and specific technology/product
- Transformational technology pivots (AI, drones, space, defense tech, quantum)
- Acquisition targets (company BEING acquired)

These catalysts transcend industry classification. Size them as you would for any
strong catalyst in the sector where the deal actually belongs.
"""
            self._prompts[cache_key] = prompt
            logger.debug("Loaded prompt", sector=sector, industry=industry, path=str(prompt_path))
            return prompt
        except Exception as e:
            logger.error("Failed to load prompt", sector=sector, industry=industry, error=str(e))
            return None

    async def classify(
        self,
        headline: str,
        ticker: str,
        prefer_groq: bool = False,
        headline_type: Optional[str] = None,
    ) -> Tuple[str, Optional[str], Optional[str], float, Optional[str], Optional[str]]:
        """
        Classify a headline from any supported sector.

        Args:
            headline: News headline text
            ticker: Primary ticker symbol
            headline_type: Optional triage classification (e.g.
                "acquisition_with_revenue_generating_business"). When provided,
                used to inject type-specific guidance into the sector prompt's
                user message (e.g. acquisition materiality gate).

        Returns:
            Tuple of (classification, sector, industry, latency_ms, position_size):
            - classification: "TRADE", "SKIP", "NOT_SUPPORTED_SECTOR", or "UNSUPPORTED_INDUSTRY"
            - sector: Sector name if supported, else None
            - industry: Industry name if supported, else None
            - latency_ms: Classification latency in milliseconds
            - position_size: "SMALL", "MODERATE", "LARGE", "MAX", or None if not TRADE
        """
        start_time = datetime.now()

        # Step 1: Check sector/industry from cache (instant, ~0ms)
        metadata = await self.metadata_cache.get_permanent(ticker)

        if not metadata:
            # Unknown ticker - can't classify without sector/industry
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.debug(
                "Classification skipped - no metadata",
                ticker=ticker,
                headline=headline[:50]
            )
            self._stats["not_supported_sector"] += 1
            return "NOT_SUPPORTED_SECTOR", None, None, latency_ms, None, None

        sector = metadata.get("sector", "")
        industry = metadata.get("industry", "")

        # Step 2: Check if supported sector
        if sector not in SUPPORTED_SECTORS:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.debug(
                "Classification skipped - unsupported sector",
                ticker=ticker,
                sector=sector,
                headline=headline[:50]
            )
            self._stats["not_supported_sector"] += 1
            return "NOT_SUPPORTED_SECTOR", sector, None, latency_ms, None, None

        # Step 3: Check if supported industry within sector
        # Normalize FMP/yfinance industry names to our canonical names
        industry = INDUSTRY_ALIASES.get(industry, industry)
        industry_map = SECTOR_INDUSTRY_MAP.get(sector, {})

        if industry not in industry_map:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(
                "Classification skipped - unsupported industry",
                ticker=ticker,
                sector=sector,
                industry=industry,
                headline=headline[:50]
            )
            self._stats["unsupported_industry"] += 1
            return "UNSUPPORTED_INDUSTRY", sector, industry, latency_ms, None, None

        # Step 4: Load industry-specific prompt
        prompt = self._load_prompt(sector, industry)
        if not prompt:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error("Failed to load prompt", sector=sector, industry=industry)
            self._stats["errors"] += 1
            return "SKIP", sector, industry, latency_ms, None, None

        # Step 5: Call Anthropic Claude for classification
        if not self.client:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error("Anthropic client not initialized")
            self._stats["errors"] += 1
            return "SKIP", sector, industry, latency_ms, None, None

        try:
            # Build context-aware user message
            # Include price and market cap so AI can assess magnitude relative to company size
            price = metadata.get("price", 0)
            market_cap = metadata.get("market_cap_millions", 0)

            # Context-aware message format
            # This allows the AI to understand that $40M into a $2M company is transformational
            if price and market_cap:
                # Acquisition materiality gate: for the acquisition-with-revenue type,
                # the acquirer is spending cash, so we trade this ONLY when the inbound
                # revenue stream is material enough to reprice the company. Hard floor
                # at 50% of market cap. Injected at runtime so all sector prompts get
                # this guidance without having to edit each one.
                acquisition_materiality_note = ""
                if headline_type == "acquisition_with_revenue_generating_business":
                    required_rev = market_cap * 0.5
                    acquisition_materiality_note = (
                        "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "OVERRIDE RULE — ACQUISITION WITH MATERIAL REVENUE (applies to THIS headline):\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "Your sector prompt contains a default rule that acquirer-side acquisitions "
                        "are SKIP because the company is spending cash. THAT DEFAULT DOES NOT APPLY "
                        "HERE. Triage has pre-classified this headline as "
                        "`acquisition_with_revenue_generating_business`, meaning the acquired entity "
                        "brings a CONCRETE inbound revenue stream named in the headline. Follow these "
                        "steps instead of the default:\n"
                        f"  1. Identify the inbound revenue figure in the headline.\n"
                        f"  2. Compare it to this company's market cap (${market_cap:.1f}M). The 50%-of-mcap "
                        f"floor is ${required_rev:.1f}M.\n"
                        f"  3. If the revenue figure is ≥ ${required_rev:.1f}M (i.e. at least 50% of mcap), "
                        f"respond TRADE — the inbound revenue materially reprices the combined entity and "
                        f"overrides the cash-outflow concern.\n"
                        f"  4. If the revenue is < ${required_rev:.1f}M, or the figure is soft (\"targeting\", "
                        f"\"up to\", \"potential\"), or not clearly tied to the acquired business, respond SKIP.\n"
                        "Worked example (follow this pattern):\n"
                        "  Headline: \"Announces $1.5B AI Acquisition, Targets $280M Revenue by 2027\"\n"
                        "  Market cap: $180M → 50% floor = $90M\n"
                        "  Inbound revenue: $280M (dated forward revenue).\n"
                        "  $280M ≥ $90M → TRADE. The default \"acquisitions = SKIP\" rule DOES NOT APPLY.\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    )

                user_message = f"""HEADLINE: {headline}

CONTEXT:
- Ticker: {ticker}
- Price: ${price:.2f}
- Market Cap: ${market_cap:.1f}M

IMPORTANT: When the headline contains dollar figures (investments, contracts, partnerships),
assess them RELATIVE to the company's market cap:
- Investment/contract > 10% of market cap = significant, worth noting
- Investment/contract > 25% of market cap = major deal, likely TRADE
- Investment/contract > 50% of market cap = transformational, very likely TRADE
- Investment/contract > 100% of market cap = massive, almost certainly TRADE
- A "$40M investment" means very different things for a $2M vs $500M company{acquisition_materiality_note}

Respond using the exact output format specified in your instructions above."""
            else:
                # Fallback to headline-only if no metadata
                user_message = headline

            # Groq-first path (for backfill — higher rate limits)
            if prefer_groq and self.groq_client:
                try:
                    groq_response = await self.groq_client.chat.completions.create(
                        model=self.groq_fallback_model,
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": user_message},
                        ],
                        temperature=0.0,
                        max_tokens=120,  # room for ENTITIES line + DECISION
                    )
                    classification, position_size, entities = parse_sector_response(
                        groq_response.choices[0].message.content
                    )
                    if classification == "TRADE":
                        self._stats["trade_signals"] += 1
                        self._stats["by_sector"][sector]["trade"] += 1
                    else:
                        self._stats["skip_signals"] += 1
                        self._stats["by_sector"][sector]["skip"] += 1

                    latency_ms = (datetime.now() - start_time).total_seconds() * 1000
                    self._stats["total_classified"] += 1
                    n = self._stats["total_classified"]
                    self._stats["avg_latency_ms"] = (
                        (self._stats["avg_latency_ms"] * (n - 1) + latency_ms) / n
                    )
                    logger.info(
                        f"🎯 {sector} classification (Groq primary): {classification}" + (f" {position_size}" if position_size else ""),
                        ticker=ticker, sector=sector, industry=industry,
                        position_size=position_size, headline=headline[:60],
                        latency_ms=round(latency_ms, 1),
                    )
                    return classification, sector, industry, latency_ms, position_size, entities
                except Exception as groq_err:
                    logger.debug(
                        "Groq primary failed, falling through to Anthropic",
                        ticker=ticker, error=str(groq_err)[:100],
                    )
                    # fall through to Anthropic below

            response = await self.client.messages.create(
                model=self.model,
                system=[
                    {
                        "type": "text",
                        "text": prompt,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
                messages=[
                    {"role": "user", "content": user_message}
                ],
                temperature=0.0,  # Deterministic for consistency
                max_tokens=10,    # Only need "TRADE" or "SKIP"
                extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
            )

            # Parse response (two-line entity-CoT format: ENTITIES + DECISION)
            classification, position_size, entities = parse_sector_response(
                response.content[0].text
            )
            if classification == "TRADE":
                self._stats["trade_signals"] += 1
                self._stats["by_sector"][sector]["trade"] += 1
            else:
                self._stats["skip_signals"] += 1
                self._stats["by_sector"][sector]["skip"] += 1

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            self._stats["total_classified"] += 1

            # Update average latency
            n = self._stats["total_classified"]
            self._stats["avg_latency_ms"] = (
                (self._stats["avg_latency_ms"] * (n - 1) + latency_ms) / n
            )

            logger.info(
                f"🎯 {sector} classification: {classification}" + (f" {position_size}" if position_size else ""),
                ticker=ticker,
                sector=sector,
                industry=industry,
                position_size=position_size,
                headline=headline[:60],
                latency_ms=round(latency_ms, 1)
            )

            return classification, sector, industry, latency_ms, position_size, entities

        except RateLimitError as e:
            # Anthropic 429 rate limit — fall back to Groq if available
            if self.groq_client:
                logger.warning(
                    "⚠️ Anthropic 429 rate limit — falling back to Groq",
                    ticker=ticker,
                    sector=sector,
                    error=str(e)[:100],
                )
                try:
                    groq_response = await self.groq_client.chat.completions.create(
                        model=self.groq_fallback_model,
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": user_message},
                        ],
                        temperature=0.0,
                        max_tokens=120,  # room for ENTITIES line + DECISION
                    )
                    classification, position_size, entities = parse_sector_response(
                        groq_response.choices[0].message.content
                    )
                    if classification == "TRADE":
                        self._stats["trade_signals"] += 1
                        self._stats["by_sector"][sector]["trade"] += 1
                    else:
                        self._stats["skip_signals"] += 1
                        self._stats["by_sector"][sector]["skip"] += 1

                    latency_ms = (datetime.now() - start_time).total_seconds() * 1000
                    self._stats["total_classified"] += 1
                    n = self._stats["total_classified"]
                    self._stats["avg_latency_ms"] = (
                        (self._stats["avg_latency_ms"] * (n - 1) + latency_ms) / n
                    )

                    logger.info(
                        f"🎯 {sector} classification (Groq fallback): {classification}" + (f" {position_size}" if position_size else ""),
                        ticker=ticker,
                        sector=sector,
                        industry=industry,
                        position_size=position_size,
                        headline=headline[:60],
                        latency_ms=round(latency_ms, 1),
                    )
                    return classification, sector, industry, latency_ms, position_size, entities

                except Exception as groq_err:
                    latency_ms = (datetime.now() - start_time).total_seconds() * 1000
                    logger.error(
                        "Groq fallback also failed",
                        ticker=ticker,
                        sector=sector,
                        error=str(groq_err),
                    )
                    self._stats["errors"] += 1
                    return "SKIP", sector, industry, latency_ms, None, None
            else:
                latency_ms = (datetime.now() - start_time).total_seconds() * 1000
                logger.error(
                    "Anthropic 429 rate limit — no Groq fallback configured",
                    ticker=ticker,
                    sector=sector,
                    error=str(e)[:100],
                )
                self._stats["errors"] += 1
                return "SKIP", sector, industry, latency_ms, None, None

        except Exception as e:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(
                "Anthropic API error",
                ticker=ticker,
                sector=sector,
                error=str(e),
                headline=headline[:50]
            )
            self._stats["errors"] += 1
            return "SKIP", sector, industry, latency_ms, None, None

    async def classify_batch(
        self,
        headlines: list[Tuple[str, str]],  # List of (headline, ticker) tuples
    ) -> list[Tuple[str, Optional[str], Optional[str], float, Optional[str]]]:
        """
        Classify multiple headlines in parallel.

        Args:
            headlines: List of (headline, ticker) tuples

        Returns:
            List of (classification, sector, industry, latency_ms, position_size) tuples
        """
        tasks = [self.classify(headline, ticker) for headline, ticker in headlines]
        return await asyncio.gather(*tasks)

    def get_stats(self) -> Dict:
        """Get classifier statistics."""
        return {
            **self._stats,
            "supported_sectors": list(SUPPORTED_SECTORS),
            "model": self.model,
            "prompts_loaded": list(self._prompts.keys()),
        }

    def get_supported_industries(self, sector: str) -> list[str]:
        """Get list of supported industries for a sector."""
        return list(SECTOR_INDUSTRY_MAP.get(sector, {}).keys())

    @staticmethod
    def is_sector_supported(sector: str) -> bool:
        """Check if a sector is supported."""
        return sector in SUPPORTED_SECTORS

    @staticmethod
    def is_industry_supported(sector: str, industry: str) -> bool:
        """Check if an industry is supported within a sector."""
        return industry in SECTOR_INDUSTRY_MAP.get(sector, {})
