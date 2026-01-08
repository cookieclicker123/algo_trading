# Surge Detection to Trade Execution - Delay Analysis

## Problem Summary
- **ACHR**: Surge detected at 13:00:04, trade triggered at 13:02:29 (**2 minutes 25 seconds delay!**)
- **SKYX**: Surge detected at 13:00:02, trade triggered over 1 minute later (missed $2.56 entry, entered at $2.74)
- **DGNX**: Similar delay issues

## Code Path: Surge Detection → Trade Execution

### 1. Surge Detection (Monitoring Cycle)
**File**: `src/newsflash/shared/statistics/recall_engine.py`
**Function**: `_monitor_for_surge()` (line 756)

**Flow**:
```python
# Line 920-940: When surge detected in monitoring cycle
if surge_detected:
    # ❌ BLOCKING OPERATION #1: DB write BEFORE trade trigger
    await self.repository.update_recall_record(...)  # Line 923
    
    # ❌ BLOCKING OPERATION #2: AWAIT trade trigger (blocks monitoring loop!)
    await self._trigger_trade_for_surge(article, surge_ticker)  # Line 937
    
    break  # Stop monitoring
```

**Problem**: 
- Line 923: **BLOCKING DB write** before trade trigger
- Line 937: **BLOCKING await** - this blocks the entire monitoring loop for this article
- When multiple articles are monitored simultaneously (like at 1pm), they queue up and block each other

### 2. Trade Trigger Function
**File**: `src/newsflash/shared/statistics/recall_engine.py`
**Function**: `_trigger_trade_for_surge()` (line 610)

**Flow**:
```python
async def _trigger_trade_for_surge(self, article, ticker):
    # ❌ BLOCKING OPERATION #3: Get NBBO snapshot
    nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)  # Line 643
    
    # Validate spread (synchronous, fast)
    # Build trade request (synchronous, fast)
    trade_request = build_trade_request_for_article(...)  # Line 711
    
    # ❌ BLOCKING OPERATION #4: Event bus publish WAITS for all subscribers
    await self.event_bus.publish(
        DomainEventType.TRADE_REQUESTED,
        domain_trade_event.model_dump()
    )  # Line 735
```

**Problems**:
- Line 643: **BLOCKING** NBBO fetch (could be slow during high load)
- Line 735: **BLOCKING** event bus publish - waits for ALL subscribers via `asyncio.gather()`

### 3. Event Bus Publish
**File**: `src/newsflash/shared/event_bus.py`
**Function**: `publish()` (line 30)

**Flow**:
```python
async def publish(self, event_type: str, event_data: Any) -> None:
    # Get subscribers
    subscribers = self._subscribers[event_type].copy()
    
    # Create tasks for all subscribers
    tasks = [asyncio.create_task(...) for subscriber in subscribers]
    
    # ❌ BLOCKING OPERATION #5: WAIT for all subscribers to complete
    await asyncio.gather(*tasks, return_exceptions=True)  # Line 54
```

**Problem**: 
- Line 54: **BLOCKING** - waits for ALL subscribers to complete
- If `BrokerageDomainListener._handle_domain_trade_request()` is slow or busy, this blocks

### 4. Domain Listener (Subscriber)
**File**: `src/newsflash/domain/brokerage/listener.py`
**Function**: `_handle_domain_trade_request()` (line 139)

**Flow**:
```python
async def _handle_domain_trade_request(self, event_type, event_data):
    # Validate event (synchronous, fast)
    domain_event = self.validate_domain_event(...)
    
    # Validate model (synchronous, fast)
    if not self.request_validator.is_valid_domain_trade_request(...):
        return
    
    # Map model (synchronous, fast)
    infra_request_data = self.request_mapper.to_infrastructure_model(...)
    
    # ❌ BLOCKING OPERATION #6: Publish infrastructure event
    await self.publish_infrastructure_event(...)  # Line 178
```

**Problem**: 
- Line 178: Another **BLOCKING** event publish, which waits for its subscribers

### 5. Infrastructure Service (Trade Execution)
**File**: `src/newsflash/infra/brokerage/service.py`
**Function**: `execute_trade()` (line 176)

**Flow**: Eventually calls Alpaca API to place order

---

## Root Causes

### Primary Issue: Blocking Operations in Critical Path

1. **Line 937 in `_monitor_for_surge`**: `await self._trigger_trade_for_surge()` 
   - **IMPACT**: Blocks monitoring loop for this article
   - **WHEN IT HURTS**: During high load (1pm), multiple articles monitoring simultaneously
   - **SOLUTION**: Use `asyncio.create_task()` instead of `await`

2. **Line 923 in `_monitor_for_surge`**: `await self.repository.update_recall_record()`
   - **IMPACT**: Blocks before trade trigger
   - **WHEN IT HURTS**: DB writes can be slow during high load
   - **SOLUTION**: Move to background task (fire-and-forget)

3. **Line 735 in `_trigger_trade_for_surge`**: `await self.event_bus.publish()`
   - **IMPACT**: Waits for ALL subscribers to complete
   - **WHEN IT HURTS**: If subscriber is slow/busy, blocks entire publish
   - **SOLUTION**: Make event bus publish fire-and-forget for trade requests, OR make subscriber non-blocking

4. **Line 643 in `_trigger_trade_for_surge`**: `await self.quote_fetcher.get_nbbo_snapshot()`
   - **IMPACT**: Blocks while fetching price
   - **WHEN IT HURTS**: API rate limits or network delays
   - **SOLUTION**: Acceptable (need price for trade), but could be optimized

### Secondary Issue: No Dedicated Surge Event

**Current Flow**:
```
Surge Detected → TradeRequestDomainEvent → BrokerageDomainListener → InfrastructureEvent → Trade Execution
```

**Problem**: Trade request goes through general domain event system, which has multiple subscribers and validation layers.

**Better Flow** (as user suggested):
```
Surge Detected → SurgeDetectedDomainEvent → DedicatedTradeExecutor (immediate, no validation delays)
```

---

## Why This Happens at 1pm

At 1pm (and 12pm), multiple articles arrive simultaneously:
1. Each article starts monitoring (30 cycles of 4-second windows)
2. When surge detected, `await _trigger_trade_for_surge()` blocks that monitoring task
3. Multiple surges detected around same time → multiple blocking `await` calls
4. Event bus publish waits for subscribers → if subscriber is busy with previous trade, blocks
5. **Result**: Trades queue up and delay accumulates

---

## Price Analysis

### ACHR at 13:00:04 (Surge Detection Time)
- **Surge Ask**: $8.67 (from `surge_detection_window_stats.surge_ask`)
- **Surge Bid**: $8.60
- **Surge Spread**: $0.07 (0.81%)
- **Entry Price** (with premium): ~$8.70-8.75
- **10-minute check price**: Need to check recall record

### SKYX at 13:00:02 (Surge Detection Time)
- **Surge Ask**: $2.56 (from `surge_detection_window_stats.surge_ask`)
- **Surge Bid**: $2.55
- **Surge Spread**: $0.01 (0.39%)
- **Entry Price** (with premium): ~$2.57-2.58
- **Actual Entry Price**: $2.74 (from trade record)
- **Price Difference**: $2.74 - $2.56 = $0.18 (7.0% worse entry)
- **If entered at $2.56 vs $2.74**: Would have been 7% better entry, likely profitable

---

## Files Responsible

1. **`src/newsflash/shared/statistics/recall_engine.py`**
   - Line 923: Blocking DB update before trade
   - Line 937: Blocking await of trade trigger
   - Line 518: Initial surge uses `asyncio.create_task()` (GOOD) but monitoring surge uses `await` (BAD)

2. **`src/newsflash/shared/statistics/recall_engine.py`**
   - Line 643: Blocking NBBO fetch
   - Line 735: Blocking event bus publish

3. **`src/newsflash/shared/event_bus.py`**
   - Line 54: Blocking wait for all subscribers

4. **`src/newsflash/domain/brokerage/listener.py`**
   - Line 178: Blocking infrastructure event publish

---

## Recommended Fixes (Priority Order)

### Fix #1: Make Monitoring Surge Trigger Non-Blocking (CRITICAL)
**File**: `recall_engine.py` line 937
**Change**: 
```python
# BEFORE (BLOCKING):
await self._trigger_trade_for_surge(article, surge_ticker)

# AFTER (NON-BLOCKING):
asyncio.create_task(self._trigger_trade_for_surge(article, surge_ticker))
```

### Fix #2: Move DB Update to Background (CRITICAL)
**File**: `recall_engine.py` line 923
**Change**:
```python
# BEFORE (BLOCKING):
await self.repository.update_recall_record(...)

# AFTER (NON-BLOCKING):
asyncio.create_task(self.repository.update_recall_record(...))
```

### Fix #3: Make Event Bus Publish Fire-and-Forget for Trade Requests (HIGH)
**Option A**: Don't await event bus publish in `_trigger_trade_for_surge`
**Option B**: Make event bus publish fire-and-forget for specific event types
**Option C**: Create dedicated surge event with immediate executor

---

## Why User's Suggestion is Correct

User suggested: "why surge is not its own event in the bus and why it isnt subscribed to be autotrader which immediately enters"

**This is the RIGHT approach because**:
1. Surge detection is a critical, time-sensitive event
2. It should bypass validation layers and go straight to execution
3. Current flow has too many layers (domain event → listener → infrastructure event → executor)
4. Each layer adds latency, especially when system is under load

**Better Architecture**:
```
SurgeDetectedDomainEvent → ImmediateTradeExecutor (bypasses validation, goes straight to Alpaca)
```

This would eliminate:
- Domain event validation delay
- Infrastructure event mapping delay  
- Multiple event bus publish delays
- Subscriber processing delays
