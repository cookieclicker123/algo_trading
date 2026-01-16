# Signal Optimization Plan: AI + Microstructure Confluence

## The JFBR Case Study

**Headline:** "Jeffs' Brands: KeepZone AI Enters into a Distribution Agreement with Advanced Vehicle and Threat Detection Systems Developer"

**Result:** +134% in 7 minutes (entry at $0.66, peak at $1.55)

### Why This Headline Worked

1. **Linguistic Signals:**
   - "Distribution Agreement" - concrete business milestone (not speculative)
   - "AI" keyword - high-momentum sector buzzword
   - "Advanced Vehicle and Threat Detection" - defense/security angle (government spend)
   - Named entity (KeepZone AI) - specific subsidiary, not vague announcement
   - No hedge words ("exploring", "may", "potential")

2. **Company Context:**
   - Micro-cap ($1.7M market cap) - small float = explosive moves
   - Internet Retail sector pivoting to AI/security - narrative shift
   - Float: 547,104 shares - extremely thin
   - Price: $0.66 - penny stock momentum territory

3. **Immediate Microstructure (first 4 seconds):**
   - Volume: 19,673 shares (3.6% of float in 4 seconds!)
   - Surge multiplier: 295,095x (from 0 prior)
   - Buying pressure: 86.6% (18,355 buys vs 1,318 sells)
   - Max excursion: 6.06% already moving
   - Latency to first trade: 0.06s (instant reaction)
   - Trade count: 43 trades (high frequency)

### Why It Was Missed

The classification service's `has_recent_volume()` prefilter ran before Alpaca's trade API had propagated the very recent trades. Despite massive activity, it was marked "no_volume_since_publication".

---

## Current System Flaws

### 1. Redundant Prefilters Block Good Trades
- **Spread filter:** Blocks wide spreads, but wide spreads often compress rapidly on runners
- **Volume prefilter:** API latency causes false negatives on instant movers
- **NEW_ACTIVITY classification:** Stocks with no prior volume skip surge logic entirely

### 2. SURGE Classification Too Restrictive
```python
if prior_avg_vol == 0:
    move_type = "NEW_ACTIVITY"  # Skips all surge checks!
```
This is backwards. A dormant stock suddenly getting massive volume IS a surge.

### 3. No AI Integration Currently
AI classification is disabled. The system relies purely on microstructure, missing headline edge.

---

## Proposed Architecture: AI-First with Microstructure Confluence

### Phase 1: Immediate Signal (0-2 seconds)

**AI Classification (Groq/Claude) - Primary Signal**
```
Article received → AI classifies headline → Returns in <500ms:
- IMMINENT: Trade setup active
- SPECULATIVE: Monitor only
- ROUTINE: Ignore
- IGNORE: Ignore
```

**Confidence Factors for AI:**
- Contract/agreement language (+)
- Named entities (companies, products) (+)
- Dollar amounts mentioned (+)
- "AI", "FDA", "patent" keywords (+)
- Hedge words ("may", "exploring") (-)
- Analyst ratings (-)
- Earnings previews (-)

### Phase 2: Microstructure Confirmation (0-4 seconds)

**Parallel to AI, check tape immediately:**

| Metric | Threshold | JFBR Value |
|--------|-----------|------------|
| Latency to first trade | < 1s | 0.06s ✓ |
| Buy imbalance | > 60% | 86.6% ✓ |
| Trade count (4s) | > 10 | 43 ✓ |
| Volume vs float | > 0.5% | 3.6% ✓ |

**Early Indicators (before price moves):**
- Bid stepping up rapidly (buyers lifting offers)
- Ask thinning (sellers pulling)
- Trade size increasing (institutions entering)
- Sub-second trade clustering (algo accumulation)

### Phase 3: Entry Decision Matrix

| AI Classification | Microstructure | Action |
|-------------------|----------------|--------|
| IMMINENT | All 4 criteria ✓ | FULL SIZE ENTRY |
| IMMINENT | 3 of 4 criteria | HALF SIZE ENTRY |
| IMMINENT | < 3 criteria | MONITOR (wait for confirmation) |
| SPECULATIVE | All 4 criteria ✓ | HALF SIZE ENTRY |
| SPECULATIVE | < 4 criteria | NO TRADE |
| ROUTINE/IGNORE | Any | NO TRADE |

---

## Industry-Specific Patterns

### Biotech (Healthcare Sector)
**High-probability headlines:**
- "FDA approval" / "FDA clearance"
- "Phase 3 results" + positive language
- "Breakthrough therapy designation"
- "Partnership with [Big Pharma]"
- "IND submission accepted"

**Microstructure signature:**
- Volume spike often delayed 5-15 seconds (institutions verify)
- Higher trade counts (retail FOMO)
- Spread compression slower (specialists cautious)

### Consumer Cyclical / Internet Retail
**High-probability headlines:**
- "Distribution agreement"
- "Partnership with [Major Retailer]"
- "AI" / "technology" pivot
- "Contract win" with dollar amounts
- "Acquisition" (acquiring or being acquired)

**Microstructure signature:**
- Instant volume (retail-driven)
- Massive buy imbalance (> 80%)
- Fast spread compression
- JFBR pattern: 0.06s latency, 86.6% buy pressure

### Technology
**High-probability headlines:**
- "Contract with [Government/Enterprise]"
- "Patent granted"
- "Strategic partnership"
- Product launch with customer names

**Microstructure signature:**
- Moderate latency (1-3s)
- Sustained volume (not just spike)
- Institutional block trades

---

## Statistical Framework for Labeling

### Win/Loss Classification
- **Big Winner:** > 20% gain within 10 minutes
- **Winner:** > 5% gain within 10 minutes
- **Loser:** < -5% or no movement
- **Big Loser:** > -10% (stop out)

### Features to Collect Per Trade

**Headline Features:**
- Word count
- Contains dollar amount (T/F)
- Contains named entity (T/F)
- Contains agreement/contract/partnership (T/F)
- Contains FDA/patent/approval (T/F)
- Contains AI/technology (T/F)
- Sentiment score (-1 to +1)
- Sector
- Industry

**Microstructure Features (first 4 seconds):**
- Latency to first trade
- Buy imbalance ratio
- Trade count
- Volume / float ratio
- Spread compression %
- Max excursion %
- Bid step-up count
- Ask thin-out count
- Block trade presence (> 1000 shares)
- Trade velocity (trades per second)

**Stock Features:**
- Market cap
- Float
- Price
- Average daily volume
- Sector/Industry

### Building the Model

1. **Collect labeled data:** 500+ trades with outcome labels
2. **Feature importance:** Which headline + micro features predict big winners?
3. **Industry segmentation:** Separate models per sector
4. **Backtesting:** Simulate on historical data

---

## Implementation Roadmap

### Week 1: Remove Blockers
- [x] Fix event bus queueing (fire-and-forget) ← DONE
- [ ] Remove spread prefilter (trade regardless of spread)
- [ ] Remove `no_volume_since_publication` prefilter
- [ ] Fix SURGE classification to handle `prior_avg_vol == 0`

### Week 2: Re-enable AI
- [ ] Re-enable Groq classification
- [ ] AI classifies immediately on article receipt
- [ ] Trade on IMMINENT + any microstructure confirmation

### Week 3: Data Collection
- [ ] Log all headline features to recall records
- [ ] Log all microstructure features (already doing this)
- [ ] Label outcomes (winner/loser) based on price_check_10min
- [ ] Export to CSV/parquet for analysis

### Week 4: Pattern Mining
- [ ] Identify top 10 headline patterns per industry
- [ ] Identify microstructure signatures per industry
- [ ] Calculate win rates per pattern combination
- [ ] Build confidence scoring model

### Week 5+: Model Refinement
- [ ] Backtest on historical headlines
- [ ] A/B test AI-only vs AI+micro confluence
- [ ] Tune thresholds per industry
- [ ] Deploy optimized signal

---

## Key Insight: The JFBR Pattern

JFBR represents the ideal trade setup:

1. **Headline:** Concrete business milestone + AI buzzword + named entity
2. **Stock:** Micro-cap, tiny float, penny price
3. **Tape:** Instant reaction (0.06s), massive buy imbalance (86.6%), volume explosion

**The pattern to look for:**
```
IF headline contains [agreement|contract|partnership|approval]
AND headline contains [AI|FDA|patent|acquisition]
AND market_cap < $50M
AND float < 5M shares
AND latency_to_first_trade < 0.5s
AND buy_imbalance > 70%
THEN: High probability runner
```

This confluence of linguistic + micro signals is where the edge lives. AI provides the headline filter (avoid noise), microstructure provides the confirmation (the market agrees).

---

## Questions to Answer with Data

1. What is the win rate for IMMINENT headlines with all 4 micro criteria vs without?
2. Which industries have highest win rate for distribution agreement headlines?
3. Does latency_to_first_trade < 0.1s predict bigger moves than 0.1-0.5s?
4. What buy_imbalance threshold maximizes risk-adjusted returns?
5. Do big winners have different trade_count patterns than small winners?
6. Does float size affect optimal position sizing?

---

## Next Steps

1. **Immediate:** Deploy event bus fix, observe tomorrow's latency
2. **Today:** Remove prefilters blocking good trades
3. **This week:** Re-enable AI classification with simpler logic
4. **Ongoing:** Collect labeled data, mine patterns, refine signal
