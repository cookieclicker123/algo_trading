# Surge Detection Timing Analysis

**Date:** 2026-01-13  
**Question:** Why does surge detection take 4 seconds? Can it be parallelized?

---

## Why 4 Seconds?

**The 4-second window is intentional** - it's the "shock window" (business requirement):
- Line 837: `shock_window_seconds = 4.0`
- This is the time window needed to detect a surge (volume, trade count, price movement)
- **This cannot be shortened** - it's a data requirement, not a technical bottleneck

---

## Current Flow Breakdown

### Scenario 1: Article Arrives Early (received_at < event_time + 4s)
1. **NBBO checks** (sequential): ~0.1s per ticker × N tickers
2. **Sector fetches** (sequential): ~0.1s per ticker × N tickers  
3. **Volume analysis** (parallel for all tickers): ~0.3-0.5s per ticker (API calls)
4. **Polling loop**: Polls every 0.1s until 4-second window completes
   - Each poll: `get_stock_trades` + `get_stock_quotes` (~0.2-0.3s per poll)
   - **Total wait time**: Up to 4 seconds (if article arrives immediately)

### Scenario 2: Article Arrives Late (received_at > event_time + 4s)
1. **Catch-up window analysis**: Analyzes entire delay period immediately (~0.3-0.5s)
2. **If surge detected**: Returns immediately (no waiting)
3. **If no surge**: Continues polling from catch-up end to shock_end
4. **Total time**: ~0.5-1.0s (no waiting for window)

---

## Current Bottlenecks

### ✅ Already Parallelized:
1. **Volume analysis for multiple tickers** (line 402-403):
   ```python
   volume_tasks = [fetch_volume_for_ticker(t) for t in tradable_tickers]
   volume_results = await asyncio.gather(*volume_tasks, return_exceptions=True)
   ```

2. **Float shares + prior history** (line 848-852):
   ```python
   float_shares_task = asyncio.create_task(...)
   prior_history_task = asyncio.create_task(...)
   await asyncio.gather(float_shares_task, prior_history_task)
   ```

### ❌ Sequential Bottlenecks:

1. **NBBO Checks (Line 289-313)** - **CRITICAL BOTTLENECK**
   ```python
   for ticker in candidates:
       nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)  # Sequential!
   ```
   **Impact:** ~0.1s per ticker × N tickers = 0.1-0.5s delay
   **Fix:** Parallelize with `asyncio.gather()`

2. **Sector Fetches (Line 357-363)** - **CRITICAL BOTTLENECK**
   ```python
   sector_tasks = [fetch_sector_quick(t) for t in tradable_tickers]
   sector_results = await asyncio.gather(*sector_tasks)  # Already parallel!
   ```
   **Wait:** Actually already parallelized, but happens AFTER NBBO checks

3. **API Latency (Line 207, 219)** - **INHERENT BOTTLENECK**
   ```python
   trades = client.get_stock_trades(trade_request)  # Synchronous HTTP (~0.2-0.3s)
   quotes_data = client.get_stock_quotes(quote_request)  # Synchronous HTTP (~0.2-0.3s)
   ```
   **Impact:** Each poll takes ~0.2-0.5s (API round-trip)
   **Fix:** Already wrapped in `asyncio.to_thread()`, but API latency is inherent

4. **Waiting for 4-Second Window** - **BUSINESS REQUIREMENT**
   - If article arrives early, we MUST wait for 4 seconds of data
   - **Cannot be optimized** - this is the data requirement

---

## Parallelization Opportunities

### 1. **Parallelize NBBO Checks** (Biggest Win)
**Current:** Sequential, ~0.1s per ticker
**After:** Parallel, ~0.1s total (for all tickers)
**Savings:** 0.1-0.5s (depending on number of tickers)

```python
# Current (Sequential):
for ticker in candidates:
    nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)

# Proposed (Parallel):
nbbo_tasks = [self.quote_fetcher.get_nbbo_snapshot(t) for t in candidates]
nbbo_results = await asyncio.gather(*nbbo_tasks, return_exceptions=True)
```

### 2. **Parallelize NBBO + Sector Fetches** (Medium Win)
**Current:** NBBO sequential → Sector parallel
**After:** Both parallel simultaneously
**Savings:** ~0.1-0.2s

### 3. **WebSocket for Real-Time Data** (Already Implemented!)
**Current:** REST API polling (`get_stock_trades`, `get_stock_quotes`)
**After:** WebSocket stream (already implemented in `stream_manager.py`)
**Savings:** ~0.2-0.3s per poll (eliminates API round-trip)

**Status:** WebSocket is implemented but may not be fully utilized for surge detection yet.

### 4. **Early Exit Optimization** (Already Implemented!)
**Current:** Polls every 0.1s, exits immediately on surge
**After:** Already exits immediately (line 996-1000)
**Savings:** Already optimized

---

## Critical Path Analysis

### Current Critical Path (Article Arrives Early):
```
Article Received
  ↓
NBBO Check (Ticker 1): 0.1s
NBBO Check (Ticker 2): 0.1s  ← Sequential!
NBBO Check (Ticker 3): 0.1s  ← Sequential!
  ↓
Sector Fetch (All): 0.1s (parallel) ✅
  ↓
Volume Analysis (All): 0.3-0.5s (parallel) ✅
  ↓
Poll Loop (every 0.1s):
  - API Call: 0.2-0.3s
  - Check surge: <0.01s
  - If surge: EXIT ✅
  - If no surge: Wait 0.1s, repeat
  ↓
Wait for 4-second window: Up to 4s (if no early surge)
  ↓
Total: ~4.0-4.5s
```

### Optimized Critical Path:
```
Article Received
  ↓
NBBO Check (All): 0.1s (parallel) ✅ NEW!
Sector Fetch (All): 0.1s (parallel) ✅
  ↓
Volume Analysis (All): 0.3-0.5s (parallel) ✅
  ↓
Poll Loop (WebSocket data):
  - Check cache: <0.01s (no API call!)
  - Check surge: <0.01s
  - If surge: EXIT ✅
  - If no surge: Wait 0.1s, repeat
  ↓
Wait for 4-second window: Up to 4s (if no early surge)
  ↓
Total: ~4.0-4.2s (saves 0.2-0.3s from WebSocket)
```

---

## Why 4 Seconds Still?

**The 4-second window is NOT a technical bottleneck** - it's a business requirement:
- We need 4 seconds of trading data to detect a surge
- If article arrives early, we MUST wait for 4 seconds to accumulate data
- If article arrives late, we analyze catch-up window immediately (no waiting)

**The actual bottlenecks are:**
1. **Sequential NBBO checks**: 0.1-0.5s (can be parallelized)
2. **API latency**: 0.2-0.3s per poll (can use WebSocket cache)
3. **Waiting for 4-second window**: Up to 4s (cannot be optimized - data requirement)

---

## Recommendations

### High Priority (Easy Wins):
1. **Parallelize NBBO checks** (line 289-313)
   - **Impact:** 0.1-0.5s savings
   - **Effort:** Low (change sequential loop to `asyncio.gather`)
   - **Risk:** Low

2. **Use WebSocket cache for surge detection**
   - **Impact:** 0.2-0.3s per poll (eliminates API calls)
   - **Effort:** Medium (integrate WebSocket cache into volume analysis)
   - **Risk:** Medium (need to ensure cache is populated)

### Medium Priority:
3. **Parallelize NBBO + Sector fetches**
   - **Impact:** 0.1-0.2s savings
   - **Effort:** Low
   - **Risk:** Low

### Low Priority (Cannot Optimize):
4. **4-second window wait**
   - **Impact:** N/A (business requirement)
   - **Effort:** N/A
   - **Risk:** N/A

---

## Expected Improvements

**Current:** ~4.0-4.5s (article arrives early, no surge)
**After Parallelization:** ~3.5-4.0s (saves 0.2-0.5s from NBBO parallelization)
**After WebSocket Integration:** ~3.2-3.7s (saves additional 0.2-0.3s from cache)

**Best Case (Article Arrives Late, Surge Detected):**
- **Current:** ~0.5-1.0s
- **After:** ~0.3-0.8s (saves 0.2s from parallelization)

---

## Conclusion

**The 4-second delay is mostly from:**
1. **Business requirement** (need 4 seconds of data) - **Cannot optimize**
2. **Sequential NBBO checks** - **Can optimize** (parallelize)
3. **API latency** - **Can optimize** (use WebSocket cache)

**At scale, parallelization will help:**
- Multiple tickers: Already parallelized ✅
- NBBO checks: Can be parallelized ❌ (currently sequential)
- WebSocket cache: Can be used ❌ (currently uses REST API)

**The biggest win:** Parallelize NBBO checks (easy, low risk, saves 0.1-0.5s)
