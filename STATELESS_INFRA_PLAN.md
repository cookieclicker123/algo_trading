# Remove Stateful Infrastructure Services Plan

**Priority:** 2
**Date:** December 2025

## Current Problems

Infrastructure services contain mutable state:
- Statistics dictionaries that are mutated
- `is_running` flags
- Cached data (prompts, connection state)
- In-memory deduplication (processed_ids)

**Principle:** Infrastructure should be stateless - only manage external resources.

## Services to Refactor

### 1. StorageInfrastructureService
**State:**
- `is_running` (line 70)
- `get_stats()` returns `is_running` (line 277)

**Solution:**
- Remove `is_running` - use lifecycle manager
- Stats calculated from file system (already stateless)

### 2. ClassificationInfrastructureService
**State:**
- Cached `system_prompt` (line 77)
- `stats` dictionary (lines 83-90)
- `is_running` (line 93)

**Solution:**
- Extract prompt loading to separate service
- Extract stats to metrics service
- Remove `is_running` - use lifecycle manager

### 3. NotificationInfrastructureService
**State:**
- `stats` dictionary (lines 64-70)
- `is_running` (line 73)

**Solution:**
- Extract stats to metrics service
- Remove `is_running` - use lifecycle manager

### 4. IBKRBrokerageService
**State:**
- `is_running` (line 68)

**Solution:**
- Remove `is_running` - use lifecycle manager

### 5. BenzingaWebSocketMicroservice
**State:**
- `stats` dictionary with lock (mutable state)
- `is_running`
- Connection state

**Solution:**
- Extract stats to metrics service
- Connection state necessary (external resource) but should be minimized
- Remove `is_running` - use lifecycle manager

### 6. Repositories
**State:**
- `processed_ids` set (in-memory deduplication)

**Solution:**
- Use external storage for deduplication (Redis, file-based tracking)
- Or accept duplicates and deduplicate in domain layer

## Implementation Approach

### Phase 1: Extract Statistics
1. Create `MetricsService` for collecting statistics
2. Move all stats dictionaries to metrics service
3. Services publish events, metrics service collects

### Phase 2: Extract Runtime State
1. Create lifecycle managers for each service type
2. Remove `is_running` flags
3. Use lifecycle managers to track state

### Phase 3: Extract Cached Data
1. Create caching service (if needed)
2. Move cached prompts/data to cache service
3. Services fetch from cache service

### Phase 4: Fix Repositories
1. Replace in-memory deduplication with external storage
2. Or move deduplication to domain layer

## Target State

Infrastructure services should:
- Only manage external resources (files, APIs, connections)
- Publish events (no return values)
- No mutable state
- Stateless operations only

