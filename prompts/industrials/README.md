# Industrials Sector Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

Route headlines to the correct prompt based on cached `industry` field from `permanent_metadata.json`.

```python
INDUSTRY_PROMPT_MAP = {
    "Aerospace & Defense": "prompts/industrials/aerospace_defense.txt",
    "Specialty Industrial Machinery": "prompts/industrials/specialty_machinery.txt",
    "Electrical Equipment & Parts": "prompts/industrials/electrical_equipment.txt",
    "Specialty Business Services": "prompts/industrials/business_services.txt",
    "Engineering & Construction": "prompts/industrials/engineering_construction.txt",
    "Security & Protection Services": "prompts/industrials/security_services.txt",
    "Pollution & Treatment Controls": "prompts/industrials/pollution_controls.txt",
    "Consulting Services": "prompts/industrials/consulting_services.txt",
    "Building Products & Equipment": "prompts/industrials/building_products.txt",
    "Integrated Freight & Logistics": "prompts/industrials/freight_logistics.txt",
    "Waste Management": "prompts/industrials/waste_management.txt",
    "Metal Fabrication": "prompts/industrials/metal_fabrication.txt",
    "Staffing & Employment Services": "prompts/industrials/staffing_services.txt",
    # Smaller industries use transportation_other.txt:
    "Marine Shipping": "prompts/industrials/transportation_other.txt",
    "Farm & Heavy Construction Machinery": "prompts/industrials/transportation_other.txt",
    "Airlines": "prompts/industrials/transportation_other.txt",
    "Conglomerates": "prompts/industrials/transportation_other.txt",
    "Airports & Air Services": "prompts/industrials/transportation_other.txt",
    "Rental & Leasing Services": "prompts/industrials/transportation_other.txt",
    "Railroads": "prompts/industrials/transportation_other.txt",
    "Industrial Distribution": "prompts/industrials/transportation_other.txt",
    "Business Equipment & Supplies": "prompts/industrials/transportation_other.txt",
    "Trucking": "prompts/industrials/transportation_other.txt",
    "Infrastructure Operations": "prompts/industrials/transportation_other.txt",
    "Tools & Accessories": "prompts/industrials/transportation_other.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, guidance) are classified as SKIP.

## Industry Profiles

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Aerospace & Defense | 127 | +18.4% | Contracts (34%), DOD deals |
| Specialty Machinery | 107 | +23.6% | Contracts (26%), Partnerships (15%) |
| Electrical Equipment | 100 | +20.3% | Contracts (18%), EV partnerships |
| Business Services | 62 | +17.4% | Partnerships (24%), Contracts (16%) |
| Engineering & Construction | 57 | +19.8% | Contracts (19%), Infrastructure |
| Security Services | 53 | +21.9% | Contracts (36%), Government |
| Pollution Controls | 43 | +16.3% | Contracts (35%), Municipal |
| Consulting | 34 | +26.0% | Partnerships (26%), Tech vendors |
| Building Products | 33 | +19.5% | Contracts (21%) |
| Freight & Logistics | 32 | +16.3% | Contracts (19%), Enterprise |
| Waste Management | 32 | +26.1% | M&A, Municipal contracts |
| Metal Fabrication | 29 | +21.3% | Contracts (31%) |
| Staffing | 25 | +30.0% | Contracts (28%) |
| Railroads | 14 | +32.9% | Contracts (71%) - strongest signal |

## Data Sources

- **Winners**: 900 Industrials headlines with 10%+ daily moves (from Alpaca historical data)

## Common SKIP Patterns (All Industries)

1. **Earnings**: EPS beats/misses, revenue, guidance - ALWAYS SKIP
2. **Conferences**: Trade shows, investor days
3. **Industry commentary**: Cycle outlooks, market conditions
4. **Administrative**: Stock offerings, executive changes

## Version

v1.0 - 2026-01-17
- Initial creation based on 900 winners analysis
- 14 industry-specific prompts (includes 1 combined prompt for smaller industries)
