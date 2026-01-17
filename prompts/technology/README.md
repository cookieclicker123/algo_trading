# Technology Sector Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

Route headlines to the correct prompt based on cached `industry` field from `permanent_metadata.json`.

```python
INDUSTRY_PROMPT_MAP = {
    "Software - Application": "prompts/technology/software_application.txt",
    "Software - Infrastructure": "prompts/technology/software_infrastructure.txt",
    "Semiconductors": "prompts/technology/semiconductors.txt",
    "Communication Equipment": "prompts/technology/communication_equipment.txt",
    "Computer Hardware": "prompts/technology/computer_hardware.txt",
    "Information Technology Services": "prompts/technology/it_services.txt",
    "Electronic Components": "prompts/technology/electronic_components.txt",
    "Solar": "prompts/technology/solar.txt",
    "Consumer Electronics": "prompts/technology/consumer_electronics.txt",
    "Semiconductor Equipment & Materials": "prompts/technology/semiconductor_equipment.txt",
    "Scientific & Technical Instruments": "prompts/technology/scientific_instruments.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, guidance) are classified as SKIP.

## Industry Profiles

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Software - Application | 385 | +22.6% | Partnerships (14%), Contracts (12%), AI launches |
| Software - Infrastructure | 245 | +21.5% | Contracts (16%), Partnerships (15%), Gov deals |
| Semiconductors | 124 | +19.3% | Contracts (17%), Partnerships (11%), Design wins |
| Communication Equipment | 117 | +18.7% | Contracts (34%), Carrier deals |
| Computer Hardware | 89 | +21.7% | Contracts (33%), Government deals |
| IT Services | 83 | +18.8% | Partnerships (23%), Contracts (18%) |
| Electronic Components | 75 | +17.8% | Contracts (23%), OEM supply |
| Solar | 73 | +16.6% | Contracts (23%), PPAs, M&A (10%) |
| Consumer Electronics | 52 | +19.1% | Contracts (23%), Retail partnerships (17%) |
| Semiconductor Equipment | 41 | +16.5% | Contracts (12%), Foundry orders |
| Scientific Instruments | 37 | +18.7% | Contracts (24%), Research partnerships |

## Data Sources

- **Winners**: 1,322 Technology headlines with 10%+ daily moves (from Alpaca historical data)

## Common SKIP Patterns (All Industries)

1. **Earnings**: EPS beats/misses, revenue, guidance - ALWAYS SKIP
2. **Conferences**: "To present at", "To participate"
3. **Lawsuits**: Class action, law firm names
4. **Administrative**: Inducement grants, Nasdaq listing rules
5. **Future announcements**: "To announce", "Will release"

## Version

v1.0 - 2026-01-17
- Initial creation based on 1,322 winners analysis
- 11 industry-specific prompts
