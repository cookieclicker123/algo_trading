# Statistics Integration Tests

## Overview

End-to-end integration tests for the StatisticsRepository that verify complete workflows.

## Test Coverage

- ✅ Append recall record workflow
- ✅ Append signal record workflow
- ✅ Update recall record workflow
- ✅ End-to-end recall workflow (append → update → verify)

## Running Tests

```bash
# Run all integration tests
pytest tests/integration/statistics/ -v

# Run specific test
pytest tests/integration/statistics/test_repository_integration.py::test_end_to_end_recall_workflow -v
```

## Test Features

- **Real File Writes**: All tests perform actual file I/O operations
- **5-Second Delay**: Files are kept for 5 seconds after tests complete for inspection
- **Automatic Cleanup**: Test directories are cleaned up after inspection period
- **End-to-End**: Tests verify complete workflows, not just individual methods
