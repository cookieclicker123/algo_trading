# Three-Stage News Filtration Strategy

## Executive Summary

This document formalizes the three-stage filtering mechanism for the NewsFlash trading system. The goal is to progressively filter news articles through increasingly sophisticated gates, reducing the burden on the AI classifier and ensuring only statistically viable trading opportunities reach the final classification stage.

**Core Philosophy**: Filter noise early with fast quantitative checks, validate market activation with microstructure analysis, then apply AI classification on pre-qualified candidates. This ensures the AI focuses on articles that already have the technical characteristics of viable setups.

---

## System Architecture Overview

### News Flow Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                    News Feed (WebSocket)                     │
│         Benzinga real-time feed (~50ms latency)              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              STAGE 1: Preliminary Filter                     │
│  Purpose: Fast, synchronous checks to remove obvious noise   │
│  Expected Rejection Rate: ~40% of feed                       │
│  Latency: <10ms per article                                  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼ (Pass ~60%)
┌─────────────────────────────────────────────────────────────┐
│          STAGE 2: Microstructure Activation Check            │
│  Purpose: Quantify real-time market response to news         │
│  Expected Rejection Rate: ~35% of remaining                  │
│  Latency: <100ms per article (Redis query + computation)     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼ (Pass ~39% overall)
┌─────────────────────────────────────────────────────────────┐
│            STAGE 3: AI News Classification                   │
│  Purpose: Determine if news is genuine catalyst vs. fluff    │
│  Expected Rejection Rate: ~20% of remaining                  │
│  Latency: ~200-500ms per article (LLM API call)             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼ (Pass ~31% overall)
┌─────────────────────────────────────────────────────────────┐
│                  Auto-Trade Execution                        │
│  Interactive Brokers paper trading portal                    │
│  Entry: Immediate on IMMINENT classification                 │
│  Exit: Naive 5-minute exit (for now, not focus)             │
└─────────────────────────────────────────────────────────────┘
```

### Overall Filtering Efficiency

- **Input**: 100 articles from feed
- **After Stage 1**: 60 articles (~40% rejected)
- **After Stage 2**: 39 articles (~35% of 60 rejected)
- **After Stage 3**: 31 articles (~20% of 39 rejected)
- **Final**: 31 articles proceed to auto-trade

---

## Stage 1: Preliminary Filter

### Purpose

The preliminary filter performs fast, synchronous checks to remove obvious non-tradables and spam before any computational overhead. This stage should reject articles that are clearly not viable, such as penny stocks, non-US exchanges, suspended securities, duplicate releases, and obvious spam.

**Design Principles**:
- **Speed First**: All checks must be synchronous and fast (<10ms total)
- **Conservative Thresholds**: Lean toward being too permissive rather than too restrictive (intuitive guesses, not brute-force proofs)
- **Data-Driven Evolution**: Statistical analysis will refine thresholds over time, but initial values should be reasonable starting points
- **Balance**: Filter out clear noise without losing potential trades

### Filter Criteria

#### 1. Penny Stocks & Micro-Caps

**Rule**: Reject if market cap < $100M OR stock price < $0.50

**Rationale**:
- Stocks below $100M market cap are typically too illiquid for reliable execution
- Stocks below $0.50 are prone to manipulation and wide bid-ask spreads
- Both criteria protect against toxic fills

**Implementation Notes**:
- Fetch market cap and current price from yfinance (cached)
- Fail gracefully: If data unavailable, default to ACCEPT (conservative)
- Log rejections for analysis

**Threshold Rationale**:
- $100M is a reasonable cutoff that excludes most OTC/Pink Sheet junk while allowing legitimate small-caps
- $0.50 price floor is standard SEC threshold for many trading restrictions
- These are intuitive thresholds that can be adjusted based on observed patterns

#### 2. Exchange Filtering

**Rule**: Reject if exchange is NOT in {NYSE, NASDAQ, AMEX}

**Rationale**:
- Only trade US primary-listed stocks for regulatory clarity and liquidity
- OTC, Pink Sheets, and foreign primary listings introduce complexity and illiquidity
- Focus on major US exchanges ensures consistent execution quality

**Allowed Exchanges**:
- NYSE (New York Stock Exchange)
- NASDAQ (all tiers: NASDAQGS, NASDAQGM, NASDAQCM)
- AMEX (NYSE American)

**Rejected Exchanges**:
- OTC (Over-The-Counter)
- Pink Sheets
- Foreign primary listings (TSX, LSE, etc.)

**Implementation Notes**:
- Check `primary_exchange` field from fundamentals
- Handle exchange aliases (NYSE = NYS = NYQ, etc.)
- Log exchange for all articles (accepted and rejected) for pattern analysis

#### 3. Trading Status

**Rule**: Reject if `trading_status != 'ACTIVE'`

**Rationale**:
- Cannot trade suspended, halted, or delisted securities
- Critical safety check to avoid execution failures

**Status Values to Reject**:
- `SUSPENDED`
- `HALTED`
- `DELISTED`
- Any non-ACTIVE status

**Implementation Notes**:
- Query IBKR or yfinance for current trading status
- Fail gracefully: If status unavailable, default to ACCEPT (conservative)
- Log status for analysis

#### 4. Duplicate News Detection

**Rule**: Reject if same headline hash seen within 3 seconds OR same ticker + similar headline (fuzzy match > 90%) within 3 seconds

**Rationale**:
- PRNewswire, BusinessWire, GlobeNewswire often publish identical releases simultaneously
- Duplicate detection prevents multiple trades on the same news event
- 3-second window captures near-simultaneous duplicates

**Implementation**:
- Hash headline text (MD5 or SHA256)
- In-memory cache of recent headline hashes (3-second TTL)
- Fuzzy matching for near-duplicates (Levenshtein distance or similar)
- Clear cache periodically to prevent memory bloat

**Threshold Rationale**:
- 3 seconds is reasonable for capturing duplicates from multiple wire services
- 90% fuzzy match threshold captures minor variations (e.g., "Company A announces" vs "Company A Announces")
- Can be adjusted based on observed duplicate patterns

#### 5. Content Quality

**Rule**: Reject if headline length < 10 characters OR headline is all caps OR headline contains spam indicators ("click here", "free", etc.)

**Rationale**:
- Very short headlines are likely incomplete or corrupted
- All-caps headlines are often spam or low-quality content
- Specific spam indicators filter obvious junk

**Spam Indicators** (non-exhaustive list, can expand):
- "click here"
- "free"
- "!!!" (excessive exclamation marks)
- Headline is all caps (after removing common acronyms like "NYSE", "NASDAQ")

**Implementation Notes**:
- Fast string checks, no external API calls
- Be conservative: Only reject obvious spam, not borderline cases
- Log rejected headlines for pattern analysis

**Threshold Rationale**:
- 10 characters is reasonable minimum for meaningful headlines
- All-caps is a strong spam indicator in financial news
- Spam indicators can be expanded based on observed patterns

#### 6. Ticker Validation

**Rule**: Reject if ticker not found in IBKR OR ticker has no recent trading activity

**Rationale**:
- Ensure symbol is actually tradeable through our broker
- Avoid wasting resources on invalid or inactive symbols

**Implementation Notes**:
- Query IBKR contract details to validate symbol
- Check for recent trading activity (last 30 days)
- Fail gracefully: If validation fails due to network issues, default to ACCEPT (conservative)
- Cache validation results for 1 hour to reduce API calls

#### 7. Market Hours Check (Optional - Phase 2)

**Rule**: Reject if outside trading hours AND no extended hours data available

**Rationale**: 
- Microstructure filter (Stage 2) requires real-time data
- If we can't measure microstructure, skip for now (can enable later)

**Implementation**:
- Check if current time is within trading hours (premarket, regular hours, post-market)
- For now, skip this check (accept all hours)
- Can be enabled later when we have extended hours data subscriptions

**Threshold Rationale**:
- This is a future enhancement, not critical for initial implementation
- Can be enabled once we have extended hours market data coverage

### Stage 1 Expected Performance

**Rejection Rate**: ~40% of feed articles

**Breakdown** (estimated):
- Penny stocks/micro-caps: ~15%
- Exchange filtering: ~10%
- Trading status: ~2%
- Duplicates: ~8%
- Content quality: ~3%
- Ticker validation: ~2%

**Latency**: <10ms per article (all synchronous checks)

**Logging**: All rejections logged with reason for statistical analysis

---

## Stage 2: Microstructure Activation Check

### Purpose

The microstructure filter measures real-time market response to news using second-level price data. If news arrives and the market immediately responds with volume surges, volatility spikes, and spread compression, the trade is likely valid regardless of historical liquidity metrics.

**Core Insight**: News + microstructure surge happening simultaneously is rare unless the news is a genuine catalyst. The odds of a company having news AND surging in microstructure by chance are very low.

**Design Principles**:
- **Time-of-Day Matching**: Compare current metrics to the same minute from the last 20 days (9:30 AM volume vs. 9:30 AM baseline, not 2:00 PM)
- **Exact Window Matching**: Measure the exact time window from publication to reception (could be 10s, 13s, 45s, 58s, etc.)
- **95th Percentile Thresholds**: Use 95th percentile for volume and volatility (balanced signal quality)
- **Mega-Cap Bypass**: Companies with market cap >= $300B skip this stage entirely

### Data Requirements

#### Data Source: Polygon.io (now Massive.com)

**Subscription Requirements**:
- Real-time tick data for all symbols
- Historical tick/second-level data for baseline calculation
- Coverage: Wilshire 5000 (approximately 3,000-5,000 actively traded symbols)
- Cost: ~$99-299/month depending on tier

**Alternative Data Sources**:
- IBKR: Real-time ticks (free with account), but historical data limited
- Alpaca: Free tier available, but may have limitations on historical depth

**Recommended Approach**:
1. **Phase 1**: Use Polygon.io for initial 20-day historical backfill
2. **Phase 2**: Use IBKR for continuous real-time tick collection
3. **Phase 3**: Hybrid: Polygon.io for backfill, IBKR for ongoing collection

#### Data Storage Strategy

**Redis (Hot Storage - Last 20 Trading Days)**:
- Purpose: Fast lookups for baseline calculation and surge detection
- Structure: Sorted sets keyed by symbol and timestamp
- Retention: Last 20 trading days (rolling window)
- Performance: <1ms for time-range queries

**PostgreSQL (Cold Storage - Backup & Archive)**:
- Purpose: Long-term backup, data older than 24 hours
- Structure: TimescaleDB extension for time-series optimization
- Retention: All historical data (indefinite)
- Performance: Used for historical analysis, not real-time queries

**Storage Schema**:
```python
# Per-tick storage in Redis
tick:{symbol}:{iso_timestamp} = {
    'bid': float,
    'ask': float,
    'last': float,
    'volume': int,
    'timestamp': iso_string,
}

# Sorted set for time-range queries
ticks:{symbol} = sorted_set(
    member: tick:{symbol}:{iso_timestamp},
    score: unix_timestamp,
)
```

#### Baseline Calculation

**Key Insight**: Measure surge between publication and reception

- News is **published** at time `T_publish` (e.g., 9:30:00 AM)
- We **receive** it via websocket at time `T_receive` (e.g., 9:30:13 AM, 13 seconds later)
- The websocket delay is actually **helpful** - it gives us time to observe market reaction
- We measure: Did the period from `T_publish` to `T_receive` show a surge?

**Measurement Window**:
- Window duration: `T_receive - T_publish` (variable: 10s, 13s, 45s, 58s, etc.)
- Window: `[T_publish, T_receive]`
- Compare this exact window to the same window from last 20 days

**Example**:
- Published at 9:30:00, received at 9:30:13 (13-second window)
- Query: "9:30:00-9:30:13" from each of last 20 days
- Compute: Volume, volatility, spread for each 13-second window
- Compare: Current window metrics vs. 95th percentile of historical windows

**Why This Works**:
- We have both timestamps (publication_time and reception_time) from the news feed
- The exact time window captures the immediate market reaction to the news
- No need for fixed 60-second windows - we use whatever time elapsed
- Surge detection is precise: we're measuring the exact period that includes the news impact

### Microstructure Metrics

#### 1. Volume Surge

**Metric**: `current_window_volume / 20day_95th_percentile_volume_for_same_window`

**Threshold**: `>= 1.0` (current volume exceeds 95th percentile)

**Calculation**:
- Query Redis for ticks in `[T_publish, T_receive]` (current window)
- Count ticks (or sum volume delta if available)
- Query same window from last 20 days (e.g., "9:30:00-9:30:13" from each of last 20 days)
- Compute 95th percentile of historical window volumes
- Compare: current volume >= 95th percentile

**Time-of-Day Matching**: 
- Compare 9:30:00-9:30:13 volume to 9:30:00-9:30:13 baseline from last 20 days
- NOT comparing to 2:00:00-2:00:13 baseline (different time of day)
- Ensures fair comparison (market opening volume vs. market opening volume)

#### 2. Volatility Spike

**Metric**: `current_window_volatility / 20day_95th_percentile_volatility_for_same_window`

**Threshold**: `>= 1.0` (current volatility exceeds 95th percentile)

**Calculation**:
- Query Redis for ticks in `[T_publish, T_receive]` (current window)
- Extract price series (last trade prices)
- Compute log returns: `log(price_t / price_{t-1})`
- Compute realized volatility: `std(log_returns) * sqrt(252 * 6.5 * 60)` (annualized)
- Query same window from last 20 days
- Compute 95th percentile of historical window volatilities
- Compare: current volatility >= 95th percentile

**Time-of-Day Matching**: Same as volume (compare same minute from last 20 days)

#### 3. Spread Compression

**Metric**: Relative minute-over-minute change in bid-ask spread

**Threshold**: `>= 0.20` (spread tightened by 20% in last minute)

**Calculation**:
- Get spread at start of window: `spread_start = (ask - bid) / mid`
- Get spread at end of window: `spread_end = (ask - bid) / mid`
- Compute compression: `(spread_start - spread_end) / spread_start`
- If compression >= 0.20 (20% tightening), signal detected

**Rationale**: 
- Measures *change* in tightness, which is more reliable than absolute level
- Spread compression indicates liquidity activation (market makers tightening)
- More reliable indicator than absolute spread level

**Alternative Calculation**:
- `current_spread <= 0.8 * previous_minute_spread` (20% compression)

#### 4. Order Book Depth Surge (Optional - Phase 2)

**Metric**: `current_depth / 20day_median_depth`

**Threshold**: `>= 1.5` (current depth is 50% above median)

**Measurement**: Sum of resting liquidity within 2 ticks of mid

**Rationale**:
- Order book depth surge indicates increased liquidity interest
- However, depth can be unreliable (spoofing, vanishing liquidity)
- Weight this signal less heavily than volume/volatility/spread

**Implementation Notes**:
- Requires Level II market data (may not be available for all symbols)
- Can be optional if Level II data is unreliable or unavailable
- Focus on volume + volatility + spread first, add depth later if needed

### Eligibility Logic

```python
IF market_cap >= $300B:
    SKIP microstructure check → Forward to AI
ELIF (volume_surge >= 95th_percentile 
      AND volatility_spike >= 95th_percentile 
      AND spread_compression >= 20%):
    ACCEPT (microstructure activated, likely real catalyst)
ELSE:
    REJECT (insufficient market response, risk of bad fills)
```

**Bypass Rule**: Companies with `market_cap >= $300B` skip microstructure check entirely
- Rationale: Mega-caps are always liquid enough; microstructure check is unnecessary
- Flow: Pre-Filter → AI Classification → Trade Execution

**Combined Scoring** (Future Enhancement):
- Each signal contributes 0.25 to activation score (0.0 to 1.0)
- Minimum 3 of 4 signals required for small/mid-cap acceptance
- Score used for position sizing: `size = base_size * ai_confidence * microstructure_score`

### Stage 2 Expected Performance

**Rejection Rate**: ~35% of articles that passed Stage 1

**Breakdown** (estimated):
- Insufficient volume surge: ~15%
- Insufficient volatility spike: ~12%
- Insufficient spread compression: ~8%

**Latency**: <100ms per article
- Redis query: ~10-50ms
- Baseline computation: ~10-30ms
- Metric calculation: ~10-20ms
- Total: <100ms (acceptable for real-time trading)

**Logging**: All rejections logged with metrics for statistical analysis

---

## Stage 3: AI News Classification

### Purpose

The AI classifier determines if news is a genuine catalyst (IMMINENT) or fluff (IGNORE). This stage only receives articles that have already passed preliminary and microstructure checks, meaning they have the technical characteristics of viable setups.

**Core Insight**: With Stages 1 and 2 pre-filtering, the AI can be more lenient and focus on content quality rather than technical viability. The prompt can be simpler since we're already filtering for tradeable characteristics.

### Current Implementation

**System**: Groq's Llama 3.3 70B (via API)

**Classification Categories**:
- `IMMINENT`: Immediate trading opportunity (10%+ intraday moves expected)
- `IGNORE`: Filter out - no trading signal

**Confidence Levels**:
- `HIGH`: Very confident in classification
- `MEDIUM`: Moderately confident
- `LOW`: Low confidence (treated as IGNORE)

**Current Prompt**: See `prompts/classification_prompt.txt`

**Filtering Logic**:
- Only `IMMINENT` classifications proceed to auto-trade
- `HIGH` or `MEDIUM` confidence required for notification
- `LOW` confidence treated as `IGNORE` (even if IMMINENT)

### Future Enhancements

**Phase 2**: Simplified Prompt
- Remove technical criteria (exchange, market cap, etc.) since Stages 1-2 handle that
- Focus purely on content quality: Is this a genuine catalyst?
- Can be more lenient since technical viability is already confirmed

**Phase 3**: Custom Model Training
- After months of data collection, train custom model on historical news that performed well
- Use classification audit trail to identify patterns
- Fine-tune LLM or train from scratch on proprietary dataset

### Stage 3 Expected Performance

**Rejection Rate**: ~20% of articles that passed Stages 1-2

**Breakdown** (estimated):
- Classified as IGNORE: ~15%
- LOW confidence (even if IMMINENT): ~5%

**Latency**: ~200-500ms per article (LLM API call)

**Logging**: All classifications logged to audit trail for analysis

---

## Auto-Trade Execution

### Entry Strategy

**Trigger**: IMMINENT classification from Stage 3

**Execution**:
- Immediate auto-trade through Interactive Brokers paper trading portal
- All key info logged: trade entry, news article, classification details
- Entry order placed immediately upon IMMINENT classification

**Position Sizing**: 
- Base size: $1,000 notional (or configurable)
- Can be enhanced later with microstructure score weighting

### Exit Strategy

**Current Approach**: Naive 5-minute exit
- Exit all positions after 5 minutes (regardless of PnL)
- This is not the focus - exit optimization comes later

**Future Enhancement**: Multi-tier exit system
- Tier 1: Immediate profit-taking (30-90 seconds)
- Tier 2: Trailing stop (90 seconds - 5 minutes)
- Tier 3: Hard exit (force close after 5-10 minutes)

**Focus Order**:
1. ✅ Ensure we can trade the appropriate news (Stages 1-3)
2. ⏭️ Then focus on exit criteria (later)

---

## Implementation Phases

### Phase 1: Codebase Pruning (Current)

**Goal**: Remove all existing filters except AI classification

**Actions**:
- Remove market cap thresholds
- Remove average volume thresholds
- Remove sector bans
- Remove holding company detection
- Remove exchange filtering (except basic NYSE/NASDAQ check)
- Keep: AI classification only

**Result**: More news gets through to Telegram, more trades attempted
- This is intentional - we'll rebuild filtering from scratch with Stages 1-2

**Timeline**: Immediate

### Phase 2: Stage 1 Implementation (After Pruning)

**Goal**: Implement preliminary filter with conservative thresholds

**Tasks**:
1. Implement penny stock filter (market cap < $100M, price < $0.50)
2. Implement exchange filter (NYSE, NASDAQ, AMEX only)
3. Implement trading status check
4. Implement duplicate detection (headline hash, 3-second window)
5. Implement content quality checks (spam indicators)
6. Implement ticker validation (IBKR check)

**Timeline**: 1-2 weeks

### Phase 3: Data Infrastructure (Parallel to Phase 2)

**Goal**: Set up data collection infrastructure for Stage 2

**Tasks**:
1. Set up PostgreSQL + TimescaleDB for tick data storage
2. Set up Redis for hot storage (last 20 trading days)
3. Subscribe to Polygon.io/Massive.com API
4. Implement real-time tick data collection from IBKR
5. Backfill 20 days of historical data from Polygon.io
6. Implement rolling window management (Redis → PostgreSQL)

**Timeline**: 2-3 weeks (can run parallel to Phase 2)

### Phase 4: Stage 2 Implementation (After Phase 2-3)

**Goal**: Implement microstructure activation check

**Tasks**:
1. Implement baseline calculation (20-day percentiles, time-of-day matching)
2. Implement volume surge detection
3. Implement volatility spike detection
4. Implement spread compression detection
5. Implement eligibility logic (3 of 4 signals, mega-cap bypass)
6. Integrate into article processing pipeline

**Timeline**: 2-3 weeks

### Phase 5: Integration & Testing

**Goal**: Integrate all three stages and test end-to-end

**Tasks**:
1. Integrate Stage 1 into article processor
2. Integrate Stage 2 into article processor
3. Test end-to-end flow with real news feed
4. Monitor rejection rates at each stage
5. Calibrate thresholds based on observed patterns

**Timeline**: 1-2 weeks

### Phase 6: Production Deployment & Monitoring

**Goal**: Deploy to production and monitor performance

**Tasks**:
1. Deploy all three stages to production
2. Monitor rejection rates and latency at each stage
3. Collect statistics for threshold calibration
4. Log all rejections with reasons for analysis
5. Monthly review: Adjust thresholds based on data, not assumptions

**Timeline**: Ongoing

---

## Threshold Calibration Strategy

### Initial Thresholds (Intuitive Guesses)

**Stage 1**:
- Market cap: $100M (reasonable cutoff, excludes OTC junk)
- Price: $0.50 (SEC standard threshold)
- Exchange: NYSE, NASDAQ, AMEX only (clear list)
- Duplicate window: 3 seconds (reasonable for wire services)
- Headline length: 10 characters (reasonable minimum)

**Stage 2**:
- Volume threshold: 95th percentile (balanced signal quality)
- Volatility threshold: 95th percentile (balanced signal quality)
- Spread compression: 20% (reasonable liquidity activation indicator)
- Mega-cap bypass: $300B (clearly liquid enough)

### Evolution Strategy

**Data-Driven Refinement**:
- Log all rejections with reasons and metrics
- Track false positives (rejected but price moved significantly)
- Track false negatives (accepted but no price movement)
- Monthly analysis: Review patterns and adjust thresholds

**Balance Philosophy**:
- Start conservative (lean toward accepting more)
- Refine based on observed patterns
- Don't over-optimize on small samples
- Mix of intuitive guesses and statistical validation

**Break-Fix Cycle**:
- Speed up iteration with intuitive initial values
- Not everything needs brute-force statistical proofs
- Statistical validation for major threshold adjustments
- Intuitive adjustments for fine-tuning

---

## Success Metrics

### Filter Efficiency

**Target Rejection Rates**:
- Stage 1: ~40% of feed (removes obvious noise)
- Stage 2: ~35% of remaining (removes non-activated news)
- Stage 3: ~20% of remaining (removes non-catalyst content)
- Overall: ~69% rejection rate (31% proceed to auto-trade)

**Signal Quality**:
- False Positive Rate: < 20% (vs. current ~60-70% without filters)
- False Negative Rate: < 5% (missed genuine catalysts)
- Capture Rate: 2-3 additional small/mid-cap trades per week

### System Performance

**Latency Targets**:
- Stage 1: <10ms per article
- Stage 2: <100ms per article
- Stage 3: <500ms per article
- Total: <600ms per article end-to-end

**Data Storage**:
- Redis: Last 20 trading days (~12 GB for 3,000 symbols)
- PostgreSQL: All historical data (~109 GB/year for 3,000 symbols)

### Trading Performance

**Execution Quality**:
- Entry Slippage: < 0.2% average
- Time-to-Fill: < 2 seconds average
- Fill Rate: > 90%

**Profitability** (Future Metrics):
- Win Rate: > 50%
- Average Win: > $50 per trade
- Average Loss: < $20 per trade
- Sharpe Ratio: > 1.5

---

## Risk Assessment

### High Risk

**Data Availability**:
- Polygon.io subscription required (~$99-299/month)
- Need 20 days of historical data before Stage 2 works
- Mitigation: Start with IBKR real-time collection, build baseline organically over 20 days

**Baseline Calculation Complexity**:
- Time-of-day matching is complex (many time buckets)
- Exact window matching requires flexible query system
- Mitigation: Start simple, add sophistication over time

**False Positives in Stage 2**:
- Some microstructure activation may be fake (pump-and-dump)
- Need to validate signals over time
- Mitigation: Combine with Stage 3 AI filter, log everything for analysis

### Medium Risk

**Stage 1 Over-Filtering**:
- Conservative thresholds might reject valid trades
- Need to balance noise reduction vs. opportunity loss
- Mitigation: Start permissive, tighten based on data

**Stage 2 Latency**:
- Redis queries + computation might exceed 100ms target
- Could delay trade execution
- Mitigation: Optimize queries, use caching, parallel processing

**Data Quality**:
- Missing ticks, gaps in data, unreliable sources
- Could affect baseline accuracy
- Mitigation: Validate data quality, handle gaps gracefully

### Low Risk

**AI Classification**:
- Already working well, no changes needed initially
- Can simplify prompt later since Stages 1-2 pre-filter

**Auto-Trade Execution**:
- IBKR integration already exists
- Just need to ensure it works with new filter pipeline

---

## Conclusion

This three-stage filtering strategy represents a systematic approach to reducing noise and improving signal quality. By progressively filtering articles through quantitative checks, microstructure analysis, and AI classification, we ensure that only statistically viable trading opportunities reach execution.

**Key Insights**:

1. **Filter Early**: Fast preliminary checks remove 40% of noise before any computation
2. **Measure Market Response**: Microstructure activation is a strong signal - news + surge rarely happens by chance
3. **Let AI Focus**: With technical viability pre-confirmed, AI can focus on content quality
4. **Data-Driven Evolution**: Start with intuitive thresholds, refine based on observed patterns
5. **Balance Speed and Quality**: Mix of fast quantitative checks and slower but sophisticated analysis

**Next Steps**:

1. ✅ Prune codebase (remove all filters except AI)
2. ⏭️ Implement Stage 1 (Preliminary Filter)
3. ⏭️ Set up data infrastructure (PostgreSQL, Redis, Polygon.io)
4. ⏭️ Implement Stage 2 (Microstructure Activation Check)
5. ⏭️ Integrate all three stages
6. ⏭️ Monitor, calibrate, and iterate

---

**Document Version**: 1.0  
**Last Updated**: 2025-11-29  
**Status**: Draft - Ready for Implementation

