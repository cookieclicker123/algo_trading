# Communication Services Sector Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

**NOTE**: Only Internet Content & Information is covered. Other Communication Services industries (Advertising, Entertainment, Telecom, Gaming, Broadcasting, Publishing) have insufficient data for reliable classification.

```python
INDUSTRY_PROMPT_MAP = {
    "Internet Content & Information": "prompts/communication_services/internet_content.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, user metrics, ad revenue) are classified as SKIP.

## Industry Profile

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Internet Content & Information | 112 | +20.9% | Partnerships (21%), AI launches |

## AI/Technology Note

AI and technology announcements are particularly impactful for Internet Content stocks. AI-powered product launches, generative AI integrations, and platform technology upgrades can drive significant moves.

## Data Sources

- **Winners**: 112 Internet Content headlines with 10%+ daily moves (from Alpaca historical data)

## Common SKIP Patterns

1. **Earnings**: EPS beats/misses, revenue, ad revenue - ALWAYS SKIP
2. **User metrics**: DAU/MAU updates, engagement data
3. **Conferences**: Tech conferences, investor presentations
4. **Minor updates**: Feature updates, content additions

## Version

v1.0 - 2026-01-17
- Initial creation based on 112 winners analysis
- 1 industry-specific prompt (Internet Content & Information only)
