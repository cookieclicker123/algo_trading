# Auto-Trade Diagnostics - Why It's Not Working

**Date:** 2025-12-09  
**Issue:** Auto-trading and notifications not working despite integration tests passing

---

## Critical Issues to Check

### 1. Is AutoTradeService Actually Started? ⚠️

**Check:** Look for this log message:
```
AutoTradeService started
```

**Location:** `composition_root.py:173` - AutoTradeService is started AFTER brokerage microservice

**Potential Issue:** If brokerage microservice fails to start, AutoTradeService might not be started either.

**Fix:** Check brokerage microservice startup logs.

---

### 2. Is Auto-Trading Enabled? ⚠️

**Check Environment Variable:**
```bash
echo $AUTO_TRADING_ENABLED
```

**Default:** `true` (from `settings.py:43`)

**Check Logs For:**
```
⏭️ AUTO-TRADE SKIPPED: Auto-trading disabled
```

**If disabled:** Set `AUTO_TRADING_ENABLED=true` in `.env`

---

### 3. Is Brokerage Service Connected to Alpaca? ⚠️⚠️⚠️

**CRITICAL:** This is likely the issue!

**Check Logs For:**
```
✅ Alpaca Connection Manager connected
```

**Or Error:**
```
Failed to connect to Alpaca: ...
```

**Required Environment Variables:**
- `ALPACA_KEY` - Must be set
- `ALPACA_SECRET` - Must be set

**Check Connection:**
```python
# In connection_manager.py:96
account = self.trading_client.get_account()  # This can fail!
```

**If connection fails:**
- Check `ALPACA_KEY` and `ALPACA_SECRET` are set
- Check credentials are valid
- Check network connectivity to Alpaca API

---

### 4. Is AutoTradeService Receiving ArticleClassified Events? ⚠️

**Check Logs For:**
```
🎯 AUTO-TRADE: Received ArticleClassified event
```

**If NOT seeing this:**
- AutoTradeService might not be subscribed
- Event bus might not be working
- Classification events might not be published

**Check Subscription:**
- AutoTradeService subscribes in `__init__` (line 286)
- Should see: `AutoTradeService initialized - subscribes to Domain.ArticleClassified events`

---

### 5. Are Articles Being Fetched from Storage? ⚠️

**Check Logs For:**
```
🔍 AUTO-TRADE: Attempting to fetch article from storage
⏭️ AUTO-TRADE SKIPPED: Article not found in storage
```

**If articles not found:**
- Race condition: Classification completes before article is stored
- Storage service might not be working
- Article might not have tickers

---

### 6. Are Trade Requests Being Published? ⚠️

**Check Logs For:**
```
🚀 AUTO-TRADING: Publishing trade request domain event
```

**If NOT seeing this:**
- Article might not have tickers
- Trade request building might be failing
- Check: `build_trade_request_for_article` might be returning None

---

### 7. Are Trades Being Executed? ⚠️

**Check Logs For:**
```
✅ Trade Executed:
```

**If NOT seeing this:**
- Brokerage service might not be subscribed to TradeExecutionRequested
- Connection might not be established
- Market might be closed (trades queued instead)

---

### 8. Are Notifications Being Sent? ⚠️

**Check Logs For:**
```
✅ NOTIFY TRADE EXECUTED: Published notification request
```

**If NOT seeing this:**
- NotifyTradeExecutedUseCase might not be started
- Telegram might not be configured
- Notification service might not be subscribed

---

## Diagnostic Checklist

Run through this checklist to find the issue:

### Step 1: Check Environment Variables
```bash
# Required for auto-trading
echo "AUTO_TRADING_ENABLED=$AUTO_TRADING_ENABLED"
echo "AUTO_TRADE_AMOUNT_USD=$AUTO_TRADE_AMOUNT_USD"

# Required for Alpaca
echo "ALPACA_KEY=${ALPACA_KEY:0:10}..."  # Show first 10 chars
echo "ALPACA_SECRET=${ALPACA_SECRET:0:10}..."  # Show first 10 chars

# Required for notifications
echo "TELEGRAM_ENABLED=$TELEGRAM_ENABLED"
echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:0:10}..."
```

### Step 2: Check Service Startup Logs
Look for these log messages in order:

1. ✅ `Brokerage microservice initialized`
2. ✅ `Auto-trade service created and started via DI container`
3. ✅ `AutoTradeService started`
4. ✅ `🚀 Starting Brokerage Service`
5. ✅ `✅ Alpaca Connection Manager connected`
6. ✅ `✅ Brokerage Service started`

**If any are missing:** That's your issue!

### Step 3: Check Event Flow
Look for these log messages when IMMINENT article is classified:

1. ✅ `🎯 AUTO-TRADE: Received ArticleClassified event`
2. ✅ `🤖 AUTO-TRADE: Processing IMMINENT article`
3. ✅ `🚀 AUTO-TRADING: Publishing trade request domain event`
4. ✅ `Received trade execution request from domain`
5. ✅ `Trade execution completed`
6. ✅ `✅ Trade Executed:`

**If any are missing:** Check the step before it.

### Step 4: Check Brokerage Connection
```bash
# Check if Alpaca connection is established (use today's date)
TODAY=$(date +%Y-%m-%d)
WEEK=$(date +%V)
grep "Alpaca Connection Manager" tmp/audit_logs/$(date +%Y)/$(date +%m)/week_${WEEK}/${TODAY}.log | tail -5
```

**Look for:**
- `✅ Alpaca Connection Manager connected` - GOOD
- `Failed to connect to Alpaca` - BAD

---

## Most Likely Issues

### Issue #1: Alpaca Not Connected (90% Likely)

**Symptoms:**
- No trades executing
- No trade execution logs
- Brokerage service might be failing to start

**Check:**
```bash
# Use today's date
TODAY=$(date +%Y-%m-%d)
WEEK=$(date +%V)
grep -i "alpaca\|connection" tmp/audit_logs/$(date +%Y)/$(date +%m)/week_${WEEK}/${TODAY}.log | grep -i "error\|fail\|connected"
```

**Fix:**
1. Verify `ALPACA_KEY` and `ALPACA_SECRET` are set
2. Check credentials are valid
3. Check network connectivity

---

### Issue #2: AutoTradeService Not Started (5% Likely)

**Symptoms:**
- No "🎯 AUTO-TRADE: Received ArticleClassified event" logs
- AutoTradeService initialized but not started

**Check:**
```bash
# Use today's date
TODAY=$(date +%Y-%m-%d)
WEEK=$(date +%V)
grep "AutoTradeService" tmp/audit_logs/$(date +%Y)/$(date +%m)/week_${WEEK}/${TODAY}.log
```

**Fix:**
- Check if brokerage microservice starts successfully
- AutoTradeService is started in composition_root AFTER brokerage

---

### Issue #3: Auto-Trading Disabled (3% Likely)

**Symptoms:**
- See: `⏭️ AUTO-TRADE SKIPPED: Auto-trading disabled`

**Fix:**
- Set `AUTO_TRADING_ENABLED=true` in `.env`

---

### Issue #4: Articles Not Found in Storage (2% Likely)

**Symptoms:**
- See: `⏭️ AUTO-TRADE SKIPPED: Article not found in storage`

**Fix:**
- Check storage service is working
- Check articles are being stored
- Might be race condition (classification before storage)

---

## Quick Diagnostic Script

Create a file `check_auto_trade.py`:

```python
import os
from dotenv import load_dotenv

load_dotenv()

print("=== AUTO-TRADE DIAGNOSTICS ===\n")

# Check environment variables
print("1. Environment Variables:")
print(f"   AUTO_TRADING_ENABLED: {os.getenv('AUTO_TRADING_ENABLED', 'NOT SET')}")
print(f"   AUTO_TRADE_AMOUNT_USD: {os.getenv('AUTO_TRADE_AMOUNT_USD', 'NOT SET')}")
print(f"   ALPACA_KEY: {'SET' if os.getenv('ALPACA_KEY') else 'NOT SET'}")
print(f"   ALPACA_SECRET: {'SET' if os.getenv('ALPACA_SECRET') else 'NOT SET'}")
print(f"   TELEGRAM_ENABLED: {os.getenv('TELEGRAM_ENABLED', 'NOT SET')}")
print(f"   TELEGRAM_BOT_TOKEN: {'SET' if os.getenv('TELEGRAM_BOT_TOKEN') else 'NOT SET'}\n")

# Check recent logs
import glob
from pathlib import Path

log_files = glob.glob("tmp/audit_logs/2025/12/week_50/2025-12-08.log")
if log_files:
    with open(log_files[0], 'r') as f:
        lines = f.readlines()
        recent_lines = lines[-100:]  # Last 100 lines
        
    print("2. Recent Log Messages (last 100 lines):")
    print("   Looking for key messages...\n")
    
    keywords = [
        "AutoTradeService",
        "Alpaca Connection",
        "AUTO-TRADE",
        "Trade Executed",
        "Brokerage Service",
    ]
    
    for keyword in keywords:
        matches = [l for l in recent_lines if keyword.lower() in l.lower()]
        if matches:
            print(f"   ✅ Found '{keyword}': {len(matches)} occurrences")
            if len(matches) <= 3:
                for match in matches[-3:]:  # Show last 3
                    print(f"      {match.strip()[:100]}")
        else:
            print(f"   ❌ NOT FOUND: '{keyword}'")
        print()
else:
    print("2. No log files found\n")

print("=== END DIAGNOSTICS ===")
```

---

## Comparison: Integration Test vs Production

### Integration Test Setup:
```python
# Test creates services directly
brokerage = await initialize_brokerage_microservice(...)
auto_trade_service = AutoTradeService(...)
await auto_trade_service.start()
```

### Production Setup:
```python
# Production uses DI container
brokerage = await container.brokerage_microservice()
auto_trade_service = container.auto_trade_service()
brokerage.auto_trade_service = auto_trade_service
await auto_trade_service.start()
```

**Key Difference:** In production, AutoTradeService is created AFTER brokerage microservice. If brokerage fails to start, AutoTradeService might not be created.

---

## Next Steps

1. **Check logs** for Alpaca connection errors
2. **Verify environment variables** are set correctly
3. **Check if AutoTradeService is receiving events**
4. **Verify brokerage service is connected**

Run the diagnostic script above to identify the exact issue.
