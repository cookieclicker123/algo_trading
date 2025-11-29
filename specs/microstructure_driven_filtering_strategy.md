# Dynamic Microstructure-Driven Filtering Strategy

## Executive Summary

This document outlines the replacement of static pre-AI filters (market cap, sector bans, ADV thresholds) with a **dynamic, real-time microstructure-based filtering system**. The goal is to capture high-quality small/mid-cap catalyst trades while maintaining protection against illiquid fills and false signals.

**Core Philosophy**: Measure market structure *changes* in real-time rather than relying on static historical metrics. If news arrives and the market immediately responds with volume surges, volatility spikes, and spread compression, the trade is likely valid regardless of market cap.

### Key Design Decisions

1. **Universal Adaptive Routing**: All trades (big-cap and small-cap) use Adaptive "URGENT" routing. No VWAP for big-caps—news moves are time-sensitive regardless of market cap.

2. **Mega-Cap Bypass**: Companies with `market_cap >= $300B` skip microstructure check entirely. They only need to pass pre-filter + AI classification.

3. **95th Percentile Thresholds**: Use 95th percentile for volume and volatility (not 99th). More balanced signal quality.

4. **Relative Spread Compression**: Measure minute-over-minute relative change (20% compression), not absolute spread level. More reliable indicator of liquidity activation.

5. **Observe, Don't Preempt**: No pre-filtering of edge cases (biotech, SPACs, etc.). Let microstructure filter do its job, log everything, analyze patterns after 1 month.

6. **Granular Data Collection**: Collect tick-by-tick data, store in Redis (last 20 trading days) and PostgreSQL (backup). Use Polygon.io/Alpaca for initial backfill, IBKR for ongoing real-time collection. Measure exact time window from publication to reception.

7. **Simplified Exits**: Maintain existing 5-minute exit strategy for now. Multi-tier exit system (immediate profit-taking, trailing stops) to be implemented later.

---

## Strategy Overview

### Current State (Static Filters)
- ❌ Market cap thresholds (e.g., >$150B for options)
- ❌ Sector bans (Financial Services, Industrials, etc.)
- ❌ Average Daily Volume (ADV) minimums
- ❌ Holding company detection (whitelist-based)

**Problems**:
- Misses sudden liquidity events in smaller names
- Creates whack-a-mole problem (new edge cases constantly appear)
- Filters out valid catalysts due to historical illiquidity
- Cannot adapt to real-time market conditions

### Target State (Dynamic Microstructure)
- ✅ Real-time volume surge detection (95th percentile vs. 20-day baseline)
- ✅ Volatility spike detection (95th percentile vs. 20-day baseline)
- ✅ Spread compression detection (relative minute-over-minute change)
- ✅ Order book depth surge detection (90th percentile vs. 20-day baseline)
- ✅ Time-of-day adjusted baselines (premarket, market hours, post-market)
- ✅ Combined eligibility scoring (3 of 4 signals required for small/mid-cap)
- ✅ Mega-cap bypass: Companies ≥$300B skip microstructure check (market cap + AI only)

**Benefits**:
- Captures sudden liquidity activation in any market cap
- Reactive to actual market conditions, not historical averages
- Dramatically reduces false positives (bad fills on non-moving stocks)
- Maintains safety through quantitative gates

---

## Architecture

### Layer 1: Pre-Filter (Comprehensive Noise Reduction)
**Purpose**: Exclude obvious non-tradables and spam before any computation

**Expected Rejection Rate**: ~40% of feed noise

**Rules**:

1. **Penny Stocks & Micro-Caps**
   - `market_cap < $100M` → REJECT
   - `price < $0.50` → REJECT
   - Rationale: Too illiquid, prone to manipulation

2. **Exchange Filtering**
   - `exchange not in [NYSE, NASDAQ, AMEX]` → REJECT
   - OTC, Pink Sheets, Foreign Primary → REJECT
   - Rationale: Only trade US primary-listed stocks

3. **Trading Status**
   - `trading_status != 'ACTIVE'` → REJECT
   - Suspended, Halted, Delisted → REJECT
   - Rationale: Cannot trade inactive symbols

4. **Duplicate News Detection**
   - `headline_hash seen within 3 seconds` → REJECT
   - Same ticker + similar headline (fuzzy match > 90%) → REJECT
   - Rationale: PRNewswire, BusinessWire, GlobeNewswire often publish same release

5. **Content Quality**
   - `len(headline) < 10` → REJECT
   - `headline is all caps` → REJECT (spam indicator)
   - `headline contains "click here" or "free"` → REJECT (spam indicator)
   - Rationale: Filter obvious spam and low-quality content

6. **Ticker Validation**
   - `ticker not found in IBKR` → REJECT
   - `ticker has no recent trading activity` → REJECT
   - Rationale: Ensure symbol is tradeable

7. **Market Hours Check** (Optional)
   - If outside trading hours AND no extended hours data → REJECT
   - Rationale: Only trade when we can measure microstructure

**Implementation**: 
- Fast, synchronous checks before any async operations
- Use in-memory cache for duplicate detection (headline hashes)
- Log all rejections with reason for analysis

**Expected Flow**:
```
PRNewswire/BusinessWire/GlobeNewswire Feed
    ↓
[Pre-Filter: Penny Stocks, Spam, Dupes, Exchange, Status]
    ↓ (Rejects ~40% of feed noise)
[Microstructure Activation Check] ← Only if market_cap < $300B
    ↓ (Rejects ~35% of remaining)
[AI News Classification]
    ↓ (Rejects ~20% of remaining)
[Trade Execution]
```

### Layer 2: Microstructure Activation Check
**Purpose**: Determine if market structure has "woken up" to the news

**Bypass Rule**: Companies with `market_cap >= $300B` skip this layer entirely
- Rationale: Mega-caps are always liquid enough; microstructure check is unnecessary
- Flow: Pre-Filter → AI Classification → Trade Execution

**Real-Time Metrics** (computed on rolling 60-second window):
1. **Volume Surge**: `current_1min_volume / 20day_95th_percentile_volume`
   - Threshold: `>= 1.0` (current volume exceeds 95th percentile)
   - Time-of-day adjusted: Compare 9:30 AM volume to 9:30 AM baseline
   - Includes premarket/postmarket: All trading hours have separate baselines

2. **Volatility Spike**: `current_1min_realized_vol / 20day_95th_percentile_vol`
   - Threshold: `>= 1.0` (current volatility exceeds 95th percentile)
   - Calculation: `std(log_returns) * sqrt(252 * 6.5 * 60)` (annualized)
   - Time-of-day adjusted: Separate baselines for each hour

3. **Spread Compression** (Relative Change): `(spread_t-1 - spread_t) / spread_t-1`
   - Threshold: `>= 0.20` (spread tightened by 20% in last minute)
   - Calculation: Minute-over-minute relative change, not absolute
   - Rationale: Measures *change* in tightness, which is more reliable than absolute level
   - Alternative: `current_spread <= 0.8 * previous_minute_spread` (20% compression)

4. **Order Book Depth Surge**: `current_depth / 20day_median_depth`
   - Threshold: `>= 1.5` (current depth is 50% above median)
   - Measurement: Sum of resting liquidity within 2 ticks of mid
   - Optional: Can be weighted less heavily if Level II data is unreliable

**Eligibility Logic**:
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

**Scoring**:
- Each signal contributes 0.25 to activation score (0.0 to 1.0)
- Minimum 3 of 4 signals required for small/mid-cap acceptance
- Score used for position sizing: `size = base_size * ai_confidence * microstructure_score`

### Layer 3: AI News Classification
**Purpose**: Determine if news is a genuine catalyst vs. fluff

**Current System**: Already implemented, works well
- Classification: `IMMINENT`, `NEUTRAL`, `IGNORE`
- Confidence: `HIGH`, `MEDIUM`, `LOW`
- Reasoning: Brief explanation

**No Changes Required**: Keep existing AI classifier

### Layer 4: Dynamic Position Sizing
**Purpose**: Size positions based on signal quality

**Formula**:
```python
base_size = $1000 notional (or configurable)
ai_confidence = 0.0 to 1.0 (from AI classifier)
microstructure_score = 0.0 to 1.0 (from Layer 2)

position_size = base_size * ai_confidence * microstructure_score
```

**Examples**:
- High confidence (0.9) + Strong micro (0.9) = 81% of base size
- Medium confidence (0.6) + Moderate micro (0.75) = 45% of base size
- Low confidence (0.4) + Weak micro (0.5) = 20% of base size (or reject)

---

## Entry Execution Strategies

### Universal Entry: Adaptive Routing (All Market Caps)
**Rationale**: News moves are time-sensitive regardless of market cap. We need fast fills to capture momentum before it dissipates.

**Why Adaptive for All**:
- Urgency-first execution (liquidity is ephemeral, 30-60 seconds)
- Wide spreads handled well (navigates $0.50+ spreads intelligently)
- Smart routing (scans all venues, finds hidden liquidity)
- Fast fills (80-90% within 1-2 seconds)
- Consistent execution strategy simplifies code and reduces edge cases

**Implementation**:
```python
order.orderType = "ADAPTIVE"
order.algoStrategy = "Adaptive"
order.algoParams = [
    ("adaptivePriority", "URGENT"),  # Need fills NOW for all news trades
]
# Optional: Use as limit order slightly above mid for price improvement
order.lmtPrice = current_mid + 0.05  # 5 cents above mid
```

**Execution Timeline**:
- 0-0.5 sec: 60% of position filled
- 0.5-1.0 sec: 30% of position filled
- 1.0-2.0 sec: 10% of position filled
- Total: 100% filled within 1-2 seconds

**Priority Settings**:
- `URGENT`: Scans all venues aggressively, prioritizes speed (use for all news trades)
- Rationale: Missing the fill is worse than paying an extra 1-2 cents. News moves persist for minutes, so we have time to profit even with slight entry slippage.

---

## Exit Strategies

### Current Approach (Simplified)
**For Now**: Maintain existing 5-minute exit strategy
- Rationale: Momentum-based exits are complex; tackle after microstructure filtering is proven
- Future Enhancement: Implement multi-tier exit system (immediate profit-taking, trailing stops, hard exits)

### Future: Multi-Tier Exit System (To Be Implemented Later)

**The Challenge**:
**"Sell the News" Phenomenon**: Once news is public, liquidity collapses 30-90 seconds post-entry:
- Bid-ask spread widens from $0.02 to $0.15+
- Depth evaporates (100K shares → 5K shares)
- Volume drops 50%+

**Window**: 5-10 minutes, with best opportunity in first 60-90 seconds

### Tier 1: Immediate Profit-Taking (30-90 seconds) - Future
**Purpose**: Lock in quick profits from first wave of momentum

**Strategy**:
- Exit 60% of position at pre-calculated profit target
- Target scales with signal quality: `base_target * (0.5 + ai_confidence * micro_score)`
- Use limit order, not market order

**Implementation**:
```python
base_profit_bps = 50  # 50 basis points base
confidence_multiplier = ai_confidence * microstructure_score
profit_target_bps = base_profit_bps * (0.5 + confidence_multiplier)

target_price = entry_price * (1 + profit_target_bps / 10000)
exit_size = int(entry_size * 0.6)

exit_order = LimitOrder("SELL", exit_size, target_price)
exit_order.tif = "DAY"  # Good-Til-Day
```

**Examples**:
- High confidence (0.9) + Strong micro (0.9) = 80 bps target
- Medium confidence (0.6) + Moderate micro (0.75) = 45 bps target
- Low confidence (0.4) + Weak micro (0.5) = 30 bps target

**Key Point**: Set this order **while entering**, not after confirmation. The move happens in 30-60 seconds.

### Tier 2: Trailing Stop (90 seconds - 5 minutes)
**Purpose**: Stay in the move as long as momentum is intact, protect gains if reversed

**Strategy**:
- Apply trailing stop to remaining 40% of position
- Trail width: 50-100 bps for big-cap, 30-50 bps for small-cap
- Automatically adjusts upward as price rallies

**Implementation**:
```python
trail_bps = 50  # 50 basis points trail
remaining_size = entry_size * 0.4

trail_stop = Order()
trail_stop.action = "SELL"
trail_stop.totalQuantity = remaining_size
trail_stop.orderType = "TRAIL"
trail_stop.trailingPercent = 0.5  # 50 bps trail (0.5%)
trail_stop.tif = "DAY"
```

**How It Works**:
- Enter at $50.00
- Price rallies to $50.80 → trailing stop at $50.30
- If price drops to $50.30 → exit automatically
- If price rallies to $51.20 → trailing stop adjusts to $50.70
- Stay in the move until momentum breaks

### Tier 3: Hard Exit (5-10 minutes)
**Purpose**: Force close remaining position if not exited via trailing stop

**Strategy**:
- If position still open after 5-7 minutes, manually close
- **Never hold past 10 minutes** (market exhausted, reversing)
- Use limit order inside bid for small-cap, market order for big-cap

**Implementation**:
```python
if hold_time_seconds > 300:  # 5 minutes
    if market_cap >= 500e9:
        # Big-cap: market order is safe
        exit_order = MarketOrder("SELL", remaining_size)
    else:
        # Small-cap: limit order inside bid (avoid slippage)
        exit_limit = current_bid - 0.03  # 3 cents inside bid
        exit_order = LimitOrder("SELL", remaining_size, exit_limit)
```

**Critical**: Never use market orders for small-cap exits. Always use limit orders 2-3 cents inside the bid.

### Exit Comparison

| Attribute | Big-Cap (VWAP Entry) | Small-Cap (Adaptive Entry) |
|-----------|----------------------|----------------------------|
| Immediate Exit | Limit at +50-100 bps, fills in 30-60 sec | Limit at +30-50 bps, may take 1-2 min |
| Trailing Stop | 50-100 bps trail (wide), captures extended moves | 30-50 bps trail (tight), exits fast |
| Hard Exit | Rarely needed; can hold 10+ min safely | Critical; do NOT hold past 5 min |
| Liquidity Risk | Bid-ask widens but remains tradable | Bid-ask can spike to 50+ bps |

---

## Data Requirements & Baseline Calculation

### Core Insight: Measure Surge Between Publication and Reception
**Key Concept**: 
- News is **published** at time `T_publish` (e.g., 9:30:00 AM)
- We **receive** it via websocket at time `T_receive` (e.g., 9:30:13 AM, 13 seconds later)
- The websocket delay is actually **helpful** - it gives us time to observe market reaction
- We measure: Did the period from `T_publish` to `T_receive` show a surge?

**Measurement Window**:
- Window duration: `T_receive - T_publish` (could be 10s, 13s, 45s, 58s, etc.)
- Window: `[T_publish, T_receive]`
- Compare this exact window to the same window from last 20 days
- Example: If published at 9:30:00 and received at 9:30:13, compare "9:30:00-9:30:13" to "9:30:00-9:30:13" from each of the last 20 days

**Why This Works**:
- We have both timestamps (publication_time and reception_time) from the news feed
- The exact time window captures the immediate market reaction to the news
- No need for fixed 60-second windows - we use whatever time elapsed
- Surge detection is precise: we're measuring the exact period that includes the news impact

### Data Collection Strategy

#### 1. Real-Time Tick Data Collection & Storage (Redis)
**Purpose**: Capture and persist every tick for all symbols, enabling arbitrary time-window queries

**IBKR Data Requirements**:
- **Level I Market Data** (Required):
  - `BID` (tickType 1): Best bid price
  - `ASK` (tickType 2): Best ask price
  - `LAST` (tickType 4): Last trade price
  - `VOLUME` (tickType 8): Cumulative daily volume

**Storage Strategy: Redis with AOF Persistence**
- **Why Redis**: Ultra-fast lookups, supports time-series queries, AOF ensures durability
- **Why AOF**: Persists every write operation, ensures no data loss on restart
- **Structure**: Store ticks as sorted sets keyed by symbol and timestamp
- **Retention**: Keep last 30 days of tick data (enough for 20-day baseline + buffer)

**Implementation**:
```python
# Subscribe to real-time data for all symbols
ib.reqMktData(contract, "", False, False)  # Level I

# Store each tick immediately to Redis
redis_key = f"tick:{symbol}:{timestamp.isoformat()}"
redis.hset(redis_key, mapping={
    'bid': ticker.bid,
    'ask': ticker.ask,
    'last': ticker.last,
    'volume': ticker.volume,
    'timestamp': timestamp.isoformat(),
})

# Also maintain sorted set for time-range queries
redis.zadd(f"ticks:{symbol}", {redis_key: timestamp.timestamp()})
```

**Data Granularity**:
- **Tick-by-tick**: Every price/volume update (stored in Redis)
- **Why**: Need to query arbitrary time windows (10s, 13s, 45s, 58s, etc.)
- **Aggregation**: Compute metrics on-the-fly when news arrives

#### 2. Historical Data Collection & Baseline Calculation
**Purpose**: Build 20-day baseline for arbitrary time windows

**Key Insight**: We don't need to pre-compute baselines for fixed windows. Instead:
1. When news arrives, we know `T_publish` and `T_receive`
2. Window duration: `delta_t = T_receive - T_publish` (e.g., 13 seconds)
3. Query Redis for ticks in `[T_publish, T_receive]` from last 20 days
4. Compute percentiles on-the-fly for this exact window

**Historical Data Collection**:
- **Backfill**: Request historical tick data from IBKR for last 30 days
- **Store**: All ticks go into Redis (same structure as real-time)
- **Why 30 days**: Need 20 days for baseline + 10 days buffer for weekends/holidays
- **Continuous**: As new ticks arrive, they're automatically stored (real-time + historical merge)

**Time-of-Day Matching**:
- **Exact matching**: Compare `[T_publish, T_receive]` to same window from last 20 days
- **Example**: 
  - News published 9:30:00, received 9:30:13 (13-second window)
  - Query: "9:30:00-9:30:13" from each of last 20 days
  - Compute: Volume, volatility, spread for each 13-second window
  - Compare: Current window metrics vs. 95th percentile of historical windows

**IBKR Data Limitations & Realistic Expectations**:

**IBKR Pro Historical Data Limitations**:
- **Tick Data**: Limited to recent periods (typically 1-2 months, varies by exchange)
- **1-Second Bars**: May be available but limited to recent data (often 1-3 months)
- **Rate Limits**: IBKR enforces rate limits on historical data requests (typically 50-100 requests per second)
- **Symbol Universe**: For 3,000-5,000 symbols (Wilshire 5000 minus penny stocks), backfilling 20 days of 1-second data would require:
  - ~3,000 symbols × 20 days × ~23,400 seconds/day = ~1.4 billion data points
  - At 50 requests/second, this would take ~8 hours of continuous requests (if no rate limiting)
  - **Reality**: IBKR rate limits make this impractical for large-scale backfill

**How Quants Actually Do It**:
1. **Specialized Data Providers** (Most Common):
   - **Polygon.io**: High-quality tick data, reasonable pricing ($99-299/month)
   - **Alpaca**: Free historical data API (limited to recent periods)
   - **QuantConnect**: Historical data included with platform
   - **Direct Exchange Feeds**: Professional quants use direct exchange connections (expensive, $10K+/month)

2. **Hybrid Approach** (Recommended for This Project):
   - **Real-Time**: Use IBKR for continuous tick collection (no limitations)
   - **Historical Backfill**: Use Polygon.io or similar for initial 20-day backfill
   - **Ongoing**: IBKR real-time ticks build the baseline organically

3. **Start Fresh Approach** (Alternative):
   - Begin collecting real-time ticks immediately
   - Build baseline organically over 20 trading days
   - Accept that microstructure filtering won't work until 20 days of data accumulated
   - **Feasible for paper trading**: Can use simpler filters initially, add microstructure after 20 days

**Recommended Approach for This Project**:
1. **Phase 1 (Initial Setup)**:
   - Use Polygon.io or Alpaca to backfill last 20 trading days of 1-second bars for all symbols
   - Store in Redis (last 20 days) and PostgreSQL (backup)
   - **Cost**: Polygon.io ~$99/month for tick data, or Alpaca free tier (limited)

2. **Phase 2 (Ongoing)**:
   - IBKR real-time ticks stored to Redis immediately
   - Rolling window: Keep last 20 trading days in Redis
   - Older data: Move to PostgreSQL for backup (after 24 hours)
   - **No ongoing cost**: IBKR real-time data is included with account

3. **Fallback (If Budget Constrained)**:
   - Start with IBKR real-time collection only
   - Build baseline organically over 20 trading days
   - Use simpler pre-filters initially, add microstructure after 20 days

**Data Sources**:
1. **IBKR Real-Time Ticks** (Primary for Ongoing):
   - Subscribe to all symbols continuously
   - Store every tick to Redis immediately
   - No rate limits, no historical depth limits
   - **Best for**: Continuous real-time data collection

2. **Polygon.io** (For Initial Backfill - Recommended):
   - **Historical Data Available**: Tick-level data going back to 2004
   - **1-Second Aggregates**: Available via aggregates API or create from raw trades
   - **Last 20 Days**: Immediately accessible, no waiting period
   - **Cost**: ~$99/month for tick data subscription
   - **Best for**: Initial historical baseline (can start immediately)
   - Store to Redis (last 20 days) and PostgreSQL (backup)

3. **Alpaca** (Alternative):
   - Free tier available but may have limitations on historical depth
   - May not support 1-second bars directly
   - **Best for**: Budget-constrained projects

**Redis Schema**:
```python
# Per-tick storage
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

**Query Pattern**:
```python
# Get all ticks for symbol in time range
start_ts = T_publish.timestamp()
end_ts = T_receive.timestamp()
tick_keys = redis.zrangebyscore(
    f"ticks:{symbol}",
    start_ts,
    end_ts,
)
ticks = [redis.hgetall(key) for key in tick_keys]
```

**Storage Strategy: Hybrid Redis + PostgreSQL**

**Redis (Hot Storage - Last 20 Trading Days)**:
- **Purpose**: Fast lookups for baseline calculation and surge detection
- **Retention**: Last 20 trading days (rolling window)
- **Why Redis**: Ultra-fast time-range queries (<1ms), sorted sets for efficient queries
- **AOF Persistence**: Ensures durability, can recover from crashes

**PostgreSQL (Cold Storage - Backup & Archive)**:
- **Purpose**: Long-term backup, data older than 24 hours
- **Retention**: All historical data (indefinite)
- **Why PostgreSQL**: Reliable, queryable, cost-effective for large datasets
- **Structure**: TimescaleDB extension for time-series optimization (optional but recommended)

**Rolling Window Management**:
```python
# Daily job (run at market close)
def manage_rolling_window():
    """
    Move data older than 20 trading days from Redis to PostgreSQL.
    Keep last 20 trading days in Redis for fast access.
    """
    cutoff_date = get_trading_date_n_days_ago(20)
    
    # Query Redis for all symbols
    for symbol in all_symbols:
        # Get ticks older than cutoff
        old_ticks = redis.zrangebyscore(
            f"ticks:{symbol}",
            0,
            cutoff_date.timestamp(),
        )
        
        # Batch insert to PostgreSQL
        if old_ticks:
            store_ticks_to_postgres(symbol, old_ticks)
            # Remove from Redis (keep only last 20 days)
            redis.zremrangebyscore(
                f"ticks:{symbol}",
                0,
                cutoff_date.timestamp(),
            )
```

**Redis Configuration**:
```redis
# redis.conf
appendonly yes
appendfsync everysec  # Balance between performance and durability
maxmemory 16gb  # Adjust based on tick volume
maxmemory-policy allkeys-lru  # Evict oldest data if memory full
```

**Storage Size Estimates**:

**Redis (Last 20 Trading Days)**:
- Per tick: ~100 bytes (key + hash fields)
- Per symbol: ~2,000 ticks/day × 100 bytes = 200 KB/day
- 3,000 symbols × 20 trading days: 200 KB × 3,000 × 20 = 12 GB
- **Feasible**: Modern servers can handle 16-32 GB Redis instances

**PostgreSQL (All Historical Data)**:
- Per tick: ~50 bytes (compressed, indexed)
- Per symbol: ~2,000 ticks/day × 50 bytes = 100 KB/day
- 3,000 symbols × 365 days: 100 KB × 3,000 × 365 = 109 GB/year
- **Manageable**: PostgreSQL with TimescaleDB can handle TB-scale data

**Data Retention Strategy**:
- **Redis**: Last 20 trading days (rolling window, auto-evict older)
- **PostgreSQL**: All data older than 24 hours (permanent archive)
- **Daily Job**: Move data from Redis to PostgreSQL at market close
- **Why**: Redis for speed, PostgreSQL for reliability and cost

#### 3. Baseline Calculation Process (On-Demand)

**Approach**: Compute baselines on-demand when news arrives. No pre-computation needed.

**Algorithm**:
```python
def compute_baseline_for_publication_window(symbol, T_publish, T_receive, lookback_days=20):
    """
    Compute baseline for exact window from publication to reception.
    
    Example: 
    - Published: 9:30:00, Received: 9:30:13 (13-second window)
    - Compare "9:30:00-9:30:13" to same window from last 20 days
    """
    window_duration = (T_receive - T_publish).total_seconds()
    
    # Query ticks for current window (publication to reception)
    current_ticks = query_ticks_from_redis(symbol, T_publish, T_receive)
    current_metrics = compute_metrics_from_ticks(current_ticks)
    
    # Query same window from last 20 days
    historical_windows = []
    for day_offset in range(lookback_days):
        target_date = T_publish.date() - timedelta(days=day_offset)
        target_publish = datetime.combine(target_date, T_publish.time())
        target_receive = target_publish + timedelta(seconds=window_duration)
        
        # Query ticks for this historical window
        historical_ticks = query_ticks_from_redis(symbol, target_publish, target_receive)
        
        if historical_ticks and len(historical_ticks) >= 3:  # Need at least 3 ticks
            historical_metrics = compute_metrics_from_ticks(historical_ticks)
            historical_windows.append(historical_metrics)
    
    # Compute percentiles across all historical windows
    if historical_windows:
        baseline = {
            'volume_95p': np.percentile([w['volume'] for w in historical_windows], 95),
            'volatility_95p': np.percentile([w['volatility'] for w in historical_windows], 95),
            'spread_median': np.median([w['avg_spread'] for w in historical_windows]),
        }
        
        # Compare current to baseline
        volume_surge = current_metrics['volume'] >= baseline['volume_95p']
        volatility_spike = current_metrics['volatility'] >= baseline['volatility_95p']
        spread_compression = current_metrics['spread_compression'] >= 0.20
        
        return {
            'baseline': baseline,
            'current': current_metrics,
            'volume_surge': volume_surge,
            'volatility_spike': volatility_spike,
            'spread_compression': spread_compression,
        }
    
    return None  # Not enough historical data

def compute_metrics_from_ticks(ticks):
    """Compute volume, volatility, and spread metrics from tick data."""
    if not ticks or len(ticks) < 2:
        return None
    
    # Volume: Count of ticks (or sum if we have volume delta)
    volume = len(ticks)
    
    # Volatility: Std dev of log returns
    prices = [float(t['last']) for t in ticks if t.get('last')]
    if len(prices) > 1:
        returns = np.diff(np.log(prices))
        volatility = np.std(returns) * np.sqrt(252 * 6.5 * 60)  # Annualized
    else:
        volatility = None
    
    # Spread: Average and compression
    spreads = []
    for tick in ticks:
        bid = float(tick.get('bid', 0))
        ask = float(tick.get('ask', 0))
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            spread_bps = ((ask - bid) / mid) * 10000
            spreads.append(spread_bps)
    
    if len(spreads) >= 2:
        avg_spread = np.mean(spreads)
        start_spread = spreads[0]
        end_spread = spreads[-1]
        compression = (start_spread - end_spread) / start_spread if start_spread > 0 else 0
    else:
        avg_spread = None
        compression = None
    
    return {
        'volume': volume,
        'volatility': volatility,
        'avg_spread': avg_spread,
        'spread_compression': compression,
    }
```

**Performance**: 
- Query time: ~10-50ms per symbol (Redis is fast)
- Computation: ~1-5ms (simple percentile calculation)
- Total: <100ms per news article (acceptable for real-time trading)

#### 4. Real-Time Surge Detection

**When News Arrives**:
1. Extract `T_publish` and `T_receive` from news article
2. Query Redis for ticks in `[T_publish, T_receive]`
3. Compute current metrics (volume, volatility, spread compression)
4. Query same window from last 20 days
5. Compare current metrics to 95th percentile of historical

**Implementation**:
```python
def detect_microstructure_surge(symbol, article):
    """
    Detect if microstructure surge occurred between publication and reception.
    
    Args:
        article: News article with 'published_at' and 'received_at' timestamps
    
    Returns:
        dict with surge detection results
    """
    T_publish = article.published_at
    T_receive = article.received_at
    
    # Query current window ticks from Redis
    current_ticks = query_ticks_from_redis(symbol, T_publish, T_receive)
    if not current_ticks or len(current_ticks) < 3:
        return {'surge_detected': False, 'reason': 'insufficient_ticks'}
    
    current_metrics = compute_metrics_from_ticks(current_ticks)
    
    # Compute baseline for same window from last 20 days
    baseline_result = compute_baseline_for_publication_window(
        symbol, T_publish, T_receive, lookback_days=20
    )
    
    if not baseline_result:
        return {'surge_detected': False, 'reason': 'insufficient_historical_data'}
    
    # Check if surge criteria met
    volume_surge = baseline_result['volume_surge']
    volatility_spike = baseline_result['volatility_spike']
    spread_compression = baseline_result['spread_compression']
    
    surge_detected = volume_surge and volatility_spike and spread_compression
    
    return {
        'surge_detected': surge_detected,
        'window_duration_seconds': (T_receive - T_publish).total_seconds(),
        'current_metrics': current_metrics,
        'baseline': baseline_result['baseline'],
        'volume_surge': volume_surge,
        'volatility_spike': volatility_spike,
        'spread_compression': spread_compression,
    }
```

**Key Point**: We measure the **exact time window** from publication to reception. The websocket delay (10-60 seconds) is actually helpful - it gives us time to observe the immediate market reaction to the news.

### Data Collection Implementation Plan

**Phase 1: Redis Setup & Real-Time Tick Collection** (Week 1-2)
- [ ] Set up Redis with AOF persistence
- [ ] Configure Redis for time-series data (sorted sets)
- [ ] Set up IBKR market data subscriptions for all symbols
- [ ] Implement tick storage to Redis (every tick, immediately)
- [ ] Handle data gaps (reconnection, missed ticks, error recovery)
- [ ] Monitor Redis memory usage and set eviction policies

**Phase 2: Historical Data Backfill** (Week 2-3)
- [ ] **Option A (Recommended)**: Use Polygon.io or Alpaca to backfill last 20 trading days
  - [ ] Set up Polygon.io/Alpaca API client
  - [ ] Request 1-second bars for all symbols (NASDAQ, NYSE, AMEX)
  - [ ] Store to Redis (last 20 days) and PostgreSQL (backup)
  - [ ] Handle missing data (weekends, holidays, market closures)
  - [ ] Validate data quality (check for gaps, outliers, duplicates)
- [ ] **Option B (Budget Constrained)**: Start fresh with IBKR real-time only
  - [ ] Begin collecting real-time ticks immediately
  - [ ] Build baseline organically over 20 trading days
  - [ ] Use simpler pre-filters initially, add microstructure after 20 days
- [ ] Set up PostgreSQL with TimescaleDB for long-term storage
- [ ] Implement rolling window management (move data older than 20 days to PostgreSQL)

**Phase 3: Baseline Calculation & Surge Detection** (Week 3-4)
- [ ] Implement `compute_baseline_for_publication_window()` function
- [ ] Implement `query_ticks_from_redis()` for arbitrary time windows
- [ ] Implement `compute_metrics_from_ticks()` (volume, volatility, spread)
- [ ] Implement `detect_microstructure_surge()` main function
- [ ] Test with various window durations (10s, 13s, 45s, 58s, etc.)
- [ ] Optimize Redis queries for performance (<100ms per article)

**Phase 4: Integration with Article Processor** (Week 4-5)
- [ ] Extract `published_at` and `received_at` from news articles
- [ ] Call `detect_microstructure_surge()` when news arrives
- [ ] Integrate surge detection into pre-AI filter flow
- [ ] Add logging for surge detection results
- [ ] Update audit trail with microstructure metrics

### Storage Size Estimates

**Per Symbol Per Day**:
- Tick data: ~2,000 ticks × 100 bytes (Redis overhead) = 200 KB/day

**For 1,000 Symbols, 30 Days**:
- Tick data: 200 KB × 1,000 × 30 = 6 GB
- **Redis memory**: ~8-10 GB (with overhead and indexes)
- **Feasible**: Modern servers can easily handle 16-32 GB Redis instances

**Redis Query Performance**:
- Time-range query: <1ms (sorted set lookup)
- Tick retrieval: <10ms (for 10-60 second windows)
- Baseline computation: <50ms (percentile calculation)
- **Total**: <100ms per news article (acceptable for real-time trading)

### Baseline Metrics Storage
```python
{
    'volume_95p': 500,           # 95th percentile 1-min volume
    'volume_99p': 800,           # 99th percentile 1-min volume
    'volatility_95p': 0.03,      # 95th percentile realized vol
    'volatility_99p': 0.05,      # 99th percentile realized vol
    'spread_median': 5.0,        # Median spread (bps)
    'spread_5p': 2.5,            # 5th percentile spread (bps)
    'depth_median': 50000,       # Median order book depth
    'depth_90p': 75000,          # 90th percentile depth
}
```

**Storage**: ~1 KB per symbol per bucket × 48 buckets × 1,000 symbols = 48 MB (negligible)

### Data Collection Best Practices

#### 1. Tick-Level Granularity with Redis Storage
**Critical Requirement**: Tick-by-tick data stored in Redis, not aggregated

**Why**:
- News published at variable times, received at variable delays (10-60 seconds)
- We must compare exact window `[T_publish, T_receive]` to same window from last 20 days
- Window duration is variable (10s, 13s, 45s, 58s, etc.) - cannot use fixed buckets
- Example: Published 9:30:00, received 9:30:13 → Compare "9:30:00-9:30:13" to historical

**Approach**:
- Collect every tick from IBKR in real-time
- Store immediately to Redis (sorted sets for time-range queries)
- When news arrives, query exact window `[T_publish, T_receive]` from Redis
- Compute metrics on-the-fly, compare to baseline from same window (last 20 days)

#### 2. Real-Time Data Collection Strategy
**For All Symbols** (continuous subscription):
- Subscribe to Level I market data via `ib.reqMktData()` for all symbols
- Store every tick immediately to Redis (no buffering, no aggregation)
- Redis sorted sets enable fast time-range queries when news arrives

**Why Continuous for All Symbols**:
- Need historical baseline for any symbol that might get news
- Redis can handle thousands of symbols (memory permitting)
- Real-time ticks are the source of truth (no need for separate historical feed)

#### 3. Handling Data Gaps
**Problem**: IBKR connection drops, missed ticks, market halts

**Solutions**:
- **Reconnection**: Auto-reconnect and resume data collection
- **Gap Detection**: Flag periods with no ticks (may indicate halt)
- **Interpolation**: For baseline calculation, use previous day's data if current day has gaps
- **Validation**: Check data quality before using in baseline calculation

#### 4. Baseline Calculation (On-Demand, No Buckets, No Batch Jobs)
**When News Arrives**:
1. Extract `T_publish` and `T_receive` from article
2. Query Redis for ticks in `[T_publish, T_receive]` (current window)
3. Query Redis for same window from last 20 days (historical windows)
4. Compute percentiles on-the-fly (<50ms)
5. Compare current metrics to 95th percentile

**No Pre-Computation Needed**:
- Redis sorted sets enable fast time-range queries
- On-demand computation is fast enough (<100ms total)
- No batch jobs, no fixed buckets, no pre-storage
- Exact time-of-day matching: Compare "9:30:00-9:30:13" to "9:30:00-9:30:13" from last 20 days

#### 6. Storage Recommendations: Redis with AOF

**Redis (Primary Storage)**:
- **Why**: Ultra-fast time-range queries, supports sorted sets, AOF persistence
- **Setup**: 
  ```redis
  # redis.conf
  appendonly yes
  appendfsync everysec
  maxmemory 16gb
  maxmemory-policy allkeys-lru
  ```
- **Performance**: <1ms for time-range queries, <10ms for tick retrieval

**AOF Persistence**:
- **Why**: Every tick is important, need durability
- **Trade-off**: Slight performance hit (everysec sync) for data safety
- **Recovery**: Can recover up to 1 second of data loss

**Backup Strategy**:
- **RDB Snapshots**: Daily snapshots for disaster recovery
- **Replication**: Redis replica for high availability (optional)

#### 7. Data Collection Architecture

```
┌─────────────────────────────────────────┐
│  IBKR Market Data Feed                  │
│  (Real-time ticks via reqMktData)      │
│  All symbols, continuous subscription   │
└──────────────┬──────────────────────────┘
               │
               └─→ [Redis with AOF] ← Every tick stored immediately
                   (Sorted sets by symbol)  (sorted by timestamp)
                       │
                       ├─→ [Real-Time Ticks] ← Current data
                       │   (last 30 days)     for surge detection
                       │
                       └─→ [Historical Ticks] ← Same storage
                           (last 30 days)      for baseline calculation
                                │
                                └─→ [On-Demand Query] ← When news arrives
                                    Query [T_publish, T_receive]
                                    Compare to same window (last 20 days)
```

#### 8. Data Collection Code Structure

**Real-Time Collector**:
```python
class TickDataCollector:
    """Collects real-time ticks from IBKR and stores to Redis."""
    
    def __init__(self, ib_client, redis_client):
        self.ib = ib_client
        self.redis = redis_client
    
    def subscribe(self, symbol):
        """Subscribe to real-time data for a symbol."""
        contract = Stock(symbol, "SMART", "USD")
        ticker = self.ib.reqMktData(contract, "", False, False)
        # Store ticker reference for cleanup
    
    def on_tick(self, ticker):
        """Callback when tick arrives - store immediately to Redis."""
        symbol = ticker.contract.symbol
        timestamp = datetime.now(timezone.utc)
        unix_ts = timestamp.timestamp()
        
        # Store tick as hash
        tick_key = f"tick:{symbol}:{timestamp.isoformat()}"
        self.redis.hset(tick_key, mapping={
            'bid': ticker.bid or '',
            'ask': ticker.ask or '',
            'last': ticker.last or '',
            'volume': ticker.volume or '',
            'timestamp': timestamp.isoformat(),
        })
        
        # Add to sorted set for time-range queries
        self.redis.zadd(f"ticks:{symbol}", {tick_key: unix_ts})
        
        # Set TTL (30 days retention)
        self.redis.expire(tick_key, 30 * 24 * 3600)
    
    def query_ticks(self, symbol, start_time, end_time):
        """Query ticks for symbol in time range from Redis."""
        start_ts = start_time.timestamp()
        end_ts = end_time.timestamp()
        
        # Get tick keys in time range
        tick_keys = self.redis.zrangebyscore(
            f"ticks:{symbol}",
            start_ts,
            end_ts,
        )
        
        # Retrieve tick data
        ticks = []
        for key in tick_keys:
            tick_data = self.redis.hgetall(key)
            if tick_data:
                ticks.append(tick_data)
        
        return sorted(ticks, key=lambda t: t['timestamp'])
```

**Historical Data Backfill** (Using Polygon.io):
```python
async def backfill_historical_ticks_polygon(symbol, days=20):
    """
    Backfill historical 1-second bars from Polygon.io.
    
    Polygon.io provides:
    - Historical tick data going back to 2004
    - 1-second aggregates available via aggregates API
    - Immediate access to last 20 days of data
    """
    from polygon import RESTClient
    from datetime import datetime, timedelta
    
    client = RESTClient(api_key=os.getenv("POLYGON_API_KEY"))
    
    # Get last 20 trading days (account for weekends)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days * 1.5)  # ~30 calendar days for 20 trading days
    
    # Request 1-second aggregates
    # Note: Polygon.io supports 1-second aggregates directly
    bars = client.get_aggs(
        ticker=symbol,
        multiplier=1,
        timespan="second",
        from_=start_date.strftime("%Y-%m-%d"),
        to=end_date.strftime("%Y-%m-%d"),
        limit=50000,  # Polygon.io allows up to 50,000 bars per request
    )
    
    # Store to Redis and PostgreSQL
    for bar in bars:
        tick_data = {
            'timestamp': datetime.fromtimestamp(bar.timestamp / 1000),
            'open': float(bar.open),
            'high': float(bar.high),
            'low': float(bar.low),
            'close': float(bar.close),
            'volume': int(bar.volume),
        }
        store_tick_to_redis(symbol, tick_data)
        store_tick_to_postgres(symbol, tick_data)
    
    logger.info(
        f"Backfilled {len(bars)} 1-second bars for {symbol}",
        symbol=symbol,
        count=len(bars),
    )

async def backfill_historical_ticks_alpaca(symbol, days=20):
    """Backfill historical 1-second bars from Alpaca (free tier)."""
    import alpaca_trade_api as tradeapi
    
    api = tradeapi.REST(
        key_id=os.getenv("ALPACA_API_KEY"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
        base_url="https://paper-api.alpaca.markets",  # or live
    )
    
    # Request 1-second bars (Alpaca may limit to recent data)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days * 1.5)
    
    bars = api.get_bars(
        symbol,
        "1Sec",
        start=start_date.isoformat(),
        end=end_date.isoformat(),
    ).df
    
    # Store to Redis and PostgreSQL
    for _, row in bars.iterrows():
        tick_data = {
            'timestamp': row.name,  # Datetime index
            'open': row['open'],
            'high': row['high'],
            'low': row['low'],
            'close': row['close'],
            'volume': row['volume'],
        }
        store_tick_to_redis(symbol, tick_data)
        store_tick_to_postgres(symbol, tick_data)
```

**Rolling Window Management**:
```python
async def manage_rolling_window():
    """
    Daily job: Move data older than 20 trading days from Redis to PostgreSQL.
    Keep last 20 trading days in Redis for fast access.
    """
    from datetime import datetime, timedelta
    
    # Get cutoff date (20 trading days ago)
    cutoff_date = get_trading_date_n_days_ago(20)
    cutoff_ts = cutoff_date.timestamp()
    
    # Process all symbols
    for symbol in all_symbols:
        # Get tick keys older than cutoff
        old_tick_keys = redis.zrangebyscore(
            f"ticks:{symbol}",
            0,
            cutoff_ts,
        )
        
        if old_tick_keys:
            # Retrieve tick data
            old_ticks = []
            for key in old_tick_keys:
                tick_data = redis.hgetall(key)
                if tick_data:
                    old_ticks.append(tick_data)
            
            # Batch insert to PostgreSQL
            if old_ticks:
                await store_ticks_to_postgres(symbol, old_ticks)
                
                # Remove from Redis (keep only last 20 days)
                redis.zremrangebyscore(
                    f"ticks:{symbol}",
                    0,
                    cutoff_ts,
                )
                
                logger.info(
                    f"Moved {len(old_ticks)} ticks for {symbol} to PostgreSQL",
                    symbol=symbol,
                    count=len(old_ticks),
                )
```

**Polygon.io Data Availability Confirmation**:
- ✅ **Historical Data**: Tick-level data available going back to 2004
- ✅ **1-Second Aggregates**: Available via aggregates API (or create from raw trades)
- ✅ **Last 20 Days**: Immediately accessible, no waiting period
- ✅ **Can Start Immediately**: All required data is available right now
- ✅ **This Definitely Works**: Polygon.io has all the data needed for the microstructure strategy

**Cost & Setup**:
- **Polygon.io**: ~$99/month for tick data subscription (recommended)
- **Alpaca**: Free tier available, but may have limitations on historical depth
- **IBKR Only**: Start fresh, build baseline over 20 days (no cost, but 20-day delay)

---

## Edge Cases & Safeguards

### Edge Case Strategy: Observe, Don't Preempt
**Philosophy**: Let the microstructure filter do its job. If a trade passes all filters (pre-filter, microstructure, AI) but still fails, log it and analyze later.

**Rationale**:
- Pre-filtering edge cases creates whack-a-mole problem
- Microstructure activation is a strong signal; if it passes, it's likely valid
- News + microstructure surge happening simultaneously is rare unless news is real catalyst
- Statistics will reveal patterns over time

**Observation Strategy**:
- Log all trades (pass/reject) with full context
- Track outcomes: Did price move? Did we get good fills?
- Monthly analysis: Review false positives/negatives
- Adjust thresholds based on data, not assumptions

**Edge Cases to Monitor** (but not pre-filter):
- Biotech/Pharma: Do they have higher false positive rate?
- Penny stocks: Do microstructure signals work for them?
- SPACs: Are they more prone to fake activation?
- Mining/Commodities: Do they move on news or just futures?

**Action**: Build comprehensive logging, analyze after 1 month of paper trading

### 7. Baseline Calculation Lag
**Problem**: Need 20 days of data; on day 1, you have nothing
**Solution**: 
- Pre-load 1-2 years of historical data
- Compute rolling percentiles daily at market close
- Store in Redis/PostgreSQL for fast runtime lookups

### 8. Time-of-Day Bias
**Problem**: 9:30 AM volume ≠ 1 PM volume ≠ 3:50 PM volume
**Solution**: 
- Compute percentiles in 30-minute buckets
- Compare 9:30 AM news to 9:30 AM baseline, not 2:00 PM baseline

### 9. Order Book Depth Unreliable
**Problem**: Resting liquidity vanishes instantly; spoofing exists
**Solution**: 
- Weight order book depth as tertiary signal
- Prioritize volume + volatility + spread

### 10. False Momentum from Options Hedging
**Problem**: Call option rally → market makers delta-hedge by shorting stock
**Solution**: 
- Check options IV and put-call ratio before entering
- If IV collapsing and puts net-selling → be cautious

### 11. News Duplicate / Multiple Feeds
**Problem**: PRNewswire, BusinessWire, GlobeNewswire publish same release within 1-2 seconds
**Solution**: 
- Deduplicate by headline hash (MD5/SHA256) within 3-second window

### 12. Premarket Illiquidity
**Problem**: Spreads and volume artificially thin before opening bell
**Solution**: 
- Compare changes against premarket-specific history
- Don't rely on regular session baselines for premarket trades

---

## Implementation Phases

### Phase 1: Data Collection Infrastructure (Week 1-2)
**Tasks**:
- [ ] Set up PostgreSQL + TimescaleDB for time-series storage
- [ ] Implement real-time tick data collection from IBKR
- [ ] Create tick buffer in memory (deque) for rolling 60-second window
- [ ] Implement async database writes (don't block on I/O)
- [ ] Handle data gaps (reconnection, missed ticks)
- [ ] Backfill 20 days of historical data for all symbols
- [ ] Unit tests for data collection and storage

**Deliverable**: Reliable data collection pipeline with historical backfill

### Phase 2: Baseline Calculation (Week 2-3)
**Tasks**:
- [ ] Implement time-of-day bucketing (30-minute windows, all trading hours)
- [ ] Compute 20-day rolling percentiles (95th for volume/volatility, median for depth)
- [ ] Create daily batch job to update baselines at market close
- [ ] Store baselines in Redis for fast runtime lookups
- [ ] Handle new symbols (use market-wide defaults until 20 days of data)
- [ ] Unit tests for percentile calculations

**Deliverable**: Complete baseline database for all symbols, all time-of-day buckets

### Phase 3: Microstructure Analyzer (Week 3-4)
**Tasks**:
- [ ] Create `MicrostructureAnalyzer` class
- [ ] Implement rolling 60-second metric computation
- [ ] Calculate volume surge (current vs. 95th percentile)
- [ ] Calculate volatility spike (current vs. 95th percentile)
- [ ] Calculate spread compression (relative minute-over-minute change)
- [ ] Calculate order book depth surge (current vs. median)
- [ ] Implement activation scoring (3 of 4 signals required)
- [ ] Unit tests for metric calculations

**Deliverable**: Standalone analyzer that can compute activation scores for any symbol

### Phase 4: Pre-AI Filter Replacement (Week 4-5)
**Tasks**:
- [ ] Remove static filters (sector bans, market cap thresholds, ADV)
- [ ] Implement minimal pre-filter (penny stocks, OTC, duplicates)
- [ ] Integrate `MicrostructureAnalyzer` into `ArticleProcessor`
- [ ] Implement eligibility scoring logic (3 of 4 signals)
- [ ] Add logging for filter decisions (pass/reject + reason)
- [ ] Update audit trail to include microstructure scores

**Deliverable**: News articles pass through microstructure check before AI classification

### Phase 5: Entry Execution (Week 5-6)
**Tasks**:
- [ ] Implement Adaptive routing for all market caps (universal strategy)
- [ ] Set `adaptivePriority` to "URGENT" for all news trades
- [ ] Add dynamic position sizing (`base_size * ai_confidence * micro_score`)
- [ ] Update `IBKRTradingService` to use Adaptive routing
- [ ] Test execution in paper trading

**Deliverable**: All trades execute with Adaptive routing (consistent strategy)

### Phase 6: Exit Strategy (Week 6-7) - Future Enhancement
**Tasks**:
- [ ] Implement Tier 1: Immediate profit-taking (60% at limit order)
- [ ] Implement Tier 2: Trailing stop (40% with 50 bps trail)
- [ ] Implement Tier 3: Hard exit (force close after 5 minutes)
- [ ] Add OCO (One-Cancels-Other) logic for exit orders
- [ ] Implement order timeout logic (auto-cancel after N seconds)
- [ ] Update `AutoTradeService` to schedule exits

**Deliverable**: Multi-tier exit system with automatic profit-taking and stop-loss

### Phase 7: Testing & Calibration (Week 7-10)
**Tasks**:
- [ ] Paper trade for 1 month (minimum)
- [ ] Log all filter decisions (pass/reject + microstructure scores + reasons)
- [ ] Track false positives (rejected but price moved significantly)
- [ ] Track false negatives (accepted but no price movement)
- [ ] Measure fill quality (slippage, partial fills, time-to-fill)
- [ ] Analyze edge cases (biotech, penny stocks, SPACs, etc.)
- [ ] Calibrate thresholds (adjust percentile requirements if needed)
- [ ] Monthly review: Adjust based on statistics, not assumptions

**Deliverable**: Calibrated system with validated signal quality and observed edge case patterns

---

## Success Metrics

### Signal Quality
- **False Positive Rate**: < 20% (currently ~60-70% with static filters)
- **False Negative Rate**: < 5% (missed genuine catalysts)
- **Capture Rate**: 2-3 additional small/mid-cap trades per week vs. static filters

### Execution Quality
- **Entry Slippage**: < 0.1% for big-cap, < 0.2% for small-cap
- **Exit Slippage**: < 0.15% for big-cap, < 0.3% for small-cap
- **Time-to-Fill**: < 90 seconds for big-cap, < 2 seconds for small-cap

### Profitability
- **Win Rate**: > 50% (vs. current ~40-45%)
- **Average Win**: > $50 per trade
- **Average Loss**: < $20 per trade
- **Sharpe Ratio**: > 1.5 (vs. current ~0.8)

---

## Code Structure

### New Files
```
src/newsflash/services/
├── microstructure_analyzer.py      # Real-time metric computation
├── baseline_calculator.py          # Historical percentile calculation
├── tick_data_collector.py          # Real-time IBKR tick data collection
├── tick_data_storage.py             # Database storage for tick data
└── exit_strategy_manager.py        # Multi-tier exit logic (future)

src/newsflash/models/
└── microstructure_models.py        # Data models for metrics/scores
```

### Modified Files
```
src/newsflash/services/
├── article_processor.py            # Replace static filters with microstructure check
├── ibkr_trading_service.py         # Add VWAP/Adaptive routing, dynamic sizing
└── auto_trade_service.py           # Integrate exit strategy manager
```

---

## Risk Assessment

### High Risk
- **Baseline Calculation**: Need reliable historical data; new symbols have no baseline
- **Premarket Trading**: Illiquid, no reliable baselines; may need separate logic
- **Order Book Depth**: Unreliable signal; may need to weight less heavily

### Medium Risk
- **Time-to-Fill**: Small-cap Adaptive routing may not fill 100% in 1-2 seconds
- **Exit Slippage**: Small-cap exits may experience wider spreads than expected
- **False Positives**: Some microstructure activation may be fake (pump-and-dump)

### Low Risk
- **Big-Cap Execution**: VWAP is well-tested, low risk
- **AI Classification**: Already working well, no changes needed
- **Position Sizing**: Conservative approach (scale down on low confidence)

---

## Conclusion

This strategy represents a **fundamental shift from static, historical filters to dynamic, real-time market structure analysis**. By measuring *changes* in liquidity, volatility, and spreads rather than absolute levels, we can:

1. **Capture more opportunities**: Small/mid-cap catalysts that would be filtered out
2. **Reduce false positives**: Only trade when market actually responds to news
3. **Improve fill quality**: Enter when liquidity is activated, not when it's thin
4. **Maintain safety**: Quantitative gates prevent toxic fills on non-moving stocks

### Key Insights

**Why This Works**:
- News + microstructure surge happening simultaneously is rare unless news is a real catalyst
- The odds of a company having news AND surging in microstructure by chance is very low
- Pre-filter removes 40% of noise, microstructure removes another 35%, AI removes another 20%
- Final signal-to-noise ratio should be dramatically improved

**Data is Critical**:
- Granular tick/second data is essential (we catch news in first minute)
- Historical baselines must include all trading hours (premarket, market, post-market)
- Time-of-day bucketing ensures fair comparison (9:30 AM vs. 9:30 AM, not 2:00 PM)
- Storage strategy (PostgreSQL + TimescaleDB) balances performance and reliability

**Implementation Complexity**:
- Data collection and storage is the hardest part
- Baseline calculation requires 20 days of historical data
- Real-time metric computation needs efficient rolling window algorithms
- But once built, the system is self-calibrating and improves over time

**Success Metrics**:
- False positive rate: < 20% (vs. current ~60-70%)
- Capture rate: 2-3 additional small/mid-cap trades per week
- Win rate: > 50% (vs. current ~40-45%)
- Sharpe ratio: > 1.5 (vs. current ~0.8)

**Next Steps**: 
1. Begin Phase 1 (Data Collection Infrastructure)
2. Backfill 20 days of historical data
3. Compute initial baselines
4. Paper trade for 1 month to observe patterns
5. Adjust thresholds based on statistics, not assumptions

**Philosophy**: Let the data speak. Don't preempt edge cases—observe them, log them, and adjust based on what actually happens.

