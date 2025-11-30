# Chapter 3.1: Complete WebSocket Removal from Services Layer

## Problem

Services layer still has massive amounts of WebSocket state and external dependency code:
1. **FeedManager** - Creates, manages, starts/stops WebSocket directly
2. **FeedHealthMonitor** - Directly accesses WebSocket stats, 120+ lines of WebSocket health logic
3. **Old benzinga_websocket_service.py** - Still exists (734 lines)
4. Services directly import and use WebSocket infrastructure

## Goal

Services should:
- ✅ Subscribe to events ONLY
- ❌ NOT create/manage WebSocket instances
- ❌ NOT access WebSocket stats directly
- ❌ NOT have WebSocket-specific logic
- ❌ NOT know about WebSocket state

Infrastructure should:
- ✅ Manage all WebSocket state
- ✅ Publish health events (not just error events)
- ✅ Provide health checking logic
- ✅ Handle all WebSocket lifecycle

## Tasks

### Task 1: Move Health Checking to Infra
- Create `infra/websocket/health_monitor.py`
- Move all WebSocket health checking logic from FeedHealthMonitor
- Health monitor should publish health events (not stats polling)

### Task 2: Create WebSocket Lifecycle Manager
- Move WebSocket creation/start/stop logic out of FeedManager
- Create initialization in service_initialization that creates websocket
- FeedManager should only subscribe to events

### Task 3: Create Health Events
- `WebSocketHealthStatusEvent` - periodic health updates
- Health monitor in infra publishes these
- FeedHealthMonitor subscribes to health events (not polls stats)

### Task 4: Remove Direct WebSocket Access
- Remove FeedManager's `processors` dict for WebSocket
- Remove FeedHealthMonitor's direct stats access
- Remove all `websocket_service.get_stats()` calls from services

### Task 5: Delete Old File
- Delete `benzinga_websocket_service.py` entirely

