# Why Lifecycle Manager Fixes the `is_running` Problem

## The Problem

Previously, each service maintained its own `is_running` flag:
- `StorageInfrastructureService.is_running`
- `ClassificationInfrastructureService.is_running`
- `NotificationInfrastructureService.is_running`
- `IBKRBrokerageService.is_running`
- All domain listeners had `is_running` flags
- Plus service layer components

**Issues with this approach:**
1. **Redundant State**: The lifecycle manager is the ONLY component that calls `start()` and `stop()`, so each service tracking its own state is redundant
2. **State Synchronization**: Services could have stale state if lifecycle manager calls are out of sync
3. **Inconsistent**: Different services check/update the flag differently
4. **Not Single Source of Truth**: Multiple places tracking the same information

## The Solution: Lifecycle Manager as Single Source of Truth

### How Lifecycle Manager Works

1. **Lifecycle Manager tracks service state:**
   ```python
   class LifecycleManager:
       def __init__(self, ...):
           self._running_services: Set[str] = set()
       
       def is_service_running(self, service_name: str) -> bool:
           """Single source of truth for service running state."""
           return service_name in self._running_services
   ```

2. **Services are idempotent:**
   - `start()` can be called multiple times safely
   - `stop()` can be called multiple times safely
   - No need for guards - event bus prevents duplicate subscriptions

3. **Services don't track lifecycle state:**
   - Removed `self.is_running = False` from all services
   - Services only manage operational state (connections, subscriptions, threads)

## Why This Works

### 1. Event Bus Prevents Duplicate Subscriptions

The event bus already checks if a handler is already subscribed:
```python
def subscribe(self, event_type: str, handler: Callable) -> None:
    if handler not in self._subscribers[event_type]:
        self._subscribers[event_type].append(handler)
    else:
        logger.warning(f"Handler already subscribed to {event_type}")
```

So services can call `start()` multiple times safely - subscriptions won't duplicate.

### 2. Unsubscribing is Safe

Unsubscribing when not subscribed is harmless:
```python
def unsubscribe(self, event_type: str, handler: Callable) -> None:
    if handler in self._subscribers[event_type]:
        self._subscribers[event_type].remove(handler)
```

So services can call `stop()` multiple times safely.

### 3. Lifecycle Manager is the Only Caller

The lifecycle manager is the ONLY component that calls `start()` and `stop()`:
- Application startup: `lifecycle_manager.start_services(services)`
- Application shutdown: `lifecycle_manager.stop_services(services)`

Since only one component orchestrates lifecycle, services don't need to track it themselves.

### 4. Health Checks Use Actual State

Instead of checking `is_running` flags, health checks use actual service state:
- **Brokerage**: `connection_manager.is_healthy()` (checks actual connection)
- **WebSocket**: `metrics_service.get_websocket_stats()["is_connected"]` (checks actual connection)
- **Storage**: Always healthy if repositories exist (stateless operations)

## Implementation Pattern

### Before (Redundant State):
```python
class StorageInfrastructureService:
    def __init__(self, ...):
        self.is_running = False  # ❌ Redundant state
    
    async def start(self) -> None:
        if self.is_running:  # ❌ Guard
            return
        self.is_running = True  # ❌ Track state
        self.event_bus.subscribe(...)
    
    async def stop(self) -> None:
        if not self.is_running:  # ❌ Guard
            return
        self.is_running = False  # ❌ Track state
        self.event_bus.unsubscribe(...)
```

### After (Idempotent, Stateless):
```python
class StorageInfrastructureService:
    def __init__(self, ...):
        # ✅ No is_running flag - lifecycle manager tracks this
    
    async def start(self) -> None:
        """Idempotent: Safe to call multiple times."""
        # ✅ No guard - event bus prevents duplicate subscriptions
        self.event_bus.subscribe(...)
    
    async def stop(self) -> None:
        """Idempotent: Safe to call multiple times."""
        # ✅ No guard - unsubscribing when not subscribed is safe
        self.event_bus.unsubscribe(...)
```

## Special Cases

### WebSocket Service: Thread Control

The WebSocket service needs a flag for thread control (not lifecycle):
```python
# ✅ Operational state for threads (not lifecycle state)
self._threads_should_run = False

def _ping_loop(self) -> None:
    while self._threads_should_run:  # ✅ Thread control, not lifecycle
        # ... ping logic
```

This is **operational state** needed by threads, not lifecycle state. It's renamed to `_threads_should_run` to make this clear.

### Connection Managers

Connection managers have their own state (connection status, reconnect logic). This is **operational state** for managing external resources, not redundant lifecycle state.

## Benefits

1. **Single Source of Truth**: LifecycleManager tracks all service state
2. **Idempotent Services**: Services can be started/stopped multiple times safely
3. **Less State**: Services have less mutable state
4. **Clearer Intent**: Services focus on operations, not lifecycle tracking
5. **Easier Testing**: No need to reset `is_running` flags in tests

## Result

- ✅ **16 services** had `is_running` flags removed
- ✅ **LifecycleManager** is now the single source of truth
- ✅ **Services** are idempotent and stateless regarding lifecycle
- ✅ **Health checks** use actual service state instead of flags

This follows the **stateless infrastructure principle**: Infrastructure services should manage external resources, not track redundant lifecycle state.

