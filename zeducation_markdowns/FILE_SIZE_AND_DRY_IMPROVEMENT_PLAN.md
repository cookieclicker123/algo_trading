# File Size Reduction & DRY Improvement Plan

**Date:** 2025-12-04  
**Goal:** Achieve 10/10 DRY and reduce large files to <400 lines

---

## Part 1: "Multiple Instances" Explanation

### What Are "Multiple Instances"?

**"Multiple instances"** refers to running the same application multiple times:

**Scenarios:**
1. **Multiple servers** - Load balancing across 2+ servers
2. **Multiple processes** - Running same app in different processes
3. **Development vs Production** - Same codebase, different environments
4. **Horizontal scaling** - Adding more app instances for capacity

### The Problem with In-Memory `processed_ids`

**Example:**
```python
class ArticleRepository:
    def __init__(self):
        self.processed_ids: Set[str] = set()  # ❌ In-memory state
```

**What happens with multiple instances:**

**Instance A (Server 1):**
```python
# Instance A processes article "benzinga:12345"
self.processed_ids.add("benzinga:12345")  # ✅ Added to Instance A's memory
```

**Instance B (Server 2):**
```python
# Instance B doesn't know about "benzinga:12345"
# Instance B's processed_ids set is EMPTY (different memory space)
# Instance B processes same article again! ❌ DUPLICATE!
```

**Why This Happens:**
- Each instance has its own memory space
- `processed_ids` set lives in RAM (not shared)
- Instance A's memory ≠ Instance B's memory
- No way to share state between instances

**Result:**
- ❌ Duplicate processing
- ❌ Inconsistent state
- ❌ Data corruption risk

### File System Solution

**With file system:**
```python
class ArticleRepository:
    async def store_article(self, article_id: str, article_data: dict):
        # Check file system (shared storage)
        existing_articles = await self._load_articles()  # ✅ Reads from disk
        if any(self._get_article_id_from_data(a) == article_id for a in existing_articles):
            return  # ✅ Skip - already exists
```

**Why This Works:**
- ✅ File system is shared storage (all instances read same files)
- ✅ Instance A writes → Instance B can read
- ✅ Consistent state across instances
- ✅ No duplicate processing

**Analogy:**
- **Memory state** = Each person has their own notebook (not shared)
- **File system** = Shared whiteboard (everyone can see and write)

---

## Part 2: File Size Reduction Opportunities

### Files >400 Lines (Target: <400 lines each)

| File | Current Lines | Target | Strategy |
|------|---------------|--------|----------|
| `connection_manager.py` | 642 | <400 | Split into: Connection, Keepalive, Reconnection |
| `websocket/service.py` | 627 | <400 | Split into: Service, ConnectionManager, MessageHandler |
| `brokerage/listener.py` | 494 | <400 | Extract base class, split handlers |
| `storage/listener.py` | 455 | <400 | Extract base class, split handlers |
| `trade_executor_extended_hours.py` | 416 | <400 | Extract price logic, order logic |
| `notification/notification.py` | 402 | <400 | Extract queue processing, message sending |

**Total Reduction Needed:** ~600 lines across 6 files

---

## Part 3: Deduplication Opportunities (DRY)

### Pattern 1: Domain Listener Event Handling (5+ instances)

**Current Pattern (Repeated 5+ times):**
```python
async def _handle_domain_xxx_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
    try:
        # Step 1: VALIDATE domain event
        domain_event = XxxRequestedDomainEvent(**event_data)
        
        # Step 2: VALIDATE domain model
        if not self.validator.is_valid(...):
            logger.warning(...)
            return
        
        # Step 3: MAP domain model → infrastructure format
        infra_request_data = self.mapper.to_infrastructure_model(...)
        
        # Step 4: PUBLISH infrastructure event
        infra_event = InfrastructureXxxEvent(...)
        await self.event_bus.publish("XxxRequested", infra_event.model_dump())
        
    except Exception as e:
        logger.error(..., error=str(e), exc_info=True)
```

**Files with this pattern:**
- `domain/brokerage/listener.py` (line 124)
- `domain/classification/listener.py` (line 102)
- `domain/storage/listener.py` (line 125)
- `domain/notification/listener.py` (line 93)
- `domain/websocket/listener.py` (similar pattern)

**Solution:** Create `BaseDomainListener` class

---

### Pattern 2: Infrastructure Event Handling (5+ instances)

**Current Pattern (Repeated 5+ times):**
```python
async def _handle_infra_xxx_from_bus(self, event_type: str, event_data: Dict[str, Any]) -> None:
    try:
        # Step 1: VALIDATE infrastructure event
        infra_event = InfrastructureXxxEvent(**event_data)
        
        # Step 2: FACTORY creates domain model
        domain_model = self.factory.create_from_infrastructure_event(infra_event)
        
        if not domain_model:
            logger.warning(...)
            return
        
        # Step 3: PUBLISH domain event
        await self.publish_xxx(domain_model, ...)
        
    except Exception as e:
        logger.error(..., error=str(e), exc_info=True)
```

**Files with this pattern:**
- `domain/brokerage/listener.py` (multiple handlers)
- `domain/classification/listener.py` (multiple handlers)
- `domain/storage/listener.py` (multiple handlers)
- `domain/notification/listener.py` (multiple handlers)
- `domain/websocket/listener.py` (multiple handlers)

**Solution:** Extract to base class methods

---

### Pattern 3: Error Handling Decorator (100+ instances)

**Current Pattern (Repeated everywhere):**
```python
try:
    # ... code ...
except Exception as e:
    logger.error(..., error=str(e), exc_info=True)
    # Sometimes: publish error event
```

**Solution:** Create `@handle_errors` decorator

---

### Pattern 4: Factory `create_from_dict` Methods (7+ instances)

**Current Pattern (Repeated 7+ times):**
```python
@staticmethod
def create_from_dict(data: Dict[str, Any]) -> Optional[Model]:
    try:
        # Validate required fields
        if not data.get("field"):
            return None
        
        # Create model
        return Model(**data)
    except Exception as e:
        logger.error(...)
        return None
```

**Files:**
- `domain/storage/factories.py` (2 methods)
- `domain/notification/factories.py` (1 method)
- `domain/websocket/factories.py` (1 method)
- `domain/brokerage/factories.py` (2 methods)

**Solution:** Create `BaseFactory` class with generic `create_from_dict`

---

### Pattern 5: Repository File I/O Patterns (2+ instances)

**Current Pattern (Similar in both):**
```python
async def _load_articles(self) -> List[Dict[str, Any]]:
    if not self.json_file.exists():
        return []
    async with aiofiles.open(self.json_file, 'r') as f:
        content = await f.read()
        return json.loads(content) if content.strip() else []

async def _save_articles(self, articles: List[Dict[str, Any]]):
    async with aiofiles.open(self.json_file, 'w') as f:
        await f.write(json.dumps(articles, indent=2, default=str))
```

**Files:**
- `infra/storage/article_repository.py`
- `infra/storage/audit_repository.py`

**Solution:** Create `BaseRepository` class with file I/O helpers

---

## Part 4: Implementation Plan

### Phase 1: Create Base Classes (DRY)

**Priority: HIGH** (Affects 5+ files each)

1. **Create `BaseDomainListener`**
   - Extract common event handling pattern
   - Generic `handle_domain_request` method
   - Generic `handle_infrastructure_event` method
   - Error handling built-in

2. **Create `BaseFactory`**
   - Generic `create_from_dict` method
   - Common validation patterns
   - Error handling built-in

3. **Create `BaseRepository`**
   - File I/O helpers
   - Common load/save patterns
   - Path management

4. **Create `@handle_errors` decorator**
   - Consistent error handling
   - Logging built-in
   - Optional error event publishing

**Estimated Reduction:** ~500 lines of duplicated code

---

### Phase 2: Split Large Files

**Priority: MEDIUM** (Improves maintainability)

1. **Split `connection_manager.py` (642 → 3 files)**
   - `connection.py` - Core connection logic (~200 lines)
   - `keepalive.py` - Keepalive management (~200 lines)
   - `reconnection.py` - Reconnection logic (~200 lines)

2. **Split `websocket/service.py` (627 → 3 files)**
   - `service.py` - Main service (~200 lines)
   - `connection_manager.py` - WebSocket connection (~200 lines)
   - `message_handler.py` - Message processing (~200 lines)

3. **Split `brokerage/listener.py` (494 → 2 files)**
   - `listener.py` - Main listener (~250 lines)
   - `handlers.py` - Event handlers (~200 lines)

4. **Split `storage/listener.py` (455 → 2 files)**
   - `listener.py` - Main listener (~250 lines)
   - `handlers.py` - Event handlers (~200 lines)

**Estimated Reduction:** ~600 lines across files (better organization)

---

### Phase 3: Refactor Domain Listeners to Use Base Class

**Priority: HIGH** (Achieves DRY)

1. **Refactor all domain listeners**
   - Inherit from `BaseDomainListener`
   - Remove duplicated event handling code
   - Use base class methods

**Estimated Reduction:** ~200 lines per listener × 5 = ~1000 lines

---

## Part 5: Detailed Breakdown

### BaseDomainListener Structure

```python
class BaseDomainListener:
    """Base class for domain listeners - handles common event patterns."""
    
    def __init__(self, event_bus: AsyncEventBus):
        self.event_bus = event_bus
    
    async def handle_domain_request(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        event_class: Type[BaseModel],
        validator: Callable,
        mapper: Callable,
        infra_event_class: Type[BaseModel],
        infra_event_type: str
    ) -> None:
        """Generic handler for domain → infrastructure requests."""
        # Common pattern: Validate → Map → Publish
    
    async def handle_infrastructure_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        infra_event_class: Type[BaseModel],
        factory: Callable,
        publisher: Callable
    ) -> None:
        """Generic handler for infrastructure → domain events."""
        # Common pattern: Validate → Factory → Publish
```

**Benefits:**
- ✅ Removes ~50 lines per listener × 5 = ~250 lines
- ✅ Consistent error handling
- ✅ Easier to maintain
- ✅ Type-safe with generics

---

### Error Handling Decorator

```python
def handle_errors(
    log_context: Optional[str] = None,
    publish_error_event: bool = False,
    error_event_type: Optional[str] = None
):
    """Decorator for consistent error handling."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    log_context or f"Error in {func.__name__}",
                    error=str(e),
                    exc_info=True
                )
                if publish_error_event:
                    # Publish error event
                    ...
                raise
        return wrapper
    return decorator
```

**Usage:**
```python
@handle_errors(log_context="BrokerageDomainListener: Error handling trade request")
async def _handle_domain_trade_request(self, event_type: str, event_data: Dict[str, Any]) -> None:
    # No try/except needed!
    ...
```

**Benefits:**
- ✅ Removes ~5 lines per handler × 50+ handlers = ~250 lines
- ✅ Consistent error handling
- ✅ Less boilerplate

---

## Part 6: Expected Results

### Before Refactoring

- **Total Lines:** ~19,500
- **Large Files (>400 lines):** 6 files
- **Code Duplication:** ~1,500 lines
- **DRY Score:** 8.0/10

### After Refactoring

- **Total Lines:** ~17,000 (reduced by ~2,500 lines)
- **Large Files (>400 lines):** 0 files
- **Code Duplication:** ~200 lines (base classes only)
- **DRY Score:** 10/10 ✅

### Benefits

1. **Maintainability**
   - ✅ Fix bugs in one place (base classes)
   - ✅ Consistent patterns
   - ✅ Easier to understand

2. **Testability**
   - ✅ Test base classes once
   - ✅ Less code to test
   - ✅ More confidence

3. **Scalability**
   - ✅ Easier to add new listeners
   - ✅ Less code to write
   - ✅ Faster development

---

## Part 7: Implementation Order

### Step 1: Create Base Classes (No Breaking Changes)
1. Create `BaseDomainListener` (new file)
2. Create `BaseFactory` (new file)
3. Create `BaseRepository` (new file)
4. Create `@handle_errors` decorator (new file)

### Step 2: Refactor One Listener (Proof of Concept)
1. Refactor `NotificationDomainListener` to use base class
2. Test thoroughly
3. Verify no regressions

### Step 3: Refactor All Listeners
1. Refactor remaining 4 domain listeners
2. Test each one
3. Verify no regressions

### Step 4: Refactor Factories
1. Refactor all factories to use `BaseFactory`
2. Test thoroughly

### Step 5: Split Large Files
1. Split `connection_manager.py`
2. Split `websocket/service.py`
3. Split domain listeners
4. Test after each split

---

## Part 8: Risk Assessment

### Low Risk (Safe to Refactor)

✅ **Base Classes**
- New files, no existing code changes
- Can be tested independently
- No breaking changes

✅ **Error Handling Decorator**
- Additive only
- Can be applied incrementally
- Easy to rollback

### Medium Risk (Needs Careful Testing)

⚠️ **Domain Listener Refactoring**
- Changes existing behavior
- Need to test all event flows
- Verify no regressions

⚠️ **File Splitting**
- Changes imports
- Need to update all references
- Test thoroughly

### Mitigation Strategy

1. **Test After Each Change**
   - Run full test suite
   - Test event flows manually
   - Verify no regressions

2. **Incremental Approach**
   - One listener at a time
   - One file at a time
   - Test → Commit → Next

3. **Keep Old Code**
   - Don't delete until verified
   - Can rollback easily
   - Compare behavior

---

## Part 9: Success Criteria

### DRY Score: 10/10 ✅

**Achieved when:**
- ✅ No duplicated event handling patterns
- ✅ No duplicated error handling
- ✅ No duplicated factory methods
- ✅ Base classes handle common patterns
- ✅ <5% code duplication

### File Size: All <400 Lines ✅

**Achieved when:**
- ✅ `connection_manager.py` → 3 files <400 lines each
- ✅ `websocket/service.py` → 3 files <400 lines each
- ✅ All domain listeners <400 lines
- ✅ All other files <400 lines

### Maintainability: Improved ✅

**Achieved when:**
- ✅ New listeners can be added quickly (inherit base class)
- ✅ Bug fixes in one place affect all listeners
- ✅ Code is easier to understand
- ✅ Tests are easier to write

---

## Part 10: Timeline Estimate

**Phase 1 (Base Classes):** 2-3 hours
- Create base classes
- Write tests
- Document usage

**Phase 2 (Refactor Listeners):** 4-6 hours
- Refactor 5 listeners
- Test each one
- Fix any issues

**Phase 3 (Refactor Factories):** 1-2 hours
- Refactor factories
- Test thoroughly

**Phase 4 (Split Files):** 3-4 hours
- Split 6 large files
- Update imports
- Test thoroughly

**Total:** ~10-15 hours of focused work

---

*Plan Date: 2025-12-04*  
*Goal: 10/10 DRY, All Files <400 Lines*

