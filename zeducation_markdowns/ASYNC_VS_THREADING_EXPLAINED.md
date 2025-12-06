# Async vs Threading in NewsFlash Codebase

## Quick Answers

### 1. Are we running everything on one event loop or many?

**Answer: Mostly ONE main event loop, with a few exceptions**

- **Main Event Loop**: FastAPI/Uvicorn runs everything on a single main event loop
- **Exception 1**: IBKR Connection Manager creates a **separate event loop in a thread** (because `ib_insync` is blocking)
- **Exception 2**: WebSocket service runs in **threads** (because `websocket-client` library is blocking)

### 2. Does typed event bus ensure parallel execution?

**Answer: YES, but it's concurrent (not parallel) - all on the same event loop**

The event bus uses `asyncio.gather()` which runs all subscribers **concurrently** on the same event loop:
```python
# From event_bus.py line 48-54
tasks = []
for subscriber in subscribers:
    task = asyncio.create_task(self._safe_call_subscriber(...))
    tasks.append(task)

await asyncio.gather(*tasks, return_exceptions=True)
```

**Key Point**: All subscribers run on the **same event loop**, so they're **concurrent** (cooperative multitasking), not **parallel** (true simultaneous execution). But they can run "at the same time" from a logical perspective - the event loop switches between them.

### 3. Are we using multithreading? Where?

**Answer: YES, in 3 specific places where async can't be used**

1. **IBKR Connection Manager** (`infra/brokerage/connection_manager.py`)
   - Thread: `_ib_thread` with its own event loop
   - Why: `ib_insync` library is blocking/synchronous
   - Pattern: Thread with separate event loop + thread-safe publishing to main loop

2. **WebSocket Service** (`infra/websocket/service.py`)
   - Threads: `websocket_thread`, `_ping_thread`, `_monitor_thread`
   - Why: `websocket-client` library is blocking/synchronous
   - Pattern: Threads run blocking code + thread-safe publishing to main loop

3. **Metrics Service** (`services/metrics/metrics_service.py`)
   - Thread: Uses `threading.Lock()` for thread-safe counters
   - Why: Thread-safe synchronization (could use asyncio.Lock, but threading.Lock is simpler here)

---

## Detailed Explanation: Async vs Threading

### What is Async?

**Async (asyncio)** = **Cooperative multitasking on a single thread**

- **Single thread** with an **event loop**
- Tasks **yield control** when waiting (I/O operations)
- Event loop **switches** between tasks when one is waiting
- **Non-blocking**: While one task waits for I/O, others can run

**Example from codebase:**
```python
# From auto_trade.py
async def fetch_article_for_trade(...):
    domain_article = await storage_service.fetch_article(article_id)  # Yields here
    # While waiting for storage, other tasks can run
    if not domain_article:
        await asyncio.sleep(delay)  # Yields here too
    return domain_article
```

**When to use Async:**
- ✅ I/O-bound operations (network requests, file I/O, database queries)
- ✅ Waiting for external services
- ✅ Event-driven programming
- ✅ Many concurrent operations

### What is Threading?

**Threading** = **True parallel execution across multiple CPU threads**

- **Multiple threads** run **simultaneously** (if you have multiple CPU cores)
- Each thread has its own **execution context**
- **Blocking**: One thread can block without affecting others
- Requires **synchronization** (locks) for shared data

**Example from codebase:**
```python
# From connection_manager.py line 481-486
def _run_ib_connection_thread(self, port: int) -> None:
    """Run IB connection in dedicated thread with its own event loop."""
    try:
        # Create and set event loop for this thread
        self._ib_event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._ib_event_loop)
        # This thread runs its own event loop, separate from main
```

**When to use Threading:**
- ✅ CPU-bound operations (if you have multiple cores)
- ✅ Blocking libraries that can't be made async (like `ib_insync`, `websocket-client`)
- ✅ Long-running background tasks that need to run independently
- ✅ When you need true parallelism (not just concurrency)

---

## Real Examples from Your Codebase

### Example 1: Async Event Bus (Concurrent, Not Parallel)

**File**: `shared/event_bus.py`

```python
async def publish(self, event_type: str, event_data: Any) -> None:
    # Get subscribers (protected by async lock)
    async with self._lock:
        subscribers = self._subscribers[event_type].copy()
    
    # Create tasks for all subscribers
    tasks = []
    for subscriber in subscribers:
        task = asyncio.create_task(self._safe_call_subscriber(...))
        tasks.append(task)
    
    # Run all subscribers concurrently (same event loop)
    await asyncio.gather(*tasks, return_exceptions=True)
```

**What happens:**
1. Event published → All subscribers start **concurrently**
2. If subscriber A waits for I/O → Event loop switches to subscriber B
3. If subscriber B waits → Event loop switches to subscriber C
4. All run on **same thread**, **same event loop**
5. They appear to run "at the same time" but are actually **cooperative**

**Why this works:**
- All subscribers are async functions
- They yield control when waiting (I/O operations)
- Event loop manages switching between them
- **No threads needed** - single event loop handles everything

### Example 2: Threading for Blocking Library (IBKR)

**File**: `infra/brokerage/connection_manager.py`

```python
# Line 481-486: Thread with separate event loop
def _run_ib_connection_thread(self, port: int) -> None:
    """Run IB connection in dedicated thread with its own event loop."""
    try:
        # Create NEW event loop for this thread
        self._ib_event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._ib_event_loop)
        
        # Run async code in this thread's event loop
        self._ib_event_loop.run_until_complete(self._connect_async(port))
```

**Why threading is needed here:**
- `ib_insync` library is **blocking/synchronous**
- It can't be used directly in async code (would block the entire event loop)
- Solution: Put it in a **separate thread** with its **own event loop**
- Thread runs blocking code → Publishes events to main loop via `call_soon_threadsafe()`

**Thread-safe publishing:**
```python
# Line 543-547: Publish from thread to main loop
if self._main_event_loop:
    self._main_event_loop.call_soon_threadsafe(
        lambda: asyncio.create_task(
            self._publish_connection_status(True, "Connected")
        )
    )
```

### Example 3: Threading for Blocking Library (WebSocket)

**File**: `infra/websocket/service.py`

```python
# Line 166-168: WebSocket thread
self.websocket_thread = threading.Thread(target=self._run_websocket_loop)
self.websocket_thread.daemon = True
self.websocket_thread.start()
```

**Why threading is needed:**
- `websocket-client` library is **blocking/synchronous**
- `ws.run_forever()` blocks the thread
- Can't use in async code (would block entire event loop)
- Solution: Run in **separate thread** → Publish events to main loop

**Thread-safe publishing:**
```python
# Line 116-119: Publish from thread to main loop
def _publish_event_threadsafe(self, coro) -> None:
    if self._main_event_loop and self._main_event_loop.is_running():
        self._main_event_loop.call_soon_threadsafe(
            lambda: asyncio.create_task(coro)
        )
```

---

## Key Differences: Async vs Threading

| Aspect | Async (asyncio) | Threading |
|--------|----------------|-----------|
| **Execution** | Single thread, cooperative multitasking | Multiple threads, true parallelism |
| **When tasks run** | One at a time, switching on I/O waits | Simultaneously (if multiple CPU cores) |
| **Blocking** | Non-blocking (yields on I/O) | Can block (each thread independent) |
| **Memory** | Low overhead (single thread) | Higher overhead (multiple threads) |
| **Synchronization** | `asyncio.Lock()` for shared data | `threading.Lock()` for shared data |
| **Best for** | I/O-bound operations | CPU-bound or blocking libraries |
| **Complexity** | Simpler (no race conditions if done right) | More complex (race conditions, deadlocks) |

---

## When to Use Each

### Use Async When:

1. **I/O-bound operations** (most of your codebase)
   ```python
   # ✅ Good: Async for I/O
   async def fetch_article(self, article_id: str):
       article = await storage_service.fetch_article(article_id)  # I/O wait
       return article
   ```

2. **Event-driven programming** (your event bus)
   ```python
   # ✅ Good: Async for events
   async def _handle_article_classified(self, event):
       await self.process_article(event)  # Can yield, other events can process
   ```

3. **Many concurrent operations**
   ```python
   # ✅ Good: Many concurrent fetches
   tasks = [fetch_article(id) for id in article_ids]
   results = await asyncio.gather(*tasks)  # All run concurrently
   ```

### Use Threading When:

1. **Blocking libraries that can't be made async** (IBKR, WebSocket client)
   ```python
   # ✅ Good: Thread for blocking library
   def _run_ib_connection_thread(self):
       # ib_insync is blocking - must run in thread
       ib = IB()
       ib.connect("127.0.0.1", 4001)  # Blocks thread, not main loop
   ```

2. **CPU-bound operations** (if you have multiple cores)
   ```python
   # ✅ Good: Thread for CPU-bound work
   def calculate_heavy_computation(data):
       # Heavy CPU work - can use multiple cores
       result = complex_algorithm(data)
       return result
   ```

3. **Long-running background tasks**
   ```python
   # ✅ Good: Thread for background monitoring
   def _monitor_loop(self):
       while self._threads_should_run:
           check_health()  # Runs independently
           time.sleep(5)
   ```

### Use Sync When:

1. **Simple utility functions** (no I/O, no waiting)
   ```python
   # ✅ Good: Sync for simple logic
   def calculate_shares(amount: float, price: float) -> int:
       return int(amount / price)  # No I/O, no waiting
   ```

2. **Data transformations** (pure functions)
   ```python
   # ✅ Good: Sync for transformations
   def format_trade_message(trade: TradeResult) -> str:
       return f"Trade: {trade.ticker} @ ${trade.fill_price}"
   ```

---

## Your Codebase Patterns

### Pattern 1: Pure Async (Most Common)

**Files**: Most of your codebase
- `services/brokerage/auto_trade.py`
- `services/storage/query_service.py`
- `use_cases/notification/notify_imminent_article_use_case.py`

**Pattern:**
```python
async def process_article(self, article: Article):
    # All async - no threads needed
    result = await self.classify(article)
    await self.store(result)
    await self.notify(result)
```

### Pattern 2: Thread + Async Bridge (IBKR, WebSocket)

**Files**: 
- `infra/brokerage/connection_manager.py`
- `infra/websocket/service.py`

**Pattern:**
```python
# 1. Thread runs blocking code
def _run_in_thread(self):
    blocking_library.connect()  # Blocks this thread
    # Publish events to main loop
    main_loop.call_soon_threadsafe(publish_event)

# 2. Main loop receives events asynchronously
async def _handle_event(self, event):
    await self.process(event)  # Async processing
```

### Pattern 3: Thread-Safe Synchronization

**File**: `services/metrics/metrics_service.py`

**Pattern:**
```python
# Thread-safe counter (could be from any thread)
self._lock = threading.Lock()

def increment_counter(self):
    with self._lock:  # Protect shared data
        self._count += 1
```

---

## Understanding the Event Loop

### Single Event Loop (Main Pattern)

```
┌─────────────────────────────────────┐
│     Main Event Loop (Single)        │
│                                     │
│  ┌─────────┐  ┌─────────┐          │
│  │ Task 1  │  │ Task 2  │          │
│  │ (async) │  │ (async) │          │
│  └─────────┘  └─────────┘          │
│       │            │                │
│       └─────┬──────┘                │
│             ▼                        │
│    Event Loop Scheduler              │
│    (switches between tasks)          │
└─────────────────────────────────────┘
```

**All async tasks run on this single loop**

### Multiple Event Loops (Your Exception)

```
┌─────────────────────────────────────┐
│     Main Event Loop                  │
│  (FastAPI/Uvicorn)                  │
│                                     │
│  ┌─────────┐  ┌─────────┐          │
│  │ Task 1  │  │ Task 2  │          │
│  └─────────┘  └─────────┘          │
└─────────────────────────────────────┘
           │
           │ call_soon_threadsafe()
           ▼
┌─────────────────────────────────────┐
│     Thread 1: IBKR Connection        │
│     (Separate Event Loop)            │
│                                     │
│  ┌─────────┐                       │
│  │ IB Task │                       │
│  └─────────┘                       │
└─────────────────────────────────────┘
```

**IBKR thread has its own event loop, publishes to main loop**

---

## Parallel Event Buses?

### Can You Have Parallel Event Buses?

**Short Answer: YES, but you probably don't need to**

**Technical Answer:**

1. **Single Event Bus (Current)**:
   - One `AsyncEventBus` instance shared across all services
   - All events published to same bus
   - All subscribers run concurrently on same event loop
   - **This is what you have now** ✅

2. **Multiple Event Buses (Possible but Unnecessary)**:
   ```python
   # You COULD create multiple buses
   bus1 = AsyncEventBus()  # For domain events
   bus2 = AsyncEventBus()  # For infrastructure events
   
   # But they'd still run on the same event loop
   # So no performance benefit, just complexity
   ```

3. **Why Single Bus is Better**:
   - Simpler architecture
   - All events in one place
   - Easier debugging
   - No need for multiple buses (event loop handles concurrency)

### Typed Event Bus and Parallelism

**The typed event bus (`subscribe_typed`) doesn't change parallelism**:
- It's just a **type safety wrapper** around the regular event bus
- Still uses the same `AsyncEventBus` underneath
- Still runs on the same event loop
- Still concurrent (not parallel)

**What typed events DO provide:**
- Type safety (Pydantic validation)
- Better IDE support
- Compile-time error checking
- But **same execution model** (concurrent on single loop)

---

## Intuitive Guide: When to Use Sync vs Async

### Rule of Thumb

**Ask yourself: "Does this function need to WAIT for something?"**

### ✅ Use Async If:

1. **Waiting for I/O** (network, file, database)
   ```python
   # ✅ Async: Waiting for network
   async def fetch_data(url):
       response = await httpx.get(url)  # Waits for network
       return response.json()
   ```

2. **Waiting for events**
   ```python
   # ✅ Async: Waiting for event
   async def handle_event(event):
       await event_bus.publish("Event", data)  # Waits for publishing
   ```

3. **Coordination between tasks**
   ```python
   # ✅ Async: Coordinating tasks
   async def process_multiple(items):
       tasks = [process(item) for item in items]
       results = await asyncio.gather(*tasks)  # Waits for all
   ```

### ✅ Use Sync If:

1. **No waiting needed** (pure computation)
   ```python
   # ✅ Sync: No I/O, no waiting
   def calculate_total(prices: list[float]) -> float:
       return sum(prices)  # Instant calculation
   ```

2. **Simple data transformation**
   ```python
   # ✅ Sync: Simple transformation
   def format_message(trade: TradeResult) -> str:
       return f"{trade.ticker}: ${trade.fill_price}"
   ```

3. **Validation logic**
   ```python
   # ✅ Sync: Validation (no I/O)
   def is_valid_ticker(ticker: str) -> bool:
       return ticker.isupper() and len(ticker) <= 5
   ```

### ⚠️ Use Threading If:

1. **Blocking library** (can't be made async)
   ```python
   # ⚠️ Threading: Blocking library
   def connect_ibkr():
       ib = IB()  # Blocking library
       ib.connect("127.0.0.1", 4001)  # Blocks thread
   ```

2. **CPU-bound work** (if multiple cores available)
   ```python
   # ⚠️ Threading: CPU-bound
   def heavy_computation(data):
       # Uses CPU intensively
       return complex_algorithm(data)
   ```

---

## Real Examples from Your Codebase

### Example: AutoTradeService (Pure Async)

**File**: `services/brokerage/auto_trade.py`

```python
async def process_imminent_article(...):
    # All async - no threads
    if not should_process_classification(...):
        return  # No waiting
    
    # Wait for storage (I/O)
    domain_article = await fetch_article_for_trade(...)  # ✅ Async
    
    if not domain_article:
        return
    
    # Build trade request (sync - no I/O)
    trade_request = build_trade_request_for_article(...)  # ✅ Sync
    
    # Publish event (I/O)
    await publish_trade_request(...)  # ✅ Async
```

**Why this pattern:**
- `fetch_article_for_trade` → **Async** (waits for storage I/O)
- `build_trade_request_for_article` → **Sync** (no I/O, just logic)
- `publish_trade_request` → **Async** (waits for event bus I/O)

### Example: IBKR Connection (Thread + Async Bridge)

**File**: `infra/brokerage/connection_manager.py`

```python
# Thread runs blocking code
def _run_ib_connection_thread(self, port: int):
    # Create separate event loop for this thread
    self._ib_event_loop = asyncio.new_event_loop()
    
    # Run async code in thread's event loop
    self._ib_event_loop.run_until_complete(
        self._connect_async(port)  # Async function
    )

# Async function runs in thread's event loop
async def _connect_async(self, port: int):
    ib = IB()  # Blocking library (but in separate thread)
    await ib.connectAsync(...)  # Async call (in thread's loop)
    
    # Publish to main loop (thread-safe)
    self._main_event_loop.call_soon_threadsafe(
        lambda: asyncio.create_task(
            self._publish_connection_status(...)  # Runs on main loop
        )
    )
```

**Why this pattern:**
- `ib_insync` is blocking → **Must use thread**
- Thread has its own event loop → **Can use async inside thread**
- Publishes to main loop → **Main loop processes events asynchronously**

---

## Summary

### Your Architecture:

1. **Main Event Loop**: Single loop runs all async code
2. **Event Bus**: Single bus, concurrent execution (not parallel)
3. **Threading**: Only for blocking libraries (IBKR, WebSocket)
4. **Thread-Safe Publishing**: Threads publish to main loop via `call_soon_threadsafe()`

### Key Takeaways:

- **Async** = Cooperative multitasking on single thread (most of your code)
- **Threading** = True parallelism, only when needed (blocking libraries)
- **Event Bus** = Concurrent (same loop), not parallel (different threads)
- **Typed Events** = Type safety, same execution model

### When to Use What:

- **Async**: I/O operations, event handling, coordination
- **Sync**: Pure functions, data transformations, validation
- **Threading**: Blocking libraries, CPU-bound work, background tasks

Your codebase uses async correctly for 95% of code, and threading only where absolutely necessary (blocking libraries).

