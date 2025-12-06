# asyncio.Lock vs threading.Lock Explained

## Quick Answer

**`asyncio.Lock`** = For protecting shared data in **async code** (same event loop, same thread)
**`threading.Lock`** = For protecting shared data in **threads** (different threads, true parallelism)

**Key Difference**: `asyncio.Lock` is **non-blocking** (yields control), `threading.Lock` is **blocking** (waits)

---

## Fundamental Differences

| Aspect | `asyncio.Lock` | `threading.Lock` |
|--------|----------------|------------------|
| **Context** | Async code (same event loop) | Threads (different threads) |
| **Blocking** | Non-blocking (yields to event loop) | Blocking (waits for lock) |
| **Syntax** | `async with lock:` | `with lock:` |
| **When to use** | Protecting shared data in async functions | Protecting shared data accessed from threads |
| **Performance** | Very fast (cooperative) | Slightly slower (OS-level) |
| **Deadlock risk** | Lower (cooperative, easier to reason about) | Higher (true parallelism, harder to debug) |

---

## Real Examples from Your Codebase

### Example 1: `asyncio.Lock` in Event Bus

**File**: `shared/event_bus.py` (line 27, 38)

```python
class AsyncEventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._lock = asyncio.Lock()  # ✅ Async lock for async code
    
    async def publish(self, event_type: str, event_data: Any) -> None:
        # Protect subscriber list from concurrent modifications
        async with self._lock:  # ✅ Async context manager
            subscribers = self._subscribers[event_type].copy()
        
        # Now subscribers are safe to use (copied while locked)
        # ... rest of publish logic
```

**Why `asyncio.Lock` here?**
- All code is **async** (runs on same event loop)
- Multiple async tasks might call `publish()` concurrently
- Need to protect `_subscribers` dict from race conditions
- Lock is **non-blocking** - if another task has the lock, current task yields to event loop

**What happens:**
1. Task A calls `publish()` → Acquires lock → Copies subscribers
2. Task B calls `publish()` → Tries to acquire lock → **Yields to event loop** (doesn't block)
3. Task A releases lock → Event loop resumes Task B → Task B acquires lock

**Key Point**: Tasks **cooperate** - they yield control instead of blocking

---

### Example 2: `asyncio.Lock` in Storage Query Service

**File**: `services/storage/query_service.py` (line 82, 154, 178)

```python
class StorageQueryService:
    def __init__(self, ...):
        # Pending fetch coordination: article_id -> (asyncio.Event, result, timestamp)
        self._pending_fetches: Dict[str, tuple[asyncio.Event, Optional[StoredArticle], datetime]] = {}
        self._fetch_lock = asyncio.Lock()  # ✅ Async lock for async code
    
    async def fetch_article(self, article_id: str, ...) -> Optional[DomainArticle]:
        # Protect _pending_fetches dict from concurrent modifications
        async with self._fetch_lock:  # ✅ Async context manager
            if article_id not in self._pending_fetches:
                # First fetch - create Event
                fetch_event_obj = asyncio.Event()
                self._pending_fetches[article_id] = (fetch_event_obj, None, datetime.now())
                # Publish fetch request...
            else:
                # Another fetch already in progress - reuse the Event
                fetch_event_obj, _, _ = self._pending_fetches[article_id]
        
        # Wait for Event (outside lock - lock is released)
        await asyncio.wait_for(fetch_event_obj.wait(), timeout=timeout)
        
        # Get result (re-acquire lock)
        async with self._fetch_lock:
            _, stored_article, _ = self._pending_fetches.get(article_id, (None, None, None))
```

**Why `asyncio.Lock` here?**
- All code is **async** (runs on same event loop)
- Multiple async tasks might fetch the same article concurrently
- Need to protect `_pending_fetches` dict from race conditions
- Lock ensures only one task modifies the dict at a time

**Race condition prevented:**
- **Without lock**: Two tasks fetch same article → Both create Events → Both publish requests → Duplicate work
- **With lock**: Two tasks fetch same article → First task creates Event → Second task reuses Event → Single request

**Key Point**: Lock is held **briefly** (just to check/modify dict), then released while waiting for Event

---

### Example 3: `asyncio.Lock` in Connection Manager

**File**: `infra/brokerage/connection_manager.py` (line 67, 121, 205)

```python
class IBKRConnectionManager:
    def __init__(self, ...):
        self._connection_lock: Optional[asyncio.Lock] = None  # ✅ Async lock
    
    async def start(self) -> None:
        # Initialize connection lock (will be set in async context)
        if self._connection_lock is None:
            try:
                self._main_event_loop = asyncio.get_running_loop()
                self._connection_lock = asyncio.Lock()  # ✅ Create in async context
            except RuntimeError:
                self._main_event_loop = asyncio.get_event_loop()
                self._connection_lock = asyncio.Lock()
    
    async def ensure_connected(self, timeout_seconds: Optional[float] = None) -> IB:
        if self._connection_lock is None:
            self._connection_lock = asyncio.Lock()
        
        # Protect connection logic from concurrent calls
        async with self._connection_lock:  # ✅ Async context manager
            if self.is_connected and self.ib:
                return self.ib
            
            # Connection logic...
```

**Why `asyncio.Lock` here?**
- All code is **async** (runs on same event loop)
- Multiple async tasks might call `ensure_connected()` concurrently
- Need to prevent multiple connection attempts at the same time
- Lock ensures only one connection attempt happens at a time

**Race condition prevented:**
- **Without lock**: Two tasks call `ensure_connected()` → Both see `not connected` → Both try to connect → Duplicate connections
- **With lock**: Two tasks call `ensure_connected()` → First task acquires lock → Second task waits → First task connects → Second task sees connected

**Key Point**: Lock prevents **duplicate connection attempts**

---

### Example 4: `threading.Lock` in Metrics Service

**File**: `services/metrics/metrics_service.py` (line 48, 204, 283)

```python
class MetricsService:
    def __init__(self, event_bus: AsyncEventBus):
        # Statistics aggregated from events
        # Using defaultdict for thread-safe counters
        self._lock = threading.Lock()  # ✅ Thread lock (accessed from async handlers)
        
        self._classification_stats = {
            "classifications_requested": 0,
            "classifications_completed": 0,
            # ...
        }
    
    async def _handle_classification_requested(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle classification requested event."""
        with self._lock:  # ✅ Thread lock (not async!)
            self._classification_stats["classifications_requested"] += 1
    
    def get_classification_stats(self, ...) -> Dict[str, Any]:
        """Get classification statistics."""
        with self._lock:  # ✅ Thread lock
            return {
                **self._classification_stats,
                "model": model,
                # ...
            }
```

**Why `threading.Lock` here?**
- Event handlers are **async** (run on event loop)
- But stats dict is accessed from **multiple async tasks concurrently**
- `threading.Lock` works in async context (can be used from async code)
- Protects stats dict from race conditions

**Wait, why not `asyncio.Lock`?**
- **Could use `asyncio.Lock`** - both work in async context
- **Using `threading.Lock`** is simpler here (no `async with` needed)
- `threading.Lock` is **slightly faster** for simple operations (no async overhead)
- Both are safe in async context, but `threading.Lock` is more common for simple counters

**Key Point**: `threading.Lock` can be used in async code, but `asyncio.Lock` is more idiomatic

---

### Example 5: `threading.Lock` in WebSocket Service

**File**: `infra/websocket/service.py` (line 99)

```python
class BenzingaWebSocketMicroservice:
    def __init__(self, ...):
        # Thread management
        self._lock = threading.Lock()  # ✅ Thread lock (protects thread state)
        self._ping_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self.websocket_thread: Optional[threading.Thread] = None
```

**Why `threading.Lock` here?**
- WebSocket service uses **threads** (not just async)
- Thread state (`_ping_thread`, `_monitor_thread`, etc.) is accessed from **multiple threads**
- Need to protect thread state from race conditions
- `threading.Lock` is required (can't use `asyncio.Lock` across threads)

**Key Point**: When you have **actual threads**, you need `threading.Lock`

---

## When to Use Each

### Use `asyncio.Lock` When:

1. **All code is async** (same event loop)
   ```python
   # ✅ Good: All async
   async def update_counter(self):
       async with self._lock:  # ✅ asyncio.Lock
           self._count += 1
   ```

2. **Protecting shared data in async functions**
   ```python
   # ✅ Good: Async function protecting shared dict
   async def add_subscriber(self, event_type: str, handler: Callable):
       async with self._lock:  # ✅ asyncio.Lock
           self._subscribers[event_type].append(handler)
   ```

3. **Want cooperative multitasking** (tasks yield instead of blocking)
   ```python
   # ✅ Good: Lock yields to event loop
   async def process(self):
       async with self._lock:  # ✅ Yields if another task has lock
           # Critical section
   ```

### Use `threading.Lock` When:

1. **You have actual threads** (different threads accessing shared data)
   ```python
   # ✅ Good: Thread accessing shared data
   def thread_function(self):
       with self._lock:  # ✅ threading.Lock
           self._shared_data += 1
   ```

2. **Simple counters in async code** (optional, but common)
   ```python
   # ✅ Good: Simple counter (threading.Lock is fine)
   async def increment(self):
       with self._lock:  # ✅ threading.Lock (simpler than async with)
           self._count += 1
   ```

3. **Protecting data accessed from both threads and async code**
   ```python
   # ✅ Good: Accessed from both contexts
   def thread_function(self):
       with self._lock:  # ✅ threading.Lock
           self._data = value
   
   async def async_function(self):
       with self._lock:  # ✅ threading.Lock (works in async too)
           return self._data
   ```

### ⚠️ Don't Mix Them

```python
# ❌ BAD: Mixing lock types
self._async_lock = asyncio.Lock()
self._thread_lock = threading.Lock()

# ❌ BAD: Using threading.Lock in async with
async def bad():
    async with self._thread_lock:  # ❌ Wrong! threading.Lock doesn't support async with
        pass

# ❌ BAD: Using asyncio.Lock from thread
def thread_function():
    with self._async_lock:  # ❌ Wrong! asyncio.Lock doesn't work across threads
        pass
```

---

## How They Work Internally

### `asyncio.Lock` (Cooperative)

```python
# Simplified internal behavior
class AsyncLock:
    def __init__(self):
        self._waiters = []  # Queue of tasks waiting for lock
        self._locked = False
    
    async def acquire(self):
        if not self._locked:
            self._locked = True
            return
        
        # Lock is held - yield to event loop
        future = asyncio.Future()
        self._waiters.append(future)
        await future  # ✅ Yields to event loop (cooperative)
    
    def release(self):
        self._locked = False
        if self._waiters:
            # Wake up next waiter
            waiter = self._waiters.pop(0)
            waiter.set_result(None)  # Resumes waiting task
```

**Key Point**: When lock is held, tasks **yield to event loop** (cooperative)

### `threading.Lock` (Blocking)

```python
# Simplified internal behavior (OS-level)
class ThreadLock:
    def __init__(self):
        self._lock = _thread.allocate_lock()  # OS-level lock
    
    def acquire(self):
        self._lock.acquire()  # ✅ Blocks thread (OS-level wait)
        # Thread is blocked until lock is released
    
    def release(self):
        self._lock.release()  # ✅ Wakes up waiting thread
```

**Key Point**: When lock is held, threads **block** (OS-level wait)

---

## Performance Comparison

### `asyncio.Lock` Performance

- **Very fast** (cooperative, no OS calls)
- **Low overhead** (just scheduling tasks)
- **Scales well** (thousands of locks, minimal overhead)

### `threading.Lock` Performance

- **Fast** (OS-level, but optimized)
- **Slightly higher overhead** (OS system calls)
- **Scales well** (hundreds of locks, minimal overhead)

**For your use case**: Both are fast enough, difference is negligible

---

## Common Patterns in Your Codebase

### Pattern 1: Protecting Dict/List in Async Code

```python
# ✅ Pattern: asyncio.Lock for async dict access
class MyService:
    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
    
    async def add_item(self, key: str, value: Any):
        async with self._lock:
            self._data[key] = value
    
    async def get_item(self, key: str) -> Optional[Any]:
        async with self._lock:
            return self._data.get(key)
```

**Used in**: `event_bus.py`, `query_service.py`, `connection_manager.py`

### Pattern 2: Simple Counters in Async Code

```python
# ✅ Pattern: threading.Lock for simple counters
class MyService:
    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()  # Simpler than asyncio.Lock
    
    async def increment(self):
        with self._lock:  # No async with needed
            self._count += 1
```

**Used in**: `metrics_service.py`

### Pattern 3: Protecting Thread State

```python
# ✅ Pattern: threading.Lock for thread state
class MyService:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
    
    def start_thread(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run)
                self._thread.start()
```

**Used in**: `websocket/service.py`

---

## Best Practices

### ✅ DO:

1. **Use `asyncio.Lock` in async code** (more idiomatic)
   ```python
   async def update(self):
       async with self._lock:  # ✅
           self._data = value
   ```

2. **Use `threading.Lock` for simple counters** (optional, but fine)
   ```python
   async def increment(self):
       with self._lock:  # ✅ threading.Lock is fine for simple ops
           self._count += 1
   ```

3. **Hold locks briefly** (release as soon as possible)
   ```python
   async def process(self):
       async with self._lock:
           data = self._data.copy()  # Copy while locked
       # Lock released - process data outside lock
       result = await process_data(data)
   ```

4. **Use context managers** (`async with` or `with`)
   ```python
   # ✅ Good: Automatic release
   async with self._lock:
       # Critical section
   # Lock automatically released
   ```

### ❌ DON'T:

1. **Don't hold locks during I/O** (blocks other tasks)
   ```python
   # ❌ BAD: Holding lock during I/O
   async def bad(self):
       async with self._lock:
           await self.fetch_data()  # ❌ Blocks other tasks from acquiring lock
           self._data = result
   ```

2. **Don't mix lock types** (use one consistently)
   ```python
   # ❌ BAD: Mixing lock types
   self._async_lock = asyncio.Lock()
   self._thread_lock = threading.Lock()
   ```

3. **Don't forget to release locks** (use context managers)
   ```python
   # ❌ BAD: Manual release (easy to forget)
   await self._lock.acquire()
   try:
       self._data = value
   finally:
       self._lock.release()  # ❌ Easy to forget
   
   # ✅ GOOD: Context manager
   async with self._lock:
       self._data = value  # ✅ Automatically released
   ```

---

## Summary

### Your Codebase Usage:

1. **`asyncio.Lock`** (3 instances):
   - `event_bus.py` - Protecting subscriber dict
   - `query_service.py` - Protecting pending fetches dict
   - `connection_manager.py` - Protecting connection logic

2. **`threading.Lock`** (2 instances):
   - `metrics_service.py` - Protecting stats dicts (simple counters)
   - `websocket/service.py` - Protecting thread state

### Key Takeaways:

- **`asyncio.Lock`** = For async code, cooperative (yields to event loop)
- **`threading.Lock`** = For threads or simple counters, blocking (OS-level wait)
- **Both work in async code**, but `asyncio.Lock` is more idiomatic
- **Use context managers** (`async with` or `with`) for automatic cleanup
- **Hold locks briefly** - release before I/O operations

Your codebase uses locks correctly! 🎯

