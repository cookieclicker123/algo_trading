# Perplexity Batch Query Template for Historical Press Release Attribution

## Overview

Use this template to find original wire press releases for historical stock moves using Perplexity Pro.

## Input Format

You'll have a list of stock moves from Alpaca historical data like:

| Ticker | Date | Time (ET) | Move % | Sector | Industry |
|--------|------|-----------|--------|--------|----------|
| JFBR | 2026-01-15 | 08:45 | +106% | Consumer Cyclical | Internet Retail |
| CJMB | 2026-01-14 | 09:32 | +122% | Industrials | Freight & Logistics |
| ... | ... | ... | ... | ... | ... |

## Batch Query Template (20 tickers at a time)

Copy this into Perplexity:

---

```
I need to find the ORIGINAL press releases that caused these stock moves. For each, provide:

1. The EXACT headline from the wire service (not rewritten by news sites)
2. Wire source: PR Newswire, Business Wire, Globe Newswire, Accesswire, or Newsfile
3. Timestamp of the press release (as precise as possible)
4. URL to the original release if available

IMPORTANT: I need the SOURCE press release from the wire services listed above, NOT news articles reporting on the move. The headline format should match wire service style (e.g., "Company Name Announces..." or "Company: Action Description").

Stock moves to research:

1. [TICKER1] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
2. [TICKER2] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
3. [TICKER3] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
4. [TICKER4] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
5. [TICKER5] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
6. [TICKER6] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
7. [TICKER7] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
8. [TICKER8] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
9. [TICKER9] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
10. [TICKER10] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
11. [TICKER11] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
12. [TICKER12] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
13. [TICKER13] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
14. [TICKER14] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
15. [TICKER15] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
16. [TICKER16] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
17. [TICKER17] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
18. [TICKER18] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
19. [TICKER19] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]
20. [TICKER20] moved +[X]% on [DATE] around [TIME] ET - [INDUSTRY]

For each, respond in this exact format:

[TICKER]:
- Headline: "[exact headline]"
- Wire: [source]
- Time: [timestamp]
- URL: [url or "not found"]
- Confidence: [HIGH/MEDIUM/LOW]

If you cannot find a wire press release for a ticker, mark it as:
[TICKER]: NO WIRE RELEASE FOUND - [reason: e.g., "move appears to be Reddit/social driven", "SEC filing only", etc.]
```

---

## Example Filled Template

```
I need to find the ORIGINAL press releases that caused these stock moves...

Stock moves to research:

1. JFBR moved +106% on 2026-01-15 around 08:45 ET - Internet Retail
2. CJMB moved +122% on 2026-01-14 around 09:32 ET - Integrated Freight & Logistics
3. SPHL moved +43% on 2026-01-13 around 07:15 ET - Residential Construction
4. GP moved +31% on 2026-01-10 around 10:05 ET - Farm & Heavy Construction Machinery
5. ASNS moved +19% on 2026-01-09 around 08:30 ET - Communication Equipment
...
```

## Expected Response Format

Perplexity should return something like:

```
JFBR:
- Headline: "Jeffs' Brands: KeepZone AI Enters into a Distribution Agreement with Advanced Vehicle and Threat Detection Systems Developer"
- Wire: Globe Newswire
- Time: 2026-01-15 08:44:00 ET
- URL: https://www.globenewswire.com/news-release/2026/01/15/...
- Confidence: HIGH

CJMB:
- Headline: "Callan JMB Signs Manufacturing Oversight, Federal Deployment Agreement"
- Wire: PR Newswire
- Time: 2026-01-14 09:30:00 ET
- URL: https://www.prnewswire.com/news-releases/...
- Confidence: HIGH
```

## Quality Control

### Verification Checklist
For each headline returned, verify:
- [ ] Headline matches wire service format (not rewritten)
- [ ] Wire source is one we receive via Benzinga (PR Newswire, Business Wire, Globe Newswire, Accesswire, Newsfile)
- [ ] Timestamp is within 20 minutes BEFORE the move start time
- [ ] URL works and shows the original release

### Red Flags (Reject These)
- Headlines that are clearly rewritten summaries ("Stock surges on deal news")
- Sources like Yahoo Finance, MarketWatch, Seeking Alpha (aggregators, not wires)
- Timestamps AFTER the move (effect, not cause)
- No URL or broken URL

### Confidence Levels
- **HIGH**: Exact wire headline found with URL, timestamp matches
- **MEDIUM**: Wire headline found but URL missing or timestamp approximate
- **LOW**: Headline found but source uncertain, may need manual verification

## Recording Results

Create a CSV with columns:

```csv
ticker,date,time_et,move_pct,sector,industry,headline,wire_source,headline_time,url,confidence,verified
JFBR,2026-01-15,08:45,106,Consumer Cyclical,Internet Retail,"Jeffs' Brands: KeepZone AI Enters...",Globe Newswire,08:44,https://...,HIGH,TRUE
```

## Workflow

1. **Prepare batch**: Get 20 moves from your Alpaca historical data
2. **Query Perplexity**: Copy filled template into Perplexity Pro
3. **Record results**: Add to CSV
4. **Verify**: Spot-check 10% of results by clicking URLs
5. **Repeat**: Process next batch

## Estimated Effort

- 1000 movers / 20 per batch = 50 batches
- ~5 minutes per batch (query + record)
- ~4-5 hours total
- Expected yield: ~70% attribution rate (700 usable headlines)

## Combining with Your Control Group

Your final dataset:
- **Positive examples**: ~700 headlines from Perplexity attribution
- **Control group**: 6,311 headlines from your recall data (already have exact Benzinga format)

This gives you:
- Wire headlines that caused big moves (positive class)
- Wire headlines that didn't cause moves (negative class)
- Same format, same sources, directly comparable
