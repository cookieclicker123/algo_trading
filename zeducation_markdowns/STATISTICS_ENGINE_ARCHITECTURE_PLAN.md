# Statistics Engine Architecture Plan

## Overview

A new **Statistics Microservice** that continuously tracks trading performance and missed opportunities to improve algorithm recall and signal quality. The service operates in real-time throughout the day and generates daily reports at session end times.

---

## Goals

### 1. **Recall Statistics Engine**
**Purpose**: Identify valuable news articles that were filtered out but could have been profitable.

**What it tracks**:
- **ALL articles** with tickers that come through WebSocket (regardless of classification/trading decisions)
- For each ticker, verify if it moved **5%+ during the session** (using session high, not just close)
- Collect metadata: industry, sector, exchange, market cap, price
- Identify which pre-filters blocked the article (no_tickers, low_price, low_market_cap, nbbo_unavailable, etc.)

**Output**: Daily JSON files showing "missed opportunities" - articles that had profitable price action but were filtered.

### 2. **Signal Statistics Engine**
**Purpose**: Analyze trades that lost money to identify root causes and improve entry/exit strategies.

**What it tracks**:
- All executed trades (entry + exit)
- Price action during the trade (entry to exit)
- Price action for **15 minutes after exit** (to identify better exit opportunities)
- Entry/exit quality metrics (liquidity, spread, ladder attempts, fill prices)
- News quality analysis via LLM (at session end)
- Filter analysis: Did phase 1 filters correctly identify bad trades?

**Output**: Daily JSON files showing trade performance, exit quality, and LLM-suggested filter improvements.

---

## Architecture Design

### Microservice Structure (Following Existing Patterns)

```
services/
└── statistics/
    ├── __init__.py                    # StatisticsMicroservice container
    ├── recall_engine/
    │   ├── __init__.py
    │   ├── recall_collector.py        # Real-time data collection
    │   ├── recall_analyzer.py         # Session-end analysis
    │   └── recall_models.py           # Domain models
    ├── signal_engine/
    │   ├── __init__.py
    │   ├── signal_collector.py        # Real-time trade tracking
    │   ├── signal_analyzer.py         # Session-end analysis
    │   └── signal_models.py           # Domain models
    ├── price_tracker.py               # Tracks 15-min post-exit price action
    ├── session_manager.py             # Manages session timing and report generation
    └── data_fetcher.py                # Fetches intraday bars, metadata from Alpaca/yfinance
```

### Infrastructure Layer

```
infra/
└── statistics/
    ├── __init__.py
    ├── service.py                     # StatisticsInfrastructureService
    ├── storage/
    │   └── statistics_repository.py   # JSON file storage
    └── llm_analyzer.py                # LLM analysis for signal engine
```

### Domain Layer

```
domain/
└── statistics/
    ├── __init__.py
    ├── models.py                      # RecallRecord, SignalRecord, SessionReport
    ├── events.py                      # StatisticsCollectedEvent, ReportGeneratedEvent
    ├── listener.py                    # StatisticsDomainListener
    └── event_protocols.py             # Event subscriber/publisher protocols
```

---

## Data Collection Requirements

### Recall Engine - Real-Time Collection

**Subscribes to**: `Domain.ArticleReceived` events

**For each article with tickers**:
```python
RecallRecord = {
    "article_id": "benzinga:49304149",
    "tickers": ["AAPL"],
    "session": "premarket",  # Which session the article arrived in
    "published_at": "2025-12-10T13:30:00Z",
    "received_at": "2025-12-10T13:30:01Z",
    "title": "Article title...",
    
    # Filter status (why wasn't it traded?)
    "filter_reasons": [
        "no_tickers",  # Article had no tickers
        "low_price",   # Price < $5
        "low_market_cap",  # Market cap < $500M
        "nbbo_unavailable",  # No NBBO in extended hours
        "not_classified_imminent",  # AI didn't classify as IMMINENT
        "traded"  # Actually was traded (for comparison)
    ],
    
    # Stock metadata (collected on-demand or cached)
    "stock_metadata": {
        "ticker": "AAPL",
        "industry": "Consumer Electronics",
        "sector": "Technology",
        "exchange": "NASDAQ",
        "market_cap_millions": 3500000,
        "price_at_article_time": 175.50
    },
    
    # Session price action (collected at session end)
    "session_price_action": {
        "session": "premarket",
        "session_start_time": "2025-12-10T04:00:00-05:00",  # ET timezone
        "session_end_time": "2025-12-10T09:30:00-05:00",
        "session_open_price": 175.50,  # First trade in session
        "session_high_price": 184.27,  # Highest price during session
        "session_low_price": 174.20,
        "session_close_price": 179.80,
        "max_move_percent": 5.0,  # (high - open) / open * 100
        "achieved_5_percent": true,  # Did it hit 5%+?
        "time_to_high_minutes": 23,  # Minutes from session start to high
        "intraday_bars": [...]  # Optional: minute bars for the session
    }
}
```

### Signal Engine - Real-Time Collection

**Subscribes to**: 
- `Domain.TradeExecuted` (entry and exit)
- `Domain.TradeFailed`

**For each trade**:
```python
SignalRecord = {
    "trade_id": "unique-trade-id",
    "article_id": "benzinga:49304149",
    "ticker": "AAPL",
    "session": "premarket",
    
    # Entry details
    "entry": {
        "executed_at": "2025-12-10T13:30:05Z",
        "fill_price": 175.50,
        "shares": 2.0,
        "entry_cost": 351.00,
        "ladder_attempts": 3,
        "spread_at_entry": 0.05,
        "nbbo_at_entry": {"bid": 175.48, "ask": 175.53, "mid": 175.505},
        "distance_to_mid": -0.005,
        "liquidity_quality": "high"  # Based on spread, attempts
    },
    
    # Exit details
    "exit": {
        "executed_at": "2025-12-10T13:35:18Z",
        "fill_price": 179.80,
        "shares": 2.0,
        "exit_proceeds": 359.60,
        "ladder_attempts": 1,
        "spread_at_exit": 0.04,
        "nbbo_at_exit": {"bid": 179.78, "ask": 179.82, "mid": 179.80},
        "distance_to_mid": 0.00,
        "liquidity_quality": "high"
    },
    
    # Trade outcome
    "outcome": {
        "pnl": 8.60,
        "pnl_percent": 2.45,
        "duration_minutes": 5.22,
        "profitable": true
    },
    
    # 15-minute post-exit tracking (background task)
    "post_exit_price_action": {
        "exit_time": "2025-12-10T13:35:18Z",
        "tracking_end_time": "2025-12-10T13:50:18Z",
        "bars": [
            {"timestamp": "2025-12-10T13:36:00Z", "high": 180.20, "low": 179.50, "close": 179.90},
            {"timestamp": "2025-12-10T13:37:00Z", "high": 181.50, "low": 179.80, "close": 181.20},
            # ... 15 minutes of minute bars
        ],
        "max_price_after_exit": 182.40,
        "max_potential_pnl_if_held": 13.80,  # (182.40 - 175.50) * 2
        "optimal_exit_time": "2025-12-10T13:42:00Z",  # When max price occurred
        "exit_quality": "early",  # early, optimal, late
        "missed_profit": 5.20  # max_potential - actual_pnl
    },
    
    # Stock metadata
    "stock_metadata": {
        "ticker": "AAPL",
        "industry": "Consumer Electronics",
        "sector": "Technology",
        "exchange": "NASDAQ"
    },
    
    # Filter analysis (what passed phase 1?)
    "phase1_filters_passed": {
        "had_tickers": true,
        "price_above_5": true,
        "market_cap_above_500m": true,
        "nbbo_available": true,
        "classification": "IMMINENT"
    }
}
```

---

## Session Management & Timing

### UK Time Schedule (Market sessions are in ET, but reports generate in UK time)

**Premarket Session**:
- Session: 4:00 AM - 9:30 AM ET (9:00 AM - 2:30 PM UK)
- Report generation: **2:30 PM UK** (9:30 AM ET - market open)
- Report ready: **2:35 PM UK**

**Market Hours Session**:
- Session: 9:30 AM - 4:00 PM ET (2:30 PM - 9:00 PM UK)
- Report generation: **9:00 PM UK** (4:00 PM ET - market close)
- Report ready: **9:05 PM UK**

**Postmarket Session**:
- Session: 4:00 PM - 8:00 PM ET (9:00 PM - 1:00 AM UK)
- Report generation: **1:00 AM UK** (8:00 PM ET - postmarket close)
- Report ready: **1:05 AM UK**

### Session Manager Design

```python
class SessionManager:
    """
    Manages session timing and triggers report generation.
    
    Responsibilities:
    - Track current market session (premarket, market, postmarket)
    - Schedule background tasks for session-end analysis
    - Coordinate real-time data collection
    - Trigger report generation at correct times
    """
    
    async def start(self):
        # Start real-time collectors
        # Schedule session-end tasks
        # Run in separate thread with own event loop
```

---

## Data Storage Structure

### JSON File Organization

```
tmp/statistics/
├── recall/
│   └── 2025/
│       └── 12/
│           └── week_49/
│               ├── 2025-12-10_premarket.json
│               ├── 2025-12-10_market.json
│               └── 2025-12-10_postmarket.json
└── signal/
    └── 2025/
        └── 12/
            └── week_49/
                ├── 2025-12-10_premarket.json
                ├── 2025-12-10_market.json
                └── 2025-12-10_postmarket.json
```

### File Format

```json
{
  "session": "premarket",
  "date": "2025-12-10",
  "session_start": "2025-12-10T04:00:00-05:00",
  "session_end": "2025-12-10T09:30:00-05:00",
  "report_generated_at": "2025-12-10T14:30:00+00:00",
  "summary": {
    "total_articles_tracked": 247,
    "articles_with_5_percent_move": 12,
    "articles_traded": 3,
    "missed_opportunities": 9
  },
  "records": [
    {
      "article_id": "benzinga:49304149",
      "tickers": ["AAPL"],
      "filter_reasons": ["not_classified_imminent"],
      "session_price_action": {
        "max_move_percent": 6.2,
        "achieved_5_percent": true
      },
      "stock_metadata": {...}
    },
    ...
  ]
}
```

---

## Real-Time Data Collection

### Recall Collector (Runs Continuously)

**Subscribes to**: `Domain.ArticleReceived` events

**On article received**:
1. Check if article has tickers → if not, skip
2. Determine current session (premarket/market/postmarket)
3. Create `RecallRecord` with article metadata
4. Fetch stock metadata (industry, sector, exchange) - cache to avoid repeated API calls
5. Store record in memory (session-specific buffer)
6. At session end → analyze price action

### Signal Collector (Runs Continuously)

**Subscribes to**: `Domain.TradeExecuted` events

**On trade executed**:
1. If BUY → create new `SignalRecord`, start tracking
2. If SELL → find matching entry, complete record, start 15-min post-exit tracking
3. Store record in memory
4. At session end → analyze with LLM

### Price Tracker (Background Task)

**For 15-minute post-exit tracking**:
- Uses Alpaca `get_stock_bars()` with `TimeFrame.Minute`
- Polls every 60 seconds for 15 minutes
- Collects: high, low, close for each minute
- Calculates: max price after exit, optimal exit time, missed profit
- Updates `SignalRecord.post_exit_price_action`

---

## Session-End Analysis

### Recall Analyzer (Runs at Session End)

**For each `RecallRecord` collected during session**:
1. Fetch session price bars using Alpaca:
   ```python
   request = StockBarsRequest(
       symbol_or_symbols=[ticker],
       timeframe=TimeFrame.Minute,
       start=session_start_time,
       end=session_end_time
   )
   bars = market_data_client.get_stock_bars(request)
   ```

2. Calculate session metrics:
   - Session open price (first bar's open)
   - Session high price (max of all bars' high)
   - Session low price (min of all bars' low)
   - Session close price (last bar's close)
   - Max move percent: `(high - open) / open * 100`
   - Did it achieve 5%+? `max_move_percent >= 5.0`

3. Update `RecallRecord.session_price_action`

4. Generate summary statistics

5. Write JSON file

6. Send Telegram notification with report summary

### Signal Analyzer (Runs at Session End)

**For each `SignalRecord` collected during session**:

1. **Price Action Analysis**:
   - Calculate entry quality metrics
   - Calculate exit quality metrics
   - Analyze post-exit price action (already collected by Price Tracker)

2. **LLM Analysis** (using Groq API):
   - Input: Article title, content, filter decisions, trade outcome
   - Output: 
     - Why did the trade lose money? (bad entry, bad news, bad exit timing)
     - Should phase 1 filters have blocked this?
     - Suggested filter improvements

3. **Generate summary statistics**:
   - Total trades
   - Profitable vs losing trades
   - Average PnL
   - Exit quality breakdown (early/optimal/late)
   - Filter effectiveness

4. Write JSON file

5. Send Telegram notification with report summary

---

## Threading Model

### Separate Thread with Async Event Loop

**Note**: Event bus is NOT thread-safe by default. We need to:
1. Subscribe to events in main thread (collectors run in main thread)
2. Use separate thread only for session timing and report generation
3. Use thread-safe publishing pattern (similar to WebSocket service)

```python
class StatisticsMicroservice:
    """
    Statistics microservice - collects and analyzes trading performance.
    
    Architecture:
    - Real-time collectors: Run in MAIN thread (subscribe to events)
    - Session manager & reporting: Run in SEPARATE thread (scheduling, analysis)
    - Data sharing: Thread-safe queues or shared memory with locks
    """
    
    def __init__(self, event_bus, ...):
        self.event_bus = event_bus
        self._main_event_loop = None  # Store reference to main loop
        self._stats_thread = None
        self._stats_loop = None
        self._data_lock = threading.Lock()  # Thread-safe data access
        self._recall_records: Dict[str, List] = {}  # Session -> records
        self._signal_records: List = []
    
    async def start(self):
        # Store reference to main event loop
        self._main_event_loop = asyncio.get_running_loop()
        
        # Start real-time collectors in MAIN thread
        await self.recall_collector.start()
        await self.signal_collector.start()
        
        # Start background thread for session management and reporting
        self._stats_thread = threading.Thread(
            target=self._run_stats_loop,
            daemon=True
        )
        self._stats_thread.start()
    
    def _run_stats_loop(self):
        """Run session management and reporting in separate thread."""
        # Create new event loop for this thread
        self._stats_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._stats_loop)
        
        # Run session manager
        self._stats_loop.run_until_complete(self.session_manager.run())
    
    def _publish_to_main_thread(self, event_type: str, event_data: dict):
        """
        Publish events from stats thread to main thread event bus.
        
        Uses call_soon_threadsafe pattern (similar to WebSocket service).
        """
        if self._main_event_loop and self._main_event_loop.is_running():
            self._main_event_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    self.event_bus.publish(event_type, event_data)
                )
            )
```

**Alternative Approach (Recommended)**: 
- Keep collectors in main thread (event subscriptions work naturally)
- Only session manager runs in separate thread
- Use thread-safe data structures for sharing between threads

---

## API Requirements

### Alpaca API Usage

**For intraday bars (session price action)**:
```python
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

request = StockBarsRequest(
    symbol_or_symbols=["AAPL"],
    timeframe=TimeFrame.Minute,
    start=session_start_datetime,  # ET timezone
    end=session_end_datetime
)
bars = market_data_client.get_stock_bars(request)
```

**For stock metadata**:
- **Exchange**: Available from Alpaca `TradingClient.get_all_assets()` (already cached in TickerValidator)
- **Industry/Sector**: Use yfinance (already used for market cap)
  ```python
  import yfinance as yf
  ticker = yf.Ticker("AAPL")
  info = ticker.info
  industry = info.get("industry")
  sector = info.get("sector")
  ```

**Rate Limits**:
- Alpaca: 10,000 requests/minute
- Batch multiple tickers in single request
- Cache metadata to avoid repeated fetches

### yfinance Usage

Already installed and used for market cap. Can extend to fetch:
- `industry`
- `sector`
- Other metadata

---

## Event Integration

### Events Subscribed To

**Recall Engine**:
- `Domain.ArticleReceived` → Track all articles with tickers
- `Infrastructure.ClassificationSkipped` → Capture filter reasons
- `Domain.ArticleClassified` → Know if article was classified as IMMINENT
- `Domain.TradeExecuted` → Know if article was actually traded

**Signal Engine**:
- `Domain.TradeExecuted` → Track entry and exit trades
- `Domain.TradeFailed` → Track failed trades (for analysis)

### Events Published

**Infrastructure Events**:
- `StatisticsCollectedEvent` → Real-time collection updates
- `RecallReportGeneratedEvent` → Session-end recall report
- `SignalReportGeneratedEvent` → Session-end signal report

**Domain Events** (optional, for future use):
- `StatisticsAnalysisCompletedEvent`
- `FilterImprovementSuggestedEvent`

---

## LLM Analysis Integration

### Signal Engine LLM Analysis

**Prompt Template**:
```
Analyze this trade that lost money:

Article: {title}
Content: {content}
Entry Price: ${entry_price}
Exit Price: ${exit_price}
PnL: ${pnl} ({pnl_percent}%)

Filters Passed:
- Had tickers: {had_tickers}
- Price > $5: {price_above_5}
- Market cap > $500M: {market_cap_above_500m}
- NBBO available: {nbbo_available}
- Classification: {classification}

Exit Quality:
- Exited at: {exit_time}
- Optimal exit would have been: {optimal_exit_time}
- Missed profit: ${missed_profit}

Questions:
1. Why did this trade lose money? (bad entry, bad news quality, bad exit timing, other)
2. Should phase 1 filters have blocked this trade? Why or why not?
3. What filter adjustments would improve signal quality?
4. Rate the news quality (1-10) and explain why.
```

**Integration**: Use existing Groq client from classification microservice (or create separate instance).

---

## Statistics Collected - Detailed List

### Recall Engine Statistics

**Per Article**:
- Article ID, title, publication time
- Tickers associated
- Session when received
- Filter reasons (why wasn't it traded)
- Stock metadata (industry, sector, exchange, market cap, price)
- Session price action (open, high, low, close, max move %)
- Achieved 5% threshold? (boolean)
- Time to peak (minutes from session start)

**Aggregate (per session)**:
- Total articles tracked
- Articles with 5%+ moves
- Articles traded
- Missed opportunities count
- Filter breakdown (how many blocked by each filter)
- Industry/sector breakdown of missed opportunities

### Signal Engine Statistics

**Per Trade**:
- Trade ID, article ID, ticker
- Entry details (price, time, attempts, liquidity metrics)
- Exit details (price, time, attempts, liquidity metrics)
- Outcome (PnL, duration, profitable?)
- Post-exit price action (15-min tracking)
- Exit quality assessment (early/optimal/late)
- Missed profit calculation
- Stock metadata
- Phase 1 filter status
- LLM analysis results

**Aggregate (per session)**:
- Total trades
- Profitable vs losing trades
- Average PnL
- Exit quality distribution
- Filter effectiveness
- Industry/sector breakdown of losing trades

---

## Improvements to Your Plan

### 1. **Intraday High Detection**
**Your concern**: Need to know if price hit 5%+ at ANY point, not just close.

**Solution**: Use Alpaca `get_stock_bars()` with `TimeFrame.Minute`:
- Fetch all minute bars for the session
- Find maximum `high` across all bars
- Calculate: `(max_high - session_open) / session_open * 100`
- If >= 5.0, mark as `achieved_5_percent: true`

### 2. **Session Boundary Handling**
**Challenge**: Articles may arrive during one session but price action spans sessions.

**Solution**: Track price action for the session the article was received in. For articles arriving in premarket, check premarket price action only. This matches your trading strategy (trade in the session news arrives).

### 3. **Metadata Caching**
**Optimization**: Cache stock metadata (industry, sector, exchange) to avoid repeated API calls:
- Cache refreshed daily at market open
- Use existing `TickerValidator` cache for exchange info
- yfinance calls can be cached (metadata changes rarely)

### 4. **Thread Safety**
**Implementation**: Use `asyncio.Lock()` for:
- Writing to in-memory buffers (recall_records, signal_records)
- File I/O operations (JSON writing)
- Event bus publishing from stats thread

### 5. **Background Task Scheduling**
**Implementation**: Use `asyncio` scheduling within the stats thread:
```python
async def session_manager_loop():
    while True:
        current_session = get_current_session()
        
        # Wait for session end
        next_session_end = get_next_session_end_time()
        await asyncio.sleep(seconds_until(next_session_end))
        
        # Generate reports for completed session
        await generate_recall_report(current_session)
        await generate_signal_report(current_session)
        
        # Wait briefly before checking next session
        await asyncio.sleep(60)
```

### 6. **Data Persistence Strategy**
**Approach**: 
- **In-memory buffers**: Collect records during session (fast, thread-safe)
- **JSON files**: Write at session end (durable, queryable)
- **Optional future**: Database storage for long-term analysis

### 7. **15-Minute Post-Exit Tracking**
**Implementation**:
- When exit trade executes → spawn background task
- Task polls Alpaca every 60 seconds for 15 minutes
- Collects minute bars and updates `SignalRecord`
- If system restarts, incomplete tracking is lost (acceptable - trades are rare)

### 8. **Error Handling & Resilience**
**Approach**:
- API failures → log and continue (don't block real-time collection)
- Missing data → mark record as incomplete, still include in report
- Thread crashes → main application continues running (daemon thread)

---

## Integration Points

### 1. **Event Bus Integration**
- Stats thread subscribes to events via thread-safe event bus
- Publishes events back to main thread when needed
- Uses existing `AsyncEventBus` with thread-safe publishing methods

### 2. **Brokerage Dependency**
- Needs `AlpacaConnectionManager` for market data client
- Shares connection with brokerage microservice
- Can reuse `AlpacaQuoteFetcher` for real-time prices

### 3. **Storage Dependency**
- Needs access to stored articles (for LLM analysis)
- Can use existing `StorageQueryService`

### 4. **Classification Dependency**
- Needs Groq API client for LLM analysis
- Can reuse or create separate instance

---

## Future Enhancements (Not in Initial Implementation)

1. **Microstructure Filters** (mentioned by user):
   - Volume spike detection (vs 20-day rolling average)
   - Volatility spike detection
   - News becomes co-signal, not primary signal

2. **Database Storage**:
   - Long-term querying and analysis
   - Trend analysis across weeks/months

3. **Real-Time Dashboard**:
   - Web UI showing live statistics
   - Filter effectiveness metrics

4. **Automated Filter Tuning**:
   - ML model learns from statistics
   - Auto-adjusts filter thresholds

---

## Questions to Clarify

1. **Article Source**: Should we track ALL articles with tickers, or only those that pass some minimum threshold (e.g., has tickers)?

2. **5% Threshold**: Should this be configurable? Or hardcoded to 5%?

3. **Session Overlap**: If an article arrives at 9:25 AM ET (end of premarket), should we check premarket price action or wait for market hours price action?

4. **Multiple Tickers**: If an article has multiple tickers (e.g., ["AAPL", "MSFT"]), should we track price action for ALL tickers or just the primary one?

5. **Post-Exit Tracking Failure**: If the 15-minute tracking fails (API error, system restart), should we still include the trade in the report (without post-exit data)?

6. **LLM Analysis Timing**: Should LLM analysis run for ALL trades (profitable and losing) or only losing trades?

7. **Report Format**: Do you want the Telegram notification to include:
   - Full report (might be very long)?
   - Summary only with link to JSON file?
   - Key insights extracted from LLM analysis?

8. **Historical Data**: Should we reprocess old sessions if we add new analysis features, or only process going forward?

---

## Proposed Implementation Phases

### Phase 1: Foundation (Week 1)
- Create StatisticsMicroservice structure
- Implement Recall Collector (real-time article tracking)
- Implement basic session manager
- JSON file storage

### Phase 2: Price Action Analysis (Week 1-2)
- Implement intraday bar fetching (session price action)
- Calculate 5% threshold detection
- Session-end recall report generation
- Telegram notifications

### Phase 3: Signal Engine (Week 2)
- Implement Signal Collector (trade tracking)
- Implement 15-minute post-exit price tracking
- Session-end signal report generation

### Phase 4: LLM Integration (Week 2-3)
- Integrate Groq API for news quality analysis
- Generate filter improvement suggestions
- Enhanced report formatting

### Phase 5: Metadata Collection (Week 3)
- Industry/sector/exchange metadata
- Enhanced filtering and analysis
- Final report formatting

---

## Risk Assessment

**Low Risk**:
- Real-time collection (simple event subscription)
- JSON file storage (straightforward)
- Session timing (well-defined schedules)

**Medium Risk**:
- Thread synchronization (needs careful locking)
- API rate limits (need batching/caching)
- Intraday bar data availability (some stocks may not have bars in extended hours)

**High Risk**:
- 15-minute post-exit tracking (background tasks, system restarts)
- LLM analysis timing (may be slow, need async handling)

---

## Next Steps

1. **Review and approve this plan**
2. **Clarify questions above**
3. **Start with Phase 1 implementation**
4. **Iterate based on early results**

Would you like me to proceed with implementation, or would you like to discuss any aspects of this plan first?
