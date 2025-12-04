# Alpaca Migration Plan

## Overview

Replace IBKR with Alpaca while keeping the domain layer and event-driven architecture intact.

## Architecture Analysis

### ✅ What Stays the Same (Domain Layer)

**Domain Models** (infrastructure-agnostic):
- `TradeRequest` - ticker, action, amount_usd, shares, leverage
- `TradeResult` - success, shares, fill_price, total_cost, error
- `Quote` - ticker, bid, ask, last, timestamp

**Domain Events** (infrastructure-agnostic):
- `TradeRequestDomainEvent` - published by use cases
- `TradeExecutedDomainEvent` - published by domain listener
- `TradeFailedDomainEvent` - published by domain listener
- `QuoteReceivedDomainEvent` - published by domain listener

**Domain Listener** (`domain/brokerage/listener.py`):
- Stays the same! Just needs to map Alpaca responses to domain models
- Already uses factories/mappers for translation

**Services Layer**:
- `AutoTradeService` - works with domain events, no changes needed
- `ExitTradeUseCase` - works with domain events, no changes needed

### 🔄 What Needs to Change (Infrastructure Layer)

**Files to Replace in `infra/brokerage/`:**

1. **`service.py`** → `AlpacaBrokerageService`
   - Replace `IBKRBrokerageService` with Alpaca API client
   - Same interface: `execute_trade()`, `get_realtime_price()`, `is_connected()`
   - Publish same infrastructure events

2. **`connection_manager.py`** → `AlpacaConnectionManager`
   - Replace IBKR connection with Alpaca REST/WebSocket client
   - Same interface: `start()`, `stop()`, `ensure_connected()`, `is_connected`
   - Publish `ConnectionStatusChanged` events

3. **`quote_fetcher.py`** → `AlpacaQuoteFetcher`
   - Replace IBKR market data with Alpaca quotes API
   - Same interface: `get_realtime_price()`, `get_last_quote_snapshot()`
   - Publish `QuoteReceived` events

4. **`trade_executor_market_hours.py`** → `AlpacaTradeExecutor`
   - Replace IBKR order placement with Alpaca orders API
   - Same interface: `execute()`
   - Publish `TradeExecuted` or `TradeFailed` events

5. **`trade_executor_extended_hours.py`** → `AlpacaExtendedHoursTradeExecutor`
   - Replace IBKR extended hours logic with Alpaca extended hours support
   - Same interface: `execute()`
   - Publish `TradeExecuted` or `TradeFailed` events

6. **`infrastructure_models.py`**
   - Update to match Alpaca API responses
   - Keep same event structure (domain listener expects these)

7. **`__init__.py`**
   - Update exports to use Alpaca classes

### 📦 Dependencies

**Remove:**
- `ib-insync>=0.9.86`

**Add:**
- `alpaca-trade-api>=3.0.0` (or `alpaca-py>=0.1.0` for newer SDK)

### 🔌 Interface Contracts

All infrastructure services must implement these interfaces (already defined):

**Connection:**
- `start()` → async
- `stop()` → async
- `is_connected()` → bool
- `ensure_connected(timeout)` → async IB/Connection

**Quote Fetcher:**
- `get_realtime_price(ib, contract, timeout)` → Optional[float]
- `get_last_quote_snapshot(symbol)` → Optional[Dict]

**Trade Executor:**
- `execute(ib, contract, trade_request, timing_info, timeout)` → Dict[str, Any]

**Service:**
- `execute_trade(trade_request, timeout)` → Dict[str, Any]
- `get_realtime_price(ticker, timeout)` → Optional[float]
- `is_connected()` → bool

### 📋 Migration Steps

1. **Install Alpaca SDK**
   ```bash
   uv pip install alpaca-trade-api
   ```

2. **Create Alpaca Infrastructure**
   - Create `infra/brokerage/alpaca/` directory
   - Implement each service matching IBKR interfaces
   - Use Alpaca REST API for trades, WebSocket for quotes (optional)

3. **Update Infrastructure Models**
   - Map Alpaca responses to `InfrastructureTradeExecutedEvent`
   - Map Alpaca quotes to `InfrastructureQuoteReceivedEvent`
   - Keep event structure identical

4. **Update Domain Listener** (minimal changes)
   - Update factories to map Alpaca responses → domain models
   - Factories already handle this abstraction

5. **Update DI Container**
   - Replace `IBKRBrokerageService` with `AlpacaBrokerageService` in `containers/application.py`
   - Update initialization function

6. **Update Configuration**
   - Replace IBKR config with Alpaca config (API key, secret, base URL)
   - Add to `config/settings.py`

7. **Remove IBKR Code**
   - Delete IBKR-specific files
   - Remove `ib-insync` dependency

### 🎯 Key Advantages of Current Architecture

1. **Domain Isolation** - Domain models/events are infrastructure-agnostic
2. **Event-Driven** - Infrastructure publishes events, domain listens
3. **Adapter Pattern** - Domain listener adapts infrastructure → domain
4. **Dependency Injection** - Easy to swap implementations
5. **Type Safety** - Pydantic models ensure contracts

### ⚠️ Potential Challenges

1. **Extended Hours** - Alpaca handles extended hours differently than IBKR
2. **Market Data** - Alpaca uses REST API vs IBKR's streaming
3. **Order Types** - Alpaca supports different order types (may need mapping)
4. **Connection Model** - Alpaca is REST-based, no persistent connection like IBKR Gateway

### 📝 Implementation Notes

**Alpaca API Differences:**
- REST-based (no persistent connection like IBKR Gateway)
- WebSocket for real-time quotes (optional, can use REST polling)
- Simpler order execution (no complex ladder strategies needed)
- Better extended hours support (built-in)
- No market data subscription conflicts

**Recommended Approach:**
1. Start with REST API for all operations (simpler)
2. Add WebSocket for quotes later if needed for latency
3. Use Alpaca's native extended hours support (no custom logic needed)
4. Leverage Alpaca's simpler order types (market/limit)

### ✅ Testing

The integration test (`tests/integration/test_full_auto_trade_flow.py`) will work with minimal changes:
- Just swap the brokerage service initialization
- All mocks and domain events stay the same
- Test flow remains identical

## Estimated Effort

- **Infrastructure Layer**: ~500-800 lines of code (similar to IBKR implementation)
- **Domain Layer**: ~0 lines (no changes needed)
- **Services Layer**: ~0 lines (no changes needed)
- **Configuration**: ~10 lines (add Alpaca config)
- **Testing**: ~50 lines (update test initialization)

**Total: ~600-900 lines of new code, mostly in `infra/brokerage/`**

