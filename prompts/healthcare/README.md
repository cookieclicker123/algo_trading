# Healthcare Industry Classifier Prompts

Industry-specific system prompts for Groq LLM headline classification.

## Usage

Route headlines to the correct prompt based on cached `industry` field from `permanent_metadata.json`.

```python
INDUSTRY_PROMPT_MAP = {
    "Biotechnology": "prompts/healthcare/biotechnology.txt",
    "Medical Devices": "prompts/healthcare/medical_devices.txt",
    "Drug Manufacturers - Specialty & Generic": "prompts/healthcare/drug_manufacturers.txt",
    "Diagnostics & Research": "prompts/healthcare/diagnostics_research.txt",
    "Health Information Services": "prompts/healthcare/health_information_services.txt",
    "Medical Instruments & Supplies": "prompts/healthcare/medical_instruments_supplies.txt",
    "Medical Care Facilities": "prompts/healthcare/medical_care_facilities.txt",
}
```

## IMPORTANT: No Earnings Trading

**We do NOT trade earnings announcements under any circumstances.** All earnings-related headlines (EPS beats, revenue beats, guidance) are classified as SKIP.

## Industry Profiles

| Industry | Winners | Avg Move | Key Signals |
|----------|---------|----------|-------------|
| Biotechnology | 1,887 | +21.1% | FDA (24%), trial results, big pharma deals |
| Medical Devices | 376 | +20.9% | 510(k) clearance, CE Mark, M&A |
| Drug Mfrs | 182 | +21.5% | DEA reclassification, FDA inspection |
| Diagnostics | 99 | +17.6% | Patent grants, offerings |
| Health Info | 96 | +23.5% | Gov contracts (HHS, NIH), strategic review |
| Med Instruments | 78 | +21.0% | Retail partnerships, WHO approval |
| Med Care Facilities | 71 | +21.5% | M&A, avoid lawsuits |

## Data Sources

- **Winners**: 2,789 Healthcare headlines with 10%+ daily moves (from Alpaca historical + recall data)
- **Losers**: 774 Healthcare headlines that did NOT achieve 10%+ (from January 2026 recall data)

## Common SKIP Patterns (All Industries)

1. **Earnings**: EPS beats/misses, revenue, guidance - ALWAYS SKIP
2. **Conferences**: "To present at", "J.P. Morgan Healthcare Conference"
3. **Lawsuits**: "Class action", law firm names
4. **Administrative**: "Inducement grants", "Nasdaq listing rule"
5. **Future announcements**: "To announce", "Will release"

## Expected Performance

- **Precision**: >85% (of TRADE signals are actual winners)
- **Recall**: 60-70% (catching majority of winners)
- **False Positive Rate**: <10% (critical for avoiding losses)

## Version

v1.0 - 2026-01-17
- Initial creation based on 2,789 winners + 774 losers analysis
- 7 industry-specific prompts
