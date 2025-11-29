# Architecture Refactoring - Quick Summary

## The Problem

The codebase is currently:
- **Tightly coupled**: Services import infrastructure directly (websocket, IBKR, JSON files)
- **Hard to test**: Can't test business logic without mocking entire infrastructure
- **Mixed concerns**: Business logic mixed with data access and infrastructure code
- **Unclear contracts**: Services communicate through direct method calls
- **Too many classes**: Business logic in classes instead of pure functions
- **Brittle**: Changes ripple across multiple services

## The Solution

Refactor to clean architecture with:
1. **Infrastructure Layer**: Isolated microservices (websocket, brokerage, data)
2. **Domain Layer**: Pure business logic with protocols/interfaces
3. **Services Layer**: Cohesive functions using domain models
4. **Use Cases Layer**: Orchestration of workflows
5. **Event Bus**: Pub/sub communication between layers
6. **Repository Pattern**: Abstract data access
7. **Dependency Injection**: Used throughout

## 8-Chapter Refactoring Plan

### Chapter 1: Remove Unnecessary Code
**Goal**: Clean house - remove dead code, unused imports, redundant functionality

**What to do**: Audit all services, remove unused code, simplify complex classes

**Expected Outcome**: Cleaner, more focused codebase

---

### Chapter 2: Deduplicate Code
**Goal**: Extract common patterns into utilities and shared models

**What to do**: Create utility functions, consolidate repeated logic, shared Pydantic models

**Expected Outcome**: Less duplication, reusable utilities

---

### Chapter 3: Separate Data from Logic
**Goal**: Extract infrastructure into three microservices

**What to do**: 
- Create WebSocket microservice with event bus
- Create Brokerage microservice with event bus
- Create Data Persistence microservice with repository pattern

**Expected Outcome**: Infrastructure isolated from business logic

---

### Chapter 4: Create Domain Layer and Contracts
**Goal**: Establish domain models, protocols, and event bus

**What to do**:
- Create domain entities and value objects
- Define protocols/interfaces
- Implement event bus for pub/sub
- Create repository pattern with Unit of Work

**Expected Outcome**: Clear contracts between layers

---

### Chapter 5: Refactor Services
**Goal**: Transform services into cohesive business operations

**What to do**: Convert classes to functions, use domain models, subscribe to events

**Expected Outcome**: Services are pure business logic

---

### Chapter 6: Create Use Cases
**Goal**: Add orchestration layer for workflows

**What to do**: Create use cases that coordinate services, handle workflows

**Expected Outcome**: Clear workflow orchestration

---

### Chapter 7: Dependency Injection
**Goal**: Implement dependency injection system-wide

**What to do**: Use FastAPI dependencies, inject all services, make everything testable

**Expected Outcome**: Easy to test, easy to swap implementations

---

### Chapter 8: Advanced Patterns (Future)
**Goal**: Add advanced patterns for scalability

**What to do**: CQRS, Saga pattern, circuit breakers, etc.

**Expected Outcome**: Production-ready patterns

---

## Current vs. Target Architecture

### Current (Messy)
```
Services → Infrastructure (direct imports)
Services → Services (circular dependencies)
Services → Data (direct file access)
Business Logic + Infrastructure mixed together
```

### Target (Clean)
```
API → Use Cases → Services → Domain
                              ↕ Events ↕
                         Infrastructure
                         (microservices)
```

## Key Principles

1. **Pure Functions**: Services are functions, not classes
2. **Immutability**: Domain models are immutable
3. **Protocols over Classes**: Use interfaces, not concrete classes
4. **Event-Driven**: Communicate via events
5. **Dependency Inversion**: High-level doesn't depend on low-level
6. **Single Responsibility**: One thing per service/function
7. **Testability**: Easy to test without infrastructure

## Directory Structure

After refactoring:
```
src/newsflash/
├── api/              # FastAPI routes + dependencies
├── use_cases/        # Orchestration
├── services/         # Business operations (functions)
├── domain/           # Pure business logic
│   ├── entities/
│   ├── value_objects/
│   ├── events/
│   └── protocols/
├── infra/            # Three microservices
│   ├── websocket/
│   ├── brokerage/
│   └── persistence/
├── repositories/     # Data access abstraction
├── models/           # Pydantic models
└── utils/            # Pure utilities
```

## Progress Checklist

- [ ] Chapter 1: Remove Unnecessary Code
- [ ] Chapter 2: Deduplicate Code
- [ ] Chapter 3: Infrastructure Microservices
- [ ] Chapter 4: Domain Layer and Contracts
- [ ] Chapter 5: Refactor Services
- [ ] Chapter 6: Create Use Cases
- [ ] Chapter 7: Dependency Injection
- [ ] Chapter 8: Advanced Patterns (Future)

## Next Steps

**Start with Chapter 1** - Remove unnecessary code.

Focus on one chapter at a time. Each chapter has subchapters to break down work further.

For detailed information, see: `specs/architecture_refactoring_plan.md`

