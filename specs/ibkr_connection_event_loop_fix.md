# IBKR Connection Event Loop Fix

## Main Problem

**Event Loop Conflicts**: ib_insync requires an event loop to exist, but when connecting in `asyncio.to_thread()` (thread pool executor), there is no event loop in that thread.

**Error**: `RuntimeError: There is no current event loop in thread 'ThreadPoolExecutor-0_0'`

## Root Cause

1. ib_insync's `connect()` method internally uses async operations
2. These operations call `asyncio.get_event_loop()` which fails if no loop exists
3. Thread pool executors don't have event loops by default

## Solution

**Create a dedicated thread with its own event loop** (similar to WebSocket service pattern):

1. Create a new thread (not thread pool)
2. Create and set a new event loop in that thread
3. Run ib_insync connection in that thread's event loop context
4. Use Future to communicate result back to main async context

## Implementation

```python
# Create Future for result communication
connection_result = asyncio.Future()

def _connect_in_dedicated_thread():
    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Create IB instance and connect
    ib = IB()
    ib.connect("127.0.0.1", port, clientId=client_id)
    
    # Store instance and notify main loop
    self.ib = ib
    self._main_event_loop.call_soon_threadsafe(
        connection_result.set_result, True
    )

# Start dedicated thread
conn_thread = threading.Thread(target=_connect_in_dedicated_thread, daemon=True)
conn_thread.start()

# Wait for result
connected = await asyncio.wait_for(connection_result, timeout=30)
```

## Status

✅ **Fixed** - Connection now runs in dedicated thread with event loop
✅ **Telegram notifications** - Working (events published on failure)
✅ **WebSocket** - Working (connected, ping/pong active)
✅ **YFinance** - Removed completely

