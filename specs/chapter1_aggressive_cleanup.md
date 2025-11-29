# Chapter 1: Aggressive Cleanup Plan

## Major Removals - Things We'll Redesign Anyway

### 1. Remove ServiceContainer (DIY Dependency Injection)
**Rationale**: We'll redesign dependency injection properly with FastAPI dependencies in Chapter 7. This 372-line class is unnecessary complexity now.

**Impact**: 
- Remove `service_container.py` entirely
- Inline service initialization in `app.py` temporarily (simpler for now)
- Simplify `main.py` to not use container

**Files to modify**:
- `src/newsflash/api/app.py` - Remove container usage, inline initialization
- `src/main.py` - Remove container usage
- Delete `src/newsflash/services/service_container.py`

### 2. Remove Unnecessary Wrapper/Pass-Through Methods

#### feed_manager.py
- `get_available_sources()` - Just returns `list(self.processors.keys())` - not used anywhere
- `get_recent_articles()` - Just passes through to article_processor, ignores source parameter
- `get_archived_articles()` - Just passes through to article_processor, ignores source parameter  
- `get_archive_stats()` - Just passes through to article_processor

**Rationale**: These don't add any value - just pass through to article_processor. API can call article_processor directly.

### 3. Remove Unnecessary Factory Functions

Many services have `get_*()` factory functions that just create instances. We can simplify:
- `get_article_processor()` - Just creates instance, no real factory logic
- `get_yfinance_service()` - Just creates instance
- Others that are just thin wrappers

**Rationale**: These factory functions don't add value - just instantiate classes. We can call constructors directly or remove them if not needed.

### 4. Remove Unused Configuration/Utilities

Check for:
- Unused config variables
- Unused utility functions
- Dead code in utils/

### 5. Simplify Service Initialization Logic

- Remove complex initialization chains
- Simplify dependency creation
- Remove unnecessary abstraction layers

