# Direct Instantiation Examples - Avoiding Dependency Injection

**Date:** 2025-12-08  
**Issue:** Multiple places where dependencies are created directly instead of being injected via DI container

---

## Summary

Found **4 categories** of direct instantiation violations:

1. **Use Cases** - 4 instances ❌
2. **Domain Validators/Factories/Mappers** - 15+ instances ⚠️ (may be acceptable)
3. **Services** - 2 instances ❌
4. **External Libraries** - 2 instances ❌

---

## 1. Use Cases - Direct Instantiation ❌

### Example 1: Storage Use Cases

**File:** `src/newsflash/services/storage/__init__.py:145-149`

```python
# ❌ WRONG: Direct instantiation
store_article_use_case = StoreArticleUseCase(event_bus=event_bus)
store_audit_log_use_case = StoreAuditLogUseCase(
    event_bus=event_bus,
    storage_query_service=query_service
)
```

**Should Be:**
```python
# ✅ CORRECT: Injected via DI container
async def initialize_storage_microservice(
    event_bus: AsyncEventBus,
    storage_config: StorageConfig,
    store_article_use_case: StoreArticleUseCase,  # ✅ Injected
    store_audit_log_use_case: StoreAuditLogUseCase,  # ✅ Injected
) -> StorageMicroservice:
```

**Impact:** 
- Not in ApplicationContainer
- Can't override for testing
- Dependency graph incomplete

---

### Example 2: WebSocket Use Cases

**File:** `src/newsflash/services/websocket/__init__.py:188-191`

```python
# ❌ WRONG: Direct instantiation
classify_article_use_case = ClassifyArticleUseCase(event_bus=event_bus)
process_article_use_case = ProcessArticleUseCase(event_bus=event_bus)
```

**Should Be:**
```python
# ✅ CORRECT: Injected via DI container
async def initialize_websocket_microservice(
    event_bus: AsyncEventBus,
    metrics_service,
    telegram_service: Optional[TelegramNotifier] = None,
    benzinga_api_key: Optional[str] = None,
    benzinga_websocket_enabled: bool = False,
    classify_article_use_case: ClassifyArticleUseCase,  # ✅ Injected
    process_article_use_case: ProcessArticleUseCase,  # ✅ Injected
) -> WebSocketMicroservice:
```

**Impact:**
- Not in ApplicationContainer
- Can't override for testing
- Dependency graph incomplete

---

## 2. Domain Validators/Factories/Mappers - Direct Instantiation ⚠️

### Example 3: Storage Domain Components

**File:** `src/newsflash/services/storage/__init__.py:127-131`

```python
# ⚠️ QUESTIONABLE: Direct instantiation
domain_listener = StorageDomainListener(
    event_bus=event_bus,
    article_validator=StoredArticleValidator(),  # ❌ Direct instantiation
    audit_validator=AuditEntryValidator(),  # ❌ Direct instantiation
    article_mapper=ArticleStorageMapper(),  # ❌ Direct instantiation
    audit_mapper=AuditLogMapper(),  # ❌ Direct instantiation
    stored_article_factory=StoredArticleFactory()  # ❌ Direct instantiation
)
```

**Analysis:**
- These are **stateless** value objects/factories
- Typically **acceptable** to create directly (no dependencies)
- But **inconsistent** - some are injected, some aren't

**Verdict:** ⚠️ **Acceptable but inconsistent** - Could be injected for consistency

---

### Example 4: Classification Domain Components

**File:** `src/newsflash/services/classification/__init__.py:106-110`

```python
# ⚠️ QUESTIONABLE: Direct instantiation
domain_listener = ClassificationDomainListener(
    event_bus=event_bus,
    request_validator=ClassificationRequestValidator(),  # ❌ Direct instantiation
    result_validator=ClassificationResultValidator(),  # ❌ Direct instantiation
    request_factory=ClassificationRequestFactory(),  # ❌ Direct instantiation
    result_factory=ClassificationResultFactory(),  # ❌ Direct instantiation
    request_mapper=ClassificationRequestMapper(),  # ❌ Direct instantiation
)
```

**Same pattern** - stateless components created directly.

---

### Example 5: Brokerage Domain Components

**File:** `src/newsflash/services/brokerage/__init__.py:130-135`

```python
# ⚠️ QUESTIONABLE: Direct instantiation
domain_listener = BrokerageDomainListener(
    event_bus=event_bus,
    request_validator=TradeRequestValidator(),  # ❌ Direct instantiation
    result_validator=TradeResultValidator(),  # ❌ Direct instantiation
    request_factory=TradeRequestFactory(),  # ❌ Direct instantiation
    result_factory=TradeResultFactory(),  # ❌ Direct instantiation
    quote_factory=QuoteFactory(),  # ❌ Direct instantiation
    request_mapper=TradeRequestMapper(),  # ❌ Direct instantiation
)
```

---

### Example 6: Notification Domain Components

**File:** `src/newsflash/services/notification/__init__.py:138-139`

```python
# ⚠️ QUESTIONABLE: Direct instantiation
domain_listener = NotificationDomainListener(
    event_bus=event_bus,
    message_validator=NotificationMessageValidator(),  # ❌ Direct instantiation
    notification_mapper=NotificationMapper(),  # ❌ Direct instantiation
)
```

---

### Example 7: WebSocket Domain Components

**File:** `src/newsflash/services/websocket/__init__.py:172-173`

```python
# ⚠️ QUESTIONABLE: Direct instantiation
domain_listener = WebSocketDomainListener(
    event_bus=event_bus,
    validator=ArticleValidator(),  # ❌ Direct instantiation
    factory=ArticleFactory(),  # ❌ Direct instantiation
)
```

---

### Example 8: StorageQueryService Factory

**File:** `src/newsflash/services/storage/query_service.py:67`

```python
# ⚠️ QUESTIONABLE: Direct instantiation
self.stored_article_factory = StoredArticleFactory()
```

**File:** `src/newsflash/services/storage/article_query.py:25`

```python
# ⚠️ QUESTIONABLE: Direct instantiation
factory = StoredArticleFactory()
```

---

## 3. Services - Direct Instantiation ❌

### Example 9: FeedManager

**File:** `src/newsflash/services/websocket/__init__.py:178`

```python
# ❌ WRONG: Direct instantiation
feed_manager = FeedManager(event_bus=event_bus)
```

**Should Be:**
```python
# ✅ CORRECT: Injected via DI container
async def initialize_websocket_microservice(
    event_bus: AsyncEventBus,
    feed_manager: FeedManager,  # ✅ Injected
    ...
) -> WebSocketMicroservice:
```

**Impact:**
- Not in ApplicationContainer
- Can't override for testing

---

### Example 10: FeedHealthMonitor

**File:** `src/newsflash/services/websocket/__init__.py:181-184`

```python
# ❌ WRONG: Direct instantiation
health_monitor = FeedHealthMonitor(
    event_bus=event_bus,
    telegram_service=telegram_service
)
```

**Should Be:**
```python
# ✅ CORRECT: Injected via DI container
async def initialize_websocket_microservice(
    event_bus: AsyncEventBus,
    health_monitor: FeedHealthMonitor,  # ✅ Injected
    ...
) -> WebSocketMicroservice:
```

---

## 4. External Libraries - Direct Instantiation ❌

### Example 11: Telegram Bot Instances

**File:** `src/newsflash/services/notification/notification.py:62, 69`

```python
# ❌ WRONG: Direct instantiation
if not test_mode and self.config_1["bot_token"] and self.enabled_1:
    self.bot_1 = Bot(token=self.config_1["bot_token"])  # ❌ Direct instantiation

if not test_mode and self.config_2["bot_token"] and self.enabled_2:
    self.bot_2 = Bot(token=self.config_2["bot_token"])  # ❌ Direct instantiation
```

**Should Be:**
```python
# ✅ CORRECT: Injected via DI container
class TelegramNotifier:
    def __init__(
        self,
        telegram_config_1: dict,
        telegram_config_2: dict,
        bot_1: Optional[Bot] = None,  # ✅ Injected
        bot_2: Optional[Bot] = None,  # ✅ Injected
    ):
        self.bot_1 = bot_1
        self.bot_2 = bot_2
```

**Impact:**
- Harder to test (can't inject mock bots)
- Breaks DI principle

---

## Summary Table

| Category | Count | Severity | Files Affected |
|----------|-------|----------|----------------|
| **Use Cases** | 4 | ❌ **High** | `storage/__init__.py`, `websocket/__init__.py` |
| **Domain Components** | 15+ | ⚠️ **Medium** | All microservice `__init__.py` files |
| **Services** | 2 | ❌ **High** | `websocket/__init__.py` |
| **External Libraries** | 2 | ❌ **High** | `notification/notification.py` |

---

## Priority Fixes

### High Priority (Breaks DI Principle)

1. **Use Cases** - Move to ApplicationContainer
   - `StoreArticleUseCase`
   - `StoreAuditLogUseCase`
   - `ProcessArticleUseCase`
   - `ClassifyArticleUseCase`

2. **Services** - Move to ApplicationContainer
   - `FeedManager`
   - `FeedHealthMonitor`

3. **External Libraries** - Inject via DI
   - `Bot` instances in `TelegramNotifier`

### Medium Priority (Inconsistency)

4. **Domain Components** - Consider injecting for consistency
   - Validators, Factories, Mappers
   - Currently acceptable (stateless) but inconsistent

---

## Impact Assessment

### Testing Impact
- **Current:** Can't easily override use cases/services for testing
- **After Fix:** Can override any dependency via container

### Architecture Impact
- **Current:** Incomplete dependency graph
- **After Fix:** Complete dependency graph in ApplicationContainer

### Maintainability Impact
- **Current:** Inconsistent patterns (some DI, some direct)
- **After Fix:** Consistent DI pattern throughout

---

## Next Steps

1. **Fix Use Cases** - Move to ApplicationContainer (highest priority)
2. **Fix Services** - Move FeedManager/FeedHealthMonitor to container
3. **Fix Bot Instances** - Inject via DI container
4. **Consider Domain Components** - Evaluate if injection adds value

---

## Conclusion

Found **23+ instances** of direct instantiation that should be injected via DI container. The most critical are:

1. **4 Use Cases** - Should be in ApplicationContainer
2. **2 Services** - Should be in ApplicationContainer  
3. **2 Bot Instances** - Should be injected

These break the DI principle and make the dependency graph incomplete.
