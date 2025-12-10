# Notification Workflow Tests

These tests verify the complete notification pipeline to identify where notifications might be failing.

## Quick Diagnostic Test

**Run the quick diagnostic test to verify the basic flow:**

```bash
python3 tests/integration/test_notification_diagnostic.py
```

This test will:
1. ✅ Create a notification message
2. ✅ Publish it via domain event
3. ✅ Verify domain listener receives it
4. ✅ Verify infrastructure service processes it
5. ✅ Verify Telegram client is called

**Expected output:**
```
✅ ALL CHECKS PASSED - Notification workflow is working correctly!
```

If any step fails, it will clearly indicate which component is broken.

## Comprehensive Integration Tests

**Run all notification workflow tests:**

```bash
python3 -m pytest tests/integration/test_notification_workflow.py -v -s
```

### Test Categories

1. **TestNotificationMessageCreation**
   - Verifies notification messages can be created from articles
   - Tests message validation

2. **TestNotificationDomainListener**
   - Tests domain listener forwards valid notifications
   - Tests domain listener rejects invalid notifications

3. **TestNotificationInfrastructureService**
   - Tests infrastructure service processes notifications correctly
   - Verifies Telegram client integration

4. **TestNotificationUseCases**
   - Tests `NotifyTradeExecutedUseCase` publishes notifications
   - Tests `NotifyImminentArticleUseCase` publishes notifications

5. **TestFullNotificationWorkflow**
   - End-to-end test of the complete flow
   - Verifies all components work together

## What These Tests Verify

### Flow Verification

```
Use Case → Domain Event → Domain Listener → Infrastructure Event → Infrastructure Service → Telegram Client
```

Each test verifies:
- ✅ Events are published correctly
- ✅ Events are received by subscribers
- ✅ Validation works correctly
- ✅ Messages are properly formatted
- ✅ Telegram client is called with correct data

## Running Specific Tests

```bash
# Test message creation
pytest tests/integration/test_notification_workflow.py::TestNotificationMessageCreation -v

# Test domain listener
pytest tests/integration/test_notification_workflow.py::TestNotificationDomainListener -v

# Test infrastructure service
pytest tests/integration/test_notification_workflow.py::TestNotificationInfrastructureService -v

# Test use cases
pytest tests/integration/test_notification_workflow.py::TestNotificationUseCases -v

# Test complete workflow
pytest tests/integration/test_notification_workflow.py::TestFullNotificationWorkflow -v
```

## Troubleshooting

If tests fail:

1. **Domain events not received:**
   - Check event bus subscription
   - Verify event type strings match

2. **Infrastructure events not published:**
   - Check domain listener validation
   - Verify mapper transforms correctly

3. **Telegram client not called:**
   - Check infrastructure service enabled flag
   - Verify Telegram client initialization

4. **Validation failures:**
   - Check notification message body is not empty
   - Verify all required fields are present

## Next Steps

After running these tests:

1. If all tests pass → The workflow is correct, issue may be:
   - Real Telegram API errors (check Telegram logs)
   - Configuration issues (bot tokens, chat IDs)
   - Network issues

2. If tests fail → The failing test indicates exactly which component needs fixing
