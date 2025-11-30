# Chapter 3.2: Brokerage Microservice - Breakdown Plan

## Current Problems

1. **ibkr_trading_service.py** - 2000 lines
   - Connection management
   - Keepalive
   - Trade execution (stocks & options)
   - Quote fetching
   - Option contract building
   - Ladder strategies
   - Pending trade handling
   - All mixed together

2. **auto_trade_service.py** - 1000 lines
   - Why separate? Should be part of brokerage business logic
   - Trading decisions
   - TradeRequest building
   - Calls trading_service directly

3. **ibkr_keepalive_service.py** - 271 lines
   - Separate service for one concern
   - Should be part of connection management

## Target Architecture

### infra/brokerage/ (Infrastructure - Stateless, Event Publishing)

**connection_manager.py** (~200 lines)
- IBKR connection lifecycle
- Keepalive logic (merged from ibkr_keepalive_service)
- Reconnection logic
- Connection health monitoring
- Publishes: ConnectionStatusChanged events

**trade_executor.py** (~400 lines)
- Pure trade execution
- Stock execution
- Option execution
- Order placement
- Fill monitoring
- Publishes: TradeExecuted, TradeFailed events

**quote_fetcher.py** (~300 lines)
- Market data requests
- NBBO fetching
- Option chain fetching
- Quote snapshots
- Publishes: QuoteReceived events

**option_contract_builder.py** (~200 lines)
- Option contract construction
- Expiry selection
- Strike selection
- Exchange selection
- Pure utility functions

**events.py** (~100 lines)
- TradeExecutedEvent
- TradeFailedEvent
- QuoteReceivedEvent
- ConnectionStatusChangedEvent
- BrokerageHealthStatusEvent

**protocol.py** (~50 lines)
- TradeCommand protocol
- QuoteRequest protocol

**service.py** (~150 lines)
- Main brokerage microservice
- Orchestrates: connection_manager, trade_executor, quote_fetcher
- Subscribes to TradeRequest events
- Publishes infrastructure events
- Lifecycle management

**Total infra/brokerage/**: ~1400 lines (down from 2000+271)

### services/brokerage/ (Business Logic - Event Subscribing)

**trade_request_builder.py** (~200 lines)
- Builds TradeRequest from articles
- Market cap checks
- Amount calculations
- Pure functions with typed I/O

**instrument_selector.py** (~150 lines)
- Options vs Stock decision logic
- Market cap thresholds
- Pure business logic functions

**auto_trade_handler.py** (~200 lines)
- Subscribes to ArticleClassified events
- Orchestrates: trade_request_builder → instrument_selector → publishes TradeRequest
- Trading decision logic

**trade_notifier.py** (~100 lines)
- Subscribes to TradeExecuted events
- Formats Telegram notifications
- Notification logic only

**Total services/brokerage/**: ~650 lines (down from 1000, but more focused)

### Remove (For Now)
- Position tracking (user request - simplify)
- Price tracking service (can add back later)

## Implementation Steps

### Step 1: Create Infrastructure Structure
1. Create `infra/brokerage/` directory
2. Define events in `events.py`
3. Define protocol in `protocol.py`
4. Create `__init__.py`

### Step 2: Extract Connection Management
1. Extract connection logic from `ibkr_trading_service.py`
2. Merge `ibkr_keepalive_service.py` into connection_manager
3. Create `infra/brokerage/connection_manager.py`
4. Publish ConnectionStatusChanged events

### Step 3: Extract Trade Execution
1. Extract pure trade execution logic
2. Remove orchestration code
3. Create `infra/brokerage/trade_executor.py`
4. Publish TradeExecuted/TradeFailed events

### Step 4: Extract Quote Fetching
1. Extract market data/quote logic
2. Create `infra/brokerage/quote_fetcher.py`
3. Publish QuoteReceived events

### Step 5: Extract Option Contract Building
1. Extract option contract logic
2. Create `infra/brokerage/option_contract_builder.py`
3. Pure utility functions

### Step 6: Create Main Brokerage Service
1. Create `infra/brokerage/service.py`
2. Orchestrate infrastructure components
3. Subscribe to TradeRequest events
4. Route to appropriate executor

### Step 7: Break Down Auto Trade Service
1. Create `services/brokerage/` directory
2. Extract trade_request_builder logic
3. Extract instrument_selector logic
4. Create auto_trade_handler (event-driven)
5. Remove position_tracker dependency

### Step 8: Update References
1. Update service_initialization.py
2. Update article_processor.py (publish TradeRequest instead of calling)
3. Update telegram_trade_handler.py
4. Delete old files

## Key Principles

1. **Small, Focused Files** - Each file < 400 lines
2. **Single Responsibility** - One concern per file
3. **Event-Driven** - Infrastructure publishes, services subscribe
4. **Typed I/O** - Clear function signatures
5. **No Orchestration in Infrastructure** - Pure execution only
6. **Business Logic Separate** - Services folder for decisions

## Expected Outcomes

- **Infrastructure**: Clean, testable, event-publishing
- **Services**: Small, focused business logic functions
- **Navigability**: Easy to find where things happen
- **Testability**: Can test infrastructure and business logic separately
- **Maintainability**: Changes isolated to specific files

