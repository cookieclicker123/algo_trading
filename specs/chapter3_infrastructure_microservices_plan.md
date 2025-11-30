# Chapter 3: Infrastructure Microservices - Detailed Plan

## Overview

Extract infrastructure concerns into three isolated microservices. This will dramatically reduce service sizes and make the codebase much more scrutable.

**Goal**: Services should NOT know about:
- WebSocket connection details
- IBKR Gateway connection details  
- JSON file storage details

Instead, infrastructure publishes events, and services subscribe to them.

---

## Subchapter 3.1: WebSocket Microservice

### Current State
- `benzinga_websocket_service.py` (~740 lines) - mixes connection management with business logic
- Directly calls `article_processor.process_article()` - tight coupling
- Connection management, reconnection, ping/pong, error handling all mixed together

### Target State
- `infra/websocket/service.py` - pure connection management
- `infra/websocket/events.py` - event definitions (ArticleReceived)
- `infra/websocket/protocol.py` - protocol/interface definition
- Publishes events to event bus instead of calling services directly

### Steps
1. Create `infra/websocket/` directory structure
2. Extract connection management logic from `benzinga_websocket_service.py`
3. Create event bus infrastructure
4. Define `ArticleReceived` event
5. Refactor WebSocket service to publish events instead of calling processor
6. Update `feed_manager.py` to subscribe to events

### Deliverables
- WebSocket microservice in `infra/websocket/`
- Event bus implementation
- Protocol definitions
- Reduced `benzinga_websocket_service.py` size

---

## Subchapter 3.2: Brokerage Microservice

### Current State
- `ibkr_trading_service.py` (~2000 lines) - massive service doing everything
- `ibkr_keepalive_service.py` - separate keepalive service
- Direct connection management, trade execution, quote fetching, option logic all mixed
- Business logic mixed with infrastructure

### Target State
- `infra/brokerage/service.py` - connection management + trade execution
- `infra/brokerage/events.py` - events (TradeExecuted, TradeFailed, QuoteReceived)
- `infra/brokerage/protocol.py` - protocol for trade commands
- Publishes events, accepts commands via protocol

### Steps
1. Create `infra/brokerage/` directory structure
2. Extract IBKR connection management
3. Extract trade execution logic
4. Create event bus for trading events
5. Define `TradeCommand` protocol and `TradeResult` events
6. Refactor trading service to use commands/events
7. Merge keepalive into brokerage microservice

### Deliverables
- Brokerage microservice in `infra/brokerage/`
- Event bus for trading
- Protocol for trade commands
- Dramatically reduced service sizes

---

## Subchapter 3.3: Data Persistence Microservice

### Current State
- `utils/json_storage.py` - JSON file operations scattered
- Services directly read/write JSON files
- No repository pattern
- No abstraction for data storage

### Target State
- `infra/persistence/repository.py` - repository implementation
- `infra/persistence/unit_of_work.py` - transactional consistency
- `infra/persistence/protocol.py` - repository protocol
- Abstract data operations behind repository interface

### Steps
1. Create `infra/persistence/` directory structure
2. Extract JSON storage operations
3. Create repository protocol/interface
4. Implement repository pattern for articles
5. Create Unit of Work for transactions
6. Update services to use repository instead of direct file access

### Deliverables
- Data persistence microservice in `infra/persistence/`
- Repository pattern implementation
- Unit of Work for transactions
- Abstracted data operations

---

## Implementation Order

1. **First**: Create event bus infrastructure (needed by all microservices)
2. **Then**: 3.1 WebSocket (simplest, good starting point)
3. **Then**: 3.3 Data Persistence (needed before full service refactoring)
4. **Last**: 3.2 Brokerage (most complex)

---

## Key Principles

- **Events, not method calls**: Infrastructure publishes events, services subscribe
- **Protocols, not classes**: Define interfaces, not concrete implementations
- **Stateless microservices**: Each microservice is independent
- **Clear boundaries**: Infrastructure never imports from services/domain

