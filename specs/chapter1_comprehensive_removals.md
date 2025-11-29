# Chapter 1: Comprehensive Code Removal Plan

## Strategy: Remove Everything We'll Redesign Anyway

Since we're doing a major refactoring in later chapters, we should remove:
1. DIY dependency injection system (ServiceContainer) - we'll use FastAPI dependencies in Chapter 7
2. Unnecessary wrapper/pass-through methods
3. Legacy no-op methods
4. Unused code and dead patterns

---

## Major Removals

### 1. ServiceContainer - DIY Dependency Injection (REMOVE ENTIRELY)

**File**: `src/newsflash/services/service_container.py` (372 lines)

**Rationale**: 
- We'll redesign dependency injection properly with FastAPI dependencies in Chapter 7
- This is overly complex and creates global state
- Temporary solution we don't need

**Impact**:
- Inline service initialization directly in `app.py` and `main.py`
- Simplify service creation - just instantiate directly for now
- Remove global state pattern

**Files to modify**:
- `src/newsflash/api/app.py` - Remove container, inline service creation
- `src/main.py` - Remove container, inline service creation  
- DELETE: `src/newsflash/services/service_container.py`

### 2. Unnecessary Wrapper Methods

#### feed_manager.py - REMOVED (just done)
- ✅ Removed `get_available_sources()` - never called
- ✅ Removed `get_recent_articles()` - just passed through, ignored source param
- ✅ Removed `get_archived_articles()` - just passed through, ignored source param
- ✅ Removed `get_archive_stats()` - just passed through

**Action needed**: Update `app.py` to call `article_processor` directly instead of through `feed_manager`

### 3. Legacy No-Op Methods

#### ibkr_trading_service.py
- ❌ **Remove**: `_start_daily_restart_watchdog()` - legacy no-op method, never called
- ❌ **Remove**: Reference to `_daily_restart_watchdog_task` in stop() method (line 374) - task is never created

### 4. Bugs Found

#### ibkr_trading_service.py - FIXED
- ✅ **FIXED**: Unreachable code after return statement in `get_ibkr_trading_service()` (lines 1991-1994)

### 5. Unnecessary Factory Functions

Many `get_*()` factory functions just create instances with no logic. We can:
- Keep them if they're used, but simplify
- Remove if they just call constructors

**To review**:
- `get_yfinance_service()` - just creates instance
- `get_article_processor()` - has dependency injection logic, may be needed temporarily
- Others to check

### 6. Source Filtering That Doesn't Work

**API endpoints** accept `source` parameter but:
- `feed_manager` methods ignore it
- Just pass through to `article_processor` which doesn't support source filtering

**Action**: Remove source filtering from API endpoints (it doesn't work anyway)

---

## Removals Checklist

- [ ] Remove ServiceContainer entirely
- [ ] Update app.py to inline service initialization
- [ ] Update main.py to inline service initialization  
- [ ] Update app.py to call article_processor directly (not through feed_manager)
- [ ] Remove source filtering from API endpoints (doesn't work)
- [ ] Remove legacy no-op methods
- [ ] Remove unused task references
- [ ] Fix any bugs found

---

## Files to Delete

1. `src/newsflash/services/service_container.py` - Entire file (372 lines)

## Files to Modify

1. `src/newsflash/api/app.py` - Remove container, inline services
2. `src/main.py` - Remove container, inline services
3. `src/newsflash/services/ibkr_trading_service.py` - Remove no-op methods

