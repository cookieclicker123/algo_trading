# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NewsFlash is a high-frequency news trading system that monitors financial news in real-time via Benzinga WebSocket, classifies articles using Groq AI, and automatically executes trades via Alpaca brokerage with extended-hours support. Telegram notifications are sent for trading activity.

## Commands

```bash
# Setup
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Run server
python -m uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload

# Run standalone (no HTTP server)
python -m src.main

# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/unit/statistics/test_recall_engine.py -v

# Format and lint
black src/ tests/ && isort src/ tests/ && mypy src/
```

Always use `python -m pytest` (not bare `pytest`) to ensure the virtual environment is used correctly.

## Architecture

### Event-Driven Design
The system uses an AsyncEventBus for pub/sub communication between microservices. Events flow through the system:
1. WebSocket receives article → publishes `ArticleReceived`
2. Classification service classifies → publishes `ArticleClassified`
3. AutoTradeService decides to trade → publishes `TradeRequested`
4. Brokerage executes → publishes `TradeExecuted`
5. Notification service sends Telegram

### Layered Structure
```
src/newsflash/
├── api/          # FastAPI routes and HTTP interface
├── use_cases/    # Business logic orchestration
├── domain/       # Business models, event listeners, factories
├── infra/        # Implementation (Alpaca, Groq, Benzinga, storage)
├── services/     # DI containers and microservice wiring
├── shared/       # Event bus, event types, statistics engines
├── config/       # Settings (environment-driven)
└── utils/        # Logging, session detection, utilities
```

### Dependency Injection
Uses `dependency-injector` framework. The composition root is at `src/newsflash/services/composition_root.py` which wires all dependencies. Services are registered in `src/newsflash/services/containers/application.py`.

### Microservices
Five independent microservices coordinated via events:
- **Storage**: Article storage/retrieval (JSON files)
- **Classification**: AI classification (Groq)
- **Brokerage**: Trade execution (Alpaca)
- **Notification**: Telegram notifications
- **WebSocket**: News polling (Benzinga)

Each microservice has: infrastructure service, domain listener, and use cases.

### Statistics Engines
Background engines track trading performance in `src/newsflash/shared/statistics/`:
- **RecallStatsEngine**: Missed trading opportunities
- **SignalStatsEngine**: Executed trades with entry/exit prices
- **FailedTradeStatsEngine**: Failed trades

Statistics stored in `tmp/statistics/{engine_type}/{year}/{month}/week_{week}/{day}/{session}/`

### Extended Hours Trading
Market session detection in `src/newsflash/utils/brokerage/session_detector.py`:
- Premarket: 4 AM - 9:30 AM ET
- Market Hours: 9:30 AM - 4 PM ET
- Postmarket: 4 PM - 8 PM ET

Extended-hours executor (`trade_executor_extended_hours.py`) uses ladder-based limit order strategy.

## Key Patterns

### Base Classes
- **BaseFactory** (`domain/base_factory.py`): Standardized model creation from dicts
- **BaseDomainListener** (`domain/base_listener.py`): Standardized event handling with validation

### Async Throughout
All services, use cases, and event handlers are async. File operations use `aiofiles`. Statistics repository uses per-file async locks for concurrent writes.

### Configuration
Environment variables in `.env` control all settings. Key toggles:
- `PAPER_TRADING`: Use paper trading (default: true)
- `AUTO_TRADING_ENABLED`: Enable automatic trade execution
- `CLASSIFICATION_ENABLED`: Enable AI classification
- `TELEGRAM_ENABLED`: Enable Telegram notifications
- `BENZINGA_WEBSOCKET_ENABLED`: Enable news feed
