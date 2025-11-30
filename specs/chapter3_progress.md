# Chapter 3: Infrastructure Microservices - Progress

## ✅ Completed: Foundation (3.0)

### Event Bus Infrastructure ✅
- ✅ Created `infra/event_bus/async_event_bus.py` - async pub/sub event bus
- ✅ Global event bus instance with get_event_bus()
- ✅ Supports async subscribers
- ✅ Error isolation (one subscriber failure doesn't affect others)

## 🔄 In Progress: Subchapter 3.1 - WebSocket Microservice

### Created Structure ✅
- ✅ `infra/websocket/__init__.py`
- ✅ `infra/websocket/events.py` - Event definitions (ArticleReceived, WebSocketConnected, etc.)
- ✅ `infra/websocket/protocol.py` - Protocol/interface definition
- ✅ `infra/websocket/service.py` - Started WebSocket microservice implementation

### Still Needed for 3.1:
- [ ] Complete conversion logic in websocket/service.py
- [ ] Complete ping/monitor loops (copy from original)
- [ ] Update `feed_manager.py` to subscribe to ArticleReceived events instead of polling queue
- [ ] Test that events are published correctly
- [ ] Remove article_queue from websocket service (replaced by events)

## 📋 Next: Complete 3.1 before moving to 3.2 or 3.3

### Steps to Complete 3.1:
1. Complete the websocket/service.py implementation (ping/monitor loops)
2. Update feed_manager to subscribe to ArticleReceived events
3. Remove queue-based processing from feed_manager
4. Test event publishing and subscription
5. Verify system still works

## Key Design Decisions

- **Event Bus**: Global singleton pattern for simplicity (can be improved in Chapter 7 with DI)
- **Events**: Use Pydantic models for type safety and validation
- **Protocols**: Define interfaces to allow swapping implementations

