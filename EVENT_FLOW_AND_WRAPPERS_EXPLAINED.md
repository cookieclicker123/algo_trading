# Event Flow, Wrapper Functions, and Services Explained

## 1. Wrapper Functions for Typed Subscriptions

### The Problem

When you use `subscribe_typed()`, it creates a **wrapper function** that you can't easily unsubscribe from.

### Current Implementation

**File: `src/newsflash/shared/typed_event_bus.py`**

```python
def subscribe_typed(
    event_type: str,
    model: Type[TEvent],
    handler: Callable[[TEvent], Awaitable[None]],
) -> None:
    event_bus = get_event_bus()
    
    # This is the WRAPPER FUNCTION
    async def _wrapper(raw_event_type: str, event_data: dict) -> None:
        event = model(**event_data)  # Reconstruct typed event from dict
        await handler(event)          # Call your actual handler
    
    event_bus.subscribe(event_type, _wrapper)  # Subscribe the wrapper, not your handler
```

### What's a Wrapper Function?

A **wrapper function** is a function that "wraps around" another function to add behavior.

**Example:**
```python
# Your original handler (what you write):
async def my_handler(event: ArticleClassifiedDomainEvent) -> None:
    print(f"Article classified: {event.result.classification}")

# The wrapper function (created by subscribe_typed):
async def _wrapper(raw_event_type: str, event_data: dict) -> None:
    # Step 1: Convert dict to typed event
    event = ArticleClassifiedDomainEvent(**event_data)
    
    # Step 2: Call your handler with the typed event
    await my_handler(event)
```

**Why the wrapper exists:**
- The event bus stores events as `dict` (untyped)
- Your handler expects a typed `ArticleClassifiedDomainEvent`
- The wrapper converts `dict` → typed event, then calls your handler

### The Unsubscribe Problem

**File: `src/newsflash/use_cases/store_article_use_case.py`**

```python
class StoreArticleUseCase:
    def __init__(self):
        self.event_bus = get_event_bus()
        
        # Subscribe using typed helper
        subscribe_typed(
            "Domain.ArticleReceived",
            ArticleReceivedDomainEvent,
            self._handle_article_received,  # Your handler
        )
    
    async def stop(self) -> None:
        # ❌ This WON'T work!
        self.event_bus.unsubscribe("Domain.ArticleReceived", self._handle_article_received)
        # Why? Because the event bus has the WRAPPER, not your handler!
```

**What's stored in the event bus:**
```python
# Event bus internal state:
_subscribers = {
    "Domain.ArticleReceived": [
        _wrapper,  # ← The wrapper function, not your handler!
    ]
}
```

**When you try to unsubscribe:**
```python
# You pass your handler:
self.event_bus.unsubscribe("Domain.ArticleReceived", self._handle_article_received)

# But the event bus has the wrapper:
if handler in self._subscribers[event_type]:  # ❌ False! handler != _wrapper
    self._subscribers[event_type].remove(handler)  # Never executes
```

### The Fix: Track Wrapper Functions

**Solution: Return the wrapper so you can unsubscribe later**

```python
def subscribe_typed(
    event_type: str,
    model: Type[TEvent],
    handler: Callable[[TEvent], Awaitable[None]],
) -> Callable:  # ✅ Return the wrapper
    event_bus = get_event_bus()
    
    async def _wrapper(raw_event_type: str, event_data: dict) -> None:
        event = model(**event_data)
        await handler(event)
    
    event_bus.subscribe(event_type, _wrapper)
    return _wrapper  # ✅ Return it so caller can store it
```

**Usage:**
```python
class StoreArticleUseCase:
    def __init__(self):
        self.event_bus = get_event_bus()
        
        # Store the wrapper
        self._article_received_wrapper = subscribe_typed(
            "Domain.ArticleReceived",
            ArticleReceivedDomainEvent,
            self._handle_article_received,
        )
    
    async def stop(self) -> None:
        # ✅ Now we can unsubscribe using the wrapper
        self.event_bus.unsubscribe("Domain.ArticleReceived", self._article_received_wrapper)
```

### Why Unsubscribe?

**Reasons to unsubscribe:**

1. **Clean Shutdown**
   ```python
   # When service stops, unsubscribe to prevent memory leaks
   async def stop(self):
       self.event_bus.unsubscribe(...)  # Clean up
   ```

2. **Testing**
   ```python
   def test_something():
       use_case = StoreArticleUseCase()
       # ... run test ...
       use_case.stop()  # Clean up subscriptions
   ```

3. **Dynamic Subscriptions**
   ```python
   # Subscribe temporarily
   wrapper = subscribe_typed(...)
   # ... do work ...
   event_bus.unsubscribe(..., wrapper)  # Unsubscribe when done
   ```

4. **Prevent Memory Leaks**
   - If you don't unsubscribe, the event bus keeps references to your handlers
   - This prevents garbage collection
   - Over time, this can cause memory leaks

---

## 2. Complete Event Flow Example

### Scenario: Article Received → Stored → Classified → Notified

Let's trace a complete bidirectional event loop for `Domain.ArticleReceived`.

### Step-by-Step Flow

#### **Step 1: Infrastructure Receives Article**

**File: `src/newsflash/infra/websocket/service.py`**

```python
class BenzingaWebSocketMicroservice:
    async def _handle_article(self, article_data: dict):
        # Infrastructure receives raw article from WebSocket
        # Convert to infrastructure event
        infra_event = InfrastructureArticleReceivedEvent(
            article_data=article_data,
            received_at=datetime.now()
        )
        
        # Publish to event bus
        await self.event_bus.publish("ArticleReceived", infra_event.model_dump())
        #                                    ↑
        #                          Infrastructure event type
```

**Event Published:** `"ArticleReceived"` (infrastructure event)

---

#### **Step 2: Domain Listener Bridges Infrastructure → Domain**

**File: `src/newsflash/domain/websocket/listener.py`**

```python
class WebSocketDomainListener:
    async def start(self):
        # Subscribe to infrastructure events
        self.event_bus.subscribe("ArticleReceived", self._handle_infra_article_received)
    
    async def _handle_infra_article_received(self, event_type: str, event_data: dict):
        # Step 1: Reconstruct typed infrastructure event
        infra_event = InfrastructureArticleReceivedEvent(**event_data)
        
        # Step 2: Convert infrastructure model → domain model
        domain_article = self.article_factory.create_from_infrastructure(infra_event.article_data)
        
        # Step 3: Validate domain model
        if not self.article_validator.is_valid_article(domain_article):
            return
        
        # Step 4: Publish domain event
        domain_event = ArticleReceivedDomainEvent(
            article=domain_article,
            received_at=datetime.now()
        )
        await self.event_bus.publish("Domain.ArticleReceived", domain_event.model_dump())
        #                                    ↑
        #                            Domain event type
```

**Event Published:** `"Domain.ArticleReceived"` (domain event)

---

#### **Step 3: Use Cases Subscribe to Domain Events**

**File: `src/newsflash/use_cases/store_article_use_case.py`**

```python
class StoreArticleUseCase:
    def __init__(self, event_bus: AsyncEventBus):
        self.event_bus = event_bus
        
        # Subscribe to domain event
        subscribe_typed(
            "Domain.ArticleReceived",
            ArticleReceivedDomainEvent,
            self._handle_article_received,
        )
    
    async def _handle_article_received(self, event: ArticleReceivedDomainEvent):
        # Use case orchestrates storage workflow
        # Step 1: Create storage request from domain article
        stored_article = self.stored_article_factory.create_from_domain_article(event.article)
        
        # Step 2: Publish domain storage request event
        storage_event = ArticleStorageRequestedDomainEvent(
            article=stored_article,
            requested_at=datetime.now()
        )
        await self.event_bus.publish("Domain.ArticleStorageRequested", storage_event.model_dump())
```

**Also subscribed:**
- `ClassifyArticleUseCase` - subscribes to `"Domain.ArticleReceived"`

**Event Published:** `"Domain.ArticleStorageRequested"` (domain event)

---

#### **Step 4: Domain Listener Bridges Domain → Infrastructure**

**File: `src/newsflash/domain/storage/listener.py`**

```python
class StorageDomainListener:
    async def start(self):
        # Subscribe to domain storage requests
        self.event_bus.subscribe("Domain.ArticleStorageRequested", self._handle_domain_storage_request)
    
    async def _handle_domain_storage_request(self, event_type: str, event_data: dict):
        # Step 1: Reconstruct typed domain event
        domain_event = ArticleStorageRequestedDomainEvent(**event_data)
        
        # Step 2: Convert domain model → infrastructure format
        infra_request = self.article_mapper.to_infrastructure_request(
            article_data=self.article_mapper.from_domain_article(domain_event.article),
            article_id=domain_event.article.article_id
        )
        
        # Step 3: Publish infrastructure event
        await self.event_bus.publish("ArticleStorageRequested", infra_request.model_dump())
        #                                    ↑
        #                          Infrastructure event type
```

**Event Published:** `"ArticleStorageRequested"` (infrastructure event)

---

#### **Step 5: Infrastructure Service Handles Storage**

**File: `src/newsflash/infra/storage/service.py`**

```python
class StorageInfrastructureService:
    async def start(self):
        # Subscribe to infrastructure storage requests
        self.event_bus.subscribe("ArticleStorageRequested", self.handle_article_storage_requested)
    
    async def handle_article_storage_requested(self, event_type: str, event_data: dict):
        # Step 1: Reconstruct typed infrastructure event
        request_data = ArticleStorageRequestData(**event_data)
        
        # Step 2: Call repository (actual I/O)
        file_path, is_archived = await self.article_repository.store_article(
            article_id=request_data.article_id,
            article_data=request_data.article_data
        )
        
        # Step 3: Publish infrastructure result event
        stored_event = ArticleStoredInfrastructureEvent(
            request_data=request_data,
            file_path=file_path,
            stored_at=datetime.now(),
            is_archived=is_archived
        )
        await self.event_bus.publish("ArticleStored", stored_event.model_dump())
```

**Event Published:** `"ArticleStored"` (infrastructure event)

---

#### **Step 6: Domain Listener Bridges Infrastructure → Domain (Result)**

**File: `src/newsflash/domain/storage/listener.py`**

```python
class StorageDomainListener:
    async def start(self):
        # Also subscribe to infrastructure results
        self.event_bus.subscribe("ArticleStored", self._handle_infra_article_stored)
    
    async def _handle_infra_article_stored(self, event_type: str, event_data: dict):
        # Step 1: Reconstruct typed infrastructure event
        infra_event = ArticleStoredInfrastructureEvent(**event_data)
        
        # Step 2: Publish domain result event
        domain_event = ArticleStoredDomainEvent(
            article_id=infra_event.request_data.article_id,
            stored_at=infra_event.stored_at,
            file_path=infra_event.file_path,
            is_archived=infra_event.is_archived
        )
        await self.event_bus.publish("Domain.ArticleStored", domain_event.model_dump())
```

**Event Published:** `"Domain.ArticleStored"` (domain event)

---

### Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    INFRASTRUCTURE LAYER                          │
│                                                                   │
│  WebSocket Service                                               │
│    ↓ publishes "ArticleReceived"                                 │
│                                                                   │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        │ Infrastructure Event
                        ↓
┌─────────────────────────────────────────────────────────────────┐
│                      DOMAIN LISTENER                             │
│                    (WebSocketDomainListener)                     │
│                                                                   │
│  • Subscribes to: "ArticleReceived"                             │
│  • Converts: Infrastructure → Domain                            │
│  • Validates: Domain model                                       │
│  • Publishes: "Domain.ArticleReceived"                          │
│                                                                   │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        │ Domain Event
                        ↓
┌─────────────────────────────────────────────────────────────────┐
│                      USE CASES LAYER                             │
│                                                                   │
│  StoreArticleUseCase                                             │
│    • Subscribes to: "Domain.ArticleReceived"                    │
│    • Orchestrates: Storage workflow                             │
│    • Publishes: "Domain.ArticleStorageRequested"                │
│                                                                   │
│  ClassifyArticleUseCase                                          │
│    • Subscribes to: "Domain.ArticleReceived"                    │
│    • Orchestrates: Classification workflow                      │
│    • Publishes: "Domain.ClassificationRequested"                │
│                                                                   │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        │ Domain Event
                        ↓
┌─────────────────────────────────────────────────────────────────┐
│                      DOMAIN LISTENER                              │
│                    (StorageDomainListener)                        │
│                                                                   │
│  • Subscribes to: "Domain.ArticleStorageRequested"              │
│  • Converts: Domain → Infrastructure                             │
│  • Publishes: "ArticleStorageRequested"                         │
│                                                                   │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        │ Infrastructure Event
                        ↓
┌─────────────────────────────────────────────────────────────────┐
│                    INFRASTRUCTURE LAYER                          │
│                                                                   │
│  Storage Infrastructure Service                                 │
│    • Subscribes to: "ArticleStorageRequested"                   │
│    • Calls: Repository (actual I/O)                             │
│    • Publishes: "ArticleStored"                                │
│                                                                   │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        │ Infrastructure Event
                        ↓
┌─────────────────────────────────────────────────────────────────┐
│                      DOMAIN LISTENER                              │
│                    (StorageDomainListener)                       │
│                                                                   │
│  • Subscribes to: "ArticleStored"                               │
│  • Converts: Infrastructure → Domain                             │
│  • Publishes: "Domain.ArticleStored"                             │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Observations

1. **Bidirectional Flow:**
   - Infrastructure → Domain (via domain listener)
   - Domain → Infrastructure (via domain listener)

2. **Event Types:**
   - Infrastructure events: `"ArticleReceived"`, `"ArticleStorageRequested"`, `"ArticleStored"`
   - Domain events: `"Domain.ArticleReceived"`, `"Domain.ArticleStorageRequested"`, `"Domain.ArticleStored"`

3. **Domain Listeners are Adapters:**
   - They bridge between infrastructure and domain
   - They handle conversion and validation
   - They're the ONLY place infrastructure and domain meet

4. **Use Cases Orchestrate:**
   - They subscribe to domain events
   - They coordinate workflows
   - They publish domain events (not infrastructure events)

5. **Infrastructure Services Do I/O:**
   - They subscribe to infrastructure events
   - They perform actual operations (file I/O, API calls)
   - They publish infrastructure result events

---

## 3. Services vs Use Cases: What's the Difference?

### Use Cases: Orchestration

**What they do:**
- Subscribe to domain events
- Coordinate workflows
- Publish domain events
- **They orchestrate** - they don't do the work themselves

**Example:**
```python
class StoreArticleUseCase:
    async def _handle_article_received(self, event: ArticleReceivedDomainEvent):
        # Step 1: Create storage request (using factory)
        stored_article = self.stored_article_factory.create_from_domain_article(event.article)
        
        # Step 2: Publish domain event (triggers storage)
        await self.event_bus.publish("Domain.ArticleStorageRequested", ...)
        
        # That's it! The use case doesn't actually store the article.
        # It just orchestrates the workflow by publishing events.
```

### Services: Focused Operations

**What they do:**
- Provide focused, reusable operations
- Work with domain models
- Can be called directly OR used via events
- **They do the work** - they perform operations

**Example:**
```python
class StorageQueryService:
    async def fetch_article(self, article_id: str) -> Optional[DomainArticle]:
        # This service DOES the work - it fetches an article
        # It can be called directly by use cases
        
        # Step 1: Publish fetch request event
        await self.event_bus.publish("Domain.ArticleFetchRequested", ...)
        
        # Step 2: Wait for result (using future)
        stored_article = await future
        
        # Step 3: Convert and return
        return self._convert_to_domain_article(stored_article)
```

### When to Use Services Directly vs Events

**Use Events When:**
- You want decoupling (don't know who will handle it)
- You want multiple handlers
- You want async, fire-and-forget behavior

**Use Services Directly When:**
- You need a return value immediately
- You need synchronous behavior
- You have a specific operation that one service does

**Example in Codebase:**

```python
class NotifyImminentArticleUseCase:
    def __init__(self, storage_query_service: StorageQueryService):
        self.storage_query_service = storage_query_service  # ✅ Direct dependency
    
    async def _handle_article_classified(self, event: ArticleClassifiedDomainEvent):
        # Need article data - call service directly
        article = await self.storage_query_service.fetch_article(event.result.article_id)
        #                                                          ↑
        #                                    Direct call - need return value
        
        # Create notification - publish event (decoupled)
        await self.event_bus.publish("Domain.NotificationRequested", ...)
        #                                                          ↑
        #                                    Event - don't need return value
```

### Services in Event-Driven Architecture

**Services are NOT part of the event flow directly**, but they:

1. **Can subscribe to events:**
   ```python
   class AutoTradeService:
       def __init__(self):
           subscribe_typed("Domain.ArticleClassified", ..., self._handle)
   ```

2. **Can be called directly:**
   ```python
   class NotifyImminentArticleUseCase:
       def __init__(self, storage_query_service: StorageQueryService):
           self.storage_query_service = storage_query_service
       
       async def _handle(self, event):
           article = await self.storage_query_service.fetch_article(...)  # Direct call
   ```

3. **Provide focused operations:**
   - One service = one responsibility
   - Services are reusable
   - Services work with domain models

### Utils: Pure Functions

**What they are:**
- Pure functions (no side effects)
- No state
- Stateless helpers

**Example:**
```python
# utils/article_utils.py
def get_article_id(article: Union[BenzingaArticle, StandardizedArticle, dict]) -> str:
    """Pure function - no side effects, no state."""
    if isinstance(article, dict):
        return article.get("id", "")
    return article.id
```

**How to use:**
- Import and call directly
- No dependency injection needed (they're stateless)
- No events needed (they're synchronous, pure functions)

---

## Summary

### Event Flow Pattern

1. **Infrastructure** receives external input → publishes infrastructure event
2. **Domain Listener** subscribes → converts → publishes domain event
3. **Use Cases** subscribe → orchestrate → publish domain events
4. **Domain Listener** subscribes → converts → publishes infrastructure event
5. **Infrastructure** subscribes → performs I/O → publishes infrastructure result
6. **Domain Listener** subscribes → converts → publishes domain result

### Services Role

- **Not directly in event flow** (they're called, not part of pub/sub)
- **Provide focused operations** (one thing, done well)
- **Can be called directly** (when you need return values)
- **Work with domain models** (type-safe, validated)

### Utils Role

- **Pure functions** (no side effects, no state)
- **Stateless helpers** (import and use directly)
- **No dependency injection** (they're just functions)

