# Consumer Cyclical Sector Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

Route headlines to the correct prompt based on cached `industry` field from `permanent_metadata.json`.

```python
INDUSTRY_PROMPT_MAP = {
    "Auto Parts": "prompts/consumer_cyclical/auto_parts.txt",
    "Specialty Retail": "prompts/consumer_cyclical/specialty_retail.txt",
    "Auto Manufacturers": "prompts/consumer_cyclical/auto_manufacturers.txt",
    "Internet Retail": "prompts/consumer_cyclical/internet_retail.txt",
    "Apparel Retail": "prompts/consumer_cyclical/apparel_retail.txt",
    # Remaining industries use consumer_services.txt:
    "Restaurants": "prompts/consumer_cyclical/consumer_services.txt",
    "Leisure": "prompts/consumer_cyclical/consumer_services.txt",
    "Furnishings, Fixtures & Appliances": "prompts/consumer_cyclical/consumer_services.txt",
    "Personal Services": "prompts/consumer_cyclical/consumer_services.txt",
    "Footwear & Accessories": "prompts/consumer_cyclical/consumer_services.txt",
    "Apparel Manufacturing": "prompts/consumer_cyclical/consumer_services.txt",
    "Gambling": "prompts/consumer_cyclical/consumer_services.txt",
    "Auto & Truck Dealerships": "prompts/consumer_cyclical/consumer_services.txt",
    "Recreational Vehicles": "prompts/consumer_cyclical/consumer_services.txt",
    "Travel Services": "prompts/consumer_cyclical/consumer_services.txt",
    "Luxury Goods": "prompts/consumer_cyclical/consumer_services.txt",
    "Resorts & Casinos": "prompts/consumer_cyclical/consumer_services.txt",
    "Residential Construction": "prompts/consumer_cyclical/consumer_services.txt",
    "Packaging & Containers": "prompts/consumer_cyclical/consumer_services.txt",
    "Department Stores": "prompts/consumer_cyclical/consumer_services.txt",
    "Textile Manufacturing": "prompts/consumer_cyclical/consumer_services.txt",
    "Lodging": "prompts/consumer_cyclical/consumer_services.txt",
    "Home Improvement Retail": "prompts/consumer_cyclical/consumer_services.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, guidance, same-store sales) are classified as SKIP.

## Industry Profiles

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Auto Parts | 86 | +17.3% | Partnerships (15%), EV deals, OEM contracts |
| Specialty Retail | 71 | +24.9% | Partnerships (13%), Brand deals |
| Auto Manufacturers | 51 | +18.0% | Contracts (22%), Partnerships (20%), EV deals |
| Internet Retail | 45 | +19.7% | Partnerships (20%), Platform deals |
| Apparel Retail | 44 | +20.8% | M&A (key), Brand collaborations |
| Restaurants | 39 | +17.7% | Franchise deals, M&A |
| Leisure | 39 | +27.6% | M&A, Event contracts |
| Footwear & Accessories | 31 | +37.5% | M&A, Brand partnerships (highest avg move!) |
| Residential Construction | 7 | +39.7% | M&A (highest avg move category) |

## Data Sources

- **Winners**: 623 Consumer Cyclical headlines with 10%+ daily moves (from Alpaca historical data)

## Common SKIP Patterns (All Industries)

1. **Earnings**: EPS beats/misses, revenue, same-store sales - ALWAYS SKIP
2. **Conferences**: Investor days, industry conferences
3. **Product updates**: Menu changes, collection launches
4. **Single location news**: Individual store openings

## Version

v1.0 - 2026-01-17
- Initial creation based on 623 winners analysis
- 6 prompt files (includes 1 combined prompt for smaller industries)
