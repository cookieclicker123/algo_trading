# Operational State vs Business State

## The Core Distinction

**Operational State** = Technical/mechanical state needed for the system to function  
**Business State** = State that represents business metrics/events that should be observable and trackable

---

## Operational State

**Definition:** Internal implementation details needed to make the system work mechanically.

**Characteristics:**
- ✅ **Implementation detail** - How the system works internally
- ✅ **Not observable** - Doesn't need to be tracked via events
- ✅ **Temporary** - Only needed while the system is running
- ✅ **Technical** - Threads, locks, task references, internal flags

**Examples from Connection Manager:**

```python
# Thread control (operational state for threads)
self._threads_should_run = False  # Controls whether threads should continue running

# Connection mechanics (operational state)
self._connection_lock: Optional[asyncio.Lock] = None  # Prevents race conditions
self._main_event_loop: Optional[asyncio.AbstractEventLoop] = None  # Event loop reference
self._ib_thread: Optional[threading.Thread] = None  # Thread reference
self._ib_event_loop: Optional[asyncio.AbstractEventLoop] = None  # Thread's event loop
self._connection_ready = threading.Event()  # Thread synchronization primitive

# Background task references (operational state)
self._connection_verification_task: Optional[asyncio.Task] = None
self._keepalive_task: Optional[asyncio.Task] = None

# Operational metrics (not business metrics)
self._operational_stats = {
    "reconnect_attempts": 0,  # Internal counter for retry logic
    "last_keepalive_time": None,  # Internal timing for keepalive loop
}
```

**Why it's operational:**
- These are **mechanical necessities** - you need locks to prevent race conditions, threads to run code, task references to cancel tasks
- They're **not business events** - nobody cares about "lock acquired" or "thread started" as business metrics
- They're **implementation details** - if you switch from threading to async, these change

---

## Business State

**Definition:** State that represents meaningful business events/metrics that should be observable, trackable, and auditable.

**Characteristics:**
- ✅ **Business meaning** - Represents something important to the business
- ✅ **Observable** - Should be tracked via events
- ✅ **Persistent** - Should be stored/aggregated (via MetricsService)
- ✅ **Auditable** - Can be queried, analyzed, reported on

**Examples from Connection Manager:**

```python
# Business state (tracked via events, not stored locally)
# These come from MetricsService which aggregates ConnectionStatusChangedEvent:

connection_attempts: int  # How many times we tried to connect
last_connection_time: datetime  # When we last connected
last_disconnection_time: datetime  # When we last disconnected
is_connected: bool  # Current connection status
```

**How it's tracked:**

1. **Event Publishing** - When business state changes, publish an event:
   ```python
   # In connection_manager.py
   await self._publish_connection_status(True, "Connected and verified")
   # This publishes ConnectionStatusChangedEvent
   ```

2. **MetricsService** - Aggregates events into business metrics:
   ```python
   # MetricsService listens to ConnectionStatusChangedEvent
   # and tracks:
   # - connection_attempts (count of events)
   # - last_connection_time (from event timestamp)
   # - last_disconnection_time (from event timestamp)
   # - is_connected (from latest event)
   ```

3. **Querying** - Business state is queryable:
   ```python
   # In connection_manager.py get_stats()
   brokerage_stats = self.metrics_service.get_brokerage_connection_stats()
   # Returns business metrics aggregated from events
   ```

---

## Why This Separation Matters

### 1. **Single Source of Truth**

**Business state** comes from **events** → **MetricsService**:
- ✅ One place to query business metrics
- ✅ Events are auditable (can replay, analyze)
- ✅ Consistent across all services

**Operational state** stays in the service:
- ✅ Only needed for internal mechanics
- ✅ Doesn't need to be shared
- ✅ Can change with implementation

### 2. **Testability**

**Business state:**
- ✅ Can test by checking events published
- ✅ Can test MetricsService aggregation
- ✅ Can mock event bus

**Operational state:**
- ✅ Can test by checking internal mechanics work
- ✅ Don't need to expose it externally

### 3. **Observability**

**Business state:**
- ✅ Visible via MetricsService
- ✅ Can be queried, reported, analyzed
- ✅ Part of health checks, dashboards

**Operational state:**
- ✅ Not exposed (implementation detail)
- ✅ Only relevant for debugging internal mechanics

### 4. **Architecture Clarity**

**Business state** = "What happened?" (events, metrics)  
**Operational state** = "How did it work?" (threads, locks, tasks)

---

## Real Example from Connection Manager

### Operational State (Lines 58-93)

```python
# Thread control flag (operational state needed by threads)
# Lifecycle is tracked by LifecycleManager, this is for thread coordination
self._threads_should_run = False

# Connection state (operational - mechanics)
self._connection_lock: Optional[asyncio.Lock] = None
self._main_event_loop: Optional[asyncio.AbstractEventLoop] = None
self._ib_thread: Optional[threading.Thread] = None
self._ib_event_loop: Optional[asyncio.AbstractEventLoop] = None
self._connection_ready = threading.Event()
self._connection_error: Optional[Exception] = None

# Background tasks (operational - task management)
self._connection_verification_task: Optional[asyncio.Task] = None
self._keepalive_task: Optional[asyncio.Task] = None

# ✅ Reduced stats - only operational stats not tracked via events
# Business stats (connection_attempts, last_connection_time, last_disconnection_time, is_connected) come from MetricsService
self._operational_stats = {
    "reconnect_attempts": 0,  # Not published as event yet (operational metric)
    "last_keepalive_time": None,  # Operational metric (not from events)
}
```

### Business State (Tracked via Events)

```python
# When connection status changes, publish event:
await self._publish_connection_status(True, "Connected and verified")

# This publishes ConnectionStatusChangedEvent, which MetricsService aggregates:
# - connection_attempts (count of connection events)
# - last_connection_time (timestamp from event)
# - last_disconnection_time (timestamp from event)
# - is_connected (from latest event)

# Then queryable via:
brokerage_stats = self.metrics_service.get_brokerage_connection_stats()
```

---

## Decision Framework

### Is it Operational State?

Ask: **"Is this needed for the system to function mechanically?"**

✅ **Yes** if:
- Thread control flags
- Locks, semaphores, synchronization primitives
- Task/thread references
- Internal counters for retry logic
- Temporary state for coordination

❌ **No** if:
- It represents a business event (connection, disconnection, trade, etc.)
- It should be observable/queryable
- It should be in dashboards/reports
- It should be auditable

### Is it Business State?

Ask: **"Does this represent something meaningful to the business?"**

✅ **Yes** if:
- Connection status
- Trade executions
- Errors/failures
- Performance metrics
- User actions

❌ **No** if:
- It's just a technical implementation detail
- It's only needed for internal coordination
- It's not meaningful outside the service

---

## Key Takeaway

**Operational State** = "How the machine works" (internal mechanics)  
**Business State** = "What the business cares about" (events, metrics)

**Rule of thumb:**
- If it should be in a dashboard → **Business State** → Track via events
- If it's just needed to make code run → **Operational State** → Keep internal

---

## Connection Manager Example Summary

| State | Type | Location | Why |
|-------|------|----------|-----|
| `_threads_should_run` | Operational | Connection Manager | Thread control flag - needed for threads to know when to stop |
| `_connection_lock` | Operational | Connection Manager | Prevents race conditions during connection |
| `_ib_thread` | Operational | Connection Manager | Reference to thread - needed to manage it |
| `_operational_stats["reconnect_attempts"]` | Operational | Connection Manager | Internal counter for retry logic |
| `connection_attempts` | Business | MetricsService | Business metric - how many times we tried to connect |
| `last_connection_time` | Business | MetricsService | Business metric - when we last connected |
| `is_connected` | Business | MetricsService | Business metric - current connection status |

**Business state** is tracked via `ConnectionStatusChangedEvent` → `MetricsService`  
**Operational state** stays in `ConnectionManager` for internal mechanics

