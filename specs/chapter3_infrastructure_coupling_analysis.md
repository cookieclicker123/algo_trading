# Chapter 3: Infrastructure Coupling Analysis

## Goal

Identify ALL infrastructure coupling in services and determine where code belongs:
- **infra/websocket/** - WebSocket connection/state management
- **services/websocket/** - WebSocket business logic (event handlers)
- **infra/brokerage/** - Brokerage connection/state management  
- **services/brokerage/** - Trading business logic
- **infra/persistence/** - Data storage operations
- **services/** - Pure business logic only

---

## Service-by-Service Analysis

### article_processor.py

**WebSocket-Related?** ❌ NO
- Does NOT create/manage WebSocket
- Receives articles via `process_article()` method (called by feed_manager)
- NO WebSocket state or dependencies

**Infrastructure Coupling:**
1. ✅ `ArticleStorage()` - Direct JSON file I/O → **DATA PERSISTENCE**
   - Should use persistence microservice repository
   - Should publish ArticleStored events

2. ✅ `ClassificationAuditTrail()` - Direct JSON file I/O → **DATA PERSISTENCE**
   - Should use persistence microservice repository
   - Should publish AuditLogged events

3. ✅ `auto_trade_service.process_imminent_article()` - Direct brokerage call → **BROKERAGE**
   - Should publish TradeRequest event instead
   - Brokerage microservice subscribes and executes

**Decision:**
- ArticleProcessor is BUSINESS LOGIC (orchestration)
- Should stay in `services/article_processor.py`
- But needs to publish events instead of calling infrastructure directly
- NO WebSocket code to move

**Changes Needed:**
- Replace `storage.store_articles()` → Publish ArticleStored event (persistence subscribes)
- Replace `audit_trail.log_imminent_classification()` → Publish AuditLogged event (persistence subscribes)
- Replace `auto_trade_service.process_imminent_article()` → Publish TradeRequest event (brokerage subscribes)

---

### auto_trade_service.py

**WebSocket-Related?** ❌ NO
- Pure trading logic

**Infrastructure Coupling:**
1. ✅ `trading_service` (IBKRTradingService) - Direct IBKR calls → **BROKERAGE**
   - Should publish TradeRequest events
   - Brokerage microservice executes trades

2. ✅ `position_tracker` - Direct JSON file access → **DATA PERSISTENCE**
   - Should use persistence repository
   - Should subscribe to TradeExecuted events to update positions

3. ✅ `audit_trail` - Direct JSON file access → **DATA PERSISTENCE**
   - Should publish AuditLogged events (persistence subscribes)

**Decision:**
- AutoTradeService is BUSINESS LOGIC (trading decisions)
- Should move to `services/brokerage/auto_trade.py`
- Remove all direct infrastructure calls

---

### position_tracker.py

**WebSocket-Related?** ❌ NO

**Infrastructure Coupling:**
1. ✅ Direct JSON file I/O (`open_positions.json`) → **DATA PERSISTENCE**
   - Should use persistence repository

**Decision:**
- PositionTracker is BUSINESS LOGIC (position management)
- Should move to `services/brokerage/position_tracker.py`
- Should subscribe to TradeExecuted/TradeClosed events
- Remove direct file access

---

### classification_audit_trail.py

**WebSocket-Related?** ❌ NO

**Infrastructure Coupling:**
1. ✅ Direct JSON file I/O → **DATA PERSISTENCE**
   - Should become a repository implementation in `infra/persistence/repository.py`

**Decision:**
- This IS infrastructure (data persistence)
- Should move to `infra/persistence/repository.py` (audit repository)
- Or use repository pattern interface

---

### price_tracking_service.py

**WebSocket-Related?** ❌ NO

**Infrastructure Coupling:**
1. ✅ `ibkr_service` - Direct IBKR calls → **BROKERAGE**
   - Should subscribe to QuoteReceived events from brokerage

2. ✅ `audit_trail` - Direct JSON file access → **DATA PERSISTENCE**
   - Should publish AuditLogged events

**Decision:**
- PriceTrackingService is BUSINESS LOGIC (price tracking)
- Should move to `services/brokerage/price_tracking.py`
- Remove direct infrastructure calls

---

### ibkr_trading_service.py

**WebSocket-Related?** ❌ NO

**Infrastructure Coupling:**
1. ✅ ALL OF IT - Direct IBKR Gateway connection → **BROKERAGE INFRASTRUCTURE**
   - Connection management
   - Trade execution
   - Quote fetching
   - All infrastructure

**Decision:**
- Pure infrastructure → Move to `infra/brokerage/service.py`

---

### ibkr_keepalive_service.py

**WebSocket-Related?** ❌ NO

**Infrastructure Coupling:**
1. ✅ ALL OF IT - IBKR connection keepalive → **BROKERAGE INFRASTRUCTURE**

**Decision:**
- Pure infrastructure → Merge into `infra/brokerage/service.py`

---

### telegram_trade_handler.py

**WebSocket-Related?** ❌ NO

**Infrastructure Coupling:**
1. ✅ `trading_service` - Direct IBKR calls → **BROKERAGE**
   - Should publish TradeRequest events

**Decision:**
- Business logic (Telegram command handling)
- Should publish TradeRequest events instead
- Could stay in `services/telegram/` or move to `services/brokerage/`

---

### ArticleStorage (utils/json_storage.py)

**WebSocket-Related?** ❌ NO

**Infrastructure Coupling:**
1. ✅ ALL OF IT - Direct JSON file I/O → **DATA PERSISTENCE INFRASTRUCTURE**

**Decision:**
- Pure infrastructure → Move to `infra/persistence/json_store.py`
- Should become ArticleRepository implementation

---

## Key Findings

### ❌ NO WebSocket Code in Remaining Services

ArticleProcessor and other services have:
- ✅ NO WebSocket connection code
- ✅ NO WebSocket state access
- ✅ Already clean of WebSocket infrastructure

**But they DO have:**
- ❌ Data persistence coupling (JSON file I/O)
- ❌ Brokerage coupling (direct IBKR calls)

### What Needs Moving

**To infra/persistence/ (Chapter 3.3):**
- ArticleStorage → infra/persistence/json_store.py (ArticleRepository)
- ClassificationAuditTrail → infra/persistence/repository.py (AuditRepository)
- PositionTracker JSON I/O → infra/persistence/repository.py (PositionRepository)

**To infra/brokerage/ (Chapter 3.2):**
- IBKRTradingService → infra/brokerage/service.py
- IBKRKeepAliveService → Merge into infra/brokerage/service.py

**To services/brokerage/ (Chapter 3.2):**
- AutoTradeService → services/brokerage/auto_trade.py
- PositionTracker → services/brokerage/position_tracker.py
- PriceTrackingService → services/brokerage/price_tracking.py
- TelegramTradeHandler → services/brokerage/telegram_trade_handler.py (or stay in telegram/)

---

## ArticleProcessor Assessment

**Current:** Orchestrates storage → classification → audit → trading

**Should Be:** Event-driven orchestrator
- Subscribes to: ArticleReceived (already done via feed_manager)
- Publishes: ArticleStored, ArticleClassified, TradeRequest
- NO direct infrastructure calls

**WebSocket-Related Code?** 
- ❌ NONE - ArticleProcessor is clean of WebSocket code
- All WebSocket code already moved to infra/websocket and services/websocket

**What ArticleProcessor Should Do:**
1. Receive article from feed_manager (already done)
2. Store article → Publish ArticleStored event (persistence subscribes)
3. Classify article → Publish ArticleClassified event
4. Log audit → Publish AuditLogged event (persistence subscribes)
5. Request trade → Publish TradeRequest event (brokerage subscribes)

**Conclusion:** ArticleProcessor has NO WebSocket code, but it DOES couple data persistence and brokerage. This will be fixed in Chapters 3.2 and 3.3.

