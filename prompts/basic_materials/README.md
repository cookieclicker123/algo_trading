# Basic Materials Sector Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

**NOTE**: Only Specialty Chemicals is covered. Other Basic Materials industries (Industrial Metals, Gold, Steel, etc.) have insufficient data for reliable classification.

```python
INDUSTRY_PROMPT_MAP = {
    "Specialty Chemicals": "prompts/basic_materials/specialty_chemicals.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, production volumes) are classified as SKIP.

## Industry Profile

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Specialty Chemicals | 49 | +17.2% | Contracts (27%), Partnerships (27%) |

## Contract & Partnership Note

Contracts and partnerships are equally dominant catalysts for Specialty Chemicals (27% each). Manufacturing contracts, supply agreements, and R&D partnerships are the key tradeable signals.

## Data Sources

- **Winners**: 49 Specialty Chemicals headlines with 10%+ daily moves (from Alpaca historical data)

## Common SKIP Patterns

1. **Earnings**: EPS beats/misses, revenue, production - ALWAYS SKIP
2. **Conferences**: Industry conferences, investor presentations
3. **Commodity pricing**: Chemical price commentary
4. **Administrative**: Stock offerings, executive changes

## Version

v1.0 - 2026-01-17
- Initial creation based on 49 winners analysis
- 1 industry-specific prompt (Specialty Chemicals only)
