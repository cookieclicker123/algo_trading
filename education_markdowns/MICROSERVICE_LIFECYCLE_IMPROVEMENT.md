# Microservice Lifecycle Management Improvement

## Issues Identified

### 1. **service_initialization.py is Still 524 Lines** ❌

**Problem:** The composition root is too long because:
- `start_services()`: ~110 lines manually starting each component
- `stop_services()`: ~110 lines manually stopping each component  
- `get_stats()` and `is_healthy()`: Additional lines

**Current Pattern:**
```python
# service_initialization.py - WRONG ❌
async def start_services(services: Services) -> None:
    await services.storage.infra.start()
    await services.storage.domain_listener.start()
    await services.storage.query_service.start()
    await services.storage.store_article_use_case.start()
    # ... 100+ more lines
```

### 2. **Classification Doesn't Have a "Service" Class** ✅ (This is Fine!)

**Reality Check:**
- ✅ Classification has: `ClassificationInfrastructureService` (infra)
- ✅ Classification has: `ClassificationDomainListener` (domain)
- ✅ Classification has: Pure functions in `request_builder.py` (services)
- ❌ Classification doesn't need a "service class" because it's **purely event-driven**

**Why This is OK:**
- Classification is **reactive** - it responds to events
- Pure functions are sufficient for classification logic
- Infrastructure + Domain Listener handle all stateful behavior
- This is actually **better** - more stateless!

### 3. **Each Microservice Should Manage Its Own Lifecycle** ✅ (Best Practice)

**Solution:** Add `start()` and `stop()` methods to each microservice.

---

## Solution: Self-Managing Microservices

### Pattern: Each Microservice Manages Its Own Lifecycle

**File Structure:**
```
services/
├── storage/
│   └── __init__.py        # Has start() and stop() methods
├── classification/
│   └── __init__.py        # Has start() and stop() methods  
├── notification/
│   └── __init__.py        # Has start() and stop() methods
├── brokerage/
│   └── __init__.py        # Has start() and stop() methods
├── websocket/
│   └── __init__.py        # Has start() and stop() methods
└── service_initialization.py  # Just calls microservice.start()/stop()
```

### Example: Storage Microservice with Lifecycle

```python
# services/storage/__init__.py
@dataclass
class StorageMicroservice:
    infra: StorageInfrastructureService
    domain_listener: StorageDomainListener
    query_service: StorageQueryService
    store_article_use_case: StoreArticleUseCase
    store_audit_log_use_case: StoreAuditLogUseCase
    
    async def start(self) -> None:
        """Start all storage microservice components."""
        logger.info("Starting storage microservice...")
        
        # Start infrastructure FIRST
        await self.infra.start()
        logger.info("Storage infrastructure started")
        
        # Start domain listener
        await self.domain_listener.start()
        logger.info("Storage domain listener started")
        
        # Start services
        await self.query_service.start()
        logger.info("Storage query service started")
        
        # Start use cases
        await self.store_article_use_case.start()
        await self.store_audit_log_use_case.start()
        logger.info("Storage use cases started")
        
        logger.info("Storage microservice started")
    
    async def stop(self) -> None:
        """Stop all storage microservice components."""
        logger.info("Stopping storage microservice...")
        
        # Stop use cases first
        await self.store_audit_log_use_case.stop()
        await self.store_article_use_case.stop()
        
        # Stop services
        await self.query_service.stop()
        
        # Stop domain listener
        await self.domain_listener.stop()
        
        # Stop infrastructure last
        await self.infra.stop()
        
        logger.info("Storage microservice stopped")
```

### Simplified Composition Root

```python
# service_initialization.py - RIGHT ✅
async def start_services(services: Services) -> None:
    """Start all services."""
    logger.info("Starting all services...")
    
    try:
        # Resolve bot conflicts (shared concern)
        await resolve_bot_conflicts(...)
        
        # Start Telegram trade handlers (shared)
        if services.trade_handler:
            await services.trade_handler.start()
        
        # Start each microservice (they manage themselves!)
        await services.storage.start()
        await services.classification.start()
        await services.notification.start()
        await services.brokerage.start()
        await services.websocket.start()
        
        logger.info("All services started successfully")
    except Exception as e:
        logger.error("Failed to start services", error=str(e))
        raise

async def stop_services(services: Services) -> None:
    """Stop all services."""
    logger.info("Stopping all services...")
    
    try:
        # Stop microservices in reverse order
        await services.websocket.stop()
        await services.brokerage.stop()
        await services.notification.stop()
        await services.classification.stop()
        await services.storage.stop()
        
        # Stop shared services
        if services.trade_handler:
            await services.trade_handler.stop()
        
        logger.info("All services stopped successfully")
    except Exception as e:
        logger.error("Failed to stop services", error=str(e))
        raise
```

**Result:** `service_initialization.py` reduced from **524 lines → ~150 lines** ✅

---

## Classification Microservice: No Service Class Needed ✅

**Question:** "Why doesn't classification have a service class like storage has `StorageQueryService`?"

**Answer:** Classification is **purely event-driven** and doesn't need a service class:

### Storage Pattern (Has Service Class)
```python
# Storage needs query service for direct calls
storage.query_service.fetch_article(id)  # Direct call
```

### Classification Pattern (Pure Functions)
```python
# Classification is reactive - responds to events
# Pure functions handle logic
from ..services.classification import create_classification_request

request = create_classification_request(article)  # Pure function
await event_bus.publish("Domain.ClassificationRequested", request)
# Infrastructure + Domain Listener handle the rest automatically
```

**Why This Works:**
- ✅ Pure functions: `create_classification_request()`, `validate_classification_request()`
- ✅ Infrastructure: `ClassificationInfrastructureService` (stateful - API client)
- ✅ Domain Listener: `ClassificationDomainListener` (handles events)
- ✅ No service class needed - everything is event-driven!

**This is actually BETTER** - more stateless, more functional! ✅

---

## Implementation Plan

1. ✅ Add `start()` and `stop()` methods to each microservice dataclass
2. ✅ Move start/stop logic from `service_initialization.py` into each microservice
3. ✅ Simplify `service_initialization.py` to just call `microservice.start()/stop()`
4. ✅ Handle cross-microservice dependencies (e.g., websocket needs telegram)
5. ✅ Handle shared services (telegram trade handlers, bot conflict resolution)

---

## Benefits

1. **Separation of Concerns** ✅
   - Each microservice knows how to start/stop itself
   - Composition root just orchestrates

2. **Testability** ✅
   - Can test microservice lifecycle in isolation
   - No need to mock entire Services container

3. **Maintainability** ✅
   - Changes to microservice startup don't affect composition root
   - Easy to add/remove microservices

4. **Readability** ✅
   - Composition root is now ~150 lines (was 524)
   - Clear separation of responsibilities

---

## Conclusion

- ✅ **Classification is fine as-is** - no service class needed (purely event-driven)
- ✅ **Each microservice should manage its own lifecycle** (best practice)
- ✅ **Composition root should be minimal** (~150 lines, not 524)

Let's implement this! 🚀

