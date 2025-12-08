# Alpaca Migration Analysis: Code Reduction & Simplification

## Executive Summary

**Estimated Code Reduction: 60-70% of brokerage infrastructure code**

When migrating from IBKR to Alpaca, you can eliminate:
- **~400-500 lines** of threading/connection management code
- **~200-300 lines** of IBKR-specific connection logic
- **~150-200 lines** of contract qualification/market data subscription code
- **~100 lines** of daily restart window logic
- **~50-100 lines** of keepalive/reconnection logic

**Total: ~900-1100 lines removed** from a codebase that's currently ~2000-2500 lines in `infra/brokerage/`

---

## 1. Connection Manager & Connection Helpers Summary

### Connection Manager (`connection_manager.py` - 632 lines)
**Purpose:** Manages IBKR Gateway connection lifecycle

**Key Responsibilities:**
- **Threading Management:** Creates dedicated thread with separate event loop for IB connection (lines 70-72, 481-514)
- **Connection Establishment:** Handles async connection to IB Gateway with timeout/retry logic (lines 191-352)
- **Keepalive Pings:** Periodic `accountValues()` calls to prevent idle disconnects (lines 459-479)
- **Connection Verification:** Periodic checks that connection is alive (lines 411-443)
- **Reconnection Logic:** Automatic reconnection with backoff after disconnects (lines 380-400)
- **Daily Restart Window:** Delays reconnection during IBKR Gateway daily restart (lines 375-379)
- **Event Publishing:** Publishes `ConnectionStatusChangedEvent` for all state changes

**IBKR-Specific Patterns:**
- `ib_insync.IB()` instance management
- Port-based connection (paper vs live)
- Client ID management
- `ib.isConnected()` checks
- `ib.accountValues()` for keepalive
- `ib.disconnectedEvent` callback handling
- Thread-safe event loop coordination

### Connection Helpers (`connection_helpers.py` - 73 lines)
**Purpose:** Stateless helper functions for connection timing

**Functions:**
- `calculate_daily_restart_window()`: Calculates when to delay reconnection during IBKR Gateway daily restart (5-minute window)
- `should_delay_reconnection()`: Checks if reconnection should be delayed

**IBKR-Specific:** Entire file is IBKR-specific (Gateway has daily restarts, Alpaca doesn't)

---

## 2. Duplication Analysis

### High Duplication Areas

#### A. Contract Qualification & Market Data (Quote Fetcher)
**Location:** `quote_fetcher.py` lines 113-119, 273-277

**Duplication:**
```python
# Repeated in get_realtime_price() and get_nbbo_snapshot()
qualify_coro = ib.qualifyContractsAsync(contract)
qualified_list = await asyncio.wait_for(qualify_coro, timeout=...)
[qualified] = qualified_list
ticker = ib.reqMktData(qualified, "", True, False)
# ... wait for data ...
ib.cancelMktData(qualified)
```

**Note:** There's even a TODO comment on line 286 acknowledging this!

**Solution for Alpaca:** 
- Alpaca doesn't need contract qualification (uses symbols directly)
- No market data subscriptions (REST API is request/response)
- Can be replaced with simple `GET /v2/stocks/{symbol}/quotes/latest`

#### B. Order Execution Helpers (Order Executor vs Trade Executors)
**Location:** `order_executor.py` (172 lines) vs `trade_executor_*.py`

**Duplication:**
- `place_ladder_order()` in `order_executor.py` vs similar logic in `trade_executor_extended_hours.py`
- `wait_for_fill()` logic appears in multiple places
- `extract_fill_details()` vs similar extraction in executors

**Solution:** These are IBKR-specific (Trade objects, order status polling). Alpaca REST API returns order status immediately.

#### C. Quote Snapshot Management
**Location:** `quote_fetcher.py` lines 44, 60-76, 48-58

**Pattern:** In-memory cache of quote snapshots (`_quote_snapshots: Dict[str, Dict[str, Any]]`)

**Note:** This is fine to keep - useful for both IBKR and Alpaca, but Alpaca implementation would be simpler (no subscription management).

---

## 3. IBKR-Specific Code to Remove

### Category 1: Threading & Event Loop Management (~400-500 lines)
**Files:** `connection_manager.py`

**Code to Remove:**
- Lines 70-72: `_ib_thread`, `_ib_event_loop`, `_connection_ready` (threading primitives)
- Lines 481-514: `_run_ib_connection_thread()` (entire dedicated thread setup)
- Lines 516-570: `_connect_async()` (thread-based connection)
- Lines 67-68: `_connection_lock`, `_main_event_loop` (event loop coordination)
- Lines 364-373: `call_soon_threadsafe()` patterns (thread-safe event publishing)

**Why:** Alpaca is REST API - no blocking operations, no need for separate threads. Can use standard `httpx` or `aiohttp` with async/await.

**Replacement:** Simple async HTTP client:
```python
async def ensure_connected(self) -> httpx.AsyncClient:
    if not self._client:
        self._client = httpx.AsyncClient(
            base_url="https://paper-api.alpaca.markets" if self.paper_trading else "https://api.alpaca.markets",
            headers={"APCA-API-KEY-ID": ..., "APCA-API-SECRET-KEY": ...}
        )
    return self._client
```

### Category 2: Connection Keepalive & Verification (~150-200 lines)
**Files:** `connection_manager.py`

**Code to Remove:**
- Lines 445-479: `_keepalive_loop()` (periodic `accountValues()` pings)
- Lines 411-443: `_verify_connection()` (periodic connection checks)
- Lines 402-409, 445-457: Task management for keepalive/verification

**Why:** HTTP connections don't need keepalive pings. Connection health is verified on each request (if it fails, you retry).

**Replacement:** None needed - HTTP client handles connection pooling automatically.

### Category 3: Daily Restart Window Logic (~100 lines)
**Files:** `connection_manager.py`, `connection_helpers.py`

**Code to Remove:**
- `connection_helpers.py`: Entire file (73 lines)
- `connection_manager.py` lines 85-86: `next_connect_time`
- Lines 375-379: `_handle_daily_restart_window()`
- Lines 386-391: Daily restart delay logic in `_reconnect_after_disconnect()`

**Why:** Alpaca doesn't have daily restarts. API is always available.

**Replacement:** None needed.

### Category 4: Contract Qualification (~150-200 lines)
**Files:** `quote_fetcher.py`, `service.py`, trade executors

**Code to Remove:**
- `quote_fetcher.py` lines 113-125: Contract qualification in `get_realtime_price()`
- Lines 273-282: Contract qualification in `get_nbbo_snapshot()`
- All `Stock()` contract creation and qualification logic

**Why:** Alpaca uses simple symbols (e.g., "AAPL"), not contracts.

**Replacement:**
```python
# Instead of:
contract = Stock(symbol, "SMART", "USD")
qualified = await ib.qualifyContractsAsync(contract)

# Just use:
symbol = "AAPL"  # That's it!
```

### Category 5: Market Data Subscriptions (~100-150 lines)
**Files:** `quote_fetcher.py`

**Code to Remove:**
- Lines 128-199: `reqMktData()` subscription and polling loop
- Lines 285-304: Similar subscription in `get_nbbo_snapshot()`
- Lines 178, 187, 195, 199, 304: `cancelMktData()` calls
- Lines 107-111: `reqMarketDataType()` calls

**Why:** Alpaca REST API is request/response - no subscriptions needed.

**Replacement:**
```python
async def get_realtime_price(self, symbol: str) -> Optional[float]:
    response = await self.client.get(f"/v2/stocks/{symbol}/quotes/latest")
    data = response.json()
    return data["quote"]["bp"]  # bid price, or use "ap" for ask
```

### Category 6: Reconnection Logic (~100-150 lines)
**Files:** `connection_manager.py`

**Code to Remove:**
- Lines 354-373: `_on_disconnect()` callback handler
- Lines 380-400: `_reconnect_after_disconnect()` with backoff
- Lines 393: `reconnect_attempts` tracking
- Lines 572-602: `_on_ib_error()` error handler (IBKR-specific error codes)

**Why:** HTTP clients handle reconnection automatically with retries. No need for manual reconnection loops.

**Replacement:** Use HTTP client retry logic:
```python
client = httpx.AsyncClient(
    timeout=30.0,
    limits=httpx.Limits(max_keepalive_connections=5),
    # Built-in retry on connection errors
)
```

### Category 7: Client ID & Port Management (~50 lines)
**Files:** `connection_manager.py`, `service.py`

**Code to Remove:**
- `client_id` parameter throughout (IBKR-specific)
- Port-based connection logic (lines 95, 268)
- Paper vs live port selection

**Why:** Alpaca uses API keys in headers, not client IDs or ports.

**Replacement:**
```python
base_url = "https://paper-api.alpaca.markets" if paper_trading else "https://api.alpaca.markets"
```

---

## 4. Code That Can Stay (With Simplification)

### Keep & Simplify

#### A. Quote Fetcher Interface
**Keep:** The interface (`get_realtime_price()`, `get_nbbo_snapshot()`)  
**Simplify:** Replace IBKR implementation with REST API calls

#### B. Trade Executors
**Keep:** The executor pattern and event publishing  
**Simplify:** Replace `ib.placeOrder()` with REST API calls

#### C. Queue Manager
**Keep:** Entire file - it's broker-agnostic  
**Note:** Already well-designed, no changes needed

#### D. Event Publishing
**Keep:** All event types and publishing logic  
**Note:** Events are broker-agnostic, perfect design

#### E. Price Calculator & Ladder Algorithms
**Keep:** All utility functions in `utils/brokerage/`  
**Note:** These are broker-agnostic business logic

---

## 5. Simplified Alpaca Architecture

### New Connection Manager (~50-100 lines vs 632 lines)
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
        # Verify connection with a simple API call
        try:
            await self.client.get("/v2/account")
            await self._publish_connection_status(True, "Connected")
        except Exception as e:
            await self._publish_connection_status(False, str(e))
    
    async def stop(self):
        await self.client.aclose()
        await self._publish_connection_status(False, "Stopped")
    
    async def ensure_connected(self):
        return self.client  # Always available
```

### New Quote Fetcher (~100-150 lines vs 334 lines)
```python
class AlpacaQuoteFetcher:
    async def get_realtime_price(self, symbol: str) -> Optional[float]:
        response = await self.client.get(f"/v2/stocks/{symbol}/quotes/latest")
        data = response.json()
        quote = data["quote"]
        return quote.get("bp") or quote.get("ap")  # bid or ask
    
    async def get_nbbo_snapshot(self, symbol: str) -> Optional[Dict]:
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

### Simplified Trade Executors
- Remove all `contract` qualification
- Replace `ib.placeOrder()` with `POST /v2/orders`
- Replace `trade.isDone()` polling with REST API status checks
- Remove all IBKR-specific order status handling

---

## 6. Migration Strategy

### Phase 1: Extract Broker-Agnostic Interfaces
1. Create abstract base classes for:
   - `ConnectionManager` (interface)
   - `QuoteFetcher` (interface)
   - `TradeExecutor` (interface)

2. Move IBKR implementations to `infra/brokerage/ibkr/` subdirectory

### Phase 2: Implement Alpaca Versions
1. Create `infra/brokerage/alpaca/` subdirectory
2. Implement simplified versions using REST API
3. Keep same event signatures (events are already broker-agnostic!)

### Phase 3: Remove IBKR-Specific Code
1. Delete `connection_helpers.py` entirely
2. Remove threading code from connection manager
3. Remove contract qualification logic
4. Remove market data subscription code
5. Remove daily restart window logic

### Phase 4: Consolidate Duplicated Code
1. Extract common quote fetching patterns
2. Consolidate order execution helpers
3. Create shared utilities for both brokers

---

## 7. Key Benefits

### Code Reduction
- **~900-1100 lines removed** (60-70% reduction)
- Simpler codebase = easier maintenance
- Fewer edge cases to handle

### Performance
- **No threading overhead** - pure async/await
- **No connection keepalive** - HTTP client handles it
- **Faster quote fetching** - direct REST calls vs subscription polling

### Reliability
- **No daily restart windows** - API always available
- **Simpler error handling** - HTTP status codes vs IBKR error codes
- **Built-in retries** - HTTP client handles reconnection

### Developer Experience
- **Easier to test** - mock HTTP responses vs mocking IBKR Gateway
- **Easier to debug** - standard HTTP logs vs IBKR-specific debugging
- **Better documentation** - Alpaca REST API docs are excellent

---

## 8. What Stays the Same (Good Design!)

✅ **Event Bus Architecture** - Perfect, broker-agnostic  
✅ **Domain Models** - `TradeRequest`, events, etc.  
✅ **Queue Manager** - Already broker-agnostic  
✅ **Price Calculation Logic** - Business logic, not broker-specific  
✅ **Ladder Algorithms** - Broker-agnostic  
✅ **Event Publishing** - All events work for both brokers  

---

## Conclusion

The migration to Alpaca will significantly simplify your brokerage infrastructure while keeping all the good architectural patterns (events, domain separation, etc.). The main reduction comes from eliminating IBKR's complex connection model (threading, keepalive, subscriptions) in favor of simple HTTP requests.

**Estimated effort:** 2-3 days to implement Alpaca versions, 1 day to remove IBKR-specific code, 1 day for testing = **~1 week total**.

