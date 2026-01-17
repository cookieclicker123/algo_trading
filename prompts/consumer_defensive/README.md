# Consumer Defensive Sector Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

**NOTE**: Only Education & Training Services is covered. Other Consumer Defensive industries (Packaged Foods, Farm Products, Household Products, etc.) have insufficient data for reliable classification.

```python
INDUSTRY_PROMPT_MAP = {
    "Education & Training Services": "prompts/consumer_defensive/education_training.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, enrollment numbers) are classified as SKIP.

## Industry Profile

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Education & Training Services | 81 | +25.9% | Partnerships (20%), AI/EdTech launches |

## AI/EdTech Note

AI and EdTech announcements are particularly impactful for Education stocks. AI-powered learning platforms, tutoring technology, and platform integrations can drive significant moves.

## Data Sources

- **Winners**: 81 Education & Training headlines with 10%+ daily moves (from Alpaca historical data)

## Common SKIP Patterns

1. **Earnings**: EPS beats/misses, revenue, enrollment - ALWAYS SKIP
2. **Conferences**: Education conferences, investor presentations
3. **Course announcements**: New courses, curriculum updates
4. **Administrative**: Stock offerings, executive changes

## Version

v1.0 - 2026-01-17
- Initial creation based on 81 winners analysis
- 1 industry-specific prompt (Education & Training Services only)
