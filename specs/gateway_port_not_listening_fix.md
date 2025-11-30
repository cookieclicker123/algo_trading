# IB Gateway Port Not Listening - Root Cause Analysis

## Diagnostic Results

From the diagnostic test:
- ✅ **Gateway process is running** (found in process list)
- ❌ **Port 4001 is NOT listening** (port is closed)
- ❌ **Socket connection times out** (no response)
- ✅ **Configuration appears correct** (user confirmed)
- ✅ **Port configuration matches** (user confirmed)

## Root Cause Identified

**The Gateway process is running, but the API server component is not listening on port 4001.**

This means:
- Gateway application started ✅
- Gateway UI is visible ✅  
- Gateway API server is NOT ready/started ❌

## Why This Happens

The IB Gateway has multiple components:
1. **Gateway Application** - The UI/process that starts
2. **API Server** - The component that listens on port 4001
3. **IBKR Connection** - Gateway's connection to IBKR servers

The API server only starts when:
- Gateway successfully logs in to IBKR
- Gateway establishes connection to IBKR servers
- Gateway is fully initialized

## Common Causes

### 1. Gateway Still Starting Up
**Symptom**: Process running but port not open yet
**Fix**: Wait 30-60 seconds for Gateway to fully initialize

### 2. Gateway Not Logged In
**Symptom**: Gateway UI shows "Not Connected" or login prompt
**Fix**: Log in to Gateway, wait for connection to IBKR

### 3. Gateway Connection Failed
**Symptom**: Gateway can't reach IBKR servers
**Fix**: Check network, IBKR server status, credentials

### 4. Gateway Session Expired
**Symptom**: Was working before, now not
**Fix**: Restart Gateway, log in again

### 5. Gateway in Broken State
**Symptom**: Stuck, not responding
**Fix**: Kill process, restart Gateway completely

## How to Check

### Check Gateway UI Status

Look at the Gateway window. You should see a table with:

| Purpose | Status |
|---------|--------|
| Interactive Brokers API Server | **✅ connected** (should be green) |
| Market Data Farm | ON: usfarm |
| API Client | disconnected (waiting for connection) |

**If "Interactive Brokers API Server" shows:**
- ❌ **disconnected** or **red X** → Gateway not ready
- ⚠️  **connecting** → Still initializing
- ✅ **connected** or **green checkmark** → Should be working

### Check Port Status

```bash
# Check if port 4001 is listening
lsof -i :4001

# Should show Gateway process if working
# If empty, Gateway API server not started
```

## Solutions

### Solution 1: Wait for Gateway to Initialize
```bash
# Give Gateway time to fully start
# Can take 30-60 seconds after launch
```

### Solution 2: Check Gateway Login
- Open Gateway UI
- Ensure you're logged in
- Wait for "Interactive Brokers API Server" to show "connected"

### Solution 3: Restart Gateway
1. Quit Gateway completely
2. Kill any remaining Gateway processes:
   ```bash
   pkill -f gateway
   ```
3. Start Gateway fresh
4. Wait 30-60 seconds
5. Verify API server status is "connected"

### Solution 4: Check Gateway Logs
- Gateway logs might show connection errors
- Check Gateway window for error messages
- Look for authentication/connection failures

## Next Steps

1. **Run port detection script** to see what's actually happening:
   ```bash
   python tests/test_ibkr_gateway_port_detection.py
   ```

2. **Check Gateway UI status** - specifically the "Interactive Brokers API Server" status

3. **Wait or restart** based on what you find

4. **Re-run diagnostics** after Gateway is ready:
   ```bash
   python tests/test_ibkr_gateway_diagnostics.py
   ```

## Expected Behavior When Working

When Gateway is properly ready:
- ✅ Process running
- ✅ Port 4001 listening
- ✅ Socket connection works
- ✅ ib_insync can connect
- ✅ Gateway UI shows "Interactive Brokers API Server: connected"

