# News Trading System Implementation Plan

## Instructions for You

### 1. Polygon.io Subscription Setup
1. **Go to [polygon.io/pricing](https://polygon.io/pricing)**
2. **Subscribe to the "Starter" plan** ($99/month) - this includes:
   - Unlimited API requests (within rate limits)
   - Access to Benzinga news data
   - Real-time market data
3. **After payment, retrieve your API key from the dashboard**
4. **Create `.env` file in project root** with:
   ```
   POLYGON_API_KEY=your_api_key_here
   ```

### 2. Environment Setup Commands
Run these commands in your terminal:
```bash
cd /Users/seb/dev/newsflash
uv venv .venv
source .venv/bin/activate
```

### 3. What This System Will Do
- Poll Polygon.io's Benzinga news API 20 times per second (every 50ms)
- Process ~1000 headlines per day with minimal latency
- Maintain <2 second latency from publication to processing
- Run continuously on FastAPI with automatic restart capabilities
- Implement robust error handling and exponential backoff

---

## Technical Implementation Plan

### Phase 1: Core Infrastructure Setup

#### 1.1 Project Structure
```
newsflash/
├── .env                          # API keys (gitignored)
├── .gitignore                    # Exclude .env, __pycache__, .venv
├── pyproject.toml                # Dependencies and project config
├── main.py                       # Main application entry point
├── src/
│   ├── __init__.py
│   ├── news_poller.py           # Core polling logic
│   ├── models.py                # Pydantic data models
│   ├── health_checker.py        # Diagnostic monitoring
│   └── config.py                # Configuration management
├── tests/
│   ├── __init__.py
│   ├── test_news_poller.py      # Unit tests
│   └── test_health_checker.py   # Health check tests
├── specs/                        # Documentation and plans
└── logs/                         # Application logs
```

#### 1.2 Dependencies (pyproject.toml)
```toml
[project]
name = "newsflash"
version = "0.1.0"
description = "High-frequency news trading system using Polygon.io"
requires-python = ">=3.9"
dependencies = [
    "httpx>=0.25.0",              # Async HTTP client
    "python-dotenv>=1.0.0",       # Environment variable loading
    "fastapi>=0.104.0",           # Web framework for health endpoints
    "uvicorn[standard]>=0.24.0",  # ASGI server
    "pydantic>=2.5.0",            # Data validation
    "asyncio-mqtt>=0.16.0",       # For future message queuing
    "structlog>=23.2.0",          # Structured logging
    "tenacity>=8.2.0", 
    "polygon-api-client>=2.0.0",           # Retry logic with exponential backoff
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "black>=23.0.0",
    "isort>=5.12.0",
    "mypy>=1.7.0",
]
```

### Phase 2: Core Polling Engine

#### 2.1 News Polling Architecture
- **Polling Frequency**: 20 requests/second (50ms intervals)
- **Rate Limit Safety**: Well below 100 req/s limit
- **Latency Target**: <2 seconds from publication to processing
- **Error Handling**: Exponential backoff with jitter
- **State Management**: Persistent timestamp tracking

#### 2.2 Key Components

**News Poller (`src/news_poller.py`)**:
- Async polling loop with `updated_gt` parameter
- Connection pooling with `httpx.AsyncClient`
- Event-driven article processing
- Graceful shutdown handling

**Data Models (`src/models.py`)**:
- Pydantic models for API responses
- Validation for article structure
- Timestamp handling utilities

**Health Checker (`src/health_checker.py`)**:
- API connectivity monitoring
- Response time tracking
- Error rate monitoring
- Automatic alerting system

### Phase 3: API Integration Details

#### 3.1 Polygon.io News Endpoint
```
GET https://api.polygon.io/benzinga/v2/news
```

**Parameters**:
- `apiKey`: Your Polygon API key
- `updated_gt`: Unix timestamp (critical for delta polling)
- `limit`: 100 (maximum articles per request)
- `sort`: "updated" (process chronologically)
- `order`: "asc" (oldest first)

#### 3.2 Rate Limiting Strategy
- **Target**: 20 requests/second
- **Safety Margin**: 80% below limit
- **Burst Handling**: Exponential backoff on 429 errors
- **Recovery**: Automatic retry with increasing delays

#### 3.3 Error Handling Matrix
| HTTP Code | Action | Backoff |
|-----------|--------|---------|
| 200 | Process normally | None |
| 429 | Rate limited | 1s, 2s, 4s, 8s... |
| 500-599 | Server error | 5s, 10s, 20s... |
| Network | Connection lost | 1s, 2s, 4s... |

### Phase 4: Deployment Architecture

#### 4.1 FastAPI Server Setup
- Health endpoint at `/health`
- Metrics endpoint at `/metrics`
- Graceful shutdown handling
- Process monitoring with automatic restart

#### 4.2 Monitoring & Diagnostics
- **Health Checks**: Every 30 seconds
- **API Response Time**: Tracked and logged
- **Error Rates**: Monitored with alerts
- **Memory Usage**: Tracked for leak detection

#### 4.3 Deployment Considerations
- **Server Location**: AWS us-east-1 (closest to Polygon servers)
- **Instance Type**: t3.medium or larger for consistent performance
- **Process Management**: systemd or supervisor for auto-restart
- **Logging**: Structured logs with rotation

### Phase 5: Testing Strategy

#### 5.1 Unit Tests
- API response parsing
- Error handling scenarios
- Rate limiting logic
- Timestamp management

#### 5.2 Integration Tests
- End-to-end polling flow
- API connectivity
- Error recovery
- Performance benchmarks

#### 5.3 Load Testing
- Sustained 20 req/s for 24 hours
- Error injection testing
- Network failure simulation
- Memory leak detection

### Phase 6: Production Readiness

#### 6.1 Security
- API key rotation capability
- Environment variable validation
- Request logging (without sensitive data)
- Rate limit monitoring

#### 6.2 Reliability
- Circuit breaker pattern for API failures
- Dead letter queue for failed processing
- State persistence across restarts
- Automatic recovery mechanisms

#### 6.3 Performance
- Connection pooling optimization
- Response caching where appropriate
- Memory usage optimization
- CPU usage monitoring

---

## Critical Success Factors

### 1. Latency Optimization
- **Network**: Deploy in us-east-1 region
- **Polling**: 50ms intervals (20 req/s)
- **Processing**: Non-blocking async architecture
- **State**: In-memory timestamp tracking

### 2. Reliability
- **Uptime**: 99.9% target with auto-restart
- **Error Handling**: Comprehensive retry logic
- **Monitoring**: Real-time health checks
- **Recovery**: Automatic failover mechanisms

### 3. Scalability
- **Rate Limits**: Respect Polygon's 100 req/s limit
- **Processing**: Event-driven architecture
- **Memory**: Efficient data structures
- **CPU**: Async/await throughout

---

## Next Steps After Setup

1. **Implement core polling engine**
2. **Add comprehensive error handling**
3. **Create health monitoring system**
4. **Write unit and integration tests**
5. **Deploy to FastAPI server**
6. **Monitor and optimize performance**

This plan ensures you'll have a robust, high-performance news trading system that can reliably capture market-moving news with minimal latency.
