# Integration Tests

## Full Auto-Trade Flow Test

`test_full_auto_trade_flow.py` - Tests the complete flow from news event to trade execution.

### What It Tests

**Mocks:**
- ✅ WebSocket (publishes ArticleReceived event directly)
- ✅ Storage (mock StorageQueryService returns articles immediately)
- ✅ Classification (publishes ArticleClassified event directly)

**Real:**
- ✅ Brokerage service (IBKRBrokerageService) - executes actual paper trades
- ✅ Event bus (AsyncEventBus) - real event-driven flow

### Flow

1. **Article Creation** - Creates mock article with ticker (AAPL by default)
2. **Classification Event** - Publishes IMMINENT classification event
3. **Auto-Trade Processing** - AutoTradeService receives event, fetches article, builds trade request
4. **Trade Execution** - BrokerageDomainListener receives trade request, executes via IBKR
5. **Verification** - Verifies trade was executed successfully

### Running the Test

```bash
# Set environment variable to enable IBKR integration
export RUN_IBKR_INTEGRATION=1

# Run the test
pytest tests/integration/test_full_auto_trade_flow.py -v

# Or run directly
python tests/integration/test_full_auto_trade_flow.py
```

### Requirements

1. **IBKR Gateway** - Must be running on port 7497 (paper trading)
2. **Market Hours** - Test should run during market hours or extended hours
3. **Paper Account** - Must have paper trading account configured
4. **Liquid Ticker** - Uses AAPL by default (change `test_ticker` variable for others)

### Expected Output

```
================================================================================
FULL AUTO-TRADE INTEGRATION TEST
================================================================================
✅ Event bus created
✅ Mock article created: benzinga:test-1234567890 with ticker AAPL
✅ Mock storage service created (returns article immediately)
✅ MetricsService started
📡 Initializing REAL IBKR Brokerage Service (paper trading)...
✅ Brokerage service started
✅ AutoTradeService started (subscribed to ArticleClassified events)

🎯 Step 1: Publishing IMMINENT classification event...
✅ ArticleClassified event published

⏳ Step 2: Waiting for auto-trade to process (max 5 seconds)...
📢 Trade Request Published:
   Ticker: AAPL
   Action: BUY
   Amount: $100.0
✅ Trade request published in 0.15 seconds

⏳ Step 3: Waiting for trade execution (max 10 seconds)...
✅ Trade Executed:
   Success: True
   Shares: 1
   Fill Price: $175.23
   Total Cost: $175.23
✅ Trade executed in 2.45 seconds

✅ Trade Execution Verified:
   Shares: 1
   Fill Price: $175.23
   Total Cost: $175.23

================================================================================
✅ INTEGRATION TEST COMPLETED SUCCESSFULLY
================================================================================
```

### Debugging

If trades fail, check:
1. **IBKR Gateway** - Is it running? Check connection logs
2. **Market Hours** - Is market open? Test may skip if closed
3. **Price Retrieval** - Check logs for "Could not get real-time price"
4. **Buying Power** - Ensure paper account has sufficient funds

### Customization

To test with different tickers or amounts:

```python
# Change ticker
test_ticker = "TSLA"  # Line ~50

# Change trade amount
trade_amount_usd=Decimal("200.0")  # Line ~115
```

