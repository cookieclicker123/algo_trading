# Three-Stage Classification Filtering System - Implementation Plan

## Executive Summary

This document outlines a three-stage filtering system to improve trading signal quality by eliminating noise, detecting statistical anomalies, and leveraging AI classification. The goal is to maximize alpha generation while minimizing false positives and spread losses.

**Core Hypothesis:** `Hard Filters + Statistical Anomaly Detection + AI Classification = High-Quality Trading Signals`

---

## System Architecture Overview

```
Article Received
      ↓
[Stage 1: Hard Filters] ──→ REJECT → Log & Exit
      ↓ PASS
[Stage 2: Statistical Filter] ──→ REJECT → Log & Exit
      ↓ PASS
[Stage 3: AI Classification] ──→ REJECT → Log & Exit
      ↓ PASS (IMMINENT)
Trade Execution Trigger
```

**Key Principle:** Each stage eliminates progressively more sophisticated forms of noise, ensuring only the highest-quality signals reach trading execution.

---

## Stage 1: Hard Filters (Pre-Classification)

### Purpose
Eliminate obvious noise and illiquid instruments **before** expending computational resources on classification and statistical analysis.

### Implementation Status
✅ **Ready to implement immediately** - Simple, deterministic rules

### Filter Criteria

#### 1. Exchange Filter
**Rule:** Only NASDAQ and NYSE stocks
- **Rationale:** Higher liquidity, better price discovery, fewer manipulation issues
- **Data Source:** Symbol metadata (exchange code)
- **Implementation:** Symbol lookup table or API call
- **Exception Handling:** If exchange unknown, default to REJECT for safety

**Pseudocode:**
```python
ALLOWED_EXCHANGES = {"NASDAQ", "NYSE"}
if ticker_exchange not in ALLOWED_EXCHANGES:
    return REJECT("Exchange not allowed")
```

#### 2. Price Filter (Penny Stock Exclusion)
**Rule:** Minimum stock price threshold
- **Options:**
  - **Conservative:** $5.00 minimum (recommended for initial implementation)
  - **Moderate:** $3.00 minimum
  - **Aggressive:** $1.00 minimum (riskier, may include some penny stocks)
- **Rationale:** Penny stocks have wider spreads, higher manipulation risk, lower liquidity
- **Data Source:** Real-time price from quote provider (Alpaca/Polygon)
- **Fallback:** If price unavailable, REJECT for safety

**Recommendation:** Start with **$5.00 minimum** to avoid all penny stock classifications. Can adjust based on empirical results.

**Pseudocode:**
```python
MIN_PRICE = 5.00  # Configurable
if current_price < MIN_PRICE:
    return REJECT("Price below minimum threshold")
```

#### 3. Market Cap Filter
**Rule:** Minimum market capitalization
- **Options:**
  - **Conservative:** $500M minimum (mid-cap+)
  - **Moderate:** $300M minimum
  - **Aggressive:** $100M minimum
- **Rationale:** Larger companies have better liquidity, more institutional interest, less manipulation
- **Data Source:** Symbol metadata or real-time calculation (shares outstanding × price)
- **Fallback:** If market cap unavailable, check daily volume as proxy

**Recommendation:** Start with **$300M minimum** for balance between coverage and quality.

**Pseudocode:**
```python
MIN_MARKET_CAP = 300_000_000  # $300M
if market_cap < MIN_MARKET_CAP:
    return REJECT("Market cap below minimum")
```

#### 4. Daily Volume Filter
**Rule:** Minimum average daily volume (rolling 20-day average)
- **Options:**
  - **Conservative:** 1M shares/day
  - **Moderate:** 500k shares/day (recommended)
  - **Aggressive:** 250k shares/day
- **Rationale:** Ensures liquidity for entry/exit without significant slippage
- **Data Source:** Historical volume data (Polygon/massive.com)
- **Cache:** Calculate once per day, cache results

**Recommendation:** Start with **500k shares/day** average.

**Pseudocode:**
```python
MIN_AVG_VOLUME = 500_000  # 500k shares/day
avg_volume = calculate_20_day_avg_volume(ticker)
if avg_volume < MIN_AVG_VOLUME:
    return REJECT("Insufficient average volume")
```

#### 5. Float Filter
**Rule:** Minimum shares outstanding (public float)
- **Options:**
  - **Conservative:** 50M shares
  - **Moderate:** 30M shares (recommended)
  - **Aggressive:** 20M shares
- **Rationale:** Low-float stocks are more easily manipulated, have wider spreads
- **Data Source:** Symbol metadata or fundamental data
- **Fallback:** If float unavailable, use market cap as proxy

**Recommendation:** Start with **30M shares minimum**.

**Pseudocode:**
```python
MIN_FLOAT = 30_000_000  # 30M shares
if shares_outstanding < MIN_FLOAT:
    return REJECT("Float too low")
```

#### 6. Ticker Validation
**Rule:** Must have at least one valid ticker symbol
- **Rationale:** Can't trade without a ticker
- **Implementation:** Check article.tickers is non-empty after normalization

**Pseudocode:**
```python
if not article.tickers or len(article.tickers) == 0:
    return REJECT("No tickers found")
```

### Stage 1 Implementation Strategy

**Data Requirements:**
- Symbol metadata service (exchange, market cap, float, daily volume)
- Real-time price feed (for price filter)
- Historical volume data (for average volume calculation)

**Data Provider Options:**
1. **Alpaca** (already integrated)
   - Pros: Free, real-time prices
   - Cons: Limited fundamental data, may need secondary source
2. **Polygon.io / massive.com**
   - Pros: Comprehensive fundamental data, historical volumes
   - Cons: Additional API cost, may require subscription tier
3. **Yahoo Finance** (via yfinance library)
   - Pros: Free, comprehensive data
   - Cons: Rate limits, less reliable for real-time
4. **Combination Approach** (recommended)
   - Real-time price: Alpaca (already available)
   - Fundamental data: Polygon.io / massive.com or yfinance
   - Cache metadata to reduce API calls

**Caching Strategy:**
- Cache symbol metadata (exchange, market cap, float) with 24-hour TTL
- Cache 20-day average volume with daily refresh (calculated at market open)
- Invalidate cache on corporate actions (splits, mergers)

**Performance Considerations:**
- Parallel API calls for all metadata lookups
- Timeout: 500ms per lookup, fail gracefully (REJECT if timeout)
- Batch lookups when possible (if multiple tickers in article)

**Implementation Location:**
- **New Service:** `HardFilterService` in `src/newsflash/services/filtering/`
- **Integration Point:** Between `ArticleReceived` event and `ClassificationRequested` event
- **Event Flow:**
  ```
  ARTICLE_RECEIVED → HardFilterService → (PASS) → CLASSIFICATION_REQUESTED
                                   → (REJECT) → ARTICLE_FILTERED (new event)
  ```

---

## Stage 2: Statistical Anomaly Detection

### Purpose
Detect genuine liquidity/microstructure anomalies that indicate market reaction to news, filtering out false signals even from "liquid" stocks.

### Implementation Status
⏳ **Requires careful planning** - Complex statistical analysis with real-time data

### Core Concept

**The Hypothesis:** When news breaks, the market's microstructure changes immediately:
- **Volume** spikes as market participants react
- **Volatility** increases as price discovery occurs rapidly
- **Order book depth** shifts as liquidity providers adjust
- **Price movement** accelerates

These changes are **anomalous** compared to the ticker's normal behavior at that time of day.

### Statistical Approach

#### Time-of-Day Normalization
**Critical:** Compare current period to **same time period** in historical data.

**Why This Matters:**
- Market behavior varies dramatically by time (premarket vs market hours)
- Volume patterns differ by minute (9:30 AM vs 2:30 PM)
- Comparing 9:30 AM today to 2:30 PM yesterday is meaningless

**Time Granularity Options:**
1. **Per-minute** (recommended for initial implementation)
   - Most granular, best signal quality
   - Requires more data storage/computation
   - Example: Compare 9:30:00-9:30:59 today vs same minute window historically

2. **Per-30-second** (more precise but computationally expensive)
   - Best signal quality
   - Requires significant data infrastructure
   - May not be necessary if per-minute works

3. **Per-5-minute** (less precise but simpler)
   - Easier to implement
   - May miss short-lived spikes
   - Lower signal quality

**Recommendation:** Start with **per-minute** granularity. Can refine later.

#### Rolling Window Analysis

**Window Size:**
- **20 trading days** (recommended)
  - Captures recent market behavior
  - Excludes old data that may not reflect current market structure
  - Balances signal quality vs data requirements

**Calculation Method:**
For each ticker, for each minute of the trading day:
1. Collect volume, volatility, order book depth for that minute across last 20 trading days
2. Calculate percentiles (50th, 75th, 90th, 95th, 99th)
3. When news arrives, compare current minute's metrics to historical distribution

**On-Demand Calculation Pseudocode:**
```python
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta
import numpy as np

async def calculate_percentile_for_minute_on_demand(
    ticker: str,
    current_time: datetime,
    metric: str,
    market_data_client: StockHistoricalDataClient,
    cache: dict,
    window_days: int = 20
) -> Optional[float]:
    """
    Calculate percentile on-demand: fetch historical data only when needed.
    
    Args:
        ticker: Stock symbol
        current_time: Current datetime (includes minute)
        metric: "volume", "volatility"
        market_data_client: Alpaca StockHistoricalDataClient
        cache: In-memory cache dict with TTL
        window_days: Number of trading days to look back
    
    Returns:
        Percentile (0-100) of current value vs historical distribution
    """
    # Check cache first
    minute_of_day = current_time.time().replace(second=0, microsecond=0)
    cache_key = f"{ticker}:{minute_of_day.strftime('%H:%M')}:{metric}"
    
    if cache_key in cache:
        cached_value, expiry = cache[cache_key]
        if datetime.now() < expiry:
            return cached_value  # Cache hit!
    
    # Cache miss - fetch historical data on-demand
    try:
        # Fetch 25 days to ensure we get 20 trading days
        start_date = current_time - timedelta(days=25)
        
        request = StockBarsRequest(
            symbol_or_symbols=[ticker],
            timeframe=TimeFrame.Minute,
            start=start_date,
            end=current_time
        )
        
        bars = market_data_client.get_stock_bars(request)
        
        if not bars or ticker not in bars:
            return None  # No data available
        
        # Extract time-of-day component (hour:minute)
        target_time = current_time.time().replace(second=0, microsecond=0)
        
        # Filter bars to same minute-of-day across all days
        historical_values = []
        for bar in bars[ticker]:
            bar_time = bar.timestamp.time().replace(second=0, microsecond=0)
            
            # Skip current minute (avoid look-ahead bias)
            if bar.timestamp.date() == current_time.date() and bar_time == target_time:
                continue
            
            # Only include bars from same minute-of-day
            if bar_time == target_time:
                if metric == "volume":
                    historical_values.append(bar.volume)
                elif metric == "volatility":
                    # Calculate volatility as high-low percentage spread
                    if bar.high > 0:
                        volatility = (bar.high - bar.low) / bar.high * 100
                        historical_values.append(volatility)
        
        if len(historical_values) < 10:  # Need at least 10 samples
            return None  # Insufficient historical data
        
        # Get current minute's metric value
        current_value = None
        if metric == "volume":
            # Get latest bar for current minute
            current_bar = get_latest_bar_for_minute(bars[ticker], current_time)
            if current_bar:
                current_value = current_bar.volume
        elif metric == "volatility":
            current_bar = get_latest_bar_for_minute(bars[ticker], current_time)
            if current_bar and current_bar.high > 0:
                current_value = (current_bar.high - current_bar.low) / current_bar.high * 100
        
        if current_value is None:
            return None
        
        # Calculate percentile
        percentile = calculate_percentile_rank(current_value, historical_values)
        
        # Cache result (TTL: 10 minutes)
        cache[cache_key] = (percentile, datetime.now() + timedelta(minutes=10))
        
        return percentile
        
    except Exception as e:
        logger.error(f"Error calculating percentile on-demand: {e}", ticker=ticker)
        return None


def calculate_percentile_rank(value: float, historical_values: list[float]) -> float:
    """
    Calculate percentile rank of value in historical distribution.
    
    Args:
        value: Current value
        historical_values: List of historical values
    
    Returns:
        Percentile (0-100)
    """
    if not historical_values:
        return None
    
    sorted_values = sorted(historical_values)
    rank = sum(1 for v in sorted_values if v < value)
    percentile = (rank / len(sorted_values)) * 100
    return percentile
```

#### Metrics to Track

##### 1. Volume (Primary Signal)
**What:** Total shares traded in the current minute
**Why:** News causes immediate trading activity
**Calculation:**
```python
volume = sum(transactions in minute)
volume_percentile = calculate_percentile_for_minute(ticker, now, "volume")
```

**Threshold:** 95th percentile or above (recommended start)
- Can adjust to 99th percentile for stricter filtering
- Consider using 90th percentile if too many false negatives

##### 2. Volatility (Secondary Signal)
**What:** Price volatility in current minute (standard deviation of prices or high-low spread)
**Why:** News causes rapid price discovery
**Calculation:**
```python
prices_in_minute = [tick.price for tick in minute_ticks]
volatility = np.std(prices_in_minute)
# OR
volatility = (high_price - low_price) / high_price  # Percentage spread
volatility_percentile = calculate_percentile_for_minute(ticker, now, "volatility")
```

**Threshold:** 95th percentile or above

##### 3. Order Book Depth (Tertiary Signal) - **OPTIONAL**
**What:** Total liquidity available at best bid/ask levels
**Why:** News causes liquidity providers to adjust, order book shifts
**Calculation:**
```python
# Level 2 order book depth (if available)
order_book_depth = sum([bid_qty for bid_qty, _ in best_n_levels]) + \
                   sum([ask_qty for _, ask_qty in best_n_levels])
depth_percentile = calculate_percentile_for_minute(ticker, now, "order_book_depth")

# OR: Use Level 1 bid/ask quantities as proxy (Alpaca provides this)
# Get from latest quote
quote = await market_data_client.get_stock_latest_quote(...)
bid_size = quote.bid_size  # Size at best bid
ask_size = quote.ask_size  # Size at best ask
depth_proxy = bid_size + ask_size  # Simple proxy for depth
```

**Threshold:** 90th percentile or above (more variable, so lower threshold)

**Availability:**
- ❌ **Alpaca:** Level 2 depth NOT available for stocks (only Level 1 best bid/ask)
- ✅ **Polygon.io:** Level 2 depth available ($199/month)
- ✅ **Workaround:** Use bid/ask size (Level 1) as depth proxy - may be sufficient

**Recommendation:** Start without order book depth metric. Spread + Volume + Volatility should be sufficient. Add depth later if needed.

##### 4. Bid-Ask Spread (Secondary Signal)
**What:** Percentage spread between best bid and ask
**Why:** Wide spreads indicate illiquidity or uncertainty
**Calculation:**
```python
spread_pct = (ask_price - bid_price) / mid_price * 100
spread_percentile = calculate_percentile_for_minute(ticker, now, "spread")
```

**Threshold:** Must be **below** 90th percentile (narrow spreads = good liquidity)
- This is a **negative signal** - reject if spread too wide

#### Combined Signal Logic

**Decision Rules:**
```python
def passes_statistical_filter(volume_pct, volatility_pct, spread_pct, depth_pct=None):
    """
    Determine if ticker passes statistical anomaly filter.
    
    Returns:
        Tuple of (passed: bool, reason: str)
    """
    # Required: Volume must be elevated
    if volume_pct < 95:
        return False, "Volume not elevated enough"
    
    # Required: Volatility must be elevated
    if volatility_pct < 95:
        return False, "Volatility not elevated enough"
    
    # Optional: Order book depth anomaly (if Level 2 data available)
    # Note: Alpaca doesn't provide Level 2 for stocks, so this will typically be None
    if depth_pct is not None and depth_pct < 90:
        # Not a hard requirement, but preferred if available
        pass
    
    # Required: Spread must be reasonable (not too wide)
    # This is the key liquidity signal - works with Level 1 data (Alpaca)
    if spread_pct > 90:  # Spread is in 90th percentile (wide)
        return False, "Spread too wide (illiquid)"
    
    return True, "All statistical checks passed"
```

**Note:** Order book depth is **optional** - Volume + Volatility + Spread are the core signals. All available via Alpaca Level 1 data.

**Alternative: Weighted Scoring**
```python
def calculate_anomaly_score(volume_pct, volatility_pct, spread_pct, depth_pct=None):
    """
    Calculate weighted anomaly score.
    
    Returns:
        Score 0-100, where 70+ indicates strong anomaly
    """
    # Positive signals (higher is better)
    volume_score = min(volume_pct / 100, 1.0) * 0.4
    volatility_score = min(volatility_pct / 100, 1.0) * 0.3
    
    # Optional depth signal
    depth_score = 0.0
    if depth_pct is not None:
        depth_score = min(depth_pct / 100, 1.0) * 0.1
    
    # Negative signal (lower is better)
    spread_penalty = max(0, (spread_pct - 50) / 50) * 0.2  # Penalty for wide spreads
    
    total_score = (volume_score + volatility_score + depth_score) * 100 - spread_penalty * 100
    return max(0, min(100, total_score))

# Threshold
if calculate_anomaly_score(...) >= 70:
    return PASS
```

**Recommendation:** Start with **simple boolean logic** (first approach). Add weighted scoring after validating concept with data.

### Data Requirements

#### Real-Time Data Needs
1. **Tick-level data** (or aggregated to 1-minute bars)
   - Volume per minute
   - Price ticks per minute (for volatility)
   - Bid/ask quotes per minute (for spread)
   - Order book snapshots (for depth) - if available

2. **Historical Data Storage**
   - Store 20+ days of minute-level data per ticker
   - Efficient storage format (Parquet, compressed JSON)
   - Fast lookup by (ticker, time_of_day)

#### Data Provider Options

##### Option 1: Alpaca (Already Integrated)

**Free Tier Capabilities:**
- ✅ Historical bar data (7+ years available)
- ✅ Minute-level aggregates (volume, OHLC, volatility can be calculated)
- ✅ Already integrated in codebase (`StockHistoricalDataClient`)
- ⚠️ **IEX exchange only** (2-3% of market volume)
- ⚠️ Real-time data delayed by 15 minutes
- ⚠️ 200 API calls per minute limit

**Alpaca Algo Trader Plus ($99/month) - RECOMMENDED**
- ✅ **Real-time data** (full market coverage, all U.S. exchanges)
- ✅ **Historical data** (7+ years)
- ✅ **Unlimited API calls** (10,000 per minute - more than sufficient)
- ✅ **WebSocket access** (unlimited symbols)
- ✅ **Real-time quotes** (bid/ask for spread calculation)
- ✅ **Real-time minute bars** (volume, OHLC for volatility calculation)
- ❌ **Level 2 order book depth NOT available for stocks** (only Level 1 - best bid/ask)
  - Note: Level 2 available for crypto, but not stocks
  - For stocks: Only best bid/ask (Level 1) available

**API Usage for Statistical Filter:**
```python
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from datetime import timedelta

# Get 20 days of minute bars for same time-of-day comparison
# Batch multiple tickers in one request
request = StockBarsRequest(
    symbol_or_symbols=["AAPL", "MSFT", "TSLA"],  # Batch multiple tickers
    timeframe=TimeFrame.Minute,
    start=datetime.now() - timedelta(days=25),  # Extra days for trading days
    end=datetime.now()
)
bars = market_data_client.get_stock_bars(request)  # Single API call for multiple tickers!

# Get real-time quotes for spread calculation
quote_request = StockLatestQuoteRequest(symbol_or_symbols=["AAPL", "MSFT", "TSLA"])
quotes = market_data_client.get_stock_latest_quote(quote_request)  # Single API call!
```

**Rate Limits:**
- 10,000 API calls per minute (Algo Trader Plus)
- Batch requests: Multiple tickers in single API call
- Your usage: ~100 calls/second max = 6,000/minute (well within limits)
- Example: 5 tickers × 20 days = can batch all 5 tickers in 1 API call!

**Missing Feature: Order Book Depth**
- Alpaca does NOT provide Level 2 order book depth for stocks
- Only provides Level 1 (best bid/ask)
- **Workaround:** Use bid/ask spread as liquidity proxy (already in plan)
- **Impact:** Minimal - spread metric already captures liquidity signal

##### Option 2: Polygon.io / massive.com

**Real-Time Plan: $199/month**
- ✅ Real-time data (full market coverage)
- ✅ Historical data (15 years - longer than Alpaca)
- ✅ **Level 2 order book depth** (full order book with multiple levels)
- ✅ Unlimited API calls
- ✅ WebSocket access
- ❌ **$100/month more expensive** than Alpaca

**Key Difference:**
- Polygon.io provides **Level 2 order book depth** (multiple bid/ask levels)
- Alpaca only provides **Level 1** (best bid/ask) for stocks

##### Recommendation: Alpaca Algo Trader Plus ($99/month)

**Why Alpaca is Better for Your Use Case:**

1. ✅ **$100/month cheaper** ($99 vs $199)
2. ✅ **Already integrated** - no new dependencies
3. ✅ **Sufficient data for statistical filter:**
   - Volume: ✅ Minute bars
   - Volatility: ✅ Calculate from OHLC (high-low spread)
   - Spread: ✅ Best bid/ask (Level 1) - sufficient for liquidity proxy
   - Order book depth: ❌ Not available, but **not critical**
     - Spread metric already captures liquidity signal
     - Level 1 bid/ask sufficient for your use case
4. ✅ **Better rate limits:** 10,000/minute (vs Polygon.io 5/second)
5. ✅ **Batch requests:** Multiple tickers in single API call
6. ✅ **Your usage pattern fits perfectly:**
   - ~100 calls/second max = 6,000/minute
   - Can batch 5 tickers in 1 API call
   - Well within Alpaca limits

**Order Book Depth Analysis:**
- **Your requirement:** Detect liquidity anomalies
- **Alpaca provides:** Best bid/ask spread (Level 1)
- **Polygon.io provides:** Full order book depth (Level 2)
- **Question:** Is Level 2 order book depth necessary?

**Answer: Probably not for your use case:**
- Spread (best bid/ask) already captures liquidity signal
- Volume spike + volatility spike + narrow spread = strong signal
- Order book depth is "nice to have" but not critical
- Can validate with Level 1 data first, upgrade later if needed

**Validation Strategy:**
1. Implement Stage 2 with Alpaca Algo Trader Plus ($99/month)
2. Validate that Level 1 data (bid/ask spread) is sufficient
3. Monitor signal quality and win rate
4. Only upgrade to Polygon.io if Level 2 depth proves necessary

#### Data Pipeline Architecture: **On-Demand Calculation (Recommended)**

**Why On-Demand is Better:**
- ✅ **No massive data storage** - Only fetch when needed
- ✅ **Cost efficient** - Only API calls for articles that pass Stage 1
- ✅ **Simpler infrastructure** - No pre-computation, no database
- ✅ **Cache results** - Avoid redundant calculations

**Implementation:**
```
Article Arrives (Stage 1 PASS) → AI Classification (IMMINENT) → 
  ↓
For each ticker in article:
  - Check cache (TTL: 5-10 minutes)
  - If cached → Use cached percentiles
  - If not cached:
    - Fetch 20 days of minute bars from Alpaca
    - Filter to same time-of-day
    - Calculate percentiles
    - Cache result (TTL: 5-10 minutes)
  ↓
Compare current minute metrics → Decision
```

**Caching Strategy:**
```python
# Simple in-memory cache with TTL
cache_key = f"{ticker}:{minute_of_day}:{date}"
if cache_key in percentile_cache and not expired:
    return cached_percentiles

# Fetch and calculate
percentiles = calculate_percentiles_on_demand(ticker, current_time)
cache[cache_key] = (percentiles, datetime.now() + timedelta(minutes=10))
return percentiles
```

**Performance Considerations:**
- **Parallel API calls:** Fetch multiple tickers concurrently
- **Timeout handling:** 500ms timeout per ticker, fail gracefully
- **Rate limiting:** Batch requests, respect 200 calls/minute limit
- **Cache hit rate:** Expect 80%+ cache hits if multiple articles for same ticker

**Storage Requirements:**
- **None!** Pure on-demand calculation
- In-memory cache only (evicted after TTL)
- Minimal memory footprint

### Implementation Architecture

#### New Service: `StatisticalFilterService`

**Responsibilities:**
- Subscribe to `ClassificationCompleted` events (after Stage 1 passes)
- Fetch real-time tick data for article tickers
- Calculate percentiles vs historical distribution
- Publish `StatisticalFilterResult` event
- Only articles passing both Stage 1 and Stage 2 proceed to trading

**Event Flow:**
```
ARTICLE_RECEIVED
  ↓
[Stage 1: Hard Filters] → PASS → CLASSIFICATION_REQUESTED
  ↓
[Stage 3: AI Classification] → PASS (IMMINENT) → CLASSIFICATION_COMPLETED
  ↓
[Stage 2: Statistical Filter] → PASS → TRADE_EXECUTION_TRIGGER
                            → REJECT → ARTICLE_FILTERED
```

**Wait, that's not right!** Stage 2 should come **before** AI classification to save API costs. Revised flow:

```
ARTICLE_RECEIVED
  ↓
[Stage 1: Hard Filters] → PASS → CLASSIFICATION_REQUESTED
  ↓
[Stage 2: Statistical Filter] → PASS → [Stage 3: AI Classification]
                            → REJECT → ARTICLE_FILTERED
```

Actually, let's reconsider: **Should Stage 2 come before or after AI classification?**

**Option A: Stage 2 Before AI (Save API Costs)**
- Pros: Don't pay for AI classification on articles that fail statistical filter
- Cons: Need tick data immediately, may delay AI classification

**Option B: Stage 2 After AI (Better Signal Quality)**
- Pros: Only do expensive statistical analysis on articles AI deems "imminent"
- Cons: Pay for AI on articles that may fail statistical filter

**Recommendation:** **Stage 2 After AI Classification (Option B)**
- Rationale: AI classification is cheap (Groq API is fast and inexpensive)
- Statistical analysis requires real-time data fetch, which may have rate limits
- Better to narrow down with AI first, then do expensive statistical checks only on high-quality candidates
- Final decision: Only trade if **both** AI says "imminent" **and** statistics show anomaly

**Final Event Flow:**
```
ARTICLE_RECEIVED
  ↓
[Stage 1: Hard Filters] → PASS → CLASSIFICATION_REQUESTED
  ↓
[Stage 3: AI Classification] → IMMINENT → [Stage 2: Statistical Filter]
                            → IGNORE → STOP
  ↓
[Stage 2: Statistical Filter] → PASS → TRADE_EXECUTION_TRIGGER
                            → REJECT → ARTICLE_FILTERED (but AI classified as imminent)
```

### Market Universe

**On-Demand Approach (No Pre-Computation)**

With on-demand percentile calculation, we don't need to define a "universe" upfront:

✅ **Lazy Loading:** Only calculate percentiles when article arrives for that ticker
✅ **No storage overhead:** Don't track stocks that never get news
✅ **Natural filtering:** Only stocks with news articles get analyzed
✅ **Stage 1 filter integration:** Only stocks passing hard filters reach Stage 2

**Universe = All Stocks That:**
1. Pass Stage 1 hard filters (exchange, price, market cap, volume, float)
2. Have articles associated with them
3. Have sufficient historical data (20+ trading days)

**No need to pre-compute or track anything!**

### Implementation Phases

#### Phase 2.1: MVP with Alpaca Free Tier (Validation)
**Scope:**
- On-demand percentile calculation (volume only)
- Per-minute time-of-day normalization
- 20-day rolling window
- Alpaca free tier integration (`StockHistoricalDataClient`)
- In-memory cache (10-minute TTL)
- Boolean pass/fail logic

**Timeline:** 1-2 weeks
**Data Needs:** Alpaca free tier (already integrated)
**Cost:** $0/month

**Purpose:** Validate concept with IEX data, test infrastructure

#### Phase 2.2: Upgrade to Alpaca Algo Trader Plus
**Scope:**
- Upgrade to Alpaca Algo Trader Plus ($99/month)
- Full market coverage (all exchanges)
- Real-time data (no 15-minute delay)
- Add volatility percentile calculation from bars (high-low spread)
- Add spread filtering (from real-time quotes)
- Batch API calls (multiple tickers per request)
- Cache optimization

**Timeline:** Immediately after Phase 2.1 validation
**Data Needs:** Alpaca Algo Trader Plus ($99/month)
**Cost:** $99/month

**Why upgrade immediately:**
- IEX data (2-3% of volume) insufficient for real statistical analysis
- Need full market coverage for accurate percentiles
- Real-time data required for production
- Already $100/month cheaper than Polygon.io

#### Phase 2.3: Enhanced Metrics
**Scope:**
- Weighted scoring system
- Optimize batch requests (5 tickers per API call)
- Performance optimization
- Advanced caching strategies

**Timeline:** 1 week after Phase 2.2
**Data Needs:** Alpaca Algo Trader Plus (already have)

#### Phase 2.4: Optional - Level 2 Order Book Depth (Only if needed)
**Scope:**
- Evaluate if Level 1 spread is sufficient
- If Level 2 depth proves necessary → Upgrade to Polygon.io ($199/month)
- Implement depth-based anomaly detection

**Timeline:** After Phase 2.3 validation (2-4 weeks)
**Decision Point:** Only if Level 1 data insufficient

**Recommendation:** Start with Alpaca, only upgrade to Polygon.io if empirical data shows Level 2 depth significantly improves signal quality.

---

## Stage 3: AI Classification

### Status
✅ **Already implemented** - No changes needed

### Current Implementation
- Groq API (Llama 3.3 70B)
- Binary classification: IMMINENT vs IGNORE
- Confidence scoring
- Reasoning extraction

### Integration Point
- Stage 3 runs after Stage 1 (hard filters)
- Stage 2 (statistical) runs after Stage 3
- Only IMMINENT + Statistical PASS = Trade signal

---

## Implementation Priority & Timeline

### Immediate (Week 1): Stage 1 Hard Filters
**Priority:** HIGH - Can be implemented immediately, significant value
**Tasks:**
1. Create `HardFilterService`
2. Integrate symbol metadata lookup (Polygon/yfinance)
3. Implement all 6 filter criteria
4. Add caching layer
5. Integrate into event flow (before classification)
6. Add metrics/logging

**Expected Impact:**
- Reduce classification API calls by 40-60%
- Eliminate obvious losers (penny stocks, illiquid)
- Improve win rate baseline

### Short-Term (Weeks 2-4): Stage 2 MVP
**Priority:** HIGH - Core differentiator
**Tasks:**
1. Implement basic percentile calculation with Alpaca free tier (validate concept)
2. Upgrade to Alpaca Algo Trader Plus ($99/month)
3. Add volatility and spread metrics
4. Implement batch API calls (5 tickers per request)
5. Create data fetching service
6. Integrate statistical filter into event flow
7. Add logging/metrics

**Expected Impact:**
- Filter out false positives from manipulation
- Improve win rate by 5-10%
- Reduce spread losses
- Cost: $99/month (vs $199/month for Polygon.io)

### Medium-Term (Weeks 5-8): Stage 2 Enhancement
**Priority:** MEDIUM - Optimization
**Tasks:**
1. Implement weighted scoring system
2. Optimize batch requests and caching
3. Performance optimization
4. Validate Level 1 data is sufficient (vs Level 2)

**Expected Impact:**
- Further improve signal quality
- Optimize API usage and latency
- Validate cost-effectiveness of Alpaca vs Polygon.io

---

## Risk Mitigation

### Stage 1 Risks
1. **Missing Valid Tickers**
   - Risk: Overly strict filters reject good trades
   - Mitigation: Start conservative, monitor rejection rate, adjust thresholds

2. **Data Availability**
   - Risk: Metadata unavailable, defaulting to reject
   - Mitigation: Fallback logic, cache aggressively, monitor data quality

### Stage 2 Risks
1. **API Rate Limits**
   - Risk: Rate limits may be hit during high-volume news periods
   - Mitigation: 
     - **Alpaca Algo Trader Plus:** 10,000 calls/minute (more than sufficient)
     - Batch requests: 5 tickers per API call = ~20 calls per article batch
     - Aggressive caching (10-minute TTL) → 80%+ cache hit rate expected
     - Your usage: 100 calls/second max = 6,000/minute (well within limits)
     - Fail gracefully if rate limited (REJECT for safety)

2. **Level 1 vs Level 2 Data**
   - Risk: Alpaca only provides Level 1 (best bid/ask), not full order book depth
   - Mitigation: 
     - Start with Level 1 data - spread metric should be sufficient
     - Validate signal quality with Level 1
     - Only upgrade to Polygon.io if empirical data shows Level 2 necessary
     - Order book depth is optional metric - Volume + Volatility + Spread are core

3. **Look-Ahead Bias**
   - Risk: Using future data in percentile calculation
   - Mitigation: 
     - Strictly exclude current minute from historical comparison
     - Use bars from previous trading days only
     - Validate in backtesting

4. **On-Demand Latency**
   - Risk: API calls add latency to trading decisions
   - Mitigation: 
     - Cache results aggressively (10-minute TTL)
     - Parallel API calls for multiple tickers
     - Timeout handling (500ms per ticker)
     - Expected latency: <1 second with cache, <3 seconds without cache

5. **Market Regime Changes**
   - Risk: Historical percentiles don't reflect current market
   - Mitigation: Adaptive windows, regime detection, threshold adjustment

6. **Data Quality Issues**
   - Risk: Missing bars or incorrect data from Alpaca
   - Mitigation: Validate data, handle missing gracefully, monitor data quality

---

## Success Metrics

### Stage 1 Metrics
- **Rejection Rate:** Target 40-60% of articles rejected
- **False Negative Rate:** <5% (valid trades rejected)
- **API Cost Reduction:** 40-60% reduction in classification calls

### Stage 2 Metrics
- **Anomaly Detection Rate:** % of IMMINENT articles that pass Stage 2
- **Win Rate Improvement:** Target 5-10% improvement vs Stage 1+3 only
- **Spread Loss Reduction:** Target 20-30% reduction in spread losses

### Combined System Metrics
- **Final Win Rate:** Target 60-70% (vs current baseline)
- **Sharpe Ratio:** Target 2.0+ (vs current baseline)
- **False Positive Rate:** Target <20% (vs current baseline)

---

## Next Steps

1. **Review & Approval:** Review this plan, discuss improvements ✅ **IN PROGRESS**
2. **Stage 1 Implementation:** Begin hard filter service
   - Create `HardFilterService`
   - Integrate symbol metadata lookup
   - Test with real articles
3. **Stage 2 MVP Implementation:** 
   - Use Alpaca free tier (already integrated)
   - Implement on-demand percentile calculation
   - Test with real articles
   - Validate IEX data coverage
4. **Stage 2 Validation:**
   - Monitor API usage and cache hit rates
   - Compare IEX vs full market coverage
   - Decide if upgrade needed (Polygon.io or Alpaca Plus)
5. **Backtesting Framework:** Prepare for validation testing

---

## Open Questions for Discussion

1. **Threshold Values:** Are the recommended thresholds ($5 price, $300M market cap, etc.) appropriate?
2. **Stage 2 Order:** ✅ **DECIDED** - Statistical filter comes after AI classification (save API costs)
3. **Market Universe:** ✅ **DECIDED** - On-demand calculation, no pre-defined universe needed
4. **Time Granularity:** Per-minute vs per-30-second for Stage 2?
5. **Percentile Thresholds:** 95th percentile strict enough, or should we use 99th?
6. **Data Provider:** ✅ **DECIDED** - Alpaca Algo Trader Plus ($99/month) over Polygon.io ($199/month)
   - Provides all required data (volume, volatility, spread)
   - Level 1 bid/ask sufficient (Level 2 depth optional)
   - Better rate limits and already integrated
7. **Cache TTL:** Is 10 minutes appropriate, or should we use shorter/longer?
8. **Level 2 Order Book Depth:** Is Level 1 (best bid/ask spread) sufficient, or do we need full order book depth?
   - **Recommendation:** Start with Level 1, validate signal quality, upgrade to Polygon.io only if necessary

---

---

## Key Decisions Summary

✅ **On-Demand Calculation:** Percentiles calculated only when needed (no pre-computation)
✅ **Alpaca Algo Trader Plus ($99/month):** Recommended over Polygon.io ($199/month)
  - $100/month cheaper
  - Provides all required data (volume, volatility, spread)
  - Level 1 bid/ask sufficient (Level 2 order book depth optional)
  - Better rate limits (10,000/min vs Polygon.io 5/sec)
  - Batch requests supported (5 tickers per API call)
✅ **Stage 2 After AI:** Statistical filter runs after AI classification (AI is cheap, data fetch may have limits)
✅ **Dynamic Universe:** No pre-defined universe - only stocks with news articles get analyzed
✅ **In-Memory Cache:** 10-minute TTL for percentile results (80%+ cache hit rate expected)

## Cost Comparison

| Feature | Alpaca Algo Trader Plus | Polygon.io Real-Time |
|---------|------------------------|---------------------|
| **Cost** | $99/month | $199/month |
| **Real-time data** | ✅ Full market | ✅ Full market |
| **Historical data** | ✅ 7+ years | ✅ 15 years |
| **Volume (minute bars)** | ✅ | ✅ |
| **Volatility (OHLC)** | ✅ | ✅ |
| **Spread (bid/ask)** | ✅ Level 1 | ✅ Level 1 |
| **Order book depth** | ❌ Level 1 only | ✅ Level 2 |
| **API rate limits** | 10,000/min | 5/sec (300/min) |
| **Batch requests** | ✅ Multiple tickers | ✅ Multiple tickers |
| **Already integrated** | ✅ Yes | ❌ No |

**Verdict:** Alpaca provides everything needed for Stage 2 at **50% of the cost**. Level 2 order book depth is optional and likely not necessary (spread metric captures liquidity signal).

---

*Document Status: Draft for Review*
*Last Updated: 2025-12-08*
*Author: System Architecture Planning*
*Revision: Updated for on-demand calculation and Alpaca free tier approach*
