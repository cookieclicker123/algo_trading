# Chapter 3.3: Data Persistence Microservice

## Overview

Migrate all JSON file persistence operations to a dedicated data persistence layer with repository pattern, Unit of Work, and event-driven architecture.

## Current State Analysis

### JSON Persistence Scattered Across Codebase

**Files with direct JSON file I/O:**
- `utils/json_storage.py` - Article storage with rolling window
- `services/classification_audit_trail.py` - Classification audit logging
- `infra/brokerage/queue_manager.py` - Trade queue persistence
- Various other scattered file operations

**Problems:**
- Direct file I/O mixed with business logic
- No transactional integrity
- No clear separation of concerns
- Difficult to migrate to database later
- Error handling inconsistent
- No event publishing on data changes

## Target Architecture

### Directory Structure

```
src/newsflash/
├── data/                          # NEW: Data persistence layer
│   ├── __init__.py
│   ├── repositories/              # Repository implementations
│   │   ├── __init__.py
│   │   ├── article_repository.py
│   │   ├── classification_audit_repository.py
│   │   ├── trade_queue_repository.py
│   │   └── base_repository.py    # Base class with common operations
│   ├── unit_of_work.py           # Unit of Work pattern
│   ├── storage/                   # Storage implementations (file-based for now)
│   │   ├── __init__.py
│   │   ├── file_storage.py       # Current JSON file storage
│   │   └── storage_protocol.py   # Protocol for future DB storage
│   ├── models/                    # Data models (ORMs)
│   │   ├── __init__.py
│   │   ├── article_model.py
│   │   ├── classification_audit_model.py
│   │   └── trade_queue_model.py
│   └── events.py                  # Data persistence events
├── utils/data/                    # NEW: Low-level file utilities
│   ├── __init__.py
│   ├── file_operations.py        # Basic read/write/atomic operations
│   └── path_utils.py             # Path resolution utilities
├── domain/                        # NEW: Pure business logic domain models
│   ├── __init__.py
│   ├── models/                    # Domain models (no persistence concerns)
│   │   ├── __init__.py
│   │   ├── article.py
│   │   ├── classification_audit.py
│   │   └── trade_queue.py
│   └── protocols/                 # Protocol definitions
│       ├── __init__.py
│       ├── repository_protocol.py
│       └── unit_of_work_protocol.py
```

## Phase Breakdown

### Phase 1: Extract File Utilities (`utils/data/`)

**Goal:** Create low-level file operation utilities that repositories will use.

**Files:**
- `utils/data/file_operations.py`
  - `read_json_file(path) -> dict`
  - `write_json_file(path, data, atomic=True)`
  - `append_json_file(path, data)`
  - `delete_file(path)`
  - `ensure_directory(path)`
  - Error handling for file operations

- `utils/data/path_utils.py`
  - `resolve_data_path(relative_path) -> Path`
  - `get_daily_file_path(base_dir, date) -> Path`
  - `get_weekly_file_path(base_dir, date) -> Path`
  - Path normalization utilities

**Deliverables:**
- ✅ All basic file I/O operations extracted
- ✅ Atomic write support (write to temp, then rename)
- ✅ Consistent error handling
- ✅ Path utilities for organized file structures

---

### Phase 2: Create Data Models (`data/models/`)

**Goal:** Define ORM-like models that map between file storage and domain models.

**Files:**
- `data/models/article_model.py`
  - `ArticleModel` - maps JSON structure to/from domain Article
  - Handles serialization/deserialization
  - No business logic, just data mapping

- `data/models/classification_audit_model.py`
  - `ClassificationAuditModel` - maps audit trail JSON structure
  - Handles date-based file organization
  - Serialization/deserialization only

- `data/models/trade_queue_model.py`
  - `TradeQueueModel` - maps queue JSON structure
  - Handles queue entry serialization

**Deliverables:**
- ✅ Pydantic models for data layer
- ✅ Mapping to/from domain models
- ✅ Validation for data integrity

---

### Phase 3: Create Storage Protocol & Implementation (`data/storage/`)

**Goal:** Abstract storage mechanism (file now, database later).

**Files:**
- `data/storage/storage_protocol.py`
  - `StorageProtocol` - interface for storage operations
  - `read(key) -> dict`
  - `write(key, data)`
  - `delete(key)`
  - `list(prefix) -> List[str]`

- `data/storage/file_storage.py`
  - `FileStorage(StorageProtocol)` - current JSON file implementation
  - Uses utilities from `utils/data/`
  - Organizes files by date/week structure
  - Handles file-based key resolution

**Deliverables:**
- ✅ Storage abstraction layer
- ✅ File-based implementation
- ✅ Ready for future database migration

---

### Phase 4: Create Repository Layer (`data/repositories/`)

**Goal:** Repository pattern for each data entity.

**Files:**
- `data/repositories/base_repository.py`
  - `BaseRepository` - common repository operations
  - CRUD operations
  - Error handling
  - Event publishing on changes

- `data/repositories/article_repository.py`
  - `ArticleRepository(BaseRepository)`
  - `save(article: Article)`
  - `get_by_id(article_id: str) -> Optional[Article]`
  - `get_recent(hours: int) -> List[Article]`
  - `get_archived(date: datetime) -> List[Article]`
  - Uses `ArticleModel` for mapping
  - Uses `FileStorage` for persistence

- `data/repositories/classification_audit_repository.py`
  - `ClassificationAuditRepository(BaseRepository)`
  - `save(audit_entry: ClassificationAudit)`
  - `get_by_date(date: datetime) -> List[ClassificationAudit]`
  - Handles daily file organization

- `data/repositories/trade_queue_repository.py`
  - `TradeQueueRepository(BaseRepository)`
  - `enqueue(trade_request: TradeRequest)`
  - `dequeue_all() -> List[TradeRequest]`
  - `clear()`

**Deliverables:**
- ✅ Repository interface for each entity
- ✅ Mapping between data models and domain models
- ✅ Event publishing on data changes

---

### Phase 5: Implement Unit of Work (`data/unit_of_work.py`)

**Goal:** Ensure transactional integrity across multiple repository operations.

**Files:**
- `data/unit_of_work.py`
  - `UnitOfWork` class
  - `articles: ArticleRepository`
  - `classification_audits: ClassificationAuditRepository`
  - `trade_queue: TradeQueueRepository`
  - `async def commit() -> None` - Persist all changes atomically
  - `async def rollback() -> None` - Discard all changes
  - `async def __aenter__ / __aexit__` - Context manager support

**Pattern:**
```python
async with unit_of_work as uow:
    await uow.articles.save(article)
    await uow.classification_audits.save(audit)
    # All changes committed atomically when exiting context
```

**Deliverables:**
- ✅ Transaction-like behavior for file operations
- ✅ Atomic commits across repositories
- ✅ Rollback support

---

### Phase 6: Create Domain Models (`domain/models/`)

**Goal:** Pure business logic models with no persistence concerns.

**Files:**
- `domain/models/article.py`
  - `Article` - domain model (matches StandardizedArticle for now)
  - Pure business logic
  - No file paths, no JSON concerns

- `domain/models/classification_audit.py`
  - `ClassificationAudit` - domain model
  - Business logic only

- `domain/models/trade_queue.py`
  - `QueuedTrade` - domain model
  - Business logic only

**Deliverables:**
- ✅ Clean domain models
- ✅ Separated from persistence concerns
- ✅ Used by services layer

---

### Phase 7: Define Protocols (`domain/protocols/`)

**Goal:** Type-safe interfaces for repositories and Unit of Work.

**Files:**
- `domain/protocols/repository_protocol.py`
  - `RepositoryProtocol[T]` - generic repository interface
  - Type hints for all operations

- `domain/protocols/unit_of_work_protocol.py`
  - `UnitOfWorkProtocol` - interface for Unit of Work
  - Type hints for all repositories

**Deliverables:**
- ✅ Protocol definitions
- ✅ Type safety
- ✅ Clear contracts

---

### Phase 8: Create Data Events (`data/events.py`)

**Goal:** Events published when data is persisted.

**Files:**
- `data/events.py`
  - `ArticleSavedEvent`
  - `ClassificationAuditSavedEvent`
  - `TradeQueuedEvent`
  - `TradeQueueClearedEvent`
  - All with Pydantic models

**Usage:**
- Repositories publish events after successful commits
- Domain/services can subscribe to events
- Enables event-driven architecture

**Deliverables:**
- ✅ Event definitions
- ✅ Published by repositories
- ✅ Consumed by domain/services

---

### Phase 9: Migrate Existing Code

**Goal:** Replace direct file I/O with repository pattern.

**Migration Steps:**

1. **Migrate Article Storage**
   - Replace `utils/json_storage.py` with `ArticleRepository`
   - Update `article_processor.py` to use repository
   - Use Unit of Work for transaction safety

2. **Migrate Classification Audit Trail**
   - Replace direct JSON writes in `classification_audit_trail.py`
   - Use `ClassificationAuditRepository`
   - Update article processor to use repository

3. **Migrate Trade Queue**
   - Update `queue_manager.py` to use `TradeQueueRepository`
   - Keep queue_manager as business logic wrapper
   - Repository handles persistence

**Deliverables:**
- ✅ All direct file I/O removed
- ✅ All code uses repositories
- ✅ Unit of Work ensures integrity

---

### Phase 10: Create Data Persistence Service (`data/service.py`)

**Goal:** High-level service that coordinates repositories and Unit of Work.

**Files:**
- `data/service.py`
   - `DataPersistenceService`
   - Factory for Unit of Work
   - Manages repository lifecycle
   - Publishes aggregate events

**Deliverables:**
- ✅ Service layer for data operations
- ✅ Factory pattern for Unit of Work
- ✅ Event aggregation

---

## Implementation Principles

### 1. Repository Pattern
- Each entity has a repository
- Repositories abstract storage mechanism
- Can swap file storage for database later

### 2. Unit of Work Pattern
- Multiple repository operations in one transaction
- All-or-nothing commits
- Rollback on errors

### 3. Domain Models
- Pure business logic
- No persistence concerns
- Maps to/from data models

### 4. Event-Driven
- Repositories publish events on data changes
- Services subscribe to events
- Decouples layers

### 5. Protocol-Based
- Clear interfaces
- Type safety
- Easy to mock for testing

## Benefits

1. **Clean Separation:** Data persistence isolated from business logic
2. **Testability:** Easy to mock repositories
3. **Flexibility:** Can swap file storage for database
4. **Reliability:** Unit of Work ensures data integrity
5. **Event-Driven:** Loose coupling via events
6. **Maintainability:** Clear structure and patterns

## Migration Path

### Current → New

**Before:**
```python
# Direct file I/O in business logic
with open("tmp/articles.json", "w") as f:
    json.dump(article_data, f)
```

**After:**
```python
# Repository pattern with Unit of Work
async with unit_of_work as uow:
    await uow.articles.save(article)
    # Committed atomically when exiting context
```

## Success Criteria

- ✅ All file I/O operations use repositories
- ✅ Unit of Work ensures transactional integrity
- ✅ Events published on all data changes
- ✅ Domain models separated from persistence
- ✅ No direct file operations in services
- ✅ Easy to swap file storage for database
- ✅ System continues to work normally

## Future Enhancements

1. **Database Migration**
   - Swap `FileStorage` for `DatabaseStorage`
   - Keep repository interfaces unchanged
   - Add migrations for existing data

2. **Caching Layer**
   - Add Redis caching to repositories
   - Transparent to services layer

3. **Query Optimization**
   - Add indexing for file-based storage
   - Prepare for database queries

