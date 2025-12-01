# Storage & Notifications Microservices Plan

## Overview

Final two microservices to complete the fully decoupled, event-driven architecture:
1. **Storage Microservice** - Data persistence layer
2. **Notifications Microservice** - Telegram notifications

Both follow the same pattern as WebSocket, Brokerage, and Classification microservices.

---

## Part 1: Storage Microservice

### 1.1 Current State Analysis

**Current Storage Operations:**
- `utils/json_storage.py` - `ArticleStorage` class
  - Stores articles as JSON files
  - Rolling window (1 hour default)
  - Daily archival
  - Deduplication by source_id
  
- `services/classification_audit_trail.py` - `ClassificationAuditTrail` class
  - Stores classification audit logs as JSON files
  - Daily files organized by year/month/week
  - Updates existing entries (trade details, price history)

**Infrastructure Coupling:**
- Direct file I/O operations
- JSON serialization/deserialization
- File system path management
- No abstraction layer

**What Needs to Change:**
- Move all file I/O to infrastructure layer
- Create domain layer with repositories
- Publish events when data is persisted
- Subscribe to domain events to trigger persistence

---

### 1.2 Storage Microservice Architecture

#### Infrastructure Layer (`infra/storage/`)

**Files:**
```
infra/storage/
├── __init__.py
├── infrastructure_models.py      # Typed models for storage operations
├── events.py                     # Infrastructure events
├── event_protocols.py            # Protocols for infrastructure events
├── article_repository.py         # Article storage implementation (file I/O)
├── audit_repository.py           # Audit trail storage implementation (file I/O)
└── service.py                    # Main storage infrastructure service
```

**Responsibilities:**
- Direct file I/O operations (JSON files)
- File system path management
- JSON serialization/deserialization
- Publishing infrastructure events when data is persisted

**Infrastructure Models:**
```python
class ArticleStorageRequestData(BaseModel):
    article_id: str
    article_data: dict  # Serialized article
    stored_at: datetime

class ArticleStoredInfrastructureEvent(BaseModel):
    request_data: ArticleStorageRequestData
    file_path: str
    stored_at: datetime

class AuditLogStorageRequestData(BaseModel):
    article_id: str
    audit_data: dict  # Serialized audit entry
    logged_at: datetime

class AuditLoggedInfrastructureEvent(BaseModel):
    request_data: AuditLogStorageRequestData
    file_path: str
    logged_at: datetime
```

---

#### Domain Layer (`domain/storage/`)

**Files:**
```
domain/storage/
├── __init__.py
├── models.py                     # Domain models: Article, AuditEntry
├── events.py                     # Domain events: ArticleStored, AuditLogged
├── event_protocols.py            # Protocols for domain events
├── validators.py                 # Business rule validation
├── mappers.py                    # Infrastructure ↔ Domain mapping
├── factories.py                  # Create domain models
└── listener.py                   # Bridge infra ↔ domain events
```

**Domain Models:**
```python
class Article(BaseModel):
    """Domain model for stored article."""
    article_id: str
    source: str
    title: str
    content: str
    tickers: list[str]
    published_at: datetime
    stored_at: datetime
    # ... other fields
    
    model_config = {"frozen": True}

class AuditEntry(BaseModel):
    """Domain model for audit trail entry."""
    article_id: str
    classification: str
    confidence: str
    news_received_at: datetime
    classified_at: datetime
    logged_at: datetime
    # ... other fields
    
    model_config = {"frozen": True}
```

**Domain Events:**
```python
class ArticleStoredDomainEvent(BaseModel):
    article: Article  # Domain model
    stored_at: datetime
    source: str = "domain.storage"

class AuditLoggedDomainEvent(BaseModel):
    entry: AuditEntry  # Domain model
    logged_at: datetime
    source: str = "domain.storage"
```

---

#### Services Layer (`services/storage/`)

**Files:**
```
services/storage/
├── __init__.py
└── article_service.py            # Subscribe to domain events, provide storage operations
```

**Responsibilities:**
- Subscribe to `Domain.ArticleStorageRequest` events
- Call infrastructure service methods
- Provide focused storage operations for use cases

---

#### Use Cases Layer (`use_cases/`)

**Files:**
```
use_cases/
└── store_article_use_case.py     # Orchestrate article storage
```

**Responsibilities:**
- Subscribe to `Domain.ArticleReceived` events
- Publish `Domain.ArticleStorageRequest` events
- Orchestrate storage workflow

---

### 1.3 Event Flow - Storage

```
1. Domain.ArticleReceived (from WebSocket domain)
   ↓
2. StoreArticleUseCase subscribes → publishes Domain.ArticleStorageRequest
   ↓
3. StorageDomainListener subscribes → maps → publishes ArticleStorageRequestInfrastructureEvent
   ↓
4. StorageInfrastructureService subscribes → calls repository → persists to file
   ↓
5. StorageInfrastructureService publishes ArticleStoredInfrastructureEvent
   ↓
6. StorageDomainListener subscribes → maps → publishes Domain.ArticleStored
   ↓
7. StorageService subscribes → logs/stats (optional)
```

---

### 1.4 Migration Checklist

**Phase 1: Infrastructure Layer**
- [ ] Create `infra/storage/` directory
- [ ] Create infrastructure models
- [ ] Create infrastructure events
- [ ] Create event protocols
- [ ] Implement `ArticleRepository` (file I/O)
- [ ] Implement `AuditRepository` (file I/O)
- [ ] Implement `StorageInfrastructureService`
- [ ] Publish infrastructure events on persistence

**Phase 2: Domain Layer**
- [ ] Create `domain/storage/` directory
- [ ] Create domain models (Article, AuditEntry)
- [ ] Create domain events
- [ ] Create event protocols
- [ ] Implement validators
- [ ] Implement mappers
- [ ] Implement factories
- [ ] Implement domain listener

**Phase 3: Services & Use Cases**
- [ ] Create `services/storage/` directory
- [ ] Implement storage services
- [ ] Create storage use case
- [ ] Wire up event subscriptions

**Phase 4: Integration**
- [ ] Update `ProcessArticleUseCase` to use storage microservice
- [ ] Update `ClassificationAuditService` to use storage microservice
- [ ] Remove direct `ArticleStorage` calls from `article_processor.py`
- [ ] Remove direct `ClassificationAuditTrail` calls from services
- [ ] Update service initialization
- [ ] Test end-to-end flow

---

## Part 2: Notifications Microservice

### 2.1 Current State Analysis

**Current Notification Operations:**
- `services/telegram_service.py` - `TelegramNotifier` class
  - Two Telegram bots (primary + secondary)
  - Message formatting
  - Message queuing
  - Test mode (JSON logging)
  
- `services/telegram_trade_handler.py` - `TelegramTradeHandler` class
  - Trade command handling
  - Trade status updates
  - Interactive trade management

**Infrastructure Coupling:**
- Direct Telegram Bot API calls
- Bot token management
- Message queue management
- No abstraction layer

**What Needs to Change:**
- Move Telegram API client to infrastructure layer
- Create domain layer for notifications
- Publish events when notifications are sent
- Subscribe to domain events to trigger notifications

---

### 2.2 Notifications Microservice Architecture

#### Infrastructure Layer (`infra/notifications/`)

**Files:**
```
infra/notifications/
├── __init__.py
├── infrastructure_models.py      # Typed models for notifications
├── events.py                     # Infrastructure events
├── event_protocols.py            # Protocols for infrastructure events
├── telegram_client.py            # Telegram Bot API client (stateful)
├── message_formatter.py          # Message formatting utilities
└── service.py                    # Main notifications infrastructure service
```

**Responsibilities:**
- Telegram Bot API client management
- Message sending
- Bot token management
- Publishing infrastructure events when messages are sent

**Infrastructure Models:**
```python
class TelegramNotificationRequestData(BaseModel):
    chat_id: str
    message_text: str
    parse_mode: Optional[str] = "HTML"
    bot_id: str = "primary"  # "primary" or "secondary"

class TelegramNotificationSentInfrastructureEvent(BaseModel):
    request_data: TelegramNotificationRequestData
    message_id: Optional[int]
    sent_at: datetime
    success: bool

class TelegramNotificationFailedInfrastructureEvent(BaseModel):
    request_data: TelegramNotificationRequestData
    error: str
    failed_at: datetime
```

---

#### Domain Layer (`domain/notifications/`)

**Files:**
```
domain/notifications/
├── __init__.py
├── models.py                     # Domain models: Notification, NotificationChannel
├── events.py                     # Domain events: NotificationRequested, NotificationSent
├── event_protocols.py            # Protocols for domain events
├── validators.py                 # Business rule validation
├── mappers.py                    # Infrastructure ↔ Domain mapping
├── factories.py                  # Create domain models
└── listener.py                   # Bridge infra ↔ domain events
```

**Domain Models:**
```python
class NotificationChannel(str, Enum):
    TELEGRAM_PRIMARY = "telegram_primary"
    TELEGRAM_SECONDARY = "telegram_secondary"

class Notification(BaseModel):
    """Domain model for notification."""
    recipient: str  # chat_id or user_id
    channel: NotificationChannel
    message: str
    formatted_message: Optional[str] = None
    requested_at: datetime
    priority: str = "normal"  # "normal", "high"
    
    model_config = {"frozen": True}
```

**Domain Events:**
```python
class NotificationRequestedDomainEvent(BaseModel):
    notification: Notification  # Domain model
    requested_at: datetime
    source: str = "domain.notifications"

class NotificationSentDomainEvent(BaseModel):
    notification: Notification
    message_id: Optional[int]
    sent_at: datetime
    success: bool
    source: str = "domain.notifications"
```

---

#### Services Layer (`services/notifications/`)

**Files:**
```
services/notifications/
├── __init__.py
└── notification_service.py       # Subscribe to domain events, provide notification operations
```

**Responsibilities:**
- Subscribe to `Domain.NotificationRequested` events
- Format messages from domain models
- Call infrastructure service methods
- Provide focused notification operations for use cases

---

#### Use Cases Layer (`use_cases/`)

**Files:**
```
use_cases/
└── send_notification_use_case.py # Orchestrate notification sending
```

**Responsibilities:**
- Subscribe to `Domain.ArticleClassified` events (for IMMINENT articles)
- Create notification domain models
- Publish `Domain.NotificationRequested` events
- Orchestrate notification workflow

---

### 2.3 Event Flow - Notifications

```
1. Domain.ArticleClassified (from Classification domain)
   ↓
2. SendNotificationUseCase subscribes → creates Notification → publishes Domain.NotificationRequested
   ↓
3. NotificationDomainListener subscribes → maps → publishes TelegramNotificationRequestInfrastructureEvent
   ↓
4. NotificationInfrastructureService subscribes → calls Telegram client → sends message
   ↓
5. NotificationInfrastructureService publishes TelegramNotificationSentInfrastructureEvent
   ↓
6. NotificationDomainListener subscribes → maps → publishes Domain.NotificationSent
   ↓
7. NotificationService subscribes → logs/stats (optional)
```

---

### 2.4 Migration Checklist

**Phase 1: Infrastructure Layer**
- [ ] Create `infra/notifications/` directory
- [ ] Create infrastructure models
- [ ] Create infrastructure events
- [ ] Create event protocols
- [ ] Implement `TelegramClient` (Bot API client)
- [ ] Implement `MessageFormatter`
- [ ] Implement `NotificationInfrastructureService`
- [ ] Publish infrastructure events on send

**Phase 2: Domain Layer**
- [ ] Create `domain/notifications/` directory
- [ ] Create domain models (Notification, NotificationChannel)
- [ ] Create domain events
- [ ] Create event protocols
- [ ] Implement validators
- [ ] Implement mappers
- [ ] Implement factories
- [ ] Implement domain listener

**Phase 3: Services & Use Cases**
- [ ] Create `services/notifications/` directory
- [ ] Implement notification services
- [ ] Create notification use case
- [ ] Wire up event subscriptions

**Phase 4: Integration**
- [ ] Update `ProcessArticleUseCase` to use notification microservice
- [ ] Remove direct `TelegramNotifier` calls from services
- [ ] Remove `TelegramTradeHandler` (keep for now, migrate later)
- [ ] Update service initialization
- [ ] Test end-to-end flow

---

## Part 3: Key Patterns & Decisions

### 3.1 Storage Microservice Patterns

**Repository Pattern:**
- `ArticleRepository` - abstracts article storage
- `AuditRepository` - abstracts audit trail storage
- Both implement same interface (protocol)

**Unit of Work:**
- Single transaction for related storage operations
- Atomic persistence guarantees

**Event-Driven Persistence:**
- Infrastructure publishes events on successful persistence
- Domain layer subscribes and publishes domain events
- Services subscribe to domain events

---

### 3.2 Notifications Microservice Patterns

**Client Abstraction:**
- `TelegramClient` - abstracts Telegram Bot API
- Future: Can add email, SMS, push notifications as separate clients

**Message Formatting:**
- Domain models contain raw data
- Services format messages before sending
- Infrastructure handles actual sending

**Multi-Channel Support:**
- Domain supports multiple channels (Telegram primary/secondary)
- Infrastructure handles channel-specific logic

---

## Part 4: Integration Points

### 4.1 Storage Integration

**Current Callers:**
- `article_processor.py` - `storage.store_articles()`
- `classification_audit_trail.py` - `audit_trail.log_imminent_classification()`

**After Migration:**
- `ProcessArticleUseCase` - publishes `Domain.ArticleStorageRequest`
- `ClassificationAuditService` - publishes `Domain.AuditLogRequest`
- Both subscribe to domain storage events for confirmation

---

### 4.2 Notifications Integration

**Current Callers:**
- `article_processor.py` - `telegram.send_notification()`
- `ProcessArticleUseCase` - calls notification service directly

**After Migration:**
- `SendNotificationUseCase` - subscribes to `Domain.ArticleClassified`
- Publishes `Domain.NotificationRequested`
- No direct service calls

---

## Part 5: Implementation Order

**Recommended Order:**
1. **Storage Microservice First** (more dependencies, critical path)
   - Articles need to be stored before classification
   - Audit trail needs articles to reference
   
2. **Notifications Microservice Second** (depends on storage for article retrieval)
   - Notifications may need to fetch article data
   - Less critical path, can work without for a bit

---

## Part 6: Testing Strategy

**Storage:**
- Test file I/O operations
- Test event publishing on persistence
- Test domain event flow
- Test repository pattern

**Notifications:**
- Test Telegram client (mocked)
- Test message formatting
- Test event publishing on send
- Test domain event flow
- Test multi-bot support

---

## Part 7: Benefits

**Storage Microservice:**
- ✅ Clear abstraction for data persistence
- ✅ Repository pattern enables future database migration
- ✅ Unit of Work ensures transactional integrity
- ✅ Event-driven makes persistence observable

**Notifications Microservice:**
- ✅ Clear abstraction for notifications
- ✅ Easy to add new notification channels
- ✅ Event-driven makes notifications observable
- ✅ Can scale notification processing independently

---

## Summary

Both microservices follow the established pattern:
1. **Infrastructure** - Stateful external dependencies (file I/O, Telegram API)
2. **Domain** - Pure business logic, typed models, events
3. **Services** - Focused operations subscribing to domain events
4. **Use Cases** - High-level orchestration

This completes the full microservices architecture with complete decoupling!

