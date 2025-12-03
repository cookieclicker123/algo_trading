# Why We Use Dependency Injection - Explanation

## What is Dependency Injection (DI)?

**Dependency Injection** means:
- Objects receive their dependencies from outside (injected)
- Objects don't create their own dependencies
- Dependencies are provided by a container/framework

## Current Problem (Manual DI)

Right now in `composition_root.py`:
```python
# ❌ BAD: Manual initialization, hardcoded
event_bus = AsyncEventBus()  # We create it
storage = await initialize_storage_microservice(event_bus)  # We pass it manually
classification = await initialize_classification_microservice(event_bus)  # We pass it manually
```

**Problems:**
1. **Hardcoded** - We manually call each initialization function
2. **Tight coupling** - Composition root knows about every microservice
3. **Hard to test** - Can't easily swap event_bus for a mock
4. **Hard to change** - Adding a new microservice means editing composition_root

## True Dependency Injection

With DI container:
```python
# ✅ GOOD: Container manages everything
container = ApplicationContainer()
services = container.services()  # Container creates everything automatically!
```

**Benefits:**
1. **Automatic** - Container resolves all dependencies automatically
2. **Loose coupling** - Composition root doesn't know about microservices
3. **Easy to test** - Override providers: `container.event_bus.override(MockEventBus())`
4. **Easy to change** - Add new microservice in container, no composition_root changes

## How DI Container Works

1. **Container defines dependencies:**
   ```python
   class ApplicationContainer:
       event_bus = providers.Singleton(AsyncEventBus)  # Provides event_bus
       storage = providers.Resource(initialize_storage, event_bus=event_bus)  # Auto-injects event_bus!
   ```

2. **Container resolves automatically:**
   - When you ask for `storage`, container sees it needs `event_bus`
   - Container creates `event_bus` first (or uses existing singleton)
   - Container passes `event_bus` to `initialize_storage`
   - You get fully wired `storage` instance!

3. **No manual wiring needed:**
   - Container knows the dependency graph
   - Container creates things in the right order
   - Container handles all the wiring automatically

## Why This Creates Dependency Injection

1. **Dependencies are injected** - Event bus is injected into storage, not created by it
2. **Container manages lifecycle** - Container creates/destroys instances
3. **Automatic resolution** - Container figures out what needs what
4. **Inversion of Control** - Container controls the flow, not our code

This is **true dependency injection** because dependencies flow in (are injected) rather than being created internally.

