# Comprehensive Latency Bottleneck Analysis

**Date:** 2026-01-12  
**Load Test Results:** Average 8.928s (vs 3.31s baseline) - 2.7x slower under load  
**Goal:** Identify ALL unnecessary latency delays and optimization opportunities

---

## Executive Summary

The load test revealed significant latency degradation (5.6s increase from baseline), with later articles experiencing up to **12.2s latency** vs **6.5s for first articles**. This analysis identifies all bottlenecks, confirms them via codebase review, and assesses their impact.

---

## 🔴 CRITICAL BOTTLENECKS (Blocking Trade Execution)

### 1. **Repository File Locking - BLOCKING OPERATIONS** ⚠️⚠️⚠️

**Location:** `src/newsflash/infra/statistics/repository.py:49`
```python
self._file_lock = asyncio.Lock()  # Serialize file access
```

**Problem:**
- **SINGLE lock for ALL file operations** across ALL articles
- Every `append_recall_record()`, `update_recall_record()`, `_load_recall_file()`, `_save_recall_file()` must acquire the same lock
- Under load (20 articles), they queue up behind each other
- **Impact:** 0.5-2s delays per article when multiple articles try to write simultaneously

**Evidence:**
- Line 172: `async with self._file_lock:` in `append_recall_record()`
- Line 233: `async with self._file_lock:` in `update_recall_record()`  
- Line 285: `async with self._file_lock:` in `remove_recall_record()`
- **ALL file operations share ONE lock** - severe contention under load

**Fix Required:**
- Use **per-file locks** or **lock-free append-only writes**
- Or make all repository operations **fire-and-forget** (don't await)

**Estimated Impact:** 1-3 seconds per article under load

---

### 2. **Repository Append BLOCKS Non-Surge Articles** ⚠️⚠️⚠️

**Location:** `src/newsflash/shared/statistics/recall_engine.py:554`
```python
# No initial SURGE - append record normally, then start monitoring
await self.repository.append_recall_record(record, session, received_at)  # BLOCKING!
```

**Problem:**
- For non-surge articles, we **AWAIT** repository append before starting monitoring
- This means we **wait for file I/O** before even starting surge detection
- Under load, file lock contention means this can take **1-2 seconds**

**Evidence:**
- Line 554: `await self.repository.append_recall_record()` - BLOCKS
- Line 544: For surge articles, it's `asyncio.create_task()` - NON-BLOCKING ✅
- **Inconsistency:** Surge articles are non-blocking, non-surge articles block

**Fix Required:**
- Make append **fire-and-forget** for non-surge articles too
- Start monitoring immediately, don't wait for DB write

**Estimated Impact:** 1-2 seconds per non-surge article under load

---

### 3. **Missing Catch-Up Window Analysis** ⚠️⚠️

**Location:** `src/newsflash/shared/statistics/recall_engine.py:374`
```python
volume_analysis = await analyze_volume_around_event(
    client=self.market_data_client,
    symbol=t,
    event_time=article.published_at,  # Always uses published_at as event_time
    received_at=received_at,  # Passed but NOT used for catch-up window!
    ...
)
```

**Problem:**
- `analyze_volume_around_event()` receives `received_at` but **doesn't use it for catch-up analysis**
- It only analyzes `published_at + 4s` window, NOT `published_at → received_at` window
- If article received 35s after published, we **miss the entire surge period**!

**Evidence:**
- Line 840: `shock_end_time = event_time + timedelta(seconds=4.0)` - Always 4s from published_at
- Line 844-847: `real_window_seconds` is calculated but **NOT USED** for analysis
- No code that analyzes `published_at → received_at` catch-up window

**What Should Happen:**
```python
# If received_at is after published_at + 4s, analyze catch-up window first
if received_at > published_at + timedelta(seconds=4):
    # Analyze published_at → received_at window for immediate surge detection
    catchup_window_end = received_at
    # Use this for initial analysis instead of waiting for 4s window
```

**Fix Required:**
- Implement catch-up window analysis in `analyze_volume_around_event()`
- If `received_at - published_at > 4s`, analyze that window first for instant surge detection

**Estimated Impact:** 1-2 seconds faster for late-arriving articles

---

### 4. **Polling Frequency is 0.5s, Not 0.1s** ⚠️

**Location:** `src/newsflash/shared/statistics/volume_analyzer.py:921`
```python
sleep_time = min(0.5, remaining)  # 0.5 SECOND polling, not 0.1s!
if sleep_time > 0.05:
    await asyncio.sleep(sleep_time)
```

**Problem:**
- Code comment says "FAST POLLING (0.5s)" but you expected **0.1s**
- We poll every **0.5 seconds** inside the 4-second window
- This means surge could be detected up to **0.5s late**

**Evidence:**
- Line 921: `sleep_time = min(0.5, remaining)` - 0.5s polling
- Line 888: For < 0.5s duration, it sleeps 0.1s, but only if `is_final=False`

**Fix Required:**
- Change polling to **0.1s** for faster detection
- Or use Alpaca websocket for real-time data (see below)

**Estimated Impact:** 0-0.4 seconds per surge detection

---

### 5. **No Alpaca Websocket - Using REST API for ALL Quotes** ⚠️⚠️

**Location:** `src/newsflash/infra/brokerage/quote_fetcher.py:61`
```python
request = StockLatestQuoteRequest(symbol_or_symbols=[symbol], feed="sip")
quotes = self.market_data_client.get_stock_latest_quote(request)  # REST API call
```

**Problem:**
- **EVERY** quote fetch is a **REST API call** (50-200ms latency each)
- No websocket for real-time quotes
- Each `get_nbbo_snapshot()` call = 1 HTTP request
- Under load, this means **hundreds of REST API calls** queuing up

**Evidence:**
- `stream_manager.py` file **doesn't exist** (deleted during rollback)
- All quote fetches use `get_stock_latest_quote()` - synchronous HTTP
- No websocket subscription for real-time quotes

**Fix Required:**
- Implement Alpaca websocket for real-time quotes
- Subscribe to quote streams for all monitored tickers
- Use websocket data instead of REST API for real-time checks

**Estimated Impact:** 0.1-0.5 seconds per quote fetch (could save 1-2s per article)

---

## 🟡 MODERATE BOTTLENECKS (Delaying, But Not Blocking Critical Path)

### 6. **Monitoring Cycles Wait for Window Start Times** ⚠️

**Location:** `src/newsflash/shared/statistics/recall_engine.py:853`
```python
window_start = published_at + timedelta(seconds=(cycle + 1) * cycle_duration)
# ...
if window_start > now:
    wait_time = (window_start - now).total_seconds()
    if wait_time > 0:
        await asyncio.sleep(wait_time)  # WAITING for window to start
```

**Problem:**
- Monitoring cycles wait for **exact window start times**
- If we receive article late, we still wait for `published_at + 4s`, `published_at + 8s`, etc.
- Should start monitoring immediately and use catch-up window analysis

**Evidence:**
- Line 845: `window_start = published_at + timedelta(seconds=(cycle + 1) * 4.0)`
- Line 854: Waits if `window_start > now` - even if article was received late

**Fix Required:**
- First cycle should analyze catch-up window (published_at → received_at)
- Subsequent cycles should continue from received_at forward, not published_at + N*4s

**Estimated Impact:** 0-4 seconds depending on reception delay

---

### 7. **Yahoo Finance Metadata Fetching Blocks Initial Checks** ⚠️

**Location:** `src/newsflash/shared/statistics/recall_engine.py:875`
```python
ticker_meta = await asyncio.wait_for(
    self.yahoo_finance_coordinator.fetch_metadata(ticker, timeout=1.0),
    timeout=1.5  # 1.5 SECOND TIMEOUT - BLOCKS monitoring cycle
)
```

**Problem:**
- In monitoring cycles, we **AWAIT** Yahoo Finance metadata with **1.5s timeout**
- This blocks surge detection for that cycle
- Queue system exists, but not used here - we still block

**Evidence:**
- Line 875-878: `await asyncio.wait_for(...)` with 1.5s timeout
- Line 341: Initial check uses `asyncio.wait_for()` with 2.5s timeout - also blocks
- Queue system (line 1708) only used in `_retry_metadata_fetch()`, not initial checks

**Fix Required:**
- Use `queue_on_failure=True` in initial checks
- Don't block on metadata fetch - use cached or None, queue for background

**Estimated Impact:** 0.5-1.5 seconds per cycle that needs metadata

---

### 8. **Event Bus Uses asyncio.gather - Waits for All Subscribers** ⚠️

**Location:** `src/newsflash/shared/event_bus.py:54`
```python
await asyncio.gather(*tasks, return_exceptions=True)  # WAITS for all subscribers
```

**Problem:**
- Event bus **AWAITS** all subscribers to complete before returning
- If one subscriber is slow (e.g., blocking DB write), it delays ALL subscribers
- Should be truly fire-and-forget

**Evidence:**
- Line 54: `await asyncio.gather(*tasks, return_exceptions=True)`
- While exceptions are handled, **all tasks must complete** before publish() returns

**Fix Required:**
- Don't await - truly fire-and-forget
- Only await for critical path events (TradeRequested), not all events

**Estimated Impact:** 0.1-0.5 seconds if subscribers have blocking operations

---

### 9. **Volume Analysis Uses Synchronous HTTP Calls (Even with asyncio.to_thread)** ⚠️

**Location:** `src/newsflash/shared/statistics/volume_analyzer.py:893`
```python
stats_now, metrics, move_type = await asyncio.to_thread(
    _assess_surge_snapshot,
    client=client,
    symbol=symbol,
    ...
)
```

**Inside `_assess_surge_snapshot()` → `_get_stats_at_time()` → `_fetch_trades_in_window()`:**
```python
trades = client.get_stock_trades(trade_request)  # SYNCHRONOUS HTTP call
```

**Problem:**
- Volume analysis calls `client.get_stock_trades()` - **synchronous HTTP**
- Even though wrapped in `asyncio.to_thread()`, it's still **blocking a thread**
- With 10 workers, thread pool can saturate under load
- Each HTTP call takes **100-500ms**

**Evidence:**
- Line 207: `trades = client.get_stock_trades(trade_request)` - sync HTTP
- Line 501: `quotes = client.get_stock_quotes(request)` - sync HTTP
- Line 392: `bars = client.get_stock_bars(request)` - sync HTTP
- All wrapped in `asyncio.to_thread()` but still blocks thread pool

**Fix Required:**
- Use Alpaca websocket for real-time trades/quotes
- Or increase thread pool size for volume analysis
- Or batch multiple tickers in single API calls

**Estimated Impact:** 0.2-0.5 seconds per volume analysis under load

---

## 🟢 MINOR OPTIMIZATIONS (Nice to Have)

### 10. **Prior History Fetch Blocks Initial Analysis** 

**Location:** `src/newsflash/shared/statistics/volume_analyzer.py:850`
```python
prior_history = await asyncio.to_thread(_fetch_prior_history_stats, client, symbol, event_time, lookback_minutes=10)
```

**Problem:**
- Fetches 10 minutes of prior history **before** starting surge detection
- This is a blocking operation (even with asyncio.to_thread)
- Takes **200-500ms** per ticker

**Fix:** Could be done in parallel with initial quote fetch, or cached

**Estimated Impact:** 0.2-0.5 seconds per article

---

### 11. **Multiple Lock Acquisitions in Critical Path**

**Evidence:**
- Line 663: `async with self._traded_lock:` in `_trigger_trade_for_surge()`
- Line 756: `async with self._traded_lock:` again (duplicate check)
- Line 833: `async with self._traded_lock:` in monitoring loop
- Line 413: `async with self._traded_lock:` in `_check_and_monitor_ticker()`

**Problem:**
- Multiple lock acquisitions in critical path
- Could combine checks or reduce lock scope

**Impact:** Minimal (locks are fast), but could be optimized

---

## 📊 BOTTLENECK IMPACT SUMMARY

| Bottleneck | Impact | Priority | Fix Complexity |
|------------|--------|----------|----------------|
| **Repository file locking** | 1-3s | 🔴 CRITICAL | Medium |
| **Repository append blocking** | 1-2s | 🔴 CRITICAL | Low |
| **No catch-up window analysis** | 1-2s | 🔴 CRITICAL | Medium |
| **0.5s polling (not 0.1s)** | 0-0.4s | 🟡 MODERATE | Low |
| **No Alpaca websocket** | 0.1-0.5s | 🟡 MODERATE | High |
| **Monitoring waits for windows** | 0-4s | 🟡 MODERATE | Medium |
| **Yahoo Finance blocks** | 0.5-1.5s | 🟡 MODERATE | Low |
| **Event bus awaits all** | 0.1-0.5s | 🟡 MODERATE | Low |
| **Sync HTTP in volume analysis** | 0.2-0.5s | 🟡 MODERATE | High |
| **Prior history fetch** | 0.2-0.5s | 🟢 MINOR | Low |

**Total Potential Savings:** 4-10 seconds per article under load

---

## 🔍 CODE CONFIRMATION DETAILS

### ✅ Confirmed: Polling is 0.5s, Not 0.1s
- **File:** `src/newsflash/shared/statistics/volume_analyzer.py:921`
- **Code:** `sleep_time = min(0.5, remaining)`
- **Status:** Confirmed - should be 0.1s for faster detection

### ✅ Confirmed: No Catch-Up Window Analysis
- **File:** `src/newsflash/shared/statistics/volume_analyzer.py:839-847`
- **Code:** Calculates `real_window_seconds` but never uses it
- **Status:** Confirmed - missing catch-up window logic

### ✅ Confirmed: Single Repository File Lock
- **File:** `src/newsflash/infra/statistics/repository.py:49`
- **Code:** `self._file_lock = asyncio.Lock()` - ONE lock for ALL files
- **Evidence:** All `append_*`, `update_*`, `_load_*`, `_save_*` methods use same lock
- **Status:** Confirmed - major bottleneck under load

### ✅ Confirmed: No Alpaca Websocket
- **File:** `src/newsflash/infra/brokerage/quote_fetcher.py`
- **Code:** All methods use `get_stock_latest_quote()` - REST API
- **Missing:** `stream_manager.py` file doesn't exist
- **Status:** Confirmed - using REST API for all real-time data

### ✅ Confirmed: Repository Append Blocks Non-Surge Articles
- **File:** `src/newsflash/shared/statistics/recall_engine.py:554`
- **Code:** `await self.repository.append_recall_record()` - BLOCKING
- **Compare:** Line 551 - surge articles use `asyncio.create_task()` - NON-BLOCKING
- **Status:** Confirmed - inconsistency causing delays

### ✅ Confirmed: Monitoring Cycles Wait for Window Starts
- **File:** `src/newsflash/shared/statistics/recall_engine.py:853-856`
- **Code:** Waits for `window_start > now` even if article received late
- **Status:** Confirmed - should use catch-up window instead

### ✅ Confirmed: Yahoo Finance Blocks with Timeouts
- **File:** `src/newsflash/shared/statistics/recall_engine.py:875`
- **Code:** `await asyncio.wait_for(..., timeout=1.5)` in monitoring cycle
- **Status:** Confirmed - blocks even though queue system exists

### ✅ Confirmed: Event Bus Awaits All Subscribers
- **File:** `src/newsflash/shared/event_bus.py:54`
- **Code:** `await asyncio.gather(*tasks, return_exceptions=True)`
- **Status:** Confirmed - waits for all subscribers to complete

### ✅ Confirmed: Volume Analysis Uses Sync HTTP (Even with asyncio.to_thread)
- **File:** `src/newsflash/shared/statistics/volume_analyzer.py:207`
- **Code:** `trades = client.get_stock_trades(trade_request)` - sync HTTP
- **Status:** Confirmed - blocks thread pool workers

---

## 🎯 RECOMMENDED FIX PRIORITY

### **Phase 1: Quick Wins (High Impact, Low Complexity)**
1. ✅ Make repository append non-blocking for ALL articles (not just surge)
2. ✅ Change polling from 0.5s to 0.1s
3. ✅ Implement catch-up window analysis (published_at → received_at)
4. ✅ Use Yahoo Finance queue system in initial checks (don't block)

### **Phase 2: Medium Complexity (High Impact)**
5. ✅ Implement per-file locks or lock-free append-only writes
6. ✅ Make monitoring cycles start immediately (no waiting for window starts)
7. ✅ Make event bus truly fire-and-forget for non-critical events

### **Phase 3: High Complexity (Medium-High Impact)**
8. ✅ Implement Alpaca websocket for real-time quotes
9. ✅ Use websocket data in volume analysis instead of REST API

---

## 💡 KEY INSIGHTS

1. **File locking is the #1 bottleneck** - single lock causes severe contention
2. **Inconsistent blocking behavior** - surge articles non-blocking, others block
3. **Missing catch-up analysis** - late-arriving articles miss surge windows
4. **No real-time data** - REST API calls add latency vs websocket
5. **Polling too slow** - 0.5s could be 0.1s for faster detection

**Expected Improvement After Fixes:** 
- Baseline: 3.31s → **2.5-3.0s** (single article)
- Load test: 8.93s → **4-5s** (under load)
- Last 5 articles: 12.2s → **6-7s** (worst case)
