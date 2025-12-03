# DI Framework Implementation - Detailed Plan

## Overview

Replace manual dependency injection with `dependency-injector` framework for:
- Automatic dependency resolution
- Easy testing (mock injection)
- Type-safe dependency management
- Industry-standard pattern

## Current State Analysis

**Current Manual DI:**
- `composition_root.py` manually wires all dependencies (133 lines)
- Cross-microservice dependencies wired manually
- Config fetched directly from `settings.py`
- Hard to test (can't easily inject mocks)

**Dependencies Identified:**
1. **Shared:**
   - `AsyncEventBus` (singleton)
   - Telegram configs (from settings)
   - Storage config (from settings)

2. **Microservice-specific:**
   - Storage: storage_config
   - Classification: GROQ_API_KEY, GROQ_MODEL
   - Notification: telegram_config_1, telegram_config_2
   - Brokerage: paper_trading, client_id
   - WebSocket: BENZINGA_API_KEY, telegram_service

3. **Cross-microservice:**
   - Notification use case → Storage query service
   - AutoTrade service → Storage query service
   - WebSocket → Telegram service
   - Trade handlers → Brokerage infra

## Implementation Strategy

### Phase 1: Container Structure (Current)

Create container classes that define providers:
- Configuration providers (singletons)
- Service providers (factories)
- Cross-microservice dependency providers

### Phase 2: Refactor Composition Root

Replace manual initialization with container.wire() calls.

### Phase 3: FastAPI Integration

Use container to provide dependencies to routes via FastAPI Depends.

## Container Architecture

```
ApplicationContainer
├── ConfigurationContainer (config providers)
├── StorageContainer (storage microservice)
├── ClassificationContainer (classification microservice)
├── NotificationContainer (notification microservice)
├── BrokerageContainer (brokerage microservice)
└── WebSocketContainer (websocket microservice)
```

## Benefits

1. **Testing:** Easy to override providers for mocks
2. **Type Safety:** Type hints enforced
3. **Maintainability:** Clear dependency graph
4. **Standards:** Industry-standard pattern

## Migration Path

1. Keep existing code working
2. Add containers alongside current code
3. Migrate one microservice at a time
4. Full migration then remove manual DI

