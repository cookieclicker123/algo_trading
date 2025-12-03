# Complete Architecture Analysis & Improvement Plan

## Part 1: The Straight Answer - How Domain, Services, and Use Cases Work Together

### The Real Truth (Not Theory, What Actually Happens)

#### Where Services Get Used

**1. Services Called by Use Cases (Direct Calls)**

These use cases call services directly because they need return values:

```python
# use_cases/notify_imminent_article_use_case.py
class NotifyImminentArticleUseCase:
    def __init__(self, storage_query_service: StorageQueryService):
        self.storage_query_service = storage_query_service  # ✅ Direct dependency
    
    async def _handle_article_classified(self, event):
        # ✅ NEED return value - call service directly
        article = await self.storage_query_service.fetch_article(event.result.article_id)
```

**Services Used This Way:**
- `StorageQueryService.fetch_article()` → Called by:
  - `NotifyImminentArticleUseCase` ✅
  - `StoreAuditLogUseCase` ✅
  - `AutoTradeService` ✅ (service calling service)

**2. Services That Subscribe to Events (Automatic - No Orchestration)**

These services are **NOT orchestrated by use cases** - they're automatic:

```python
# services/brokerage/auto_trade.py
class AutoTradeService:
    def __init__(self, event_bus: AsyncEventBus):
        # ✅ Subscribes to events itself - NO use case calls this
        subscribe_typed(event_bus, "Domain.ArticleClassified", self._handle)
```

**Services That Work This Way:**
- `AutoTradeService` → Subscribes to `Domain.ArticleClassified` (automatic trading)
- `FeedManager` → Subscribes to `Domain.ArticleReceived` (automatic feed management)
- `FeedHealthMonitor` → Subscribes to health events (automatic monitoring)

**Key Point:** These are **NOT orchestrated** - they react automatically to events!

**3. Pure Functions Used by Use Cases**

Use cases call pure functions directly when they need stateless operations:

```python
# use_cases/classify_article_use_case.py
class ClassifyArticleUseCase:
    async def _handle_article_received(self, event):
        # ✅ Use pure function from services
        from ..services.classification import create_classification_request
        
        request = create_classification_request(event.article)
        await self.event_bus.publish("Domain.ClassificationRequested", ...)
```

**4. Services Used by Other Services**

Services can call other services:

```python
# services/brokerage/auto_trade.py
class AutoTradeService:
    def __init__(self, storage_query_service: StorageQueryService):
        self.storage_query_service = storage_query_service  # ✅ Service → Service
    
    async def _handle_article_classified(self, event):
        article = await self.storage_query_service.fetch_article(...)  # Service calls service
```

---

### Complete Service Usage Map (Current Reality)

| Service | Who Uses It | How | Pattern |
|---------|-------------|-----|---------|
| `StorageQueryService.fetch_article()` | `NotifyImminentArticleUseCase` | Direct call | Use case → Service (need return value) |
| `StorageQueryService.fetch_article()` | `StoreAuditLogUseCase` | Direct call | Use case → Service (need return value) |
| `StorageQueryService.fetch_article()` | `AutoTradeService` | Direct call | Service → Service (need return value) |
| `AutoTradeService` | None - subscribes to events | Event subscription | Automatic behavior (no orchestration) |
| `FeedManager` | None - subscribes to events | Event subscription | Automatic behavior (no orchestration) |
| `FeedHealthMonitor` | None - subscribes to events | Event subscription | Automatic behavior (no orchestration) |
| `TelegramNotifier` | `FeedHealthMonitor` | Injected dependency | Service → Service (direct use) |
| Pure functions (e.g., `create_classification_request`) | `ClassifyArticleUseCase` | Direct function call | Use case → Pure function |

---

### The Answer to "Who Uses Services?"

**Answer:** 
1. **Use cases call services directly** when they need return values (e.g., fetch article)
2. **Use cases use pure functions** when they need stateless operations
3. **Some services subscribe to events** - they're automatic, NOT orchestrated
4. **Services can call other services** - valid pattern (e.g., AutoTradeService → StorageQueryService)

**The Goal is NOT "use cases orchestrate all services"** - the goal is:
- Use cases orchestrate **workflows** (calling services when needed, publishing events)
- Some services are **automatic** (subscribe to events themselves)
- Services provide **focused operations** (pure functions or minimal classes)

---

## Part 2: Composition Root Pattern Explained in Detail

### What is Composition Root Pattern?

**Definition:** The Composition Root is a **single location** where all object graphs are composed (created). Instead of creating dependencies scattered throughout the application, create them all in ONE place.

**Key Principle:** "Create all dependencies at the application's entry point, and only there."

---

### Wrong Approach: God Object ❌

**Current:** `service_initialization.py` (609 lines)

```python
# ❌ WRONG: Everything in one giant function
async def initialize_services() -> Services:
    services = Services()
    event_bus = AsyncEventBus()
    
    # Infrastructure (lines 100-200)
    services.classification_infra = ClassificationInfrastructureService(
        event_bus=event_bus,
        api_key=GROQ_API_KEY,
        model=GROQ_MODEL
    )
    services.storage_infra = StorageInfrastructureService(...)
    services.notification_infra = NotificationInfrastructureService(...)
    
    # Domain listeners (lines 150-250)
    services.classification_domain_listener = ClassificationDomainListener(...)
    services.storage_domain_listener = StorageDomainListener(...)
    
    # Services (lines 200-300)
    services.storage_query_service = StorageQueryService(
        event_bus=event_bus,
        article_repository=services.storage_infra.article_repository  # Manual wiring
    )
    
    # Use cases (lines 250-350)
    services.classify_article_use_case = ClassifyArticleUseCase(event_bus=event_bus)
    services.notify_imminent_article_use_case = NotifyImminentArticleUseCase(
        event_bus=event_bus,
        storage_query_service=services.storage_query_service  # Manual wiring
    )
    
    # Telegram services (lines 300-400)
    services.telegram = TelegramNotifier(...)
    services.trade_handler = get_telegram_trade_handler(...)
    
    # WebSocket services (lines 400-500)
    services.feed_manager = FeedManager(event_bus=event_bus)
    services.health_monitor = FeedHealthMonitor(
        event_bus=event_bus,
        telegram_service=services.telegram  # Manual wiring
    )
    
    # ... 100+ more lines of manual wiring
    
    return services
```

**Problems:**
- ❌ One file knows about EVERYTHING (infrastructure, domain, services, use cases, routes)
- ❌ 609 lines of manual wiring
- ❌ Hard to test (can't test one microservice in isolation)
- ❌ Hard to modify (add/remove services = edit this file)
- ❌ Circular dependencies hidden here
- ❌ Order matters (must initialize infrastructure → domain → services → use cases)

---

### Right Approach: Composition Root Pattern ✅

**Principle:** Each module initializes itself, composition root wires them together.

#### Step 1: Each Microservice Initializes Itself

**File:** `services/storage/__init__.py`

```python
"""Storage microservice - self-contained initialization."""
from dataclasses import dataclass
from typing import Optional
from ...shared.event_bus import AsyncEventBus
from ...infra.storage import StorageInfrastructureService
from ...domain.storage.listener import StorageDomainListener
from .query_service import StorageQueryService
from ...use_cases.storage.store_article_use_case import StoreArticleUseCase
from ...use_cases.storage.store_audit_log_use_case import StoreAuditLogUseCase
from ...config.settings import get_storage_config


@dataclass
class StorageMicroservice:
    """
    Storage microservice container.
    
    Holds all storage-related components:
    - Infrastructure service
    - Domain listener (bridge)
    - Services (pure functions + minimal classes)
    - Use cases
    """
    infra: StorageInfrastructureService
    domain_listener: StorageDomainListener
    query_service: StorageQueryService
    store_article_use_case: StoreArticleUseCase
    store_audit_log_use_case: StoreAuditLogUseCase


async def initialize_storage_microservice(event_bus: AsyncEventBus) -> StorageMicroservice:
    """
    Initialize storage microservice independently.
    
    This function knows ONLY about storage microservice.
    It doesn't know about other microservices.
    
    Args:
        event_bus: Event bus instance (shared dependency)
        
    Returns:
        StorageMicroservice: Initialized storage microservice
    """
    logger.info("Initializing storage microservice...")
    
    # Step 1: Infrastructure layer
    storage_config = get_storage_config()
    infra = StorageInfrastructureService(
        event_bus=event_bus,
        storage_config=storage_config
    )
    logger.info("Storage infrastructure initialized")
    
    # Step 2: Domain listener (bridge infrastructure ↔ domain)
    from ...domain.storage.validators import StoredArticleValidator, AuditEntryValidator
    from ...domain.storage.mappers import ArticleStorageMapper, AuditLogMapper
    from ...domain.storage.factories import StoredArticleFactory
    
    domain_listener = StorageDomainListener(
        event_bus=event_bus,
        article_validator=StoredArticleValidator(),
        audit_validator=AuditEntryValidator(),
        article_mapper=ArticleStorageMapper(),
        audit_mapper=AuditLogMapper(),
        stored_article_factory=StoredArticleFactory()
    )
    logger.info("Storage domain listener initialized")
    
    # Step 3: Services layer
    fetch_timeout = storage_config.get("article_fetch_timeout_seconds", 5.0)
    query_service = StorageQueryService(
        event_bus=event_bus,
        article_repository=infra.article_repository,  # ✅ Internal dependency
        fetch_timeout_seconds=fetch_timeout
    )
    logger.info("Storage query service initialized")
    
    # Step 4: Use cases layer
    store_article_use_case = StoreArticleUseCase(event_bus=event_bus)
    store_audit_log_use_case = StoreAuditLogUseCase(
        event_bus=event_bus,
        storage_query_service=query_service  # ✅ Internal dependency wired here
    )
    logger.info("Storage use cases initialized")
    
    return StorageMicroservice(
        infra=infra,
        domain_listener=domain_listener,
        query_service=query_service,
        store_article_use_case=store_article_use_case,
        store_audit_log_use_case=store_audit_log_use_case
    )
```

**Key Points:**
- ✅ This file knows ONLY about storage microservice
- ✅ All internal dependencies are wired here
- ✅ Can test storage microservice in isolation
- ✅ No knowledge of other microservices

**Repeat for each microservice:**
- `services/classification/__init__.py` - Initialize classification microservice
- `services/notification/__init__.py` - Initialize notification microservice
- `services/brokerage/__init__.py` - Initialize brokerage microservice
- `services/websocket/__init__.py` - Initialize websocket microservice

---

#### Step 2: Composition Root Wires Everything Together

**File:** `services/service_initialization.py` (Now ~100 lines instead of 609!)

```python
"""Composition Root - wires microservices together."""
from dataclasses import dataclass
from typing import Optional
from .shared.event_bus import AsyncEventBus

# Import microservice initializers
from .storage import initialize_storage_microservice, StorageMicroservice
from .notification import initialize_notification_microservice, NotificationMicroservice
from .classification import initialize_classification_microservice, ClassificationMicroservice
from .brokerage import initialize_brokerage_microservice, BrokerageMicroservice
from .websocket import initialize_websocket_microservice, WebSocketMicroservice

# Import shared services
from .notification.notification import TelegramNotifier
from .notification.trade_handler import get_telegram_trade_handler
from ...config.settings import get_telegram_config, get_telegram_config_2


@dataclass
class Services:
    """
    Services container - holds all microservices.
    
    This is the composition root's view of the application.
    Each microservice is self-contained.
    """
    storage: StorageMicroservice
    notification: NotificationMicroservice
    classification: ClassificationMicroservice
    brokerage: BrokerageMicroservice
    websocket: WebSocketMicroservice
    telegram: Optional[TelegramNotifier] = None
    event_bus: Optional[AsyncEventBus] = None


async def initialize_services() -> Services:
    """
    Composition Root - wires microservices together.
    
    This is the ONLY place that knows about cross-microservice dependencies.
    All microservices initialize themselves independently.
    
    Responsibilities:
    1. Create shared dependencies (event bus)
    2. Initialize each microservice independently
    3. Wire cross-microservice dependencies (minimal, explicit)
    
    Returns:
        Services: Composed services container
    """
    logger.info("Initializing services...")
    
    # Step 1: Create shared dependencies
    event_bus = AsyncEventBus()
    logger.info("Event bus created")
    
    # Step 2: Initialize microservices independently
    # ✅ Order doesn't matter - they're independent!
    storage = await initialize_storage_microservice(event_bus)
    logger.info("Storage microservice initialized")
    
    classification = await initialize_classification_microservice(event_bus)
    logger.info("Classification microservice initialized")
    
    notification = await initialize_notification_microservice(event_bus)
    logger.info("Notification microservice initialized")
    
    brokerage = await initialize_brokerage_microservice(event_bus)
    logger.info("Brokerage microservice initialized")
    
    # Step 3: Initialize shared services (used by multiple microservices)
    telegram_config_1 = get_telegram_config()
    telegram_config_2 = get_telegram_config_2()
    telegram = TelegramNotifier(
        telegram_config_1=telegram_config_1,
        telegram_config_2=telegram_config_2
    )
    logger.info("Telegram notifier initialized")
    
    # Step 4: Initialize websocket (needs telegram for health monitoring)
    websocket = await initialize_websocket_microservice(
        event_bus=event_bus,
        telegram_service=telegram  # Cross-microservice dependency
    )
    logger.info("WebSocket microservice initialized")
    
    # Step 5: Wire cross-microservice dependencies (minimal, explicit)
    # ✅ This is the ONLY place cross-microservice dependencies are wired!
    
    # Notification use case needs storage query service
    notification.use_case.storage_query_service = storage.query_service
    
    # Brokerage auto-trade service needs storage query service
    brokerage.auto_trade_service.storage_query_service = storage.query_service
    
    logger.info("Cross-microservice dependencies wired")
    
    return Services(
        storage=storage,
        notification=notification,
        classification=classification,
        brokerage=brokerage,
        websocket=websocket,
        telegram=telegram,
        event_bus=event_bus
    )
```

**Key Points:**
- ✅ Composition root is minimal (~100 lines instead of 609)
- ✅ Cross-microservice dependencies are explicit and minimal
- ✅ Easy to see what depends on what
- ✅ Each microservice is independent and testable

---

### Visual Comparison

**Wrong (Current - 609 lines):**
```
service_initialization.py (609 lines) - God Object
├── Lines 100-200: Creates ALL infrastructure services
├── Lines 200-300: Creates ALL domain listeners
├── Lines 300-400: Creates ALL services
├── Lines 400-500: Creates ALL use cases
├── Lines 500-600: Wires ALL dependencies manually
└── Knows about EVERYTHING
```

**Right (Composition Root - ~100 lines):**
```
service_initialization.py (~100 lines) - Composition Root
├── Create event bus
├── Call initialize_storage_microservice()
├── Call initialize_classification_microservice()
├── Call initialize_notification_microservice()
├── Call initialize_brokerage_microservice()
├── Call initialize_websocket_microservice()
└── Wire cross-microservice dependencies (2-3 lines)

services/storage/__init__.py - Self-contained
├── Create storage infrastructure
├── Create storage domain listener
├── Create storage services
├── Create storage use cases
└── Wire internal dependencies

services/classification/__init__.py - Self-contained
├── Create classification infrastructure
├── Create classification domain listener
├── Create classification use case
└── Wire internal dependencies

... (repeat for each microservice)
```

---

## Part 3: Folder Structure Problem

### Current Structure ❌

```
services/
  ├── classification/
  ├── notification/
  ├── storage/
  ├── brokerage/
  └── websocket/

domain/
  ├── classification/
  ├── notification/
  ├── storage/
  ├── brokerage/
  └── websocket/

infra/
  ├── classification/
  ├── notification/
  ├── storage/
  ├── brokerage/
  └── websocket/

use_cases/
  ├── classify_article_use_case.py        ❌ Flat! Should be in classification/
  ├── notify_imminent_article_use_case.py ❌ Flat! Should be in notification/
  ├── store_article_use_case.py          ❌ Flat! Should be in storage/
  ├── store_audit_log_use_case.py        ❌ Flat! Should be in storage/
  └── process_article_use_case.py        ❌ Flat! Should be in websocket/

api/routes/
  ├── articles.py   ❌ Flat! Should be in storage/
  ├── feeds.py      ❌ Flat! Should be in websocket/
  └── health.py     ✅ Global - stays at root
```

**Problem:** Inconsistent! Routes and use cases don't match services/domain/infra structure.

---

### Correct Structure ✅

```
services/
  ├── classification/
  ├── notification/
  ├── storage/
  ├── brokerage/
  └── websocket/

domain/
  ├── classification/
  ├── notification/
  ├── storage/
  ├── brokerage/
  └── websocket/

infra/
  ├── classification/
  ├── notification/
  ├── storage/
  ├── brokerage/
  └── websocket/

use_cases/
  ├── classification/
  │   └── classify_article_use_case.py
  ├── notification/
  │   └── notify_imminent_article_use_case.py
  ├── storage/
  │   ├── store_article_use_case.py
  │   └── store_audit_log_use_case.py
  ├── websocket/
  │   └── process_article_use_case.py
  └── brokerage/ (empty folder for future use cases)

api/routes/
  ├── classification/ (empty folder for future routes)
  ├── notification/ (empty folder for future routes)
  ├── storage/
  │   └── articles.py  (moved from articles.py)
  ├── websocket/
  │   └── feeds.py     (moved from feeds.py)
  └── health.py        (global - stays at root)
```

**Why This Matters:**
- ✅ Consistent structure across all layers
- ✅ Easy to find related code (everything for "storage" in one place)
- ✅ Clear microservice boundaries
- ✅ Scalable (easy to add new routes/use cases)

---

## Part 4: Dependency Injection

### Current State: Manual Dependency Injection ❌

**Everywhere in `service_initialization.py`:**
```python
# Manual wiring scattered throughout
services.storage_query_service = StorageQueryService(
    event_bus=event_bus,
    article_repository=services.storage_infra.article_repository,
    fetch_timeout_seconds=fetch_timeout
)

services.notify_imminent_article_use_case = NotifyImminentArticleUseCase(
    event_bus=event_bus,
    storage_query_service=services.storage_query_service  # Manual wiring
)
```

**Problems:**
- ❌ Manual wiring everywhere (609 lines)
- ❌ Hard to change dependencies
- ❌ No automatic resolution
- ❌ Can't easily swap implementations for testing

---

### Options for Better DI

#### Option 1: FastAPI Dependencies (Recommended for Routes) ✅

**What:** Use FastAPI's built-in dependency injection for API routes.

**How:**
```python
# api/dependencies.py
from fastapi import Depends, Request
from typing import Annotated
from ...services.service_initialization import Services

async def get_services(request: Request) -> Services:
    """Get services from app.state."""
    return request.app.state.services

def get_storage_query_service(services: Services = Depends(get_services)):
    """Get storage query service."""
    return services.storage.query_service

# Type alias for cleaner signatures
StorageQueryServiceDep = Annotated[StorageQueryService, Depends(get_storage_query_service)]

# api/routes/storage/articles.py
from fastapi import APIRouter, Depends
from ...services.storage import StorageQueryService
from ...api.dependencies import StorageQueryServiceDep

router = APIRouter(prefix="/storage/articles", tags=["storage"])

@router.get("/recent")
async def get_recent_articles(
    limit: int = 10,
    storage_service: StorageQueryServiceDep = Depends(get_storage_query_service)
):
    """Get recent articles."""
    articles = await storage_service.get_recent_articles(limit=limit)
    return articles
```

**Benefits:**
- ✅ Built into FastAPI (no external dependencies)
- ✅ Automatic resolution
- ✅ Type-safe
- ✅ Easy to override for testing

**When to Use:** For API routes only.

---

#### Option 2: Dependency-Injector Library

**What:** Full-featured DI container library.

**Benefits:** Automatic dependency resolution, good for complex graphs
**Drawbacks:** External dependency, more complex, learning curve

**When to Use:** Only if you have very complex dependency graphs.

---

#### Option 3: Manual DI with Dataclasses (Recommended for Services) ✅

**What:** Keep manual DI but use dataclasses to make dependencies explicit.

**How:**
```python
# services/storage/__init__.py
from dataclasses import dataclass

@dataclass
class StorageMicroservice:
    """Storage microservice container - explicit dependencies."""
    infra: StorageInfrastructureService
    domain_listener: StorageDomainListener
    query_service: StorageQueryService
    store_article_use_case: StoreArticleUseCase
    store_audit_log_use_case: StoreAuditLogUseCase

async def initialize_storage_microservice(event_bus: AsyncEventBus) -> StorageMicroservice:
    """Initialize with explicit dependencies."""
    infra = StorageInfrastructureService(event_bus=event_bus, ...)
    query_service = StorageQueryService(event_bus=event_bus, repo=infra.repo)
    # ... wire dependencies explicitly
    
    return StorageMicroservice(
        infra=infra,
        query_service=query_service,
        # ... explicit dependencies
    )
```

**Benefits:**
- ✅ No external dependencies
- ✅ Explicit and clear
- ✅ Simple to understand
- ✅ Good for small-medium apps

**When to Use:** For service initialization (what we'll do).

---

### Recommended Approach: Hybrid ✅

1. **FastAPI Dependencies** for API routes
   - Built-in, type-safe, automatic
   
2. **Manual DI with Dataclasses** for services
   - Simple, explicit, no external dependencies

**Reasoning:**
- FastAPI dependencies are perfect for routes
- Manual DI is simple and explicit for services
- No need for complex DI library
- Easy to understand and maintain

---

## Part 5: Shutdown Cleanup Issue

### Current Problem ❌

**Error from terminal:**
```
RecursionError: maximum recursion depth exceeded
Task was destroyed but it is pending!
```

**Location:** `api/lifespan.py` lines 75-84

**Cause:** 
- `asyncio.all_tasks()` returns tasks that have nested tasks
- `asyncio.gather()` on cancelled tasks causes recursion
- Nested task cancellation creates infinite recursion

**Current Code:**
```python
# ❌ WRONG: Causes recursion
tasks = [task for task in asyncio.all_tasks() if not task.done()]
for task in tasks:
    task.cancel()
await asyncio.gather(*tasks, return_exceptions=True)  # ❌ Recursion here!
```

---

### Right Approach: Proper Task Cleanup ✅

**Fix:**
```python
async def cleanup_background_tasks():
    """
    Cancel and wait for background tasks properly.
    
    Handles nested tasks without recursion error.
    Uses asyncio.wait() instead of gather() to avoid recursion.
    """
    # Get all tasks except current one
    current_task = asyncio.current_task()
    tasks = [
        task for task in asyncio.all_tasks()
        if task != current_task and not task.done()
    ]
    
    if not tasks:
        return
    
    logger.info(f"Cancelling {len(tasks)} remaining background tasks")
    
    # Cancel all tasks (non-recursive)
    for task in tasks:
        if not task.done():
            task.cancel()
    
    # Wait for tasks with timeout (using wait() not gather())
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=5.0,  # 5 second timeout
            return_when=asyncio.ALL_COMPLETED
        )
        
        # Log any tasks that didn't complete
        if pending:
            logger.warning(f"{len(pending)} tasks did not complete within timeout")
        
        # Check for exceptions (ignore CancelledError - expected)
        for task in done:
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected for cancelled tasks
            except Exception as e:
                logger.error(f"Task exception during cleanup", error=str(e))
                
    except Exception as e:
        logger.error(f"Error during task cleanup", error=str(e))

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    services = await initialize_services()
    await start_services(services)
    app.state.services = services
    
    yield
    
    # Shutdown
    logger.info("Shutting down NewsFlash API server")
    
    try:
        services = getattr(app.state, "services", None)
        if services:
            await stop_services(services)
    except Exception as e:
        logger.error("Error stopping services", error=str(e))
    
    # Cleanup tasks (with proper error handling)
    try:
        await cleanup_background_tasks()
    except Exception as e:
        logger.error("Error during task cleanup", error=str(e))
    
    logger.info("API server shutdown completed")
```

**Key Changes:**
- ✅ Use `asyncio.wait()` instead of `asyncio.gather()` (avoids recursion)
- ✅ Timeout to prevent hanging (5 seconds)
- ✅ Proper error handling
- ✅ Log pending tasks for debugging

---

## Summary: Complete Improvement Plan

### 1. Folder Structure ✅
- Move use cases into microservice folders
- Move routes into microservice folders
- Match services/domain/infra structure

### 2. Service Initialization ✅
- Each microservice initializes itself
- Composition root wires them together (~100 lines instead of 609)
- Clear, explicit dependencies with dataclasses

### 3. Dependency Injection ✅
- FastAPI Dependencies for routes
- Manual DI with dataclasses for services

### 4. Cleanup/Shutdown ✅
- Proper task cancellation with `asyncio.wait()`
- Timeout handling
- Error handling

---

## Implementation Order

1. **Fix shutdown cleanup** (urgent - breaking issue) 🔴
2. **Reorganize folders** (use cases and routes) 🟡
3. **Create microservice initialization modules** 🟢
4. **Refactor composition root** 🟢
5. **Add FastAPI dependencies for routes** 🟢

**Ready to implement when you approve!**

