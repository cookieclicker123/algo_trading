# Domain Layer Isolation Assessment

**Date:** 2025-12-04  
**Question:** Is domain layer completely insulated from infrastructure? Can we add a new brokerage without touching domain code?

---

## Critical Finding: Domain Layer HAS Infrastructure Dependencies

### ❌ Domain Layer Imports Infrastructure Models

**Files with Infrastructure Dependencies:**

1. **`domain/brokerage/listener.py`**
   ```python
   from ...infra.brokerage.infrastructure_models import (
       InfrastructureTradeExecutionRequestEvent,
       InfrastructureTradeExecutedEvent,
       InfrastructureTradeFailedEvent,
       InfrastructureQuoteReceivedEvent,
       InfrastructureTradeQueuedEvent,
       InfrastructureConnectionStatusEvent,
       InfrastructureBrokerageHealthEvent
   )
   from ...infra.brokerage.event_protocols import (
       InfrastructureTradeExecutionRequestEventSubscriber,
       InfrastructureTradeExecutedEventSubscriber
   )
   ```

2. **`domain/brokerage/factories.py`**
   ```python
   from ...infra.brokerage.infrastructure_models import (
       InfrastructureTradeRequestData,
       InfrastructureTradeExecutedEvent,
       InfrastructureQuoteReceivedEvent
   )
   ```

3. **`domain/classification/listener.py`**
   ```python
   from ...infra.classification.infrastructure_models import (
       ClassificationRequestedInfrastructureEvent,
       ClassificationCompletedInfrastructureEvent,
       ClassificationFailedInfrastructureEvent
   )
   ```

4. **`domain/websocket/listener.py`**
   - Imports infrastructure models (checked via grep)

---

## What IS Isolated (Pure Domain)

### ✅ Domain Models - NO Infrastructure Dependencies

**Files:**
- `domain/brokerage/models.py` - Pure domain models, no infra imports
- `domain/websocket/models.py` - Pure domain models
- `domain/classification/models.py` - Pure domain models
- `domain/storage/models.py` - Pure domain models
- `domain/notification/models.py` - Pure domain models

**Status:** ✅ **FULLY ISOLATED**

### ✅ Domain Validators - NO Infrastructure Dependencies

**Files:**
- `domain/brokerage/validators.py` - Only validates domain models
- `domain/websocket/validators.py` - Only validates domain models
- `domain/classification/validators.py` - Only validates domain models

**Status:** ✅ **FULLY ISOLATED**

### ✅ Domain Events - NO Infrastructure Dependencies

**Files:**
- `domain/brokerage/events.py` - Pure domain events
- `domain/websocket/events.py` - Pure domain events
- `domain/classification/events.py` - Pure domain events

**Status:** ✅ **FULLY ISOLATED**

### ✅ Domain Mappers - NO Infrastructure Dependencies (Almost)

**Files:**
- `domain/brokerage/mappers.py` - Transforms domain ↔ infrastructure (but uses infrastructure models as input/output)
- `domain/websocket/mappers.py` - Similar pattern

**Status:** ⚠️ **PARTIALLY ISOLATED** (knows infrastructure model structure)

---

## What IS NOT Isolated (Domain Listeners & Factories)

### ❌ Domain Listeners - HAVE Infrastructure Dependencies

**Why:**
- Domain listeners are "bridges" - they translate infrastructure events → domain events
- They need to know infrastructure event structure to reconstruct typed events from dicts
- They subscribe to infrastructure event types

**Impact:**
- If infrastructure models change, domain listeners must change
- If you add a new brokerage with different event structure, domain listeners need updates

### ❌ Domain Factories - HAVE Infrastructure Dependencies

**Why:**
- Factories transform infrastructure models → domain models
- They need infrastructure models as input to know what to transform

**Impact:**
- If infrastructure models change, factories must change
- If new brokerage uses different models, factories need updates

---

## Can You Add a New Brokerage Without Touching Domain?

### Current Answer: ⚠️ **PARTIALLY**

**What You CAN Do:**
- ✅ Add new brokerage implementation in `infra/brokerage/`
- ✅ As long as it publishes the SAME infrastructure events with the SAME structure
- ✅ Domain listeners/factories will work automatically

**What You CANNOT Do:**
- ❌ Change infrastructure event structure without updating domain listeners/factories
- ❌ Add new event types without updating domain listeners
- ❌ Use different model structure without updating factories

**Example:**
```python
# ✅ WORKS: New brokerage publishes same events
class NewBrokerageService:
    async def execute_trade(self, request):
        # ... execute trade ...
        event = InfrastructureTradeExecutedEvent(...)  # Same structure
        await event_bus.publish("TradeExecuted", event.model_dump())
        # Domain listener automatically handles it!

# ❌ FAILS: New brokerage uses different event structure
class NewBrokerageService:
    async def execute_trade(self, request):
        # ... execute trade ...
        event = DifferentTradeExecutedEvent(...)  # Different structure!
        await event_bus.publish("TradeExecuted", event.model_dump())
        # Domain listener fails - doesn't know about DifferentTradeExecutedEvent
```

---

## Is This a Problem?

### Current Architecture: **ACCEPTABLE BUT NOT IDEAL**

**Why It's Acceptable:**
- Domain models/validators/events are pure (no infrastructure dependencies)
- Domain listeners/factories are "adapters" - they're meant to translate
- Infrastructure defines the contract, domain adapts to it
- This is a common pattern (Adapter Pattern)

**Why It's Not Ideal:**
- Domain layer knows about infrastructure models
- Adding new infrastructure requires domain changes (if models differ)
- Domain cannot be tested in complete isolation (needs infrastructure models)

---

## True Isolation Would Require

### Option 1: Domain Defines Contract (Inversion)

**Domain defines:**
- Domain event contracts (what domain expects)
- Infrastructure must adapt to domain contracts

**Pros:**
- ✅ Domain is truly isolated
- ✅ Infrastructure adapts to domain (not vice versa)
- ✅ Domain can be tested in isolation

**Cons:**
- ❌ Infrastructure must adapt to domain (less flexible)
- ❌ Requires refactoring

### Option 2: Pure Dict Events (No Typed Models)

**Infrastructure publishes:**
- Raw dicts (no typed models)
- Domain listeners parse dicts directly

**Pros:**
- ✅ Domain doesn't import infrastructure models
- ✅ More flexible (can handle different structures)

**Cons:**
- ❌ Lose type safety
- ❌ More error-prone
- ❌ Domain must know dict structure anyway

### Option 3: Shared Contracts (Current + Better Documentation)

**Keep current architecture but:**
- Document infrastructure contracts clearly
- Make contracts stable (don't change them)
- Domain listeners are "adapters" (documented as such)

**Pros:**
- ✅ Type safety maintained
- ✅ Clear contracts
- ✅ Minimal changes needed

**Cons:**
- ⚠️ Domain still knows about infrastructure models
- ⚠️ Not true isolation

---

## Testability Assessment

### Can Domain Be Tested in Isolation?

**Domain Models:** ✅ **YES**
- Pure Python classes, no dependencies
- Can test without infrastructure

**Domain Validators:** ✅ **YES**
- Only validate domain models
- Can test without infrastructure

**Domain Events:** ✅ **YES**
- Pure Pydantic models
- Can test without infrastructure

**Domain Mappers:** ⚠️ **PARTIALLY**
- Need infrastructure models as input/output
- Can mock infrastructure models for testing

**Domain Factories:** ⚠️ **PARTIALLY**
- Need infrastructure models as input
- Can mock infrastructure models for testing

**Domain Listeners:** ❌ **NO**
- Subscribe to infrastructure events
- Need infrastructure models to reconstruct events
- Can mock, but not true isolation

---

## Final Assessment

### Statelessness: 9.5/10 ✅
- All business logic is stateless
- Repositories are stateless
- Services don't maintain mutable state

### Dependency Injection: 9/10 ✅
- Excellent DI container usage
- No global state
- Clear dependency graph

### Domain Isolation: 7/10 ⚠️

**Strengths:**
- ✅ Domain models are pure (no infrastructure dependencies)
- ✅ Domain validators are pure
- ✅ Domain events are pure
- ✅ Domain logic is separated from infrastructure logic

**Weaknesses:**
- ❌ Domain listeners import infrastructure models
- ❌ Domain factories import infrastructure models
- ❌ Domain cannot be tested in complete isolation
- ❌ Adding new infrastructure may require domain changes

### Overall Architecture: 8.5/10

**Why Not 10/10:**
- Domain layer has infrastructure dependencies (listeners/factories)
- Domain cannot be tested in complete isolation
- Adding new infrastructure may require domain changes

---

## Recommendation

### Current State: **GOOD ENOUGH**

**For Your Use Case:**
- ✅ You can add a new brokerage IF it publishes the same events
- ✅ Domain models/validators/events are pure
- ✅ Domain listeners/factories are "adapters" (acceptable pattern)

**To Achieve True Isolation:**
- Would require refactoring domain listeners/factories
- Would need to invert dependency (domain defines contract)
- Current architecture is acceptable for most use cases

**Bottom Line:**
- Domain is **mostly** isolated
- Pure domain logic (models/validators/events) is **fully** isolated
- Domain adapters (listeners/factories) know about infrastructure (acceptable pattern)
- You can add new infrastructure as long as it follows the same contracts

---

*Assessment Date: 2025-12-04*  
*Status: Domain is mostly isolated, but not completely*

