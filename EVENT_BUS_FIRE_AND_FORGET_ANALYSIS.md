# Event Bus Fire-and-Forget Analysis

**Date:** 2026-01-12  
**Goal:** Quantify potential savings from making event bus truly fire-and-forget for non-critical events

---

## Current Implementation

**Event Bus:** `src/newsflash/shared/event_bus.py:54`
```python
await asyncio.gather(*tasks, return_exceptions=True)  # WAITS for all subscribers
```

**Problem:** Event bus waits for ALL subscribers to complete before `publish()` returns.

---

## Event Flow Analysis

### TradeExecuted Event Subscribers

When `Domain.TradeExecuted` is published, these subscribers handle it:

1. **SignalStatsEngine._handle_trade_executed** ⚠️ **BLOCKS**
   - AWAITS `repository.append_signal_record()`
   - File write operation: load file → append record → update summary → save file
   - **Time:** 0.1-0.3 seconds (file I/O + JSON serialization)
   - **Impact:** Blocks event bus until file write completes

2. **RecallStatsEngine._handle_trade_executed** ✅ **FAST**
   - Updates in-memory set (`self._traded_articles.add()`)
   - **Time:** <0.001 seconds (in-memory operation)

3. **NotifyTradeExecutedUseCase._handle_trade_executed** ⚠️⚠️ **MAJOR BLOCKER**
   - AWAITS `storage_query_service.fetch_article()` (0.1-0.5s)
   - AWAITS `analyze_volume_around_event()` ⚠️ **EXPENSIVE!** (0.5-2.0s)
   - Then publishes `NotificationRequested` event
   - **Time:** 0.6-2.5 seconds total
   - **Impact:** Blocks event bus for volume analysis (most expensive operation)

4. **NotifyImminentArticleUseCase._handle_trade_executed** ⚠️ **BLOCKS**
   - AWAITS `storage_query_service.fetch_article()` (0.1-0.5s)
   - Then publishes `NotificationRequested` event
   - **Time:** 0.1-0.5 seconds

5. **MetricsService._handle_trade_executed** ✅ **FAST**
   - Updates in-memory dictionary
   - **Time:** <0.001 seconds

### TradeFailed Event Subscribers

When `Domain.TradeFailed` is published, these subscribers handle it:

1. **FailedTradeStatsEngine._handle_trade_failed** ⚠️ **BLOCKS**
   - AWAITS `repository.append_failed_trade_record()`
   - File write operation: load file → append record → update summary → save file
   - **Time:** 0.1-0.3 seconds (file I/O + JSON serialization)

2. **RecallStatsEngine._handle_trade_failed** ✅ **FAST**
   - Updates in-memory set
   - **Time:** <0.001 seconds

3. **MetricsService** ✅ **FAST**
   - Updates in-memory dictionary
   - **Time:** <0.001 seconds

---

## Blocking Operations Breakdown

| Subscriber | Operation | Time | Blocking? |
|-----------|-----------|------|-----------|
| SignalStatsEngine | `append_signal_record()` | 0.1-0.3s | ✅ Yes |
| FailedTradeStatsEngine | `append_failed_trade_record()` | 0.1-0.3s | ✅ Yes |
| NotifyTradeExecutedUseCase | `fetch_article()` + `analyze_volume_around_event()` | 0.6-2.5s | ✅✅ Yes (major) |
| NotifyImminentArticleUseCase | `fetch_article()` | 0.1-0.5s | ✅ Yes |
| RecallStatsEngine | In-memory set update | <0.001s | ❌ No |
| MetricsService | In-memory dict update | <0.001s | ❌ No |

---

## Potential Savings Calculation

### Scenario: Load Test (20 Articles)

**TradeExecuted Events:** ~0-20 (depends on trade success rate)
- In test: 0 executed (all failed)
- In production: ~10-20 per 20 articles (50-100% success rate)

**TradeFailed Events:** ~0-20 (depends on trade failure rate)
- In test: 20 failed
- In production: ~0-5 per 20 articles (0-25% failure rate)

### Savings Per Event (if fire-and-forget):

**TradeExecuted (with successful trades):**
- SignalStatsEngine: 0.1-0.3s
- NotifyTradeExecutedUseCase: 0.6-2.5s (volume analysis is expensive!)
- NotifyImminentArticleUseCase: 0.1-0.5s
- **Total per event:** 0.8-3.3 seconds

**TradeFailed:**
- FailedTradeStatsEngine: 0.1-0.3s
- **Total per event:** 0.1-0.3 seconds

### Total Savings (20 articles, 50% success rate = 10 executed, 10 failed):

- TradeExecuted savings: 10 × 0.8-3.3s = **8-33 seconds**
- TradeFailed savings: 10 × 0.1-0.3s = **1-3 seconds**
- **Total potential savings:** **9-36 seconds**

**BUT:** These events happen AFTER trade execution, not during the critical path!
- TradeExecuted is published AFTER trade completes
- TradeFailed is published AFTER trade fails
- **These don't affect latency to trade execution**

### Actual Impact on Trade Latency:

**ZERO impact** - TradeExecuted/TradeFailed events are published AFTER the trade completes. The blocking operations happen in the "cleanup/notification" phase, not in the critical path.

The only critical event is `TradeRequested`, which MUST await (trade execution path).

---

## Critical vs Non-Critical Events

### Critical Events (MUST await):
- `Domain.TradeRequested` - Critical path for trade execution
  - Subscribers: BrokerageDomainListener
  - MUST complete before trade can execute

### Non-Critical Events (could be fire-and-forget):
- `Domain.TradeExecuted` - Statistics/notifications (after trade completes)
- `Domain.TradeFailed` - Statistics/notifications (after trade fails)
- `Domain.ArticleReceived` - Statistics (recall engine already uses create_task)
- `Domain.ArticleClassified` - Statistics (recall engine already uses create_task)

---

## Implementation Complexity

**To implement fire-and-forget:**
1. Add parameter to `publish()`: `await_subscribers: bool = True`
2. Identify critical vs non-critical events
3. Update all call sites to specify await behavior
4. Test thoroughly to ensure critical events still await

**Risks:**
- If statistics writes fail silently, data is lost (but this is non-critical)
- If notification use cases fail silently, notifications are lost (but this is non-critical)
- Complexity in identifying which events are critical vs non-critical

---

## Recommendation

### ❌ **NOT WORTH IT**

**Reasons:**
1. **Zero impact on trade latency** - TradeExecuted/TradeFailed happen AFTER trades complete
2. **Already optimized** - RecallEngine uses `create_task()` for most blocking operations
3. **Complexity vs benefit** - High complexity (API changes, careful event classification) for minimal benefit
4. **Current performance is good** - 4.709s average latency, within 4-5s target

**The blocking operations happen in the "cleanup/notification" phase:**
- File writes for statistics (0.1-0.3s each)
- Volume analysis for notifications (0.5-2.0s)
- Storage queries for notifications (0.1-0.5s)

**These don't affect the critical path:**
- Article received → Surge detection → Trade request → Trade execution

**If we want to optimize these, better approaches:**
1. Make SignalStatsEngine/FailedTradeStatsEngine use `create_task()` (like RecallEngine does)
2. Make NotifyTradeExecutedUseCase skip volume analysis or do it async
3. These are smaller, targeted fixes vs changing the entire event bus architecture

---

## Conclusion

**Skip Event Bus Fire-and-Forget (#7)** - The blocking operations don't affect trade latency (they happen after trades complete), and the complexity isn't worth the minimal benefit.

**Better alternatives:**
- Make statistics engines use `create_task()` for file writes (simple, targeted fix)
- Optimize notification use cases separately (skip expensive volume analysis, or make it async)
- These are smaller changes with less risk and similar benefit
