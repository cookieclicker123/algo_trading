# Consumer Defensive Sector Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

```python
INDUSTRY_PROMPT_MAP = {
    "Education & Training Services": "prompts/consumer_defensive/education_training.txt",
    "Food Distribution": "prompts/consumer_defensive/food_distribution.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, enrollment numbers) are classified as SKIP.

## Industry Profiles

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Education & Training Services | 81 | +25.9% | Partnerships (20%), AI/EdTech launches |
| Food Distribution | 9 | +35.4% | Acquisitions/M&A, Partnerships/MOUs |

## Food Distribution Notes

Food Distribution companies (food wholesalers, distributors, grocery suppliers) move aggressively on:
- **Acquisitions** - Material Definitive Agreements, business acquisitions (+44% example)
- **Partnerships/MOUs** - Legally-binding MOUs, business injection agreements (+22% example)

Key companies: TWG (Top Wealth Group), HFFG (HF Foods), UNFI (United Natural Foods), WILC (Willi-Food)

## AI/EdTech Note

AI and EdTech announcements are particularly impactful for Education stocks. AI-powered learning platforms, tutoring technology, and platform integrations can drive significant moves.

## Data Sources

- **Education Winners**: 81 headlines with 10%+ daily moves
- **Food Distribution Winners**: 9 headlines with 10%+ daily moves (primarily TWG)

## Common SKIP Patterns

1. **Earnings**: EPS beats/misses, revenue, enrollment, guidance - ALWAYS SKIP
2. **Compliance**: Nasdaq compliance extensions, regulatory notices - SKIP
3. **Offerings**: IPO pricing, secondary offerings - SKIP
4. **Conferences**: Investor conferences, earnings calls

## Version

v1.1 - 2026-01-20
- Added Food Distribution industry (9 winners analysis)
- 2 industry-specific prompts now available

v1.0 - 2026-01-17
- Initial creation based on 81 Education winners analysis
- 1 industry-specific prompt (Education & Training Services only)
