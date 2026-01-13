# Critical Path Analysis: Surge Detection → Trade Placement

**Date:** 2026-01-12  
**Goal:** Identify all blocking operations in the critical path from surge detection to trade placement

---

## Critical Path Flow

```
Article Received → Surge Detection → Trade Trigger → Trade Request → Trade Execution
```

---

## Critical Path Breakdown

### 1. Surge Detection (`analyze_volume_around_event`)

**Location:** `src/newsflash/shared/statistics/volume_analyzer.py:822`

**Blocking Operations:**
- `client.get_stock_trades()` - Synchronous HTTP call (wrapped in `asyncio.to_thread()`)
  - **Time:** 0.5-2.0 seconds per analysis
  - **Impact:** Blocks thread pool worker (not event loop, but still blocking)
  - **Frequency:** Every polling cycle (0.1s intervals) until surge detected

**Status:** ⚠️ Blocking (thread pool), but parallelized via `asyncio.to_thread()`

---

### 2. Trade Trigger (`_trigger_trade_for_surge`)

**Location:** `src/newsflash/shared/statistics/recall_engine.py:647`

**Called via:** `asyncio.create_task()` - non-blocking ✅

**Blocking Operations:**

1. **Quote Fetch for Spread Validation** ⚠️ **BLOCKING**
   - `quote_fetcher.get_nbbo_snapshot(ticker)`
   - Uses `market_data_client.get_stock_latest_quote()` - REST API call
   - **Time:** 0.1-0.3 seconds
   - **Impact:** Blocks event loop until quote is fetched
   - **Purpose:** Validate spread (< 2%) and get current price for trade sizing

2. **Trade Request Build** ✅ **FAST**
   - `build_trade_request_for_article()` - Synchronous, in-memory operation
   - **Time:** <0.001 seconds

3. **Event Bus Publish** ⚠️ **BLOCKING** (but necessary)
   - `event_bus.publish(TradeRequested)`
   - **Time:** <0.01 seconds (event bus processing)
   - **Impact:** Blocks until event is published (necessary for trade execution)

---

### 3. Trade Request Handling (`BrokerageDomainListener`)

**Location:** `src/newsflash/domain/brokerage/listener.py:139`

**Blocking Operations:**
- Validation, mapping, event publishing - all fast (<0.001s)
- ✅ No significant blocking operations

---

### 4. Trade Execution (`BrokerageService.execute_trade`)

**Location:** `src/newsflash/infra/brokerage/service.py:176`

**Blocking Operations:**

1. **Quote Fetch (Again)** ⚠️ **BLOCKING**
   - `quote_fetcher.get_nbbo_snapshot()` - REST API call
   - **Time:** 0.1-0.3 seconds
   - **Impact:** Blocks event loop (happens AFTER TradeRequested, but still in execution path)
   - **Purpose:** Get current NBBO for trade execution

2. **Alpaca Trade Execution** ⚠️ **BLOCKING** (external API)
   - Order placement via Alpaca API
   - **Time:** 0.3-0.7 seconds
   - **Impact:** Blocks event loop (unavoidable - external API)

---

## Blocking Operations Summary

| Operation | Location | Time | Type | Optimizable? |
|-----------|----------|------|------|--------------|
| Volume analysis (`get_stock_trades`) | volume_analyzer.py | 0.5-2.0s | HTTP (thread pool) | ✅ WebSocket |
| Quote fetch (spread validation) | recall_engine.py:680 | 0.1-0.3s | REST API | ✅ WebSocket |
| Trade request build | recall_engine.py:748 | <0.001s | In-memory | ❌ Already fast |
| Event bus publish | recall_engine.py:772 | <0.01s | Event bus | ❌ Necessary |
| Quote fetch (trade execution) | service.py | 0.1-0.3s | REST API | ✅ WebSocket |
| Alpaca trade execution | service.py | 0.3-0.7s | External API | ❌ Unavoidable |

---

## Optimization Opportunities

### ✅ **WebSocket Integration** (High Impact, High Complexity)

**Current State:**
- Quotes: REST API (`get_stock_latest_quote`)
- Trades: REST API (`get_stock_trades`) for volume analysis

**Potential Savings:**
- Quote fetch: 0.1-0.3s → ~0.01s (10-30x faster)
- Volume analysis: 0.5-2.0s → ~0.1-0.3s (2-7x faster)
- **Total potential savings:** 0.6-2.3 seconds per trade

**Implementation:**
1. Implement Alpaca WebSocket for quotes (Bottleneck #8)
2. Use WebSocket data in volume analysis (Bottleneck #9)
3. Subscribe to quote streams for monitored tickers

**Complexity:** High (new infrastructure, state management, reconnection logic)

---

### ❌ **Other Optimizations** (None Found)

**Already Optimized:**
- ✅ Trade trigger uses `asyncio.create_task()` (non-blocking)
- ✅ Repository writes use `asyncio.create_task()` (non-blocking)
- ✅ Parallel fetch of float_shares and prior_history
- ✅ Fast polling (0.1s intervals)
- ✅ Catch-up window analysis (0-60s bounds)

**Cannot Optimize:**
- ❌ Event bus publish - necessary for trade execution
- ❌ Alpaca trade execution - external API, unavoidable
- ❌ Trade request build - already fast (<0.001s)

---

## Current Performance

**Baseline (single article):** 3.31 seconds  
**Load test (20 articles):** 4.709 seconds  
**Target:** 4-5 seconds ✅ **ACHIEVED**

**Breakdown (estimated):**
- Surge detection: ~1.0-2.0s (volume analysis)
- Trade trigger: ~0.1-0.3s (quote fetch)
- Trade execution: ~0.4-1.0s (quote + Alpaca API)
- Overhead: ~0.2-0.4s (event bus, validation, etc.)
- **Total:** ~1.7-3.7s per article (matches observed 3.31s baseline)

---

## Conclusion

**The ONLY remaining optimization opportunity is WebSocket integration:**

1. **Quote WebSocket** - Replace REST API calls (2x quote fetches per trade)
   - **Savings:** 0.2-0.6 seconds per trade
   - **Complexity:** High (new infrastructure)

2. **Trade Data WebSocket** - Replace REST API for volume analysis
   - **Savings:** 0.4-1.7 seconds per volume analysis
   - **Complexity:** Very High (depends on quote WebSocket, real-time data handling)

**Current performance is already at target (4.7s vs 4-5s target).** WebSocket integration would provide additional improvements but is not necessary to meet current goals.

---

## Recommendation

**Status:** ✅ **TARGET ACHIEVED** (4.709s average latency, within 4-5s target)

**Next Steps:**
- **Optional:** Implement WebSocket integration for additional performance gains (0.6-2.3s potential savings)
- **Priority:** Low (current performance is acceptable)
- **Complexity:** High (requires significant infrastructure changes)

**All other optimizations have been completed or are not applicable.**
