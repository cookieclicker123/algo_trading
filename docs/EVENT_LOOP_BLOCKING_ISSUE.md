# Event Loop Blocking Issue - Root Cause Analysis

**STATUS: FIXED** (2026-02-12)

## Summary

The system was experiencing **severe event loop blocking** caused by **synchronous HTTP calls** to the Alpaca SDK running inside async functions. When Alpaca was slow or had network issues, these calls blocked the entire event loop for minutes to hours.

**Solution**: All synchronous Alpaca SDK calls are now wrapped in `run_in_executor()` via the `run_sync_alpaca_call()` helper function.

## Evidence from Logs

```
lag_ms: 3,785,407  (63 MINUTES blocked!)
lag_ms: 2,921,001  (48 minutes)
lag_ms: 2,858,410  (47 minutes)
"no pong for 22003.2s" (6 HOURS without ping/pong)
```

When the event loop finally unblocks, everything wakes up at once and sees stale data.

## Root Cause

**12+ synchronous Alpaca SDK calls** in the codebase:

| File | Line | Call |
|------|------|------|
| `auto_trade.py` | 645 | `market_data_client.get_stock_trades()` |
| `auto_trade.py` | 1597 | `market_data_client.get_stock_trades()` |
| `auto_trade.py` | 1695 | `market_data_client.get_stock_quotes()` |
| `quote_fetcher.py` | 78, 87, 155, 174 | `get_stock_latest_quote()` |
| `market_data_validator.py` | 123, 154 | `get_stock_trades()`, `get_stock_latest_quote()` |
| `price_monitor.py` | 248, 315, 423 | `get_stock_bars()`, `get_stock_trades()` |

### Why This Blocks

The Alpaca SDK uses `requests` library internally, which is synchronous:

```python
# This BLOCKS the event loop until Alpaca responds
pub_quotes = market_data_client.get_stock_quotes(...)
```

When Alpaca is slow (network issues, rate limiting, high load):
1. The synchronous call waits
2. **The entire event loop freezes**
3. No async tasks run (pings, WebSocket handling, health checks)
4. WebSocket connections become "zombie" (no pong received)
5. Benzinga thinks we're dead, closes connection
6. When the call finally returns, everything wakes up with stale data

## Is This Us or Alpaca?

**Both, but fixable on our side.**

- Alpaca sometimes has slow API responses
- But we're calling their synchronous SDK inside async code
- We should run sync calls in a thread pool to not block

## The Fix

Wrap synchronous calls in `run_in_executor()`:

```python
# BEFORE (blocks event loop)
pub_quotes = market_data_client.get_stock_quotes(request)

# AFTER (runs in thread pool, doesn't block)
import asyncio
from functools import partial

loop = asyncio.get_event_loop()
pub_quotes = await loop.run_in_executor(
    None,  # Use default thread pool
    partial(market_data_client.get_stock_quotes, request)
)
```

## What Changed Recently

We added the **pub→recv price filter** which makes a historical quote request:
- `auto_trade.py:1695` - `get_stock_quotes()` for publication-time price
- This new synchronous call is in the critical trading path
- When it blocks, the whole system stalls

## Priority Fix Order

1. **`auto_trade.py:1695`** - The new pub→recv filter (most critical, in trading path)
2. **`auto_trade.py:645`** - Confluence signal checking
3. **`quote_fetcher.py`** - All 4 calls (used for NBBO fetching)
4. **`price_monitor.py`** - Background monitoring (less critical but still blocking)

## Alternative: Disable New Filter Temporarily

To restore stability immediately, comment out the pub→recv historical quote fetch until the async fix is implemented:

```python
# TEMPORARY: Skip pub→recv check to avoid blocking
# TODO: Fix with run_in_executor
pub_time_ask = None  # Disable the blocking call
```

## Actual Root Cause (Git History Analysis)

The issue **suddenly appeared** after commit `e1f1f3a` on Feb 2, 2025: "take profit is now automated"

This commit changed surge monitoring to call `analyze_volume_around_event()` in `volume_analyzer.py`, which had **unwrapped sync Alpaca SDK calls inside async functions**:

- Line 794: `_fetch_minute_bar()` - sync call without `to_thread`
- Line 809: `client.get_stock_quotes()` - sync call without `to_thread`

These calls were in the hot path of every trade evaluation, blocking the event loop whenever Alpaca was slow.

## Fix Applied

### Part 1: Async Wrappers

Created `src/newsflash/utils/async_alpaca.py` with the `run_sync_alpaca_call()` helper.

Updated files:
- `src/newsflash/services/brokerage/auto_trade.py` (3 calls)
- `src/newsflash/infra/brokerage/quote_fetcher.py` (4 calls)
- `src/newsflash/infra/brokerage/market_data_validator.py` (2 calls)
- `src/newsflash/shared/statistics/price_monitor.py` (3 calls)
- `src/newsflash/infra/brokerage/ticker_validator.py` (2 calls)
- `src/newsflash/infra/classification/service.py` (1 caller updated)

### Part 2: Volume Analyzer (ROOT CAUSE FIX)

Updated `src/newsflash/shared/statistics/volume_analyzer.py`:
- All sync Alpaca SDK calls now run via `asyncio.to_thread()`
- Sync helper functions (`_fetch_minute_bar`, `_fetch_prior_history_stats`, etc.) are designed to be SYNC and called via `to_thread` from async code
- Line 795: `await asyncio.to_thread(_fetch_minute_bar, ...)`
- Line 811: `await asyncio.to_thread(client.get_stock_quotes, ...)`
- Line 1057: `asyncio.to_thread(_fetch_prior_history_stats, ...)`
- Line 1082: `await asyncio.to_thread(_fetch_shadow_spread)`

### Part 3: Statistics Engines (Background Enrichment)

Updated `get_asset()` calls in background enrichment tasks:
- `src/newsflash/shared/statistics/signal_engine.py` (2 calls)
- `src/newsflash/shared/statistics/failed_trades_engine.py` (2 calls)
- `src/newsflash/shared/statistics/record_manager.py` (1 call)

All `trading_client.get_asset()` calls now wrapped with `asyncio.to_thread()`.

**Total: 19+ synchronous calls wrapped in async executors.**

### Part 4: Connection Pool Configuration

Running many concurrent requests exhausted urllib3's default connection pool (size 10), causing:
- `"Connection pool is full, discarding connection: data.alpaca.markets"`
- `SSLError: UNEXPECTED_EOF_WHILE_READING`

**Fix**: Increased connection pool size to 50 via `configure_alpaca_client_pool()`:
- `src/newsflash/utils/async_alpaca.py` - Added `configure_alpaca_client_pool()` helper
- `src/newsflash/infra/brokerage/connection_manager.py` - Applied to all Alpaca clients

## Verification

Run this grep to verify no unwrapped blocking calls remain in the main codebase:
```bash
grep -rn "\.get_stock_trades\|\.get_stock_quotes\|\.get_stock_bars\|\.get_asset\|\.get_all_assets" src/newsflash/ | grep -v "to_thread\|run_sync_alpaca_call\|jobs/"
```

Expected: Only the `async_alpaca.py` docstring example should match.

### Part 2: File Cache for Ticker Validator

The `get_all_assets()` call fetches 8,448 tickers from Alpaca. This was called on EVERY startup.

**New behavior:**
1. On first startup: Fetch from Alpaca, save to `data/cache/alpaca_tradeable_tickers.json`
2. On subsequent startups: Load from file (instant), refresh in background
3. Hourly refresh saves to file for next startup

This eliminates the heavy Alpaca API call blocking startup.

All Alpaca SDK calls now run in a thread pool and do not block the event loop.
