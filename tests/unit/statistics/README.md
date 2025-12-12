# Statistics Unit Tests

## Overview

Comprehensive unit tests for the StatisticsRepository covering all methods and edge cases.

## Test Coverage

### Path Calculation (`TestPathCalculation`)
- ✅ Premarket path calculation
- ✅ Market hours path calculation
- ✅ Postmarket path calculation
- ✅ Week number calculation
- ✅ Directory structure validation

### Session Mapping (`TestSessionMapping`)
- ✅ Premarket → MarketSession.PREMARKET
- ✅ Market hours → MarketSession.MARKET
- ✅ Postmarket → MarketSession.POSTMARKET
- ✅ Closed → MarketSession.CLOSED
- ✅ Unknown session defaults to MARKET

### Session Times (`TestSessionTimes`)
- ✅ Premarket times (4:00 AM - 9:30 AM ET)
- ✅ Market hours times (9:30 AM - 4:00 PM ET)
- ✅ Postmarket times (4:00 PM - 8:00 PM ET)

### Recall Operations (`TestRecallOperations`)
- ✅ Append record creates file
- ✅ Append record stores data correctly
- ✅ Summary updates on append
- ✅ Multiple records append correctly
- ✅ Update existing record
- ✅ Missed opportunity counting logic

### Signal Operations (`TestSignalOperations`)
- ✅ Append record creates file
- ✅ Append record stores data correctly
- ✅ Summary updates on append
- ✅ Profit/loss tracking
- ✅ Average spread calculation
- ✅ Industry/sector breakdown

### Concurrent Operations (`TestConcurrentOperations`)
- ✅ Multiple concurrent appends work correctly

### File Loading (`TestFileLoading`)
- ✅ Load existing file
- ✅ Load nonexistent file creates new

## Running Tests

```bash
# Run all unit tests
pytest tests/unit/statistics/ -v

# Run specific test class
pytest tests/unit/statistics/test_repository.py::TestRecallOperations -v

# Run with coverage
pytest tests/unit/statistics/ --cov=src/newsflash/infra/statistics --cov-report=html
```

## Test Features

- **Real File Writes**: All tests perform actual file I/O operations
- **5-Second Delay**: Files are kept for 5 seconds after tests complete for inspection
- **Automatic Cleanup**: Test directories are cleaned up after inspection period
- **Comprehensive Coverage**: Tests cover all repository methods and edge cases
