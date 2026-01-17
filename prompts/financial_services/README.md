# Financial Services Sector Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

Route headlines to the correct prompt based on cached `industry` field from `permanent_metadata.json`.

```python
INDUSTRY_PROMPT_MAP = {
    "Capital Markets": "prompts/financial_services/capital_markets.txt",
    "Asset Management": "prompts/financial_services/asset_management.txt",
    # Remaining industries use banking_insurance.txt:
    "Insurance Brokers": "prompts/financial_services/banking_insurance.txt",
    "Credit Services": "prompts/financial_services/banking_insurance.txt",
    "Banks - Regional": "prompts/financial_services/banking_insurance.txt",
    "Mortgage Finance": "prompts/financial_services/banking_insurance.txt",
    "Insurance - Property & Casualty": "prompts/financial_services/banking_insurance.txt",
    "Financial Conglomerates": "prompts/financial_services/banking_insurance.txt",
    "Shell Companies": "prompts/financial_services/banking_insurance.txt",
    "Insurance - Reinsurance": "prompts/financial_services/banking_insurance.txt",
    "Insurance - Life": "prompts/financial_services/banking_insurance.txt",
    "Insurance - Diversified": "prompts/financial_services/banking_insurance.txt",
    "Exchange Traded Fund": "prompts/financial_services/banking_insurance.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, NIM, loan growth, premium growth) are classified as SKIP.

## Industry Profiles

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Capital Markets | 121 | +20.6% | Contracts (17%), Crypto news, Partnerships (12%) |
| Asset Management | 68 | +34.4% | Contracts (19%), Partnerships (19%) - HIGHEST AVG! |
| Insurance Brokers | 41 | +22.7% | Contracts (17%), Partnerships (15%) |
| Credit Services | 39 | +17.8% | M&A, Fintech partnerships |
| Banks - Regional | 38 | +13.4% | M&A/mergers, Digital banking |
| Mortgage Finance | 20 | +17.4% | M&A, Servicing contracts |
| Insurance - P&C | 15 | +16.6% | M&A |

## Crypto/Digital Asset Note

Crypto and digital asset news is particularly impactful for Capital Markets stocks. Bitcoin treasury strategies, crypto trading platform launches, and digital asset partnerships can drive significant moves.

## Data Sources

- **Winners**: 366 Financial Services headlines with 10%+ daily moves (from Alpaca historical data)

## Common SKIP Patterns (All Industries)

1. **Earnings**: EPS beats/misses, revenue, NIM, loan growth - ALWAYS SKIP
2. **Conferences**: Industry conferences, investor days
3. **Regulatory**: SEC filings, stress test results
4. **Rate commentary**: Interest rate outlook, yield curve

## Version

v1.0 - 2026-01-17
- Initial creation based on 366 winners analysis
- 3 prompt files (includes 1 combined prompt for banking/insurance)
