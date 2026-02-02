"""
Multi-sector headline classifier using industry-specific Groq LLM prompts.

Simple flow: headline → sector check → industry check → LLM classification → TRADE/SKIP

This is the primary trading decision maker - no microstructure filters.
Speed is critical - classify as fast as possible for immediate entry.

Supported sectors:
- Healthcare (7 industries)
- Technology (11 industries)
- Industrials (14 industry groupings)
- Consumer Cyclical (6 industry groupings)
- Financial Services (3 industry groupings)
- Consumer Defensive (3 industries)
- Basic Materials (4 industries)
- Communication Services (1 industry - Electronic Gaming & Multimedia only)
"""
import asyncio
from pathlib import Path
from typing import Optional, Dict, Tuple

from datetime import datetime

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
    # TECHNOLOGY (11 industries)
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
    # CONSUMER CYCLICAL (6 industry groupings)
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
        # Blacklisted industries (0% win rate, -10.8% avg PnL):
        # - Internet Content & Information
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
    # BASIC MATERIALS (4 industries)
    # =========================================================================
    "Basic Materials": {
        "Specialty Chemicals": "specialty_chemicals.txt",
        "Agricultural Inputs": "agricultural_inputs.txt",
        # Gold and Precious Metals - M&A, contracts, discoveries are key catalysts
        "Gold": "precious_metals_mining.txt",
        "Other Precious Metals & Mining": "precious_metals_mining.txt",
    },
}

# Set of all supported sectors
SUPPORTED_SECTORS = set(SECTOR_INDUSTRY_MAP.keys())


def get_prompt_directory(sector: str) -> str:
    """Convert sector name to prompt directory name."""
    return sector.lower().replace(" ", "_")


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
        model: str = "llama-3.3-70b-versatile",
    ):
        """
        Initialize multi-sector classifier.

        Args:
            api_key: Groq API key
            metadata_cache: MetadataCache instance for instant sector/industry lookup
            model: Groq model to use (default: llama-3.3-70b-versatile)
        """
        self.api_key = api_key
        self.metadata_cache = metadata_cache
        self.model = model

        # Groq client
        self.client = AsyncGroq(api_key=api_key) if api_key else None

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
    ) -> Tuple[str, Optional[str], Optional[str], float]:
        """
        Classify a headline from any supported sector.

        Args:
            headline: News headline text
            ticker: Primary ticker symbol

        Returns:
            Tuple of (classification, sector, industry, latency_ms):
            - classification: "TRADE", "SKIP", "NOT_SUPPORTED_SECTOR", or "UNSUPPORTED_INDUSTRY"
            - sector: Sector name if supported, else None
            - industry: Industry name if supported, else None
            - latency_ms: Classification latency in milliseconds
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
            return "NOT_SUPPORTED_SECTOR", None, None, latency_ms

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
            return "NOT_SUPPORTED_SECTOR", sector, None, latency_ms

        # Step 3: Check if supported industry within sector
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
            return "UNSUPPORTED_INDUSTRY", sector, industry, latency_ms

        # Step 4: Load industry-specific prompt
        prompt = self._load_prompt(sector, industry)
        if not prompt:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error("Failed to load prompt", sector=sector, industry=industry)
            self._stats["errors"] += 1
            return "SKIP", sector, industry, latency_ms

        # Step 5: Call Groq LLM for classification
        if not self.client:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error("Groq client not initialized")
            self._stats["errors"] += 1
            return "SKIP", sector, industry, latency_ms

        try:
            # Simple prompt: just the headline
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": headline}
                ],
                temperature=0.0,  # Deterministic for consistency
                max_tokens=10,    # Only need "TRADE" or "SKIP"
            )

            # Parse response
            result = response.choices[0].message.content.strip().upper()

            # Normalize to TRADE or SKIP
            if "TRADE" in result:
                classification = "TRADE"
                self._stats["trade_signals"] += 1
                self._stats["by_sector"][sector]["trade"] += 1
            else:
                classification = "SKIP"
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
                f"🎯 {sector} classification: {classification}",
                ticker=ticker,
                sector=sector,
                industry=industry,
                headline=headline[:60],
                latency_ms=round(latency_ms, 1)
            )

            return classification, sector, industry, latency_ms

        except Exception as e:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(
                "Groq API error",
                ticker=ticker,
                sector=sector,
                error=str(e),
                headline=headline[:50]
            )
            self._stats["errors"] += 1
            return "SKIP", sector, industry, latency_ms

    async def classify_batch(
        self,
        headlines: list[Tuple[str, str]],  # List of (headline, ticker) tuples
    ) -> list[Tuple[str, Optional[str], Optional[str], float]]:
        """
        Classify multiple headlines in parallel.

        Args:
            headlines: List of (headline, ticker) tuples

        Returns:
            List of (classification, sector, industry, latency_ms) tuples
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
