# Bad vs Good Implementation: Side-by-Side Comparison

## Current Implementation Review

Let me show you what we currently have vs. what it should be:

---

## Example 1: Infrastructure Event Format

### ❌ Bad Practice (Current - Partially Implemented)

```python
# infra/websocket/events.py - CURRENT
from ...models.base_models import StandardizedArticle  # ❌ Infrastructure imports shared model

class ArticleReceivedEvent(BaseModel):
    """
    Event published when a news article is received from WebSocket.
    """
    article: StandardizedArticle  # ❌ Uses shared model
    received_at: datetime
    source: str = "benzinga_websocket"
```

**Problems:**
1. Infrastructure depends on `StandardizedArticle` which is shared between infra and domain
2. Domain must know about infrastructure model structure
3. Can't easily swap infrastructure without changing shared models

### ✅ Good Practice (What It Should Be)

```python
# infra/websocket/events.py - FIXED
from typing import Dict, Any
from pydantic import BaseModel
from datetime import datetime

class ArticleReceivedEvent(BaseModel):
    """
    Infrastructure event - raw infrastructure format.
    
    This is infrastructure's own format - domain doesn't need to know about it.
    """
    article_data: Dict[str, Any]  # ✅ Raw data, infrastructure format
    received_at: datetime
    source: str = "benzinga_websocket"
    
    # OR if you want type safety but still infrastructure-specific:
    # article_data: BenzingaRawArticle  # Infrastructure-specific model
```

**Benefits:**
1. Infrastructure owns its data format completely
2. Domain doesn't know about infrastructure models
3. Can swap infrastructure without changing domain

---

## Example 2: Infrastructure Publishing Event

### ❌ Bad Practice (What Infrastructure Currently Does)

```python
# infra/websocket/service.py - CURRENT
from ...models.benzinga_models import convert_benzinga_to_standardized
from ...models.base_models import StandardizedArticle  # ❌ Infrastructure imports domain/shared models

def _process_news_articles(self, articles: list):
    for article_data in articles:
        # Convert to StandardizedArticle (shared model)
        standardized = convert_benzinga_to_standardized(article_data)  # ❌ Transformation in infra
        
        # Publish event with StandardizedArticle
        event = ArticleReceivedEvent(
            article=standardized,  # ❌ Using shared model
            received_at=datetime.now()
        )
        await self.event_bus.publish("ArticleReceived", event.model_dump())
```

**Problems:**
1. Infrastructure does transformation (should be domain's job)
2. Infrastructure depends on domain/shared models
3. Tight coupling

### ✅ Good Practice (What Infrastructure Should Do)

```python
# infra/websocket/service.py - FIXED
# ✅ NO domain/shared model imports!

def _process_news_articles(self, articles: list):
    """Process raw articles from WebSocket - publish raw infrastructure format."""
    for raw_article in articles:
        # Publish raw infrastructure data (no transformation)
        event = ArticleReceivedEvent(
            article_data=raw_article,  # ✅ Raw dict, infrastructure format
            received_at=datetime.now(),
            source="benzinga_websocket"
        )
        await self.event_bus.publish("ArticleReceived", event.model_dump())
```

**Benefits:**
1. Infrastructure just publishes raw data
2. No transformation in infrastructure
3. No coupling to domain/shared models

---

## Example 3: Domain Listener - How We Actually Implemented It

### ✅ Good Practice (What We Did - Domain Listener)

```python
# domain/websocket/listener.py - OUR IMPLEMENTATION ✅
# ✅ NO infrastructure imports!

from .factories import ArticleFactory
from .validators import ArticleValidator
from .models import Article  # ✅ Only domain models

class WebSocketDomainListener:
    def __init__(self):
        self.factory = ArticleFactory()
        self.validator = ArticleValidator()
        self.event_bus = get_event_bus()
    
    async def _handle_article_received(self, event_type: str, event_data: dict):
        """
        Handle infrastructure ArticleReceivedEvent.
        
        Process:
        1. Extract raw data from infrastructure event
        2. Validate raw data (business rule #1)
        3. Transform to domain model via Factory
        4. Validate domain model (business rule #2)
        5. Publish domain event
        """
        # 1. Extract raw infrastructure data
        article_data = event_data.get("article_data")  # ✅ Raw dict
        
        # 2. Validate raw data
        if not self.validator.is_valid_article_data(article_data):
            logger.warning("Invalid article data rejected at domain boundary")
            return  # ✅ Reject invalid data
        
        # 3. Transform to domain model
        domain_article = self.factory.create_from_dict(article_data)
        
        if not domain_article:
            logger.warning("Failed to create domain article")
            return
        
        # 4. Validate domain model
        if not self.validator.is_valid_domain_article(domain_article):
            logger.warning("Domain article validation failed")
            return
        
        # 5. Publish domain event
        domain_event = ArticleReceivedDomainEvent(
            article=domain_article.to_dict(),  # ✅ Domain model as dict
            received_at=event_data["received_at"]
        )
        await self.event_bus.publish("Domain.ArticleReceived", domain_event.model_dump())
```

**This is correct!** ✅ Domain listener:
- Has no infrastructure imports
- Validates at boundary
- Transforms infrastructure format → domain format
- Publishes domain events

**Current Issue**: The listener expects `article_data` but infrastructure currently sends `article` (StandardizedArticle). We need to fix infrastructure to send raw data.

---

## Example 4: Domain Models vs Infrastructure Models

### Domain Model (What We Created - ✅ GOOD)

```python
# domain/websocket/models.py - OUR IMPLEMENTATION ✅

class Article(BaseModel):
    """
    Domain model - pure business logic, immutable, no infrastructure concerns.
    
    This represents the BUSINESS CONCEPT of an article, not how it's stored/transmitted.
    """
    id: str  # Business identifier: "source:source_id"
    source: ArticleSource  # Domain enum: BENZINGA
    source_id: str
    title: str
    tickers: FrozenSet[str]  # ✅ Immutable set
    published_at: datetime
    # ... business fields only
    
    model_config = {"frozen": True}  # ✅ Immutable
    
    def has_tickers(self) -> bool:  # ✅ Business logic method
        """Check if article has any tickers."""
        return len(self.tickers) > 0
    
    def is_recent(self, hours: int = 1) -> bool:  # ✅ Business logic method
        """Check if article was published within specified hours."""
        # ...
```

**This is correct!** ✅ Domain model:
- Immutable (frozen)
- Business logic methods
- No infrastructure concerns

### Infrastructure Model (What We Currently Have - ⚠️ NEEDS REVIEW)

```python
# models/base_models.py - CURRENT (Shared Model)
class StandardizedArticle(BaseModel):
    """Standardized model for news articles from any source."""
    source: NewsSource
    source_id: str
    title: str
    tickers: List[str]  # ⚠️ Not immutable
    published: datetime  # ⚠️ Different field name than domain
    raw_data: Dict[str, Any]  # ⚠️ Infrastructure concern (raw data)
    
    # ⚠️ This is shared between infrastructure and domain
```

**Problem**: This is a "shared" model - used by both infrastructure and domain. 

**Better Approach**: 
- Infrastructure should have its own model format (or just use Dict)
- Domain should have its own model (which we created)
- No shared models

---

## Example 5: The Transformation (Mapper)

### ✅ Good Practice (What We Implemented)

```python
# domain/websocket/mappers.py - OUR IMPLEMENTATION ✅

class ArticleMapper:
    """Maps infrastructure format → domain format."""
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> Optional[Article]:
        """
        Transform raw infrastructure data → domain Article.
        
        This is the ONLY place that knows both formats.
        """
        # Infrastructure format might have:
        # - "benzinga_id" field
        # - "headline" field
        # - "published" field (different name)
        
        # Domain format has:
        # - "id" field (composite: "source:source_id")
        # - "title" field
        # - "published_at" field (different name)
        
        # Map infrastructure → domain
        source = ArticleMapper._map_source(data.get("source", ""))
        source_id = data.get("source_id") or data.get("benzinga_id")  # Handle different infra formats
        
        return Article(
            id=f"{source.value}:{source_id}",  # ✅ Composite ID
            source=source,  # ✅ Domain enum
            source_id=source_id,
            title=data.get("title") or data.get("headline"),  # ✅ Handle different field names
            published_at=data.get("published_at") or data.get("published"),  # ✅ Map field names
            tickers=frozenset(data.get("tickers", [])),  # ✅ Convert to immutable
            # ...
        )
    
    @staticmethod
    def _map_source(source: str) -> ArticleSource:
        """Map infrastructure source string → domain enum."""
        if "benzinga" in source.lower():
            return ArticleSource.BENZINGA
        return ArticleSource.BENZINGA
```

**This is correct!** ✅ Mapper:
- Knows both infrastructure and domain formats
- Handles field name differences
- Handles type conversions (list → frozenset)
- Is the ONLY place that knows both formats

---

## What Needs to Be Fixed

### Fix 1: Infrastructure Event Format

**Current**:
```python
class ArticleReceivedEvent(BaseModel):
    article: StandardizedArticle  # ❌
```

**Should be**:
```python
class ArticleReceivedEvent(BaseModel):
    article_data: Dict[str, Any]  # ✅
```

### Fix 2: Infrastructure Publishing

**Current**:
```python
standardized = convert_benzinga_to_standardized(article_data)
event = ArticleReceivedEvent(article=standardized)  # ❌
```

**Should be**:
```python
event = ArticleReceivedEvent(article_data=raw_article)  # ✅
```

### Fix 3: Domain Listener Expectation

**Current** (what we have):
```python
article_data = event_data.get("article")  # ⚠️ Expects "article"
```

**Should be** (already correct structure, just need to update key):
```python
article_data = event_data.get("article_data")  # ✅ Expects "article_data"
```

---

## Summary: What We Did Right vs What Needs Fixing

### ✅ What We Did Right

1. **Domain Models** - Created pure domain Article model (immutable, business logic)
2. **Domain Validators** - Created ArticleValidator (validates at boundary)
3. **Domain Mappers** - Created ArticleMapper (transforms infra → domain)
4. **Domain Factories** - Created ArticleFactory (creates domain objects)
5. **Domain Events** - Created domain events (ArticleReceivedDomainEvent)
6. **Domain Listener** - Created WebSocketDomainListener (subscribes to infra, publishes domain)
7. **No Infrastructure Imports in Domain** - Domain has zero infrastructure dependencies ✅

### ⚠️ What Needs Fixing

1. **Infrastructure Event Format** - Still uses StandardizedArticle, should use raw Dict
2. **Infrastructure Publishing** - Transforms to StandardizedArticle, should publish raw data
3. **Domain Listener Key** - Expects "article", should expect "article_data" (easy fix)

---

## The Complete Flow (After Fixes)

```
┌──────────────────────────────────────────────────────────────┐
│           INFRASTRUCTURE LAYER                                 │
│                                                               │
│  WebSocket receives: {"benzinga_id": "123", "headline": ...} │
│         ↓                                                     │
│  Infrastructure publishes:                                    │
│    ArticleReceivedEvent(                                      │
│      article_data={...raw_data...},  # ✅ Raw format          │
│      received_at=datetime.now()                               │
│    )                                                          │
│         ↓                                                     │
│  Event Bus: "ArticleReceived"                                │
└──────────────────────────────────────────────────────────────┘
                          ║
                          ║ PROTOCOL:
                          ║ - article_data: Dict[str, Any]
                          ║ - received_at: datetime
                          ║
┌──────────────────────────────────────────────────────────────┐
│                 DOMAIN LAYER                                  │
│                                                               │
│  Domain Listener receives event                               │
│         ↓                                                     │
│  1. Extract: article_data = event["article_data"]            │
│         ↓                                                     │
│  2. Validate: ArticleValidator.is_valid_article_data()       │
│         ↓                                                     │
│  3. Transform: ArticleMapper.from_dict() → Article           │
│         ↓                                                     │
│  4. Validate: ArticleValidator.is_valid_domain_article()     │
│         ↓                                                     │
│  5. Publish: ArticleReceivedDomainEvent(                     │
│        article=domain_article.to_dict()  # ✅ Domain format  │
│     )                                                         │
│         ↓                                                     │
│  Event Bus: "Domain.ArticleReceived"                         │
└──────────────────────────────────────────────────────────────┘
                          ║
                          ║ PROTOCOL:
                          ║ - article: Dict[str, Any] (domain Article)
                          ║ - received_at: datetime
                          ║
┌──────────────────────────────────────────────────────────────┐
│              SERVICES/USE CASES LAYER                         │
│                                                               │
│  Services subscribe to "Domain.ArticleReceived"              │
│  Work with domain Article model (pure business logic)        │
└──────────────────────────────────────────────────────────────┘
```

**Key Points:**
1. Infrastructure publishes raw data ✅
2. Domain validates and transforms ✅
3. Domain publishes domain models ✅
4. Services only see domain models ✅

