# Load Scaling Analysis: Surge Detection & Trade Placement Under Massive Load

**Date:** 2026-01-12  
**Goal:** Analyze whether surge detection and trade placement happen "very near" surge event time under massive load, and assess parallelization

---

## Architecture Under Load

### Current Parallelization

1. **Article Processing** ✅ **Fully Parallel**
   - Each article gets its own monitoring task via `asyncio.create_task()`
   - All articles processed concurrently (no queuing)
   - **Status:** ✅ Optimal

2. **Volume Analysis** ⚠️ **Limited by Thread Pool**
   - Uses `asyncio.to_thread()` for synchronous HTTP calls
   - Default thread pool: ~32 workers (min(32, cpu_count + 4))
   - **Problem:** Thread pool can saturate under massive load
   - **Impact:** Volume analyses queue up, delaying surge detection

3. **Trade Triggers** ✅ **Fully Parallel**
   - Each surge triggers `asyncio.create_task(_trigger_trade_for_surge())`
   - All trade triggers run concurrently
   - **Status:** ✅ Optimal

4. **Quote Fetches** ❌ **Sequential & Blocking**
   - REST API calls block event loop (no thread pool)
   - Under load: Quote fetches queue up sequentially
   - **Impact:** Trade placement delayed if many surges detected simultaneously

---

## Bottlenecks Under Massive Load

### Scenario: 100 Articles Arrive Simultaneously

**What Happens:**

1. **Article Received** (100 articles)
   - All 100 articles trigger monitoring tasks concurrently ✅
   - All 100 start volume analysis concurrently ✅

2. **Volume Analysis** (100 concurrent analyses)
   - Each analysis uses `asyncio.to_thread()` for HTTP calls
   - **Thread pool:** 32 workers available
   - **First 32 analyses:** Start immediately ✅
   - **Remaining 68 analyses:** Queue up, wait for thread pool workers ⚠️
   - **Delay:** Up to 2.0s per queued analysis (if all workers busy)
   - **Worst case:** Last analysis delayed by ~4-5 seconds (68/32 * 2.0s)

3. **Surge Detection** (when surge occurs)
   - Surge detected in first cycle (0-4s window) - **Near real-time** ✅
   - But if volume analysis is queued, detection delayed by queue time ⚠️

4. **Trade Trigger** (when surge detected)
   - Each surge triggers trade concurrently ✅
   - Quote fetch blocks event loop (0.1-0.3s per trade) ⚠️
   - **If 10 surges detected simultaneously:** Quote fetches queue up
   - **Delay:** 10 trades × 0.3s = **3 seconds** for last trade

5. **Trade Execution** (Alpaca API)
   - Sequential per trade (external API)
   - **Expected:** Some delay under load (unavoidable)

---

## Key Bottlenecks

### 1. Thread Pool Saturation ⚠️ **MODERATE IMPACT**

**Location:** Volume analysis (`asyncio.to_thread()`)

**Problem:**
- Default thread pool: ~32 workers
- Under massive load (100+ articles), volume analyses queue up
- Each analysis takes 0.5-2.0s
- **Delay:** Up to 4-5 seconds for queued analyses

**Current Status:**
- ✅ Already uses thread pool (parallelized)
- ⚠️ Thread pool size is limited (32 workers)
- ❌ No way to increase without custom executor

**Impact on Surge Detection:**
- **First 32 articles:** Near real-time (0-4s)
- **Remaining articles:** Delayed by queue time (4-8s total)

**Is This Optimizable?**
- ❌ Not easily - Python's default thread pool is fixed
- ✅ Could use custom ThreadPoolExecutor with more workers
- ✅ Better: WebSocket (eliminates HTTP calls entirely)

---

### 2. Quote Fetch Blocking ⚠️ **MODERATE IMPACT**

**Location:** `_trigger_trade_for_surge()` → `quote_fetcher.get_nbbo_snapshot()`

**Problem:**
- REST API call blocks event loop (not thread pool)
- If 10 surges detected simultaneously, quote fetches queue up
- Each fetch: 0.1-0.3s
- **Delay:** 0.3s × 10 = 3 seconds for last trade

**Current Status:**
- ❌ Sequential, blocking
- ❌ Not parallelized

**Impact on Trade Placement:**
- **First trade:** 0.1-0.3s
- **Tenth trade (if simultaneous):** 3.0s delay

**Is This Optimizable?**
- ✅ **YES** - WebSocket quotes (instant, no blocking)
- ✅ **YES** - Could use thread pool for quote fetches (quick fix)
- ✅ Better: WebSocket (eliminates blocking entirely)

---

### 3. Trade Execution (Alpaca API) ❌ **UNAVOIDABLE**

**Location:** Brokerage service → Alpaca API

**Problem:**
- External API, sequential per trade
- Cannot be parallelized (Alpaca API limitation)

**Current Status:**
- ❌ Sequential (external API)
- ❌ Unavoidable

**Impact:**
- Trade execution takes 0.3-0.7s per trade
- Under load: Some queuing at Alpaca side (unavoidable)

---

## Parallelization Assessment

| Component | Parallelization | Status | Optimizable? |
|-----------|----------------|--------|--------------|
| Article processing | ✅ Fully parallel (create_task) | Optimal | ❌ Already optimal |
| Volume analysis | ⚠️ Thread pool (32 workers) | Good, but limited | ⚠️ Could increase workers, better: WebSocket |
| Trade triggers | ✅ Fully parallel (create_task) | Optimal | ❌ Already optimal |
| Quote fetches | ❌ Sequential (REST API) | Poor | ✅ YES - WebSocket or thread pool |
| Trade execution | ❌ Sequential (Alpaca API) | Unavoidable | ❌ External API limitation |

---

## Answer: Will Surge Detection Happen "Very Near" Surge Event Time?

### ✅ **YES, with caveats:**

**Under Normal Load (20 articles):**
- ✅ **Near real-time** (0-4s from surge event)
- ✅ All articles processed concurrently
- ✅ Minimal queuing

**Under Massive Load (100+ articles):**
- ⚠️ **Mostly near real-time** (0-4s for first 32 articles)
- ⚠️ **Delayed** (4-8s for remaining articles due to thread pool saturation)
- ⚠️ Trade placement delayed by quote fetch queuing (0.3-3.0s additional)

**Key Insight:**
- Architecture is **mostly parallelized** ✅
- Main bottlenecks: **Thread pool saturation** and **REST API blocking** ⚠️
- **WebSocket integration** would eliminate both bottlenecks ✅

---

## Is Parallelization "As Good As It Can Be"?

### ❌ **NO - There are optimization opportunities:**

1. **Quote Fetches** ❌ **NOT parallelized**
   - Currently: Sequential, blocking REST API
   - Could: Use WebSocket (instant) or thread pool (parallel)
   - **Impact:** 0.3-3.0s delay under load

2. **Thread Pool Size** ⚠️ **Limited**
   - Currently: Default (~32 workers)
   - Could: Increase thread pool size or use WebSocket
   - **Impact:** 4-8s delay for queued analyses under massive load

3. **Volume Analysis** ⚠️ **Good, but could be better**
   - Currently: Thread pool (parallelized)
   - Could: WebSocket (eliminates HTTP entirely)
   - **Impact:** Eliminates 0.5-2.0s per analysis

---

## Recommendations

### Current State: ✅ **GOOD** (4.7s average, within target)

**For Normal Load (20-50 articles):**
- ✅ Performance is excellent
- ✅ Near real-time surge detection
- ✅ No optimization needed

**For Massive Load (100+ articles):**
- ⚠️ Thread pool saturation causes delays (4-8s for queued analyses)
- ⚠️ Quote fetch blocking causes delays (0.3-3.0s per trade)
- ✅ **WebSocket integration would eliminate both bottlenecks**

### Next Steps (Optional):

1. **WebSocket Integration** (High impact, high complexity)
   - Eliminates thread pool saturation (no HTTP calls)
   - Eliminates quote fetch blocking (instant quotes)
   - **Savings:** 0.6-2.3s per trade

2. **Increase Thread Pool Size** (Quick fix, moderate impact)
   - Custom ThreadPoolExecutor with more workers
   - Reduces queuing delay under load
   - **Savings:** 2-4s for queued analyses

3. **Thread Pool for Quote Fetches** (Quick fix, small impact)
   - Use `asyncio.to_thread()` for quote fetches
   - Parallelizes quote fetches
   - **Savings:** 0.3-3.0s per trade under load

---

## Conclusion

**Current Architecture:**
- ✅ **Mostly parallelized** - article processing, monitoring, trade triggers all concurrent
- ⚠️ **Thread pool bottleneck** - volume analysis queues under massive load
- ⚠️ **REST API bottleneck** - quote fetches block and queue

**Under Normal Load:** ✅ **Near real-time** (0-4s from surge event)  
**Under Massive Load:** ⚠️ **Mostly near real-time** (0-4s for first batch, 4-8s for queued)

**Parallelization Status:** ⚠️ **Good, but not optimal** - WebSocket integration would eliminate remaining bottlenecks
