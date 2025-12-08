# Alpaca Migration Plan: What Stays, What Goes, What Simplifies

## Executive Summary

**Goal**: Migrate from IBKR to Alpaca while keeping the same event-driven architecture, domain models, and business logic. Simplify infrastructure by removing threading complexity and IBKR-specific code.

**Key Simplifications**:
- ✅ Remove **1 thread** (IBKR connection thread) - join main event loop
- ✅ Remove **~900-1100 lines** of IBKR-specific code
- ✅ Keep **all domain models, events, protocols, and business logic**
- ✅ Keep **ladder strategy** (still useful for extended hours)
- ✅ Keep **market session detection** (still needed for routing)
- ✅ Keep **queue manager** (broker-agnostic)

---

## 1. Thread Inventory Across Codebase

### Current Threads (4 total)

#### ✅ **KEEP: WebSocket Threads** (3 threads)
**Location**: `src/newsflash/infra/websocket/service.py`

1. **`websocket_thread`** - Main WebSocket connection loop
   - **Why**: `websocket-client` library is blocking/synchronous
   - **Purpose**: Receives news feed from Benzinga
   - **Status**: ✅ Keep (not brokerage-related)

2. **`_ping_thread`** - WebSocket ping keepalive
   - **Why**: Blocking ping operations
   - **Purpose**: Maintains WebSocket connection
   - **Status**: ✅ Keep (not brokerage-related)

3. **`_monitor_thread`** - Connection monitor
   - **Why**: Blocking monitoring operations
   - **Purpose**: Monitors WebSocket health
   - **Status**: ✅ Keep (not brokerage-related)

#### ✅ **KEEP: Metrics Thread Lock** (1 lock, not a thread)
**Location**: `src/newsflash/services/metrics/metrics_service.py`

- **`threading.Lock()`** - Thread-safe counter synchronization
- **Why**: Thread-safe synchronization for metrics
- **Status**: ✅ Keep (could use `asyncio.Lock` but threading.Lock is fine)

#### ❌ **REMOVE: IBKR Connection Thread** (1 thread)
**Location**: `src/newsflash/infra/brokerage/connection_manager.py`

- **`_ib_thread`** - Dedicated thread with separate event loop for IBKR
- **Why**: `ib_insync` library is blocking/synchronous
- **Purpose**: Manages IBKR Gateway connection
- **Status**: ❌ **REMOVE** - Alpaca uses REST API, can join main event loop

#### ✅ **KEEP: WebSocket Health Monitor Thread** (1 thread)
**Location**: `src/newsflash/infra/websocket/health_monitor.py`

- **`monitor_thread`** - Health monitoring for WebSocket
- **Why**: Background monitoring task
- **Status**: ✅ Keep (not brokerage-related)

---

## 2. What STAYS (Broker-Agnostic / General)

### ✅ **Domain Models & Business Logic** (100% Keep)
- **Location**: `src/newsflash/domain/brokerage/`
- **Files**: `models.py`, `mappers.py`
- **Why**: Domain models are broker-agnostic
- **Examples**:
  - `TradeRequest`
  - `TradeStatus`
  - `MarketSession` enum
  - `TradeResult`
  - Domain mappers

### ✅ **Event Architecture** (100% Keep)
- **Location**: `src/newsflash/infra/brokerage/events.py`, `event_builders.py`
- **Why**: Events are broker-agnostic, perfect design
- **Events**:
  - `TradeExecutedEvent`
  - `TradeFailedEvent`
  - `QuoteReceivedEvent`
  - `ConnectionStatusChangedEvent`
  - `BrokerageHealthStatusEvent`
  - `TradeRequestQueuedEvent`

### ✅ **Protocols/Interfaces** (100% Keep)
- **Location**: `src/newsflash/infra/brokerage/protocol.py`
- **Why**: Define contracts for different broker implementations
- **Protocols**:
  - `BrokerageServiceProtocol`
  - `TradeExecutorProtocol`
  - `QuoteFetcherProtocol`

### ✅ **Queue Manager** (100% Keep)
- **Location**: `src/newsflash/infra/brokerage/queue_manager.py`
- **Why**: Broker-agnostic, handles closed-market queuing
- **Functionality**: Queues trades when market is closed, retrieves for premarket

### ✅ **Market Session Detection** (100% Keep)
- **Location**: `src/newsflash/utils/brokerage/session_detector.py`
- **Why**: Still needed to route trades to correct executor
- **Functionality**: Detects premarket/market/postmarket/closed
- **Note**: Alpaca supports extended hours, but we still need to know which session we're in for routing

### ✅ **Ladder Algorithms** (100% Keep)
- **Location**: `src/newsflash/utils/brokerage/ladder_algorithms.py`
- **Why**: Still useful for extended hours trading (low liquidity)
- **Functionality**: Calculates ladder prices, steps, intervals
- **Note**: Alpaca extended hours has lower liquidity, ladder strategy still valuable

### ✅ **Price Calculator** (100% Keep)
- **Location**: `src/newsflash/infra/brokerage/price_calculator.py`
- **Why**: Business logic for calculating trade prices/quantities
- **Functionality**: Calculates trade prices, quantities, handles 2x leverage

### ✅ **Event Bus** (100% Keep)
- **Location**: `src/newsflash/shared/event_bus.py`
- **Why**: Core infrastructure, broker-agnostic
- **Functionality**: Async event publishing/subscribing

---

## 3. What GOES (IBKR-Specific)

### ❌ **Connection Manager Threading** (~400-500 lines)
**Location**: `src/newsflash/infra/brokerage/connection_manager.py`

**Remove**:
- `_ib_thread` - Dedicated thread
- `_ib_event_loop` - Separate event loop
- `_connection_lock` - Thread-safe coordination
- `_run_ib_connection_thread()` - Thread setup function
- `call_soon_threadsafe()` patterns - Thread-safe event publishing

**Why**: Alpaca uses REST API, no blocking operations, can use standard async/await

**Replacement**: Simple async HTTP client (50-100 lines):
```python
class AlpacaConnectionManager:
    def __init__(self, event_bus, api_key, api_secret, paper_trading):
        self.client = httpx.AsyncClient(
            base_url="https://paper-api.alpaca.markets" if paper_trading else "https://api.alpaca.markets",
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
        )
        self.event_bus = event_bus
        self.is_connected = True  # HTTP is always "connected"
    
    async def start(self):
        # Verify connection with simple API call
        try:
            await self.client.get("/v2/account")
            await self._publish_connection_status(True, "Connected")
        except Exception as e:
            await self._publish_connection_status(False, str(e))
    
    async def stop(self):
        await self.client.aclose()
        await self._publish_connection_status(False, "Stopped")
```

### ❌ **Connection Keepalive & Verification** (~150-200 lines)
**Location**: `src/newsflash/infra/brokerage/connection_manager.py`

**Remove**:
- `_keepalive_loop()` - Periodic `accountValues()` pings
- `_verify_connection()` - Periodic connection checks
- Task management for keepalive/verification

**Why**: HTTP connections don't need keepalive pings. Connection health verified on each request.

**Replacement**: None needed - HTTP client handles connection pooling automatically.

### ❌ **Daily Restart Window Logic** (~100 lines)
**Location**: `src/newsflash/infra/brokerage/connection_helpers.py` (entire file), `connection_manager.py`

**Remove**:
- `connection_helpers.py` - Entire file (73 lines)
- `next_connect_time` - Daily restart delay tracking
- `_handle_daily_restart_window()` - Delay logic
- Daily restart delay in reconnection logic

**Why**: Alpaca doesn't have daily restarts. API is always available.

**Replacement**: None needed.

### ❌ **Contract Qualification** (~150-200 lines)
**Location**: `src/newsflash/infra/brokerage/quote_fetcher.py`, `service.py`, trade executors

**Remove**:
- `Stock()` contract creation
- `ib.qualifyContractsAsync()` calls
- Contract qualification logic

**Why**: Alpaca uses simple symbols (e.g., "AAPL"), not contracts.

**Replacement**: Just use symbol strings directly:
```python
# Instead of:
contract = Stock(symbol, "SMART", "USD")
qualified = await ib.qualifyContractsAsync(contract)

# Just use:
symbol = "AAPL"  # That's it!
```

### ❌ **Market Data Subscriptions** (~100-150 lines)
**Location**: `src/newsflash/infra/brokerage/quote_fetcher.py`

**Remove**:
- `reqMktData()` subscription and polling loops
- `cancelMktData()` calls
- `reqMarketDataType()` calls
- Subscription management

**Why**: Alpaca REST API is request/response - no subscriptions needed.

**Replacement**: Simple REST API calls:
```python
async def get_realtime_price(self, symbol: str) -> Optional[float]:
    response = await self.client.get(f"/v2/stocks/{symbol}/quotes/latest")
    data = response.json()
    return data["quote"]["bp"]  # bid price, or use "ap" for ask
```

### ❌ **Reconnection Logic** (~100-150 lines)
**Location**: `src/newsflash/infra/brokerage/connection_manager.py`

**Remove**:
- `_on_disconnect()` callback handler
- `_reconnect_after_disconnect()` with backoff
- `reconnect_attempts` tracking
- `_on_ib_error()` error handler (IBKR-specific error codes)

**Why**: HTTP clients handle reconnection automatically with retries.

**Replacement**: Use HTTP client retry logic:
```python
client = httpx.AsyncClient(
    timeout=30.0,
    limits=httpx.Limits(max_keepalive_connections=5),
    # Built-in retry on connection errors
)
```

### ❌ **Client ID & Port Management** (~50 lines)
**Location**: `src/newsflash/infra/brokerage/connection_manager.py`, `service.py`

**Remove**:
- `client_id` parameter throughout
- Port-based connection logic
- Paper vs live port selection

**Why**: Alpaca uses API keys in headers, not client IDs or ports.

**Replacement**: Use API keys from environment:
```python
base_url = "https://paper-api.alpaca.markets" if paper_trading else "https://api.alpaca.markets"
headers = {
    "APCA-API-KEY-ID": os.getenv("ALPACA_KEY"),
    "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET")
}
```

---

## 4. What SIMPLIFIES (Can Be Simplified)

### 🔄 **Quote Fetcher** (Simplify from ~334 lines to ~100-150 lines)
**Location**: `src/newsflash/infra/brokerage/quote_fetcher.py`

**Current**: Complex subscription-based quote fetching with contract qualification

**Simplified**: Direct REST API calls:
```python
class AlpacaQuoteFetcher:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
    
    async def get_realtime_price(self, symbol: str) -> Optional[float]:
        """Get realtime price - simplified REST API call."""
        response = await self.client.get(f"/v2/stocks/{symbol}/quotes/latest")
        data = response.json()
        quote = data["quote"]
        return quote.get("bp") or quote.get("ap")  # bid or ask
    
    async def get_nbbo_snapshot(self, symbol: str) -> Optional[Dict]:
        """Get NBBO snapshot - simplified REST API call."""
        response = await self.client.get(f"/v2/stocks/{symbol}/quotes/latest")
        data = response.json()
        quote = data["quote"]
        return {
            "bid": quote.get("bp"),
            "ask": quote.get("ap"),
            "spread": quote.get("ap", 0) - quote.get("bp", 0),
            "mid": (quote.get("bp", 0) + quote.get("ap", 0)) / 2,
        }
```

**Removes**: Contract qualification, subscriptions, polling loops

### 🔄 **Trade Executors** (Simplify significantly)
**Location**: `src/newsflash/infra/brokerage/trade_executor_*.py`

**Current**: Complex IBKR-specific order placement with `Trade` objects and status polling

**Simplified**: REST API order placement:
```python
# Market hours executor
async def execute(self, trade_request: TradeRequest, ...):
    order_data = MarketOrderRequest(
        symbol=trade_request.ticker,
        qty=quantity,
        side=OrderSide.BUY if trade_request.action == "BUY" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY
    )
    order = await self.client.submit_order(order_data)
    # Order status immediately available, no polling needed
    return order

# Extended hours executor (still uses ladder, but simpler)
async def execute(self, trade_request: TradeRequest, ...):
    # Still use ladder strategy for extended hours
    for attempt in ladder_attempts:
        limit_price = calculate_limit_price(base_price, cents_offset)
        order_data = LimitOrderRequest(
            symbol=trade_request.ticker,
            qty=quantity,
            side=OrderSide.BUY,
            limit_price=limit_price,
            time_in_force=TimeInForce.DAY,
            extended_hours=True  # ✅ Alpaca extended hours flag
        )
        order = await self.client.submit_order(order_data)
        # Check order status via REST API (no polling Trade objects)
        if order.status == "filled":
            return order
```

**Removes**: Contract qualification, `Trade` object polling, `wait_for_fill()` complexity

**Keeps**: Ladder strategy logic (still useful for extended hours)

### 🔄 **Order Executor Helpers** (Simplify)
**Location**: `src/newsflash/infra/brokerage/order_executor.py`

**Current**: IBKR-specific `place_ladder_order()`, `wait_for_fill()` with `Trade` objects

**Simplified**: REST API helpers:
```python
async def place_ladder_order(
    client: httpx.AsyncClient,
    symbol: str,
    action: str,
    quantity: int,
    limit_price: float,
    extended_hours: bool = True
) -> dict:
    """Place ladder limit order via REST API."""
    order_data = LimitOrderRequest(
        symbol=symbol,
        qty=quantity,
        side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
        limit_price=limit_price,
        time_in_force=TimeInForce.DAY,
        extended_hours=extended_hours
    )
    order = await client.submit_order(order_data)
    return order

async def wait_for_fill(
    client: httpx.AsyncClient,
    order_id: str,
    timeout: float
) -> bool:
    """Wait for order fill via REST API polling."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        order = await client.get_order_by_id(order_id)
        if order.status == "filled":
            return True
        await asyncio.sleep(0.5)  # Simple polling
    return False
```

**Removes**: `Trade` object complexity, IBKR-specific status checks

---

## 5. Market Session Detection - Do We Still Need It?

### ✅ **YES - Keep It**

**Why**:
1. **Routing Logic**: Still need to route trades to correct executor (market hours vs extended hours)
2. **Order Type Selection**: Market orders vs limit orders based on session
3. **Extended Hours Flag**: Alpaca requires `extended_hours=True` flag for premarket/postmarket orders
4. **Queue Management**: Still need to queue trades when market is closed

**Alpaca Support**:
- ✅ Supports extended hours (4:00 AM - 8:00 PM ET)
- ✅ Requires `extended_hours=True` flag for limit orders during extended hours
- ✅ Market orders only during regular hours (9:30 AM - 4:00 PM ET)

**Our Logic**:
- **Market Hours (9:30 AM - 4:00 PM ET)**: Use market orders
- **Extended Hours (4:00 AM - 9:30 AM, 4:00 PM - 8:00 PM ET)**: Use limit orders with ladder strategy + `extended_hours=True`
- **Closed (8:00 PM - 4:00 AM ET)**: Queue trades for next premarket

**Conclusion**: Keep `session_detector.py` - still needed for routing and order type selection.

---

## 6. Ladder Strategy - Do We Still Need It?

### ✅ **YES - Keep It (But Can Simplify)**

**Why**:
1. **Extended Hours Liquidity**: Alpaca extended hours has lower liquidity (same as IBKR)
2. **Fill Optimization**: Ladder strategy helps get fills in low-liquidity environments
3. **Price Discovery**: Gradually moves price to find liquidity
4. **Your Comment**: "I will try to build the algo to avoid low liquidity anyway soon" - but ladder is still useful as fallback

**Alpaca Considerations**:
- ✅ Alpaca supports limit orders with `extended_hours=True`
- ✅ Can still use ladder strategy (place multiple limit orders at different prices)
- ⚠️ **Note**: Alpaca may have different fill behavior than IBKR (REST API vs subscription-based)

**Simplification Opportunities**:
- Remove IBKR-specific `Trade` object polling
- Use REST API order status checks instead
- Keep ladder algorithm logic (pure functions)
- Simplify `wait_for_fill()` to use REST API polling

**Conclusion**: Keep ladder strategy, but simplify implementation to use REST API instead of IBKR `Trade` objects.

---

## 7. Migration Implementation Plan

### Phase 1: Setup Alpaca Infrastructure (1-2 days)

1. **Create Alpaca Connection Manager**
   - File: `src/newsflash/infra/brokerage/alpaca/connection_manager.py`
   - Simple async HTTP client
   - Use `ALPACA_KEY` and `ALPACA_SECRET` from `.env`
   - Join main event loop (no threads)

2. **Create Alpaca Quote Fetcher**
   - File: `src/newsflash/infra/brokerage/alpaca/quote_fetcher.py`
   - REST API calls to `/v2/stocks/{symbol}/quotes/latest`
   - No contract qualification, no subscriptions

3. **Create Alpaca Trade Executors**
   - Files: `alpaca/trade_executor_market_hours.py`, `alpaca/trade_executor_extended_hours.py`
   - Use `alpaca-py` SDK for order placement
   - Keep ladder strategy for extended hours
   - Use REST API for order status checks

### Phase 2: Update Service Layer (1 day)

1. **Create Alpaca Brokerage Service**
   - File: `src/newsflash/infra/brokerage/alpaca/service.py`
   - Implement `BrokerageServiceProtocol`
   - Use session detector for routing
   - Keep same event publishing

2. **Update Service Factory**
   - Add Alpaca service option
   - Use environment variable to choose broker

### Phase 3: Remove IBKR-Specific Code (1 day)

1. **Delete Files**:
   - `connection_helpers.py` (entire file)
   - IBKR-specific connection manager code

2. **Refactor**:
   - Remove threading from connection manager
   - Remove contract qualification
   - Remove market data subscriptions

### Phase 4: Testing (1 day)

1. **Test Account Info** (✅ Already working)
2. **Test Quote Fetching**
3. **Test Market Hours Orders**
4. **Test Extended Hours Orders (with ladder)**
5. **Test Queue Management**

---

## 8. Key Simplifications Summary

### Threading Reduction
- **Before**: 4 threads (3 WebSocket + 1 IBKR)
- **After**: 3 threads (3 WebSocket only)
- **Removed**: 1 IBKR connection thread → joins main event loop

### Code Reduction
- **Connection Manager**: 632 lines → ~100 lines (84% reduction)
- **Quote Fetcher**: 334 lines → ~150 lines (55% reduction)
- **Trade Executors**: ~600 lines → ~400 lines (33% reduction)
- **Total**: ~900-1100 lines removed

### Complexity Reduction
- ❌ No more threading coordination
- ❌ No more contract qualification
- ❌ No more market data subscriptions
- ❌ No more daily restart windows
- ❌ No more keepalive pings
- ✅ Simple REST API calls
- ✅ Standard async/await patterns
- ✅ Join main event loop

---

## 9. What Stays Exactly the Same

✅ **Domain Models** - `TradeRequest`, `TradeStatus`, `MarketSession`, etc.  
✅ **Event Architecture** - All events, event bus, event publishing  
✅ **Protocols** - `BrokerageServiceProtocol`, `TradeExecutorProtocol`, etc.  
✅ **Queue Manager** - Broker-agnostic trade queuing  
✅ **Session Detection** - Market session routing logic  
✅ **Ladder Algorithms** - Price ladder calculations (pure functions)  
✅ **Price Calculator** - Trade price/quantity calculations  
✅ **Business Logic** - All domain logic stays the same  
✅ **Event Signatures** - Events work for both brokers  

---

## 10. Environment Variables Needed

Add to `.env`:
```bash
ALPACA_KEY=your_api_key
ALPACA_SECRET=your_api_secret
ALPACA_PAPER_TRADING=true  # or false for live
```

---

## Conclusion

The migration to Alpaca will significantly simplify your brokerage infrastructure while keeping all the good architectural patterns (events, domain separation, etc.). The main reduction comes from eliminating IBKR's complex connection model (threading, keepalive, subscriptions) in favor of simple HTTP requests.

**Estimated effort**: 4-5 days total
- Phase 1: 1-2 days (Alpaca infrastructure)
- Phase 2: 1 day (Service layer)
- Phase 3: 1 day (Remove IBKR code)
- Phase 4: 1 day (Testing)

**Key Benefits**:
- ✅ Simpler codebase (60-70% reduction)
- ✅ No threading overhead
- ✅ Standard async/await patterns
- ✅ Easier to test (mock HTTP responses)
- ✅ Better documentation (Alpaca REST API docs)
- ✅ Same event-driven architecture
- ✅ Same domain models and business logic
