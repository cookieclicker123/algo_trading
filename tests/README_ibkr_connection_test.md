# IBKR Connection Integration Test

## Purpose

This progressive integration test isolates and verifies IB Gateway connection functionality step-by-step to identify root causes when the connection fails in the full workflow.

## Testing Strategy

The test uses a **progressive hypothesis-driven approach**:

1. **Start simple**: Test the most basic connection scenario
2. **Add complexity incrementally**: Each test builds on the previous
3. **Isolate failures**: If a test fails, we know exactly where the problem is
4. **Rule out issues**: Each passing test rules out a category of problems

## Test Suite

### Test 1: Minimal Connection (Isolated)
**Hypothesis**: The basic IB connection mechanism works in isolation.

**What it tests**:
- IBKRConnectionManager initialization
- Connection to IB Gateway
- Basic connection verification

**What it rules out**:
- Event bus conflicts
- Telegram service conflicts
- Health monitoring conflicts
- Service orchestration issues

---

### Test 2: Connection with Event Bus
**Hypothesis**: Connection works when event bus is involved.

**What it tests**:
- Event publishing on connection status changes
- Event subscription and handling
- Event bus integration

**What it rules out**:
- Event loop conflicts
- Event publishing issues
- Subscription/handling problems

---

### Test 3: Connection with Telegram Notifications
**Hypothesis**: Connection works when Telegram notifications are enabled.

**What it tests**:
- Telegram service integration
- Notification sending on connection events
- End-to-end event flow: Connection → Event → Telegram

**What it rules out**:
- Telegram service conflicts
- Notification delivery issues
- Event-to-Telegram integration problems

---

### Test 4: Connection with Health Monitoring
**Hypothesis**: Connection works when health monitoring is active.

**What it tests**:
- Health monitor subscription to connection events
- Health status tracking
- Full monitoring stack integration

**What it rules out**:
- Health monitoring conflicts
- Multiple subscriber issues
- Monitoring integration problems

---

### Test 5: Full Workflow (Start/Stop/Restart)
**Hypothesis**: Connection lifecycle management works correctly.

**What it tests**:
- Connection startup
- Connection verification
- Graceful shutdown
- Reconnection after stop

**What it rules out**:
- Lifecycle management issues
- Cleanup problems
- Reconnection logic issues

## Running the Test

```bash
# Make sure IB Gateway is running
# Make sure you're using paper trading port (4001 by default)

# Run all tests progressively
python tests/test_ibkr_connection_integration.py

# Or run specific tests by modifying the main function
```

## Expected Behavior

### Successful Run
- All tests should pass ✅
- Connection should establish within 5-10 seconds
- Telegram notifications should be sent
- Events should be published and received
- Health monitoring should report healthy status

### Failure Analysis

If **Test 1 fails**:
- Problem is in the core connection mechanism
- Check: IB Gateway is running, port is correct, client ID not in use
- Likely causes: Gateway not ready, network issues, port conflicts

If **Test 1 passes, Test 2 fails**:
- Problem is with event bus integration
- Check: Event publishing, event subscription, event loop conflicts

If **Test 2 passes, Test 3 fails**:
- Problem is with Telegram integration
- Check: Telegram service initialization, notification sending, bot configuration

If **Test 3 passes, Test 4 fails**:
- Problem is with health monitoring
- Check: Health monitor initialization, event subscriptions, monitoring logic

If **Test 4 passes, Test 5 fails**:
- Problem is with lifecycle management
- Check: Start/stop logic, cleanup, reconnection

## Client IDs

Each test uses a different client ID (6, 7, 8, 9, 10) to avoid conflicts:
- Main service uses client ID 5
- Tests use IDs 6-10
- Make sure these IDs aren't in use elsewhere

## Troubleshooting

### Connection Timeouts
- **Symptom**: Tests timeout waiting for connection
- **Possible causes**:
  - IB Gateway not running
  - Gateway not ready (needs a few seconds to start)
  - Wrong port (check paper trading vs live trading)
  - Client ID already in use
- **Fix**: Ensure Gateway is running and ready, use correct port

### Event Not Received
- **Symptom**: Tests pass but no events logged
- **Possible causes**:
  - Event bus not publishing events
  - Subscriber not registered
  - Event loop not running
- **Fix**: Check event bus initialization, subscription timing

### Telegram Not Working
- **Symptom**: Connection works but no Telegram messages
- **Possible causes**:
  - Telegram bot token invalid
  - Chat ID incorrect
  - Telegram service not started
- **Fix**: Check Telegram configuration, verify bot is running

## Integration with Full Workflow

Once all tests pass:
1. The isolated components are working correctly
2. The problem (if any) is in the **orchestration** or **interaction** between components
3. Focus debugging on:
   - Service initialization order
   - FastAPI startup sequence
   - Background task scheduling
   - Event loop management in the full app

## Next Steps After Tests Pass

If all tests pass but the full workflow still fails:
1. Compare test initialization with full app initialization
2. Check for timing issues (startup sequence)
3. Look for resource conflicts (ports, client IDs)
4. Verify FastAPI event loop compatibility
5. Check for missing service dependencies

