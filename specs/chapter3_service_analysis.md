# Chapter 3: Service Analysis - Infrastructure Coupling

## Analysis Goal

Identify all infrastructure coupling in services and determine where code belongs:
- **infra/websocket/** - WebSocket connection/state
- **services/websocket/** - WebSocket business logic (event handlers)
- **infra/brokerage/** - Brokerage connection/state
- **services/brokerage/** - Trading business logic
- **infra/persistence/** - Data storage operations
- **services/** - Pure business logic only

---

## Service Analysis

### article_processor.py

**Current State:**
- Uses `ArticleStorage` (JSON files) → **DATA PERSISTENCE**
- Uses `ClassificationAuditTrail` (JSON files) → **DATA PERSISTENCE**
- Calls `auto_trade_service.process_imminent_article()` directly → **BROKERAGE**
- Uses `TelegramNotifier` → Business logic (notifications)
- Uses `NewsClassifier` → Business logic (classification)
- Uses `YFinanceService` → External API (market data)

**Infrastructure Coupling:**
1. ✅ **ArticleStorage** - Direct JSON file operations → Move to `infra/persistence`
2. ✅ **ClassificationAuditTrail** - Direct JSON file operations → Move to `infra/persistence`
3. ✅ **auto_trade_service call** - Direct brokerage call → Should publish event to brokerage microservice

**What ArticleProcessor Should Be:**
- Orchestrator that subscribes to ArticleReceived events
- Publishes events: ArticleStored, ArticleClassified
- Subscribes to events for storage/audit (via persistence microservice events)
- Publishes TradeRequest events (to brokerage microservice)

**Decision:**
- ArticleProcessor is BUSINESS LOGIC (orchestration)
- But it should NOT directly call storage or trading
- Should publish/subscribe to events only
- Move to `services/article_processor.py` (stays where it is)
- Remove direct infrastructure calls

---

### auto_trade_service.py

**Current State:**
- Directly uses `trading_service` (IBKRTradingService) → **BROKERAGE**
- Directly uses `position_tracker` → **DATA PERSISTENCE** (JSON files)
- Uses `audit_trail` → **DATA PERSISTENCE**
- Uses `price_tracking_service` → Might be brokerage-related

**Infrastructure Coupling:**
1. ✅ **trading_service** - Direct IBKR calls → Move to `infra/brokerage`, publish TradeRequest events
2. ✅ **position_tracker** - Direct JSON file access → Move to `infra/persistence`
3. ✅ **audit_trail** - Direct JSON file access → Move to `infra/persistence`

**What AutoTradeService Should Be:**
- Subscribes to ArticleClassified events (IMMINENT)
- Publishes TradeRequest events (to brokerage microservice)
- Subscribes to TradeExecuted events (to update positions/audit)

**Decision:**
- AutoTradeService is BUSINESS LOGIC (trading decisions)
- Should go in `services/brokerage/` folder (trading business logic)
- Remove direct infrastructure calls

---

### position_tracker.py

**Current State:**
- Reads/writes JSON file directly → **DATA PERSISTENCE**
- Tracks positions in memory
- Used by trading services

**Infrastructure Coupling:**
1. ✅ **JSON file operations** → Move to `infra/persistence`

**What PositionTracker Should Be:**
- Business logic for position tracking
- Subscribes to TradeExecuted/TradeClosed events
- Publishes PositionUpdated events

**Decision:**
- PositionTracker is BUSINESS LOGIC (position management)
- Should go in `services/brokerage/` folder
- Remove direct file access

---

### classification_audit_trail.py

**Current State:**
- Reads/writes JSON files directly → **DATA PERSISTENCE**
- Logs classification events

**Infrastructure Coupling:**
1. ✅ **JSON file operations** → Move to `infra/persistence`

**What ClassificationAuditTrail Should Be:**
- Repository interface for audit data
- Should use persistence microservice

**Decision:**
- Move to `infra/persistence/repository.py` or use repository pattern

---

### price_tracking_service.py

**Current State:**
- Uses `ibkr_service` directly → **BROKERAGE**
- Uses `audit_trail` → **DATA PERSISTENCE**

**Infrastructure Coupling:**
1. ✅ **ibkr_service** - Direct IBKR calls → Move to `infra/brokerage`
2. ✅ **audit_trail** - Direct JSON file access → Move to `infra/persistence`

**What PriceTrackingService Should Be:**
- Business logic for price tracking
- Subscribes to QuoteReceived events (from brokerage)
- Publishes PriceUpdate events
- Subscribes to TradeExecuted events (to track prices after trade)

**Decision:**
- PriceTrackingService is BUSINESS LOGIC (price tracking)
- Should go in `services/brokerage/` folder
- Remove direct infrastructure calls

---

### ibkr_trading_service.py

**Current State:**
- Direct IBKR Gateway connection → **BROKERAGE INFRASTRUCTURE**
- Trade execution → **BROKERAGE INFRASTRUCTURE**
- Quote fetching → **BROKERAGE INFRASTRUCTURE**
- Connection management → **BROKERAGE INFRASTRUCTURE**

**Infrastructure Coupling:**
1. ✅ **All of it** → Move to `infra/brokerage/`

**Decision:**
- Pure infrastructure → Move to `infra/brokerage/service.py`

---

### ibkr_keepalive_service.py

**Current State:**
- Direct IBKR connection management → **BROKERAGE INFRASTRUCTURE**

**Infrastructure Coupling:**
1. ✅ **All of it** → Move to `infra/brokerage/` (merge into brokerage service)

**Decision:**
- Pure infrastructure → Merge into `infra/brokerage/service.py`

---

### telegram_trade_handler.py

**Current State:**
- Uses `trading_service` directly → **BROKERAGE**

**Infrastructure Coupling:**
1. ✅ **trading_service** - Direct IBKR calls → Should publish TradeRequest events

**Decision:**
- Business logic (Telegram command handling)
- Should publish TradeRequest events instead of calling trading_service
- Stay in `services/telegram/` or `services/brokerage/`

---

## Summary: What Goes Where

### infra/websocket/ ✅ (Already Done)
- service.py - Connection management
- health_monitor.py - Health checking
- events.py - Event definitions

### services/websocket/ ✅ (Already Done)
- feed_manager.py - Subscribes to ArticleReceived
- feed_health_monitor.py - Subscribes to health events

### infra/brokerage/ (Chapter 3.2)
- service.py - IBKR connection + trade execution (from ibkr_trading_service.py)
- keepalive.py - Keepalive logic (from ibkr_keepalive_service.py, merge into service)
- events.py - TradeExecuted, TradeFailed, QuoteReceived, etc.
- protocol.py - Trade command protocol

### services/brokerage/ (Chapter 3.2)
- auto_trade_service.py - Trading decisions (publishes TradeRequest events)
- position_tracker.py - Position management (subscribes to TradeExecuted events)
- price_tracking_service.py - Price tracking (subscribes to QuoteReceived events)
- telegram_trade_handler.py - Trade command handling (publishes TradeRequest events)

### infra/persistence/ (Chapter 3.3)
- repository.py - ArticleRepository, AuditRepository, PositionRepository
- json_store.py - JSON implementation (from ArticleStorage, ClassificationAuditTrail, PositionTracker)
- unit_of_work.py - Transaction management
- events.py - DataPersisted, AuditLogged, etc.

### services/ (Pure Business Logic)
- article_processor.py - Article processing orchestration (publishes/subscribes to events)
- news_classifier.py - AI classification (business logic)
- telegram_service.py - Notifications (business logic)
- translation_service.py - Translation (business logic)
- yfinance_service.py - Market data fetching (could be infrastructure or utility)

---

## Next Steps

1. **For Chapter 3.1 completion**: Ensure article_processor doesn't have WebSocket coupling (already clean!)
2. **For Chapter 3.2**: Extract brokerage infrastructure
3. **For Chapter 3.3**: Extract data persistence infrastructure

The key insight: **ArticleProcessor should publish events, not call infrastructure directly!**

