# Chapter 3.2: Brokerage Microservice - Detailed Breakdown Plan

## Overview

Break down 2000+ lines of tightly-coupled IBKR trading code into:
- Focused infrastructure microservice
- Separated business logic services
- Use case layer for orchestration
- Small, testable, navigable units

---

## Analysis: What We Have vs. What We Need

### Current State Analysis

**ibkr_trading_service.py (2000 lines) - Mixed Concerns:**
- Connection management + keepalive
- Market session detection
- Quote fetching/NBBO building
- Stock trade execution (market hours)
- Stock trade execution (extended hours - ladder strategy)
- Option contract building
- Option trade execution
- Telegram notifications
- Position tracking

**auto_trade_service.py (1000 lines) - Orchestration:**
- Trade request building
- Instrument selection (options vs stocks)
- Market cap checks (via yfinance)
- Session-aware trade routing
- Trade execution orchestration
- Position tracking
- Telegram notifications
- Exit strategy

**yfinance_service.py (329 lines) - External API:**
- Market cap lookups (for option threshold)
- Sector/industry metadata (for audit trail)
- **Question**: Can IBKR provide this? 
  - IBKR contract details exist but fundamental data requires different API
  - For now: Keep as simple utility (not microservice)
  - Future: Could move to IBKR fundamentals API if needed

**price_tracking_service.py (198 lines) - REMOVE:**
- User requested removal to simplify

**position_tracker.py (289 lines) - REMOVE (temporarily):**
- User requested removal to simplify

---

## Architecture: Logical Units of Work

### 1. Infrastructure Layer (`infra/brokerage/`)

**connection_manager.py** (~250 lines)
- IBKR connection lifecycle
- Keepalive logic (merged from ibkr_keepalive_service)
- Reconnection handling
- Connection health monitoring
- Daily restart handling
- Publishes: `ConnectionStatusChangedEvent`, `BrokerageHealthStatusEvent`

**quote_fetcher.py** (~300 lines)
- Market data requests
- NBBO fetching (stocks)
- Option chain fetching
- Quote snapshot management
- NBBO formatting utilities
- Publishes: `QuoteReceivedEvent`

**session_detector.py** (~100 lines) - NEW, EXTRACT ALGORITHM
- Market session detection (premarket, market_hours, postmarket, closed)
- Time zone handling
- Session boundary calculations
- Pure utility functions (no infrastructure)
- Used by trade executors

**option_contract_builder.py** (~250 lines)
- Option contract construction
- Expiry selection logic
- Strike selection (ATM)
- Exchange selection (denylist handling)
- Contract qualification
- Pure utility functions

**trade_executor_market_hours.py** (~200 lines)
- Pure stock execution (market orders)
- Simple fill monitoring
- Market hours specific logic
- Publishes: `TradeExecutedEvent`, `TradeFailedEvent`

**trade_executor_extended_hours.py** (~350 lines)
- Extended hours execution (premarket/postmarket)
- Limit order ladder strategy
- Price ladder building
- Leverage handling
- Extended hours specific logic
- Publishes: `TradeExecutedEvent`, `TradeFailedEvent`

**trade_executor_options.py** (~300 lines)
- Option execution (market hours only)
- Option ladder strategy
- Option-specific NBBO handling
- Publishes: `TradeExecutedEvent`, `TradeFailedEvent`

**events.py** ✅ DONE
- All event definitions

**protocol.py** ✅ DONE  
- All protocol definitions

**service.py** (~200 lines)
- Main brokerage microservice orchestrator
- Subscribes to: `TradeRequestEvent`
- Routes to appropriate executor based on instrument + session
- Manages infrastructure components lifecycle

**Total Infrastructure**: ~2000 lines → ~1850 lines (but MUCH more organized)

---

### 2. Business Logic Layer (`services/brokerage/`)

**instrument_selector.py** (~150 lines)
- Options vs Stock decision logic
- Market cap threshold checking (via yfinance utility)
- Pure business logic functions
- Typed I/O: `(article, session) -> TradeInstrument`

**trade_request_builder.py** (~200 lines)
- Builds TradeRequest from articles
- Session-aware amount calculations
- Leverage calculations for extended hours
- Pure functions with typed I/O

**market_session_aware.py** (~100 lines) - NEW
- Session-aware business logic utilities
- Next premarket calculation
- Market closed handling
- Pure utility functions

**Total Business Logic Services**: ~450 lines (down from 1000, more focused)

---

### 3. Use Case Layer (`use_cases/`) - NEW

**auto_trade_use_case.py** (~200 lines)
- Orchestrates: instrument_selector → trade_request_builder → publishes TradeRequest
- Handles market closed delays
- Subscribes to: `ArticleClassifiedEvent` (IMMINENT)
- Publishes: `TradeRequestEvent`
- Single responsibility: Auto-trade orchestration

**trade_notification_use_case.py** (~150 lines)
- Subscribes to: `TradeExecutedEvent`, `TradeFailedEvent`
- Formats notifications
- Publishes: `NotificationRequestEvent` (for telegram microservice)
- Single responsibility: Trade notifications

**Total Use Cases**: ~350 lines (orchestration moved here)

---

### 4. Utilities (`utils/brokerage/` or stay in existing utils)

**yfinance_utils.py** (~100 lines)
- Simple utility functions for market cap/sector/industry
- NOT a microservice - just helper functions
- Can be replaced with IBKR fundamentals API later if needed

**ladder_algorithms.py** (~150 lines) - NEW, EXTRACT REPETITIVE CODE
- Price ladder building algorithms
- Common ladder logic shared by extended hours + options
- Pure functions

**nbbo_formatters.py** (~100 lines) - NEW, EXTRACT REPETITIVE CODE
- NBBO formatting utilities
- Shared formatting logic
- Pure functions

**Total Utilities**: ~350 lines

---

## File Structure

```
infra/brokerage/
├── __init__.py
├── events.py ✅
├── protocol.py ✅
├── service.py (orchestrator)
├── connection_manager.py
├── quote_fetcher.py
├── session_detector.py
├── option_contract_builder.py
├── trade_executor_market_hours.py
├── trade_executor_extended_hours.py
└── trade_executor_options.py

services/brokerage/
├── __init__.py
├── instrument_selector.py
├── trade_request_builder.py
└── market_session_aware.py

use_cases/  (NEW)
├── __init__.py
├── auto_trade_use_case.py
└── trade_notification_use_case.py

utils/brokerage/  (or existing utils/)
├── yfinance_utils.py (simplified)
├── ladder_algorithms.py
└── nbbo_formatters.py
```

---

## Key Decisions

### ✅ Remove (Simplify)
1. **price_tracking_service.py** - Not needed yet
2. **position_tracker.py** - Remove temporarily (can add back later)
3. **ibkr_keepalive_service.py** - Merge into connection_manager

### ✅ Split by Session Type
- **Market Hours**: Simple market orders
- **Extended Hours**: Complex ladder strategy
- **Options**: Separate executor (only market hours)

### ✅ Extract Repetitive Algorithms
- Session detection (used everywhere) → `session_detector.py`
- Price ladder building (extended hours + options) → `ladder_algorithms.py`
- NBBO formatting (used everywhere) → `nbbo_formatters.py`
- Option contract building (complex logic) → `option_contract_builder.py`

### ✅ Separate Concerns
- **Infrastructure**: Connection, quotes, execution (publishes events)
- **Business Logic**: Instrument selection, trade request building (pure functions)
- **Use Cases**: Orchestration (subscribes/publishes events)
- **Utilities**: Helper algorithms (pure functions)

### ✅ YFinance Decision
- Keep as simple utility (NOT a microservice)
- Only used for: market cap checks, metadata gathering
- Can be replaced with IBKR fundamentals API later if needed
- Move to `utils/brokerage/yfinance_utils.py`

### ✅ Telegram
- Recognized as separate microservice
- Will be addressed after brokerage/data/websocket
- For now: Services publish `NotificationRequestEvent`
- Telegram microservice subscribes later

---

## Implementation Phases

### Phase 1: Extract Infrastructure Algorithms (Small, Testable)
1. Extract `session_detector.py` (pure utility)
2. Extract `ladder_algorithms.py` (pure utility)
3. Extract `nbbo_formatters.py` (pure utility)
4. Extract `option_contract_builder.py` (pure utility)

**Goal**: Remove repetitive code, make algorithms reusable

### Phase 2: Extract Connection Management
1. Extract connection logic from `ibkr_trading_service.py`
2. Merge keepalive from `ibkr_keepalive_service.py`
3. Create `connection_manager.py`
4. Publish connection events

**Goal**: Isolate connection concerns

### Phase 3: Extract Quote Fetching
1. Extract quote/NBBO logic
2. Create `quote_fetcher.py`
3. Publish quote events

**Goal**: Separate market data concerns

### Phase 4: Split Trade Executors by Session
1. Extract market hours executor
2. Extract extended hours executor
3. Extract options executor
4. Each publishes trade events

**Goal**: Separate execution strategies

### Phase 5: Create Brokerage Service Orchestrator
1. Create `infra/brokerage/service.py`
2. Orchestrate infrastructure components
3. Subscribe to `TradeRequestEvent`
4. Route to appropriate executor

**Goal**: Single entry point for infrastructure

### Phase 6: Extract Business Logic
1. Create `services/brokerage/` directory
2. Extract instrument_selector
3. Extract trade_request_builder
4. Extract market_session_aware utilities

**Goal**: Pure business logic functions

### Phase 7: Create Use Case Layer
1. Create `use_cases/` directory
2. Create `auto_trade_use_case.py`
3. Create `trade_notification_use_case.py`
4. Move orchestration from auto_trade_service

**Goal**: Separate orchestration from business logic

### Phase 8: Clean Up & Integration
1. Update all references
2. Remove old files
3. Update service_initialization.py
4. Test integration

**Goal**: System works with new structure

---

## Expected Outcomes

### Before:
- `ibkr_trading_service.py`: 2000 lines (everything mixed)
- `auto_trade_service.py`: 1000 lines (orchestration + logic)
- Hard to test, hard to navigate, hard to debug

### After:
- **Infrastructure**: ~1850 lines across 9 focused files (~200 lines each)
- **Business Logic**: ~450 lines across 3 focused files
- **Use Cases**: ~350 lines across 2 focused files
- **Utilities**: ~350 lines across 3 focused files

### Benefits:
1. ✅ **Navigable**: Know exactly where to find things
2. ✅ **Testable**: Can test each component in isolation
3. ✅ **Maintainable**: Changes isolated to specific files
4. ✅ **Readable**: Small files with single responsibilities
5. ✅ **Event-Driven**: Infrastructure publishes, services subscribe
6. ✅ **Prepared for Domain Layer**: Everything ready for pure logic abstraction

---

## Remaining Questions

1. **YFinance**: Keep as utility for now? ✅ YES
2. **Position Tracking**: Remove temporarily? ✅ YES  
3. **Price Tracking**: Remove? ✅ YES
4. **Telegram**: Handle after main 3? ✅ YES
5. **Use Cases**: Build now for orchestration? ✅ YES
6. **Domain Layer**: Prepare but don't build yet? ✅ YES

---

## Next Steps

1. Review this breakdown
2. Approve approach
3. Start Phase 1 (extract algorithms)
4. Continue phase by phase
5. Test after each phase

