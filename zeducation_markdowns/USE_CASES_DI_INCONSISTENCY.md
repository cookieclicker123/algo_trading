# Use Cases DI Inconsistency - Analysis & Fix

**Date:** 2025-12-08  
**Issue:** Use cases are inconsistently managed - some in ApplicationContainer, some created manually

---

## The Problem

### Current State: Inconsistent Pattern

**Use Cases IN ApplicationContainer (✅ Correct):**
- `NotifyImminentArticleUseCase` ✅
- `NotifyTradeExecutedUseCase` ✅
- `NotifyExitTradeUseCase` ✅
- `ExitTradeUseCase` ✅

**Use Cases CREATED MANUALLY in Microservice Initialization (❌ Wrong):**
- `StoreArticleUseCase` - created in `initialize_storage_microservice()` line 145
- `StoreAuditLogUseCase` - created in `initialize_storage_microservice()` line 146-149
- `ProcessArticleUseCase` - created in `initialize_websocket_microservice()` line 191
- `ClassifyArticleUseCase` - created in `initialize_websocket_microservice()` line 188

---

## Why This Is Wrong

### 1. Breaks Dependency Injection Principle
**Current (Wrong):**
```python
# In initialize_storage_microservice()
store_article_use_case = StoreArticleUseCase(event_bus=event_bus)  # ❌ Manual creation
```

**Should Be:**
```python
# In ApplicationContainer
store_article_use_case = providers.Factory(
    StoreArticleUseCase,
    event_bus=shared.event_bus,
)

# In initialize_storage_microservice()
async def initialize_storage_microservice(
    event_bus: AsyncEventBus,
    storage_config: StorageConfig,
    store_article_use_case: StoreArticleUseCase,  # ✅ Injected
    store_audit_log_use_case: StoreAuditLogUseCase,  # ✅ Injected
) -> StorageMicroservice:
```

### 2. Dependency Graph Not Complete
- ApplicationContainer doesn't show ALL dependencies
- Can't see storage/websocket use cases in the container
- Dependency graph is incomplete

### 3. Harder to Test
- Can't easily override use cases for testing
- Can't inject mocks via container
- Must modify microservice initialization functions

### 4. Inconsistent Architecture
- Some use cases managed by DI container
- Some use cases created manually
- No single source of truth

---

## The Fix

### Step 1: Add Use Cases to ApplicationContainer

```python
# In ApplicationContainer

# Storage use cases
store_article_use_case = providers.Factory(
    StoreArticleUseCase,
    event_bus=shared.event_bus,
)

store_audit_log_use_case = providers.Factory(
    StoreAuditLogUseCase,
    event_bus=shared.event_bus,
    storage_query_service=storage_query_service,  # Needs storage microservice
)

# WebSocket use cases
process_article_use_case = providers.Factory(
    ProcessArticleUseCase,
    event_bus=shared.event_bus,
)

classify_article_use_case = providers.Factory(
    ClassifyArticleUseCase,
    event_bus=shared.event_bus,
)
```

### Step 2: Update Microservice Initialization Functions

**Storage:**
```python
async def initialize_storage_microservice(
    event_bus: AsyncEventBus,
    storage_config: StorageConfig,
    store_article_use_case: StoreArticleUseCase,  # ✅ Injected
    store_audit_log_use_case: StoreAuditLogUseCase,  # ✅ Injected
) -> StorageMicroservice:
    # ... create infra, domain_listener, query_service ...
    
    # Use injected use cases instead of creating
    return StorageMicroservice(
        infra=infra,
        domain_listener=domain_listener,
        query_service=query_service,
        store_article_use_case=store_article_use_case,  # ✅ Injected
        store_audit_log_use_case=store_audit_log_use_case,  # ✅ Injected
    )
```

**WebSocket:**
```python
async def initialize_websocket_microservice(
    event_bus: AsyncEventBus,
    metrics_service,
    telegram_service: Optional[TelegramNotifier] = None,
    benzinga_api_key: Optional[str] = None,
    benzinga_websocket_enabled: bool = False,
    process_article_use_case: ProcessArticleUseCase,  # ✅ Injected
    classify_article_use_case: ClassifyArticleUseCase,  # ✅ Injected
) -> WebSocketMicroservice:
    # ... create infra, domain_listener, services ...
    
    return WebSocketMicroservice(
        infra=infra,
        domain_listener=domain_listener,
        feed_manager=feed_manager,
        health_monitor=health_monitor,
        process_article_use_case=process_article_use_case,  # ✅ Injected
        classify_article_use_case=classify_article_use_case,  # ✅ Injected
    )
```

### Step 3: Update ApplicationContainer Providers

```python
# Storage microservice - now receives use cases
storage_microservice = providers.Factory(
    initialize_storage_microservice,
    event_bus=shared.event_bus,
    storage_config=config.storage_config,
    store_article_use_case=store_article_use_case,  # ✅ Injected
    store_audit_log_use_case=store_audit_log_use_case,  # ✅ Injected
)

# WebSocket microservice - now receives use cases
websocket_microservice_factory = providers.Factory(
    initialize_websocket_microservice,
    event_bus=shared.event_bus,
    benzinga_api_key=config.benzinga_api_key,
    benzinga_websocket_enabled=config.benzinga_websocket_enabled,
    metrics_service=shared.metrics_service,
    telegram_service=telegram_service_factory,  # Will be provided later
    process_article_use_case=process_article_use_case,  # ✅ Injected
    classify_article_use_case=classify_article_use_case,  # ✅ Injected
)
```

### Step 4: Handle Circular Dependencies

**Issue:** `store_audit_log_use_case` needs `storage_query_service`, but `storage_query_service` comes from `storage_microservice`.

**Solution:** Use container override pattern (already used for notification use cases):

```python
# In composition_root.py
storage = await container.storage_microservice()

# Override storage_query_service provider
container.storage_query_service.override(
    providers.Callable(lambda: storage.query_service)
)

# Now create store_audit_log_use_case (it can use storage_query_service)
store_audit_log_use_case = container.store_audit_log_use_case()

# Override storage_microservice to use the use case
# (This is tricky - might need to recreate storage microservice)
```

**Better Solution:** Create `store_audit_log_use_case` AFTER storage microservice is created:

```python
# In ApplicationContainer - define provider that will be called later
store_audit_log_use_case = providers.Factory(
    StoreAuditLogUseCase,
    event_bus=shared.event_bus,
    storage_query_service=storage_query_service,  # Will be overridden
)

# In composition_root.py
storage = await container.storage_microservice()

# Override storage_query_service
container.storage_query_service.override(
    providers.Callable(lambda: storage.query_service)
)

# Now create store_audit_log_use_case
store_audit_log_use_case = container.store_audit_log_use_case()

# Update storage microservice to use injected use case
# (This requires recreating storage microservice or updating it)
```

**Best Solution:** Accept that `store_audit_log_use_case` needs to be created after storage microservice, similar to notification use cases:

```python
# In ApplicationContainer - define but don't create yet
store_audit_log_use_case_provider = providers.Factory(
    StoreAuditLogUseCase,
    event_bus=shared.event_bus,
    storage_query_service=storage_query_service,  # Will be overridden
)

# In composition_root.py - create after storage is ready
storage = await container.storage_microservice()
container.storage_query_service.override(...)
store_audit_log_use_case = container.store_audit_log_use_case_provider()

# Update storage microservice (requires recreating or patching)
# OR: Accept that storage microservice creates its own use cases
#     but other microservices receive theirs via DI
```

---

## Recommended Approach

### Option 1: Hybrid Approach (Pragmatic)
- **Cross-microservice use cases** → ApplicationContainer (notification, brokerage)
- **Internal use cases** → Created in microservice initialization (storage, websocket)

**Rationale:**
- Storage/websocket use cases are internal to their microservices
- They don't need to be shared or overridden
- Keeps microservices self-contained

**Pros:**
- Simpler
- No circular dependency issues
- Microservices remain independent

**Cons:**
- Inconsistent pattern
- Can't override for testing easily

### Option 2: Full DI (Ideal)
- **ALL use cases** → ApplicationContainer
- Microservices receive use cases as dependencies

**Rationale:**
- Complete dependency graph
- Consistent pattern
- Easier to test and override

**Pros:**
- Complete DI
- Consistent architecture
- Better testability

**Cons:**
- More complex (circular dependencies)
- Requires careful ordering in composition root

---

## Recommendation

**Go with Option 2 (Full DI)** because:
1. You're already doing it for notification/brokerage use cases
2. Consistency is important for maintainability
3. Better testability
4. Complete dependency graph

**Implementation Strategy:**
1. Add all use cases to ApplicationContainer
2. Update microservice initialization functions to receive use cases
3. Handle circular dependencies using container overrides (like notification use cases)
4. Update composition root to create use cases in correct order

---

## Impact Assessment

### Files to Change:
1. `src/newsflash/services/containers/application.py` - Add use case providers
2. `src/newsflash/services/storage/__init__.py` - Update initialization function
3. `src/newsflash/services/websocket/__init__.py` - Update initialization function
4. `src/newsflash/services/composition_root.py` - Update initialization order

### Breaking Changes:
- None - internal refactoring only

### Testing Impact:
- Easier to test (can override use cases in container)
- More consistent test setup

---

## Conclusion

You are **absolutely correct** - for proper DI, ALL use cases should be in ApplicationContainer. The current inconsistency breaks the DI pattern and makes the dependency graph incomplete.

**Next Steps:**
1. Review this analysis
2. Decide on approach (Option 1 or 2)
3. Implement the fix
4. Update tests
