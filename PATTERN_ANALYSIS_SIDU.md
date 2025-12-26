# Pattern Analysis: SIDU Big Mover (14.37% gain) vs Other Articles

## Key Findings from December 22, 2025 Premarket Data

### SIDU (14.37% gain) - The Big Mover

**Headline**: "Sidus Space Awarded Contract Under Missile Defense Agency's SHIELD IDIQ Program"

**Critical Observations:**

1. **Volume Surge Pattern**:
   - 3min before: 22,691 volume
   - 2min before: 185,332 volume (8.2x increase!) ⚠️ **ALREADY SURGING BEFORE PUBLICATION**
   - 1min before: 107,832 volume
   - at_event: 232,122 volume (entire minute bar, not 13-second window)
   - **Issue**: Volume was already surging 2 minutes BEFORE news was published
   - **Implication**: Either news leakage, pre-news activity, or the volume analyzer is using minute bars instead of precise window

2. **Publication → Reception Window**:
   - Published: 13:32:00Z
   - Received: 13:32:13.322182Z
   - **13 seconds elapsed** - very fast
   - **CRITICAL**: We need to measure volume in THIS 13-second window, not the entire minute
   - Current system shows 232,122 volume (entire minute), but we need the actual 13-second volume
   - If 232k is for 13 seconds, normalized to 60s = **1,070,769 volume/minute** (vs prior avg of 105k)
   - That's a **10x surge** in the publication→reception window!

3. **Microstructure**:
   - Initial spread: 0.01 (0.649%) - **TIGHT**
   - Initial bid_size: 4,300
   - Initial ask_size: 14,900
   - After 5min: spread still 0.01, bid_size: 6,100, ask_size: 8,700
   - **Liquidity was good** - tight spread, decent sizes

4. **Headline Analysis**:
   - Keywords: "awarded", "contract", "defense", "agency"
   - Sentiment: **POSITIVE** (contract award)
   - Type: Government/Defense contract (high-value, reliable)

5. **Missing Data**:
   - `ticker_metadata`: {} (empty - Finnhub API issue at time)
   - `filter_reason`: null (should show why it wasn't traded)
   - **This is critical** - without metadata, we can't analyze industry/sector patterns

### Other 1%+ Movers (for comparison)

1. **BWAY** (1.22% gain):
   - Volume: NO_DATA (illiquid)
   - Spread: 0.18 (1.054%) - wider than SIDU
   - Headline: "Parsons Awarded $30M Contract..." (also contract-related!)

2. **ELAB** (2.09% gain):
   - Volume: NO_DATA
   - Spread: 0.29 (13.81%) - **VERY WIDE**
   - Headline: "AGA Precision Systems... AS9100 Certification..." (certification news)

3. **SOPA** (3.81% gain):
   - Volume: Limited data
   - Headline: "Distribution Solutions Group Amends Credit Facility..."

4. **ADEA** (4.36% gain):
   - Volume: NO_DATA
   - Spread: 1.73 (13.84%) - **VERY WIDE**
   - Headline: "Adelaide Energy Announces..." (energy sector)

### Patterns Identified

**What Makes SIDU Different:**

1. **Volume Surge BEFORE Publication**:
   - 2min before: 185k volume (massive surge)
   - This suggests either:
     a) News leakage (someone got news early)
     b) Pre-news activity (rumors, anticipation)
     c) The volume analyzer is using minute bars (not precise window)

2. **Tight Spread + Good Liquidity**:
   - Spread: 0.01 (0.649%) - very tight
   - Bid/ask sizes: 4,300 / 14,900 - decent liquidity
   - Compare to ADEA: spread 1.73 (13.84%) - much wider, less liquid

3. **Headline Type**:
   - Government/Defense contract award
   - High-value, reliable news
   - Positive sentiment keywords: "awarded", "contract"

4. **Market Cap** (unknown - metadata missing):
   - Need to check if SIDU is small-cap (often more volatile on news)

### Critical Issues to Fix

1. **Publication → Reception Window Not Captured**:
   - Current: `stats_at_event` shows entire minute bar (232k)
   - Needed: Actual volume in 13-second window (published_at → received_at)
   - **Fix**: Ensure `_fetch_trades_in_window` is used with precise window
   - **Store**: `pub_to_recv_volume`, `pub_to_recv_normalized_minute_volume`, `pub_to_recv_seconds`

2. **Metadata Not Populating**:
   - SIDU has empty `ticker_metadata: {}`
   - Need: industry, sector, market_cap_millions
   - **Fix**: Ensure FinnhubCoordinator is working (we fixed API method name)

3. **Filter Reason Not Populating**:
   - SIDU has `filter_reason: null`
   - Need: Why wasn't it traded? (ai_classified_ignore, prefilter_low_market_cap, etc.)
   - **Fix**: Ensure filter_reason is set immediately when events fire

4. **Volume Data Quality**:
   - Many articles show `NO_DATA` for volume
   - Need to distinguish: illiquid ticker vs API limitation
   - **Current**: `last_reportable_volume` helps, but need more context

### Metrics to Collect for Pattern Recognition

**Already Collecting:**
- ✅ Volume at intervals (3min, 2min, 1min, 30sec, at_event)
- ✅ Spread, bid, ask, mid prices
- ✅ Headline keywords and sentiment (just added)
- ✅ Session context (premarket, market, postmarket)

**Need to Add:**
1. **Publication → Reception Window** (CRITICAL):
   - `pub_to_recv_seconds`: Time between publication and reception
   - `pub_to_recv_volume`: Actual volume in that window
   - `pub_to_recv_normalized_minute_volume`: Normalized to 60s for comparison
   - `pub_to_recv_vol_per_second`: Volume per second
   - **This is the key metric** - if algorithms are swarming, volume will surge in this window

2. **Microstructure Changes**:
   - `spread_tightening_pct`: % change in spread from 3min before to at_event
   - `bid_size_change_pct`: % change in bid_size
   - `ask_size_change_pct`: % change in ask_size
   - `liquidity_ratio`: (bid_size + ask_size) / spread

3. **Volatility**:
   - Price volatility in 3min before vs at_event
   - VWAP changes

4. **Trade Characteristics**:
   - Trade count in publication→reception window
   - Average trade size
   - Trade frequency (trades per second)

5. **Headline Analysis** (just added):
   - Keywords extracted
   - Sentiment classification
   - News type (contract, earnings, merger, etc.)

### Statistical Analysis Plan

**After 30 days of data collection, analyze:**

1. **Big Movers (>5% gain) vs Small Movers (<1% gain)**:
   - Headline keywords frequency
   - Volume surge patterns (pub→recv window)
   - Spread characteristics
   - Market cap distribution
   - Industry/sector patterns
   - Time of day patterns

2. **Volume Surge Detection**:
   - If `pub_to_recv_normalized_minute_volume` > 3x `prior_avg_volume` → strong signal
   - If volume surging BEFORE publication → possible leakage/pre-news activity

3. **Microstructure Patterns**:
   - Spread tightening > 20% → liquidity improving (good sign)
   - Bid/ask sizes increasing → more liquidity (good sign)
   - Liquidity ratio > 1000 → very liquid (good for execution)

4. **Headline Patterns**:
   - "awarded" + "contract" + government/defense → high probability of move
   - "earnings" + "beats" → positive move likely
   - "merger" + "acquisition" → often big moves

### Recommendations

1. **Fix Publication → Reception Window Capture** (URGENT):
   - Ensure `_fetch_trades_in_window` is actually being used
   - Verify `window_end=received_at` is passed correctly
   - Store normalized metrics for comparison

2. **Fix Metadata Population** (URGENT):
   - Ensure FinnhubCoordinator is working (API method fixed)
   - Ensure metadata is populated immediately (not queued)

3. **Fix Filter Reason Population** (URGENT):
   - Ensure filter_reason is set immediately when events fire
   - Track all filter reasons (prefilter + AI classification)

4. **Add More Microstructure Metrics**:
   - Bid/ask size changes
   - Spread tightening
   - Liquidity ratios

5. **Headline Analysis** (DONE):
   - Keywords extraction
   - Sentiment classification

### Next Steps

1. **Immediate**: Fix publication→reception window capture
2. **Immediate**: Fix metadata and filter_reason population
3. **Short-term**: Collect 30 days of data
4. **Medium-term**: Build statistical analysis script to identify patterns
5. **Long-term**: Use patterns to build dynamic filtering system
