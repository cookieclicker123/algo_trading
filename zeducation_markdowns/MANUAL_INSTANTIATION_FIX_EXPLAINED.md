# Manual Instantiation Fix - Explained

## The Problem: Why Manual Instantiation Was Happening

### The Issue

In `composition_root.py`, we were manually creating three services:
```python
# ❌ BEFORE: Manual instantiation
notification_use_case = NotifyImminentArticleUseCase(
    event_bus=event_bus,
    storage_query_service=storage.query_service,
)

auto_trade_service = AutoTradeService(
    event_bus=event_bus,
    storage_query_service=storage.query_service,
    enabled=auto_trading_enabled,
    trade_amount_usd=auto_trade_amount_usd,
)

exit_trade_use_case = ExitTradeUseCase(event_bus=event_bus)
```

### Why This Was Necessary (Before Fix)

**The Root Cause: Async Resource Dependency**

1. **`storage_microservice` is async:**
   ```python
   # In application.py
   storage_microservice = providers.Factory(
       initialize_storage_microservice,  # This is an async function!
       event_bus=shared.event_bus,
       storage_config=config.storage_config,
   )
   ```

2. **When you call it, you get a coroutine:**
   ```python
   storage = await container.storage_microservice()  # Must await!
   ```

3. **Other services need `storage.query_service`:**
   ```python
   # These need storage.query_service
   notification_use_case = NotifyImminentArticleUseCase(
       storage_query_service=storage.query_service  # From awaited storage
   )
   ```

4. **The container providers couldn't handle this:**
   ```python
   # ❌ This doesn't work because storage_microservice is async
   notification_use_case = providers.Factory(
       NotifyImminentArticleUseCase,
       storage_query_service=providers.Factory(
           lambda ms: ms.query_service,
           storage_microservice,  # This is a coroutine, not the actual service!
       ),
   )
   ```
   
   **Problem:** When the container tries to resolve `storage_microservice`, it gets a coroutine (Future), not the actual `StorageMicroservice` instance. The lambda `lambda ms: ms.query_service` tries to access `.query_service` on a coroutine, which fails.

### Why Manual Instantiation Was "Necessary"

We had to:
1. First await `storage = await container.storage_microservice()`
2. Then manually extract `storage.query_service`
3. Then manually create the dependent services

This broke the DI pattern because we were bypassing the container.

---

## The Solution: Provider Override Pattern

### How We Fixed It

**Step 1: Define providers in container (they exist but can't resolve async dependency yet)**
```python
# In application.py
storage_query_service = providers.Callable(
    lambda storage_ms: storage_ms.query_service
)

notification_use_case = providers.Factory(
    NotifyImminentArticleUseCase,
    event_bus=shared.event_bus,
    storage_query_service=storage_query_service,  # Will be overridden
)
```

**Step 2: After awaiting storage, override the provider with the actual value**
```python
# In composition_root.py
# First, await the async resource
storage = await container.storage_microservice()

# Then, override the provider with the actual awaited value
container.storage_query_service.override(
    providers.Callable(lambda: storage.query_service)
)

# Now container providers work!
notification_use_case = container.notification_use_case()  # ✅ Uses container
auto_trade_service = container.auto_trade_service()  # ✅ Uses container
exit_trade_use_case = container.exit_trade_use_case()  # ✅ Uses container
```

### Why This Works

1. **Provider Override:** `container.storage_query_service.override()` replaces the provider with one that returns the already-awaited `storage.query_service`
2. **Dependency Resolution:** When `container.notification_use_case()` is called:
   - Container sees it needs `storage_query_service`
   - Container calls the overridden provider: `lambda: storage.query_service`
   - Gets the actual `StorageQueryService` instance
   - Passes it to `NotifyImminentArticleUseCase`
   - ✅ Works!

3. **All Dependencies Resolved:** All three services now use container providers instead of manual instantiation

---

## Key Concepts Explained

### 1. Why Async Resources Are Hard

**Async functions return coroutines:**
```python
async def initialize_storage_microservice(...) -> StorageMicroservice:
    # ... async initialization
    return StorageMicroservice(...)

# When you call it:
result = initialize_storage_microservice(...)  # Returns coroutine, not StorageMicroservice!
actual_result = await result  # Must await to get actual value
```

**DI containers work synchronously:**
- When container resolves `storage_microservice`, it gets a coroutine
- Container doesn't know it needs to await
- Lambda functions try to access attributes on coroutine → fails

### 2. Provider Override Pattern

**Override allows us to replace a provider after async initialization:**
```python
# Define provider (can't resolve async yet)
storage_query_service = providers.Callable(lambda ms: ms.query_service)

# After async initialization, override with actual value
container.storage_query_service.override(
    providers.Callable(lambda: storage.query_service)  # Uses awaited storage
)
```

**Benefits:**
- ✅ Container still manages dependencies
- ✅ Works with async resources
- ✅ All services use DI container
- ✅ No manual instantiation

### 3. Why This Is Better Than Manual Instantiation

**Before (Manual):**
```python
# ❌ Bypasses container
notification_use_case = NotifyImminentArticleUseCase(
    event_bus=event_bus,  # Where did event_bus come from?
    storage_query_service=storage.query_service,  # Manual extraction
)
```

**Problems:**
- Hard to test (can't override providers)
- Hard to mock dependencies
- Bypasses DI container
- Inconsistent with rest of codebase

**After (DI Container):**
```python
# ✅ Uses container
notification_use_case = container.notification_use_case()
```

**Benefits:**
- ✅ Container manages all dependencies
- ✅ Easy to test (override providers)
- ✅ Easy to mock (override providers)
- ✅ Consistent with rest of codebase
- ✅ All dependencies tracked in one place

---

## Future Improvements

### Option 1: Use `providers.Resource` (If Available)

Some DI frameworks support async resources:
```python
# Hypothetical - dependency-injector doesn't support this directly
storage_microservice = providers.Resource(
    initialize_storage_microservice,
    event_bus=shared.event_bus,
)
```

But `dependency-injector` doesn't have built-in async resource support, so we use the override pattern.

### Option 2: Async Factory Pattern

Create a helper that handles async initialization:
```python
async def create_with_async_deps(container, provider_name, *args):
    """Helper to create services that depend on async resources."""
    # Await async dependencies first
    storage = await container.storage_microservice()
    container.storage_query_service.override(...)
    
    # Then create the service
    return container.get(provider_name)(*args)
```

But the override pattern is simpler and clearer.

---

## Summary

**The Problem:**
- Async resources (like `storage_microservice`) return coroutines
- DI containers can't automatically await coroutines
- Manual instantiation was necessary to extract dependencies

**The Solution:**
- Use provider override pattern
- Await async resource first
- Override provider with actual value
- Use container providers for all services

**Result:**
- ✅ No more manual instantiation
- ✅ All services use DI container
- ✅ Better testability
- ✅ Consistent DI pattern

**Key Takeaway:**
When you have async resources, use the **provider override pattern**:
1. Define providers in container (they reference async resource)
2. Await async resource in composition root
3. Override provider with actual awaited value
4. Use container providers normally

This maintains proper DI while handling async dependencies!

