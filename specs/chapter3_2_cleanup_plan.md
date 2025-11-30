# Chapter 3.2: Old Brokerage Code Cleanup Plan

## Old Code to Remove

### ✅ Safe to Remove Immediately

1. **`services/ibkr_keepalive_service.py`** (271 lines)
   - **Status**: Fully replaced by `infra/brokerage/connection_manager.py`
   - **Action**: DELETE - no longer used anywhere

### ⚠️ Deprecate First (Still in Use)

2. **`services/ibkr_trading_service.py`** (1988 lines)
   - **Status**: Replaced by `infra/brokerage/service.py`
   - **Still used by**:
     - `service_initialization.py` - initializes old service
     - `auto_trade_service.py` - uses old service
     - `telegram_trade_handler.py` - uses old service
     - `price_tracking_service.py` - uses old service
   - **Action**: Mark as DEPRECATED, remove after migration

3. **`services/auto_trade_service.py`** (987 lines)
   - **Status**: Replaced by `use_cases/auto_trade_use_case.py`
   - **Still used by**:
     - `service_initialization.py` - initializes old service
     - `article_processor.py` - uses old service
   - **Action**: Mark as DEPRECATED, remove after migration

### 🔄 Temporarily Removed (Per User Request)

4. **`services/position_tracker.py`** (289 lines)
   - **Status**: Temporarily removed per simplification plan
   - **Action**: Keep for now, will be restored later if needed

5. **`services/price_tracking_service.py`** (198 lines)
   - **Status**: Temporarily removed per simplification plan
   - **Action**: Keep for now, will be restored later if needed

## Migration Path

### Step 1: Remove Keepalive Service ✅
- Delete `services/ibkr_keepalive_service.py`
- Verify no imports remain

### Step 2: Mark Old Services as Deprecated
- Add deprecation warnings to old services
- Document migration path

### Step 3: Migrate service_initialization.py (Future)
- Replace `get_ibkr_trading_service()` with `IBKRBrokerageService`
- Replace `AutoTradeService` with `AutoTradeUseCase`

### Step 4: Migrate telegram_trade_handler.py (Future)
- Update to use new brokerage service

### Step 5: Remove Deprecated Code (After Migration)
- Delete old services once all references are updated

