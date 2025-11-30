# Chapter 3.2: Brokerage Microservice - Simplified Breakdown

## Simplifications

1. ✅ **Remove options support completely**
2. ✅ **Remove yfinance completely** (no market cap, sector, industry filtering)
3. ✅ **Stock trading only** - simple and clean
4. ✅ **Decouple from Telegram** - Telegram is separate microservice
5. ✅ **Decouple from LLM/Classification** - Groq is separate microservice
6. ✅ **Clear session separation**: Market Hours | Extended Hours | Closed (queue)

---

## Architecture: Stock Trading Only

### Session Types (Simple Protocol)

1. **Market Hours** (9:30 AM - 4:00 PM ET)
   - Simple market orders
   - Fast execution
   - 2x leverage support

2. **Extended Hours** (4:00 AM - 9:30 AM, 4:00 PM - 8:00 PM ET)
   - Limit order ladder strategy
   - 2x leverage support

3. **Closed** (8:00 PM - 4:00 AM, weekends)
   - Queue trade for next premarket
   - Separate queue management service

---

## File Structure

### Infrastructure Layer (`infra/brokerage/`)

```
infra/brokerage/
├── __init__.py
├── events.py ✅ (already done)
├── protocol.py ✅ (already done)
├── service.py (main orchestrator)
├── connection_manager.py (~250 lines)
├── quote_fetcher.py (~200 lines)
├── session_detector.py (~100 lines)
├── trade_executor_market_hours.py (~150 lines) - STOCK ONLY
├── trade_executor_extended_hours.py (~250 lines) - STOCK ONLY, LADDER
└── queue_manager.py (~150 lines) - NEW: handles closed market queuing
```

**Total Infrastructure**: ~1100 lines across 9 files

### Business Logic Layer (`services/brokerage/`)

```
services/brokerage/
├── __init__.py
├── trade_request_builder.py (~150 lines) - Builds TradeRequest from articles
└── market_session_handler.py (~100 lines) - Routes based on session
```

**Total Business Logic**: ~250 lines across 2 files

### Use Case Layer (`use_cases/`)

```
use_cases/
├── __init__.py
├── auto_trade_use_case.py (~150 lines)
│   - Subscribes to: ArticleClassifiedEvent (IMMINENT)
│   - Orchestrates: trade_request_builder → market_session_handler
│   - Publishes: TradeRequestEvent (if market/open) OR queues (if closed)
└── trade_queue_use_case.py (~100 lines) - NEW
    - Manages queue for closed market
    - Subscribes to queue requests
    - Publishes TradeRequestEvent when market opens
```

**Total Use Cases**: ~250 lines across 2 files

### Utilities (`utils/brokerage/`)

```
utils/brokerage/
├── ladder_algorithms.py (~150 lines) - Price ladder building (extended hours only)
└── nbbo_formatters.py (~100 lines) - NBBO formatting utilities
```

**Total Utilities**: ~250 lines across 2 files

---

## Detailed Breakdown

### infra/brokerage/connection_manager.py (~250 lines)

**Responsibilities:**
- IBKR connection lifecycle
- Keepalive logic (merged from ibkr_keepalive_service)
- Reconnection handling
- Connection health monitoring
- Daily restart handling

**Publishes:**
- `ConnectionStatusChangedEvent`
- `BrokerageHealthStatusEvent`

**No dependencies on:**
- Telegram
- Classification
- Position tracking

---

### infra/brokerage/quote_fetcher.py (~200 lines)

**Responsibilities:**
- Market data requests (stocks only)
- NBBO fetching
- Quote snapshot management

**Publishes:**
- `QuoteReceivedEvent`

**No dependencies on:**
- Options
- YFinance
- Telegram

---

### infra/brokerage/session_detector.py (~100 lines)

**Responsibilities:**
- Market session detection
- Returns: `"market_hours" | "extended_hours" | "closed"`
- Time zone handling (ET)
- Pure utility functions

**No events** - pure utility, used by executors and handlers

---

### infra/brokerage/trade_executor_market_hours.py (~150 lines)

**Responsibilities:**
- Stock execution during market hours (9:30 AM - 4:00 PM ET)
- Simple market orders
- Fast fill monitoring
- 2x leverage support

**Publishes:**
- `TradeExecutedEvent` (success)
- `TradeFailedEvent` (failure)

**No dependencies on:**
- Options
- Extended hours logic
- Telegram
- YFinance

---

### infra/brokerage/trade_executor_extended_hours.py (~250 lines)

**Responsibilities:**
- Stock execution during extended hours (premarket/postmarket)
- Limit order ladder strategy
- Leverage support
- Uses `ladder_algorithms.py` utility

**Publishes:**
- `TradeExecutedEvent` (success)
- `TradeFailedEvent` (failure)

**No dependencies on:**
- Options
- Market hours logic
- Telegram
- YFinance

---

### infra/brokerage/queue_manager.py (~150 lines) - NEW

**Responsibilities:**
- Stores queued trades (when market is closed)
- Publishes TradeRequestEvent when next premarket opens
- Simple in-memory queue (can move to persistence layer later)

**Publishes:**
- `TradeRequestEvent` (when market opens)

**Subscribes to:**
- Queue requests from use cases

**No dependencies on:**
- Telegram
- YFinance
- Complex persistence (yet)

---

### infra/brokerage/service.py (~150 lines)

**Responsibilities:**
- Main brokerage microservice orchestrator
- Manages lifecycle of all infrastructure components
- Subscribes to: `TradeRequestEvent`
- Routes to appropriate executor based on session
- No business logic - pure routing

**Publishes:**
- Infrastructure events (via components)

**Subscribes to:**
- `TradeRequestEvent`

---

### services/brokerage/trade_request_builder.py (~150 lines)

**Responsibilities:**
- Builds TradeRequest from StandardizedArticle
- Session-aware amount calculations
- Leverage calculations for extended hours
- Pure functions with typed I/O

**Input**: `(article: StandardizedArticle, session: str) -> TradeRequest`
**Output**: `TradeRequest`

**No dependencies on:**
- IBKR infrastructure
- Telegram
- YFinance
- Options

---

### services/brokerage/market_session_handler.py (~100 lines)

**Responsibilities:**
- Routes trade requests based on session
- Market hours → publish TradeRequestEvent immediately
- Extended hours → publish TradeRequestEvent immediately  
- Closed → publish QueueTradeEvent (for queue_manager)

**Pure routing logic**

**Publishes:**
- `TradeRequestEvent` (market/extended)
- `QueueTradeEvent` (closed)

---

### use_cases/auto_trade_use_case.py (~150 lines)

**Responsibilities:**
- Orchestrates auto-trade flow
- Subscribes to: `ArticleClassifiedEvent` (IMMINENT only)
- Calls: trade_request_builder → market_session_handler
- Single responsibility: Auto-trade orchestration

**Subscribes to:**
- `ArticleClassifiedEvent`

**Publishes:**
- Events via market_session_handler

**No dependencies on:**
- Direct IBKR calls
- Telegram
- YFinance

---

### use_cases/trade_queue_use_case.py (~100 lines) - NEW

**Responsibilities:**
- Manages trade queue for closed market
- Subscribes to: `QueueTradeEvent`
- Stores queued trades
- Publishes `TradeRequestEvent` when premarket opens

**Subscribes to:**
- `QueueTradeEvent`
- `MarketSessionChangedEvent` (when market opens)

**Publishes:**
- `TradeRequestEvent` (when market opens)

---

### utils/brokerage/ladder_algorithms.py (~150 lines)

**Responsibilities:**
- Price ladder building algorithms
- Used by extended_hours executor
- Pure functions

**No dependencies on infrastructure**

---

### utils/brokerage/nbbo_formatters.py (~100 lines)

**Responsibilities:**
- NBBO formatting utilities
- Shared formatting logic
- Pure functions

---

## Event Flow

### Auto-Trade Flow (Market/Extended Hours)

```
ArticleClassifiedEvent (IMMINENT)
  ↓
auto_trade_use_case
  ↓
trade_request_builder (builds TradeRequest)
  ↓
market_session_handler
  ↓ (if market/extended)
TradeRequestEvent
  ↓
brokerage_service (routes based on session)
  ↓
trade_executor_market_hours OR trade_executor_extended_hours
  ↓
TradeExecutedEvent / TradeFailedEvent
```

### Auto-Trade Flow (Closed Market)

```
ArticleClassifiedEvent (IMMINENT)
  ↓
auto_trade_use_case
  ↓
trade_request_builder
  ↓
market_session_handler
  ↓ (if closed)
QueueTradeEvent
  ↓
trade_queue_use_case (stores in queue)
  ↓ (when premarket opens)
TradeRequestEvent
  ↓
brokerage_service → trade_executor_extended_hours
  ↓
TradeExecutedEvent / TradeFailedEvent
```

---

## What's Removed

1. ❌ **Options support** - completely removed
2. ❌ **YFinance** - completely removed
3. ❌ **Price tracking service** - removed
4. ❌ **Position tracker** - removed temporarily
5. ❌ **Market cap filtering** - removed (no longer needed)
6. ❌ **Sector/industry metadata** - removed (no longer needed)
7. ❌ **Telegram coupling** - decoupled (Telegram is separate microservice)
8. ❌ **Classification coupling** - decoupled (Groq/LLM is separate microservice)

---

## Implementation Phases (Simplified)

### Phase 1: Extract Utilities & Session Detection
1. Extract `session_detector.py`
2. Extract `ladder_algorithms.py`
3. Extract `nbbo_formatters.py`

**Goal**: Remove repetitive code

### Phase 2: Extract Connection Management
1. Extract connection logic
2. Merge keepalive
3. Create `connection_manager.py`
4. Publish connection events

**Goal**: Isolate connection concerns

### Phase 3: Extract Quote Fetching
1. Extract quote/NBBO logic (stocks only)
2. Create `quote_fetcher.py`
3. Publish quote events

**Goal**: Separate market data concerns

### Phase 4: Split Trade Executors (Stock Only)
1. Extract market hours executor (stocks only)
2. Extract extended hours executor (stocks only, ladder)
3. Remove all option logic
4. Each publishes trade events

**Goal**: Separate execution strategies

### Phase 5: Create Queue Manager
1. Create `queue_manager.py`
2. Handle closed market queuing
3. Publish TradeRequestEvent when market opens

**Goal**: Handle closed market trades

### Phase 6: Create Brokerage Service Orchestrator
1. Create `infra/brokerage/service.py`
2. Orchestrate infrastructure components
3. Subscribe to TradeRequestEvent
4. Route to appropriate executor

**Goal**: Single entry point

### Phase 7: Extract Business Logic
1. Create `services/brokerage/` directory
2. Extract trade_request_builder (stocks only)
3. Extract market_session_handler

**Goal**: Pure business logic functions

### Phase 8: Create Use Case Layer
1. Create `use_cases/` directory
2. Create `auto_trade_use_case.py`
3. Create `trade_queue_use_case.py`
4. Move orchestration from auto_trade_service

**Goal**: Separate orchestration

### Phase 9: Decouple & Clean Up
1. Remove all Telegram dependencies from IBKR
2. Remove all YFinance dependencies
3. Remove all option code
4. Remove position tracking
5. Remove price tracking
6. Update all references
7. Delete old files

**Goal**: Clean separation

---

## Key Principles

1. ✅ **Stock trading only** - no options
2. ✅ **No YFinance** - removed completely
3. ✅ **Session-based routing** - clear protocol
4. ✅ **Event-driven** - infrastructure publishes, services subscribe
5. ✅ **No Telegram coupling** - separate microservice
6. ✅ **No Classification coupling** - separate microservice
7. ✅ **Small, focused files** - ~100-250 lines each
8. ✅ **Clear protocols** - Pydantic models throughout
9. ✅ **Pure utilities** - algorithms separated

---

## Total Code Estimate

**Before:**
- ibkr_trading_service.py: 2000 lines (everything)
- auto_trade_service.py: 1000 lines (orchestration + logic)
- **Total: ~3000 lines mixed together**

**After:**
- Infrastructure: ~1100 lines (9 focused files)
- Business Logic: ~250 lines (2 focused files)
- Use Cases: ~250 lines (2 focused files)
- Utilities: ~250 lines (2 focused files)
- **Total: ~1850 lines (well-organized, testable)**

**Reduction: ~40% less code, 1000% more organized!**

---

## What Each Microservice Does (Clear Separation)

### Brokerage Microservice (infra/brokerage/)
- **Only**: Stock trade execution via IBKR
- **Input**: TradeRequestEvent
- **Output**: TradeExecutedEvent / TradeFailedEvent
- **No**: Options, YFinance, Telegram, Classification

### Classification Microservice (future: infra/classification/)
- **Only**: LLM-based news classification (Groq)
- **Input**: ArticleReceivedEvent
- **Output**: ArticleClassifiedEvent
- **No**: Trading, Telegram, IBKR

### Telegram Microservice (future: infra/telegram/)
- **Only**: Telegram notifications
- **Input**: NotificationRequestEvent
- **Output**: (external - Telegram API)
- **No**: Trading, Classification, IBKR

### Data Persistence Microservice (future: infra/persistence/)
- **Only**: Data storage/retrieval
- **Input**: Data events
- **Output**: Data persisted events
- **No**: Trading, Classification, Telegram

**Clear separation = clear understanding!**

