# Why Failed Trades Are Faster Than Successful Fills

**Date:** 2026-01-13  
**Observation:** 4.6s latency with OSRH (all failed) vs expected slower with fills  
**Question:** Does fill time add latency vs immediate rejection?

---

## The Answer: YES - Failed Trades Are Faster

### Failed Trade Flow (OSRH - Wash Trade Error):
1. **Submit order** → API call (~0.1s)
2. **Immediate rejection** → API returns error (~0.1s)
3. **Done** → TradeFailed event published (~0.01s)
4. **Total:** ~0.2-0.3 seconds for trade execution

### Successful Fill Flow (AAPL - Should Fill):
1. **Submit order** → API call (~0.1s)
2. **Wait for fill** → Poll order status every 0.5s
3. **Check fill status** → Up to 10 seconds timeout
4. **Get fill price** → Additional API call (~0.1s)
5. **Done** → TradeExecuted event published (~0.01s)
6. **Total:** ~0.5-10 seconds (depending on fill speed)

---

## Why 4.6s Latency Despite Fast Failures?

**The 4.6s latency is NOT from trade execution - it's from surge detection!**

Looking at the logs:
- **Surge Detection → Trade Request:** 4.032s average
- **Trade Execution:** ~0.2-0.3s (immediate rejection)

**Breakdown:**
- Article received → Surge detected: ~4.0s
- Surge detected → Trade request: ~0.03s
- Trade request → Trade failed: ~0.2-0.3s (immediate rejection)
- **Total:** ~4.6s

---

## What Happens With Successful Fills?

**Expected breakdown with AAPL (fills successfully):**
- Article received → Surge detected: ~4.0s (same)
- Surge detected → Trade request: ~0.03s (same)
- Trade request → Trade executed: ~0.5-2.0s (fill wait time)
- **Total:** ~4.5-6.0s

**Difference:** +0.5-2.0s for fill wait time

---

## Test Strategy

**Created:** `test_baseline_trade_latency_load_aapl.py`

**Purpose:**
- Test with AAPL (highly liquid, should fill)
- Compare executed vs failed trade latencies
- Measure real fill time impact

**Expected Results:**
- Failed trades: ~4.6s (immediate rejection)
- Successful fills: ~5.0-6.5s (includes fill wait)
- Fill wait time: ~0.5-2.0s difference

---

## Why This Matters

**Current test (OSRH):**
- ✅ Measures surge detection latency accurately
- ✅ Measures trade request latency accurately
- ❌ Does NOT measure real fill time (all fail immediately)

**AAPL test:**
- ✅ Measures surge detection latency (same)
- ✅ Measures trade request latency (same)
- ✅ Measures REAL fill time (orders actually fill)

**Conclusion:** The 4.6s latency is accurate for surge detection, but we're not seeing the real fill time because all trades fail immediately. Testing with AAPL will show the true fill time impact.
