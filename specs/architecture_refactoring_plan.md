# Architecture Refactoring Plan

## Overview

This document outlines a comprehensive refactoring plan to transform the NewsFlash codebase from a tightly-coupled, side-effect-riddled system into a clean, testable, maintainable architecture following Domain-Driven Design (DDD) principles.

**Goal**: Create a system where:
- Infrastructure concerns (websocket, brokerage, data storage) are isolated as microservices
- Business logic is pure and testable in the domain layer
- Services are cohesive operations using domain models
- Use cases orchestrate workflows
- Dependency injection is used throughout
- Events enable pub/sub communication between layers

---

## Current State Analysis

### Services Inventory
1. `article_processor.py` - Processes articles, orchestrates classification and trading
2. `auto_trade_service.py` - Executes trades automatically
3. `benzinga_websocket_service.py` - WebSocket connection to news feed
4. `classification_audit_trail.py` - Logs classification events
5. `feed_health_monitor.py` - Monitors feed health
6. `feed_manager.py` - Manages multiple news feeds
7. `ibkr_keepalive_service.py` - Keeps IBKR connection alive
8. `ibkr_trading_service.py` - Trading operations with IBKR
9. `news_classifier.py` - AI classification service
10. `position_tracker.py` - Tracks open positions
11. `price_tracking_service.py` - Tracks prices
12. `service_container.py` - Dependency injection container
13. `telegram_service.py` - Telegram notifications
14. `telegram_trade_handler.py` - Handles Telegram trade commands
15. `translation_service.py` - Translates articles
16. `yfinance_service.py` - Fetches market data

### Key Problems Identified

1. **Side Effects Everywhere**: Services directly import and use infrastructure (websocket, IBKR, JSON files)
2. **Mixed Responsibilities**: Services contain both business logic and infrastructure code
3. **Tight Coupling**: Services import each other directly, creating circular dependencies
4. **No Abstraction**: Infrastructure details leak into business logic
5. **Hard to Test**: Can't test business logic without mocking entire infrastructure
6. **No Clear Contracts**: Services communicate through direct method calls, not events
7. **Data Operations in Services**: Services read/write JSON files directly
8. **Too Many Classes**: Business logic is in classes instead of pure functions
9. **No Repository Pattern**: Data access is scattered throughout services
10. **No Dependency Injection**: Services create their own dependencies

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      API Layer (FastAPI)                     │
│              - Routes & endpoints                            │
│              - Dependency injection                          │
│              - Request/response models                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Use Cases Layer                           │
│              - Orchestrate workflows                        │
│              - Coordinate services                          │
│              - Return domain events                         │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Services Layer                            │
│              - Cohesive business operations                 │
│              - Pure functions (not classes)                 │
│              - Use domain models                            │
│              - Subscribe to events                          │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
┌──────────────────┐      ┌──────────────────┐
│  Domain Layer    │      │ Infrastructure   │
│  - Pure logic    │◄────►│  - WebSocket     │
│  - Business rules│Events│  - Brokerage     │
│  - Entities      │      │  - Data Store    │
│  - Value objects │      │  - External APIs │
└──────────────────┘      └──────────────────┘
          │                         │
          └────────────┬────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  Repository Layer                            │
│              - Map domain ↔ ORM                             │
│              - Abstract data source                         │
│              - Unit of Work pattern                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Refactoring Chapters

### Chapter 1: Remove Unnecessary Code
**Goal**: Identify and remove dead code, unused imports, redundant functionality, and overly complex implementations that don't add value.

**Subchapters**:
1.1. Audit service files for unused code
1.2. Remove duplicate functionality across services
1.3. Simplify overly complex classes
1.4. Remove unused configuration
1.5. Clean up unused models/utilities

**Deliverables**:
- List of removed code with rationale
- Cleaner, more focused codebase

---

### Chapter 2: Deduplicate Code
**Goal**: Extract common patterns into utilities, consolidate repeated logic into reusable functions, and centralize data structures in models.

**Subchapters**:
2.1. Extract common utility functions
2.2. Consolidate repeated data operations
2.3. Create shared Pydantic models for common structures
2.4. Extract configuration patterns
2.5. Create helper functions for common calculations

**Deliverables**:
- Utility functions in `utils/` organized by purpose
- Shared models in `models/` for common data structures
- Reduced code duplication

---

### Chapter 3: Separate Data from Logic - Infrastructure Microservices
**Goal**: Extract infrastructure concerns into three isolated microservices with clear interfaces.

**Subchapters**:
3.1. **WebSocket Microservice**
   - Extract websocket connection logic
   - Create event bus for publishing news events
   - Define protocols/interfaces for news events
   - Isolate connection management from business logic

3.2. **Brokerage Microservice**
   - Extract IBKR trading operations
   - Create event bus for publishing trading events
   - Define protocols/interfaces for trading commands/results
   - Isolate connection management from business logic

3.3. **Data Persistence Microservice**
   - Extract JSON storage operations
   - Create repository pattern for data access
   - Define protocols/interfaces for data operations
   - Prepare for database migration (PostgreSQL)
   - Implement Unit of Work pattern for transactions

**Deliverables**:
- Three infrastructure microservices in `infra/`
- Event buses for pub/sub communication
- Protocol definitions for each microservice
- Clear separation of infrastructure from business logic

---

### Chapter 4: Create Domain Layer and Contracts
**Goal**: Establish domain models, protocols for communication, and clear contracts between layers.

**Subchapters**:
4.1. **Domain Models**
   - Extract business entities (Article, Classification, Trade, Position)
   - Create value objects (Ticker, Price, MarketCap)
   - Define domain events (ArticleReceived, Classified, TradeExecuted)
   - Use Pydantic for type safety

4.2. **Protocols and Interfaces**
   - Define protocols for infrastructure services
   - Create event protocols for pub/sub
   - Define repository protocols
   - Create service interfaces

4.3. **Event Bus Implementation**
   - Implement async event bus
   - Define event types and payloads
   - Create subscription mechanism
   - Enable domain to subscribe to infrastructure events

4.4. **Repository Pattern**
   - Create repository interfaces
   - Implement domain ↔ ORM mapping
   - Implement Unit of Work for transactional consistency
   - Create data transfer objects (DTOs)

**Deliverables**:
- Domain layer with pure business logic
- Protocol definitions for all interfaces
- Event bus implementation
- Repository pattern with Unit of Work

---

### Chapter 5: Refactor Services to Use Domain and Infrastructure
**Goal**: Transform services into cohesive business operations that use domain models and subscribe to infrastructure events.

**Subchapters**:
5.1. **Refactor Service Functions**
   - Convert classes to functions where appropriate
   - Remove infrastructure dependencies
   - Use domain models instead of infrastructure models
   - Subscribe to infrastructure events via event bus

5.2. **Article Processing Service**
   - Use domain Article model
   - Subscribe to news events from WebSocket microservice
   - Emit domain events for classification
   - Use repository for persistence

5.3. **Classification Service**
   - Use domain Classification model
   - Subscribe to ArticleReceived events
   - Emit Classified events
   - Pure business logic (no infrastructure)

5.4. **Trading Service**
   - Use domain Trade/Position models
   - Subscribe to Classified events
   - Emit trading commands to Brokerage microservice
   - Subscribe to trading result events

5.5. **Notification Service**
   - Subscribe to domain events
   - Format notifications based on domain models
   - No direct infrastructure access

**Deliverables**:
- Services as cohesive business operations
- Services use domain models
- Services communicate via events
- No infrastructure dependencies in services

---

### Chapter 6: Create Use Cases Layer
**Goal**: Add orchestration layer that coordinates services and manages workflows.

**Subchapters**:
6.1. **Use Case Structure**
   - Define use case functions
   - Orchestrate multiple services
   - Handle workflow state
   - Return domain events

6.2. **Process News Article Use Case**
   - Orchestrate: Receive → Classify → Notify → Trade
   - Coordinate multiple services
   - Handle errors and retries
   - Emit workflow completion events

6.3. **Execute Trade Use Case**
   - Orchestrate: Validate → Execute → Track → Exit
   - Coordinate trading and position tracking
   - Handle execution errors
   - Emit trade lifecycle events

6.4. **Monitor Feed Use Case**
   - Orchestrate: Monitor → Alert → Recover
   - Coordinate health monitoring
   - Handle connection issues
   - Emit health events

**Deliverables**:
- Use cases that orchestrate services
- Clear workflow definitions
- Workflow completion events

---

### Chapter 7: Implement Dependency Injection System-Wide
**Goal**: Use FastAPI dependencies and dependency injection throughout the codebase.

**Subchapters**:
7.1. **FastAPI Dependencies**
   - Create dependency functions for all services
   - Create dependency functions for repositories
   - Create dependency functions for infrastructure clients
   - Wire dependencies in FastAPI routes

7.2. **Service Dependencies**
   - Services receive dependencies via constructor/parameters
   - No direct instantiation of dependencies
   - Dependencies are protocols/interfaces
   - Easy to mock for testing

7.3. **Use Case Dependencies**
   - Use cases receive services via parameters
   - Dependencies are injected from FastAPI
   - Clear dependency chain

7.4. **Testing Support**
   - Create test fixtures for dependencies
   - Easy to swap implementations
   - Mock infrastructure easily

**Deliverables**:
- Dependency injection throughout
- FastAPI dependencies file
- All dependencies are interfaces/protocols
- Easy to test and mock

---

### Chapter 8: Advanced Patterns (Future)
**Goal**: Implement advanced patterns for scalability and reliability.

**Subchapters**:
8.1. Command Query Responsibility Segregation (CQRS)
8.2. Saga pattern for distributed transactions
8.3. Outbox pattern for reliable event publishing
8.4. Circuit breaker pattern for external services
8.5. Retry and backoff strategies

**Note**: This chapter is for future work after core refactoring is complete.

---

## Directory Structure After Refactoring

```
src/newsflash/
├── api/
│   ├── dependencies.py      # FastAPI dependency injection
│   ├── routes/
│   │   ├── articles.py
│   │   ├── trading.py
│   │   └── health.py
│   └── app.py               # FastAPI app creation
│
├── use_cases/
│   ├── process_article.py
│   ├── execute_trade.py
│   └── monitor_feed.py
│
├── services/
│   ├── classification.py    # Functions, not classes
│   ├── trading.py
│   └── notification.py
│
├── domain/
│   ├── entities/
│   │   ├── article.py
│   │   ├── trade.py
│   │   └── position.py
│   ├── value_objects/
│   │   ├── ticker.py
│   │   └── price.py
│   ├── events/
│   │   ├── article_received.py
│   │   ├── classified.py
│   │   └── trade_executed.py
│   └── protocols/
│       ├── repository.py
│       └── event_bus.py
│
├── infra/
│   ├── websocket/
│   │   ├── service.py       # WebSocket microservice
│   │   ├── events.py        # WebSocket events
│   │   └── protocol.py      # WebSocket protocol
│   ├── brokerage/
│   │   ├── service.py       # Brokerage microservice
│   │   ├── events.py        # Trading events
│   │   └── protocol.py      # Brokerage protocol
│   ├── persistence/
│   │   ├── repository.py    # Repository implementations
│   │   ├── unit_of_work.py  # Transaction management
│   │   └── json_store.py    # JSON implementation (temporary)
│   └── event_bus/
│       └── async_bus.py     # Event bus implementation
│
├── repositories/
│   ├── article_repository.py
│   ├── trade_repository.py
│   └── position_repository.py
│
├── models/
│   ├── api/
│   │   ├── requests.py
│   │   └── responses.py
│   ├── domain/
│   │   └── (shared with domain/entities)
│   └── infra/
│       └── orm.py           # ORM models for PostgreSQL
│
├── utils/
│   ├── calculations/
│   ├── formatting/
│   └── validation/
│
└── config/
    └── settings.py
```

---

## Implementation Guidelines

### Principles to Follow

1. **Pure Functions**: Services should be functions that take inputs and return outputs, with no side effects
2. **Immutability**: Domain models should be immutable (frozen Pydantic models)
3. **Protocols over Classes**: Use protocols/interfaces instead of concrete classes
4. **Event-Driven**: Use events for communication between layers
5. **Dependency Inversion**: High-level modules should not depend on low-level modules
6. **Single Responsibility**: Each service/function should do one thing well
7. **Testability**: All business logic should be easily testable without infrastructure

### Testing Strategy

- **Unit Tests**: Test pure domain logic and service functions in isolation
- **Integration Tests**: Test microservices with mocked infrastructure
- **Contract Tests**: Test protocol implementations
- **End-to-End Tests**: Test full workflows with test infrastructure

### Migration Strategy

- Refactor incrementally, one service at a time
- Maintain backward compatibility during transition
- Use feature flags if needed
- Keep old code working until new code is proven

---

## Success Criteria

After refactoring, the system should:

1. ✅ Infrastructure concerns isolated in microservices
2. ✅ Business logic is pure and testable
3. ✅ Services are cohesive functions using domain models
4. ✅ Use cases orchestrate workflows
5. ✅ Dependency injection used throughout
6. ✅ Events enable pub/sub communication
7. ✅ Repository pattern abstracts data access
8. ✅ Unit of Work ensures transactional consistency
9. ✅ Easy to test (unit, integration, contract)
10. ✅ Easy to swap implementations (websocket, brokerage, database)

---

## Progress Tracking

### Chapter 1: Remove Unnecessary Code
- [ ] 1.1 Audit service files
- [ ] 1.2 Remove duplicates
- [ ] 1.3 Simplify classes
- [ ] 1.4 Clean config
- [ ] 1.5 Clean models/utils

### Chapter 2: Deduplicate Code
- [ ] 2.1 Extract utilities
- [ ] 2.2 Consolidate data ops
- [ ] 2.3 Shared models
- [ ] 2.4 Config patterns
- [ ] 2.5 Helper functions

### Chapter 3: Infrastructure Microservices
- [ ] 3.1 WebSocket microservice
- [ ] 3.2 Brokerage microservice
- [ ] 3.3 Data persistence microservice

### Chapter 4: Domain Layer and Contracts
- [ ] 4.1 Domain models
- [ ] 4.2 Protocols/interfaces
- [ ] 4.3 Event bus
- [ ] 4.4 Repository pattern

### Chapter 5: Refactor Services
- [ ] 5.1 Service functions
- [ ] 5.2 Article processing
- [ ] 5.3 Classification
- [ ] 5.4 Trading
- [ ] 5.5 Notifications

### Chapter 6: Use Cases
- [ ] 6.1 Use case structure
- [ ] 6.2 Process article use case
- [ ] 6.3 Execute trade use case
- [ ] 6.4 Monitor feed use case

### Chapter 7: Dependency Injection
- [ ] 7.1 FastAPI dependencies
- [ ] 7.2 Service dependencies
- [ ] 7.3 Use case dependencies
- [ ] 7.4 Testing support

### Chapter 8: Advanced Patterns (Future)
- [ ] TBD

---

**Document Version**: 1.0  
**Created**: 2025-11-29  
**Status**: Ready for Implementation - Start with Chapter 1

