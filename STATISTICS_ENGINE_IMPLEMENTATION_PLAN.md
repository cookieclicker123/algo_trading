# Statistics Engine - Final Implementation Plan

## Overview

Implementation of a Statistics Microservice with two engines (Recall & Signal) that continuously track trading performance and missed opportunities. The service operates in a separate thread with its own event loop, uses thread-safe data collection, and generates daily reports with LLM analysis at session end times.

**Output**: 6 JSON files per day (3 sessions × 2 engines) in `tmp/statistics/` with Telegram notifications.

---

## Chapter 1: Foundation & Infrastructure Setup

### 1.1 Create Statistics Microservice Structure

**Goal**: Establish directory structure following existing patterns

**Files to Create**:
```
src/newsflash/
├── services/statistics/
│   ├── __init__.py
│   ├── recall_engine/
│   │   ├── __init__.py
│   │   ├── recall_collector.py
│   │   ├── recall_analyzer.py
│   │   └── recall_models.py
│   ├── signal_engine/
│   │   ├── __init__.py
│   │   ├── signal_collector.py
│   │   ├── signal_analyzer.py
│   │   └── signal_models.py
│   ├── price_tracker.py
│   ├── session_manager.py
│   └── data_fetcher.py
├── infra/statistics/
│   ├── __init__.py
│   ├── service.py
│   ├── storage/
│   │   └── statistics_repository.py
│   └── llm_analyzer.py
└── domain/statistics/
    ├── __init__.py
    ├── models.py
    ├── events.py
    ├── listener.py
    └── event_protocols.py
```

**Tasks**:
- [ ] Create all directory structures
- [ ] Add empty `__init__.py` files
- [ ] Follow existing microservice naming conventions

---

### 1.2 Domain Models

**Goal**: Define core domain models for statistics tracking

**File**: `domain/statistics/models.py`

**Models to Implement**:

```python
# Recall Engine Models
class RecallRecord(BaseModel):
    """Record of an article that could have been traded."""
    article_id: str
    tickers: List[str]  # All tickers (track all if multiple)
    session: MarketSession
    published_at: datetime
    received_at: datetime
    title: str
    
    # Filter reasons (why wasn't it traded?)
    filter_reasons: List[str]  # e.g., ["low_price", "nbbo_unavailable", "not_classified_imminent"]
    
    # Stock metadata (one per ticker)
    stock_metadata: Dict[str, StockMetadata]  # ticker -> metadata
    
    # Session price action (one per ticker)
    session_price_action: Dict[str, SessionPriceAction]  # ticker -> price action

class StockMetadata(BaseModel):
    ticker: str
    industry: Optional[str]
    sector: Optional[str]
    exchange: str
    market_cap_millions: Optional[float]
    price_at_article_time: Optional[float]

class SessionPriceAction(BaseModel):
    session: MarketSession
    session_start_time: datetime
    session_end_time: datetime
    session_open_price: Optional[float]
    session_high_price: Optional[float]  # KEY: For 5% detection
    session_low_price: Optional[float]
    session_close_price: Optional[float]
    max_move_percent: Optional[float]  # (high - open) / open * 100
    achieved_5_percent: bool
    time_to_high_minutes: Optional[int]

# Signal Engine Models
class SignalRecord(BaseModel):
    """Record of a trade execution."""
    trade_id: str
    article_id: str
    tickers: List[str]  # All tickers from article
    traded_ticker: str  # Which ticker was actually traded
    session: MarketSession
    
    entry: EntryDetails
    exit: Optional[ExitDetails]  # None if trade still open
    outcome: Optional[TradeOutcome]
    
    post_exit_price_action: Optional[PostExitPriceAction]
    
    # Stock metadata (one per ticker)
    stock_metadata: Dict[str, StockMetadata]
    
    # Phase 1 filters that PASSED (to check if too lenient)
    phase1_filters_passed: Phase1FilterStatus

class EntryDetails(BaseModel):
    executed_at: datetime
    fill_price: float
    shares: float
    entry_cost: float
    ladder_attempts: int
    ladder_time_seconds: float  # Time spent in ladder
    spread_at_entry: Optional[float]
    nbbo_at_entry: Optional[Dict[str, float]]
    distance_to_mid: Optional[float]
    liquidity_quality: str

class ExitDetails(BaseModel):
    executed_at: datetime
    fill_price: float
    shares: float
    exit_proceeds: float
    ladder_attempts: int
    ladder_time_seconds: float  # Time spent in ladder
    spread_at_exit: Optional[float]
    nbbo_at_exit: Optional[Dict[str, float]]
    distance_to_mid: Optional[float]
    liquidity_quality: str

class TradeOutcome(BaseModel):
    pnl: float
    pnl_percent: float
    duration_minutes: float
    profitable: bool

class PostExitPriceAction(BaseModel):
    exit_time: datetime
    tracking_end_time: datetime
    bars: List[PriceBar]  # 15 minutes of minute bars
    max_price_after_exit: Optional[float]
    max_potential_pnl_if_held: Optional[float]
    optimal_exit_time: Optional[datetime]
    exit_quality: str  # "early", "optimal", "late"
    missed_profit: Optional[float]

class PriceBar(BaseModel):
    timestamp: datetime
    high: float
    low: float
    close: float
    open: float

class Phase1FilterStatus(BaseModel):
    """What phase 1 filters PASSED (to analyze if too lenient)."""
    had_tickers: bool
    price_above_5: bool
    market_cap_above_500m: bool
    nbbo_available: bool
    classification: str  # "IMMINENT", "NOT_IMMINENT", "SKIPPED"

# Report Models
class RecallSessionReport(BaseModel):
    session: MarketSession
    date: str  # YYYY-MM-DD
    session_start: datetime
    session_end: datetime
    report_generated_at: datetime
    
    summary: RecallSummary
    records: List[RecallRecord]
    llm_analysis: Optional[LLMAnalysis]

class SignalSessionReport(BaseModel):
    session: MarketSession
    date: str
    session_start: datetime
    session_end: datetime
    report_generated_at: datetime
    
    summary: SignalSummary
    records: List[SignalRecord]
    llm_analysis: Optional[LLMAnalysis]

class LLMAnalysis(BaseModel):
    batch_analyses: List[str]  # One per batch
    aggregated_analysis: str  # Final summary
    filter_improvements: List[str]  # Suggested improvements
    generated_at: datetime
```

**Tasks**:
- [ ] Implement all domain models with Pydantic BaseModel
- [ ] Add proper type hints
- [ ] Add field validators where needed
- [ ] Ensure immutability with `frozen=True` where appropriate

---

### 1.3 Domain Events

**Goal**: Define domain events for statistics collection and reporting

**File**: `domain/statistics/events.py`

**Events to Implement**:

```python
class RecallRecordCollectedEvent(BaseModel):
    """Published when a recall record is collected."""
    record: RecallRecord
    collected_at: datetime

class SignalRecordCollectedEvent(BaseModel):
    """Published when a signal record is collected."""
    record: SignalRecord
    collected_at: datetime

class StatisticsAnalysisRequestedEvent(BaseModel):
    """Request LLM analysis for a session report."""
    session: MarketSession
    date: str
    report_type: str  # "recall" or "signal"
    requested_at: datetime

class StatisticsAnalysisCompletedEvent(BaseModel):
    """Published when LLM analysis completes."""
    session: MarketSession
    date: str
    report_type: str
    llm_analysis: LLMAnalysis
    completed_at: datetime

class FilterImprovementSuggestedEvent(BaseModel):
    """Published when filter improvements are suggested."""
    session: MarketSession
    date: str
    improvements: List[str]
    suggested_at: datetime

class StatisticsReportGeneratedEvent(BaseModel):
    """Published when a session report is generated."""
    session: MarketSession
    date: str
    report_type: str
    file_path: str
    generated_at: datetime
```

**Tasks**:
- [ ] Implement all event models
- [ ] Add to `DomainEventType` enum in `shared/event_types.py`
- [ ] Ensure events are immutable (`frozen=True`)

---

### 1.4 Infrastructure Storage

**Goal**: JSON file storage for statistics reports

**File**: `infra/statistics/storage/statistics_repository.py`

**Methods to Implement**:

```python
class StatisticsRepository:
    """Thread-safe JSON file storage for statistics reports."""
    
    async def save_recall_report(self, report: RecallSessionReport) -> str:
        """Save recall report to JSON file. Returns file path."""
        # Path: tmp/statistics/recall/YYYY/MM/week_XX/YYYY-MM-DD_{session}.json
        # Use threading.Lock() for file I/O
    
    async def save_signal_report(self, report: SignalSessionReport) -> str:
        """Save signal report to JSON file. Returns file path."""
        # Path: tmp/statistics/signal/YYYY/MM/week_XX/YYYY-MM-DD_{session}.json
        # Use threading.Lock() for file I/O
    
    def _get_file_path(self, report_type: str, session: MarketSession, date: str) -> Path:
        """Calculate file path from report metadata."""
        # Extract year, month, week number from date
        # Create directory structure if needed
```

**Tasks**:
- [ ] Implement JSON serialization (using `model_dump_json()`)
- [ ] Create directory structure automatically
- [ ] Use `threading.Lock()` for thread-safe file writes
- [ ] Handle file I/O errors with retries

---

## Chapter 2: Data Collection Infrastructure

### 2.1 Data Fetcher

**Goal**: Fetch intraday bars, stock metadata from Alpaca/yfinance

**File**: `services/statistics/data_fetcher.py`

**Methods to Implement**:

```python
class StatisticsDataFetcher:
    """Fetches market data for statistics analysis."""
    
    def __init__(
        self,
        market_data_client: StockHistoricalDataClient,
        trading_client: TradingClient
    ):
        self.market_data_client = market_data_client
        self.trading_client = trading_client
    
    async def fetch_session_bars(
        self,
        ticker: str,
        session: MarketSession,
        session_start: datetime,
        session_end: datetime
    ) -> Optional[List[Bar]]:
        """
        Fetch minute bars for a session.
        
        Returns list of bars or None if unavailable.
        Uses Alpaca get_stock_bars with TimeFrame.Minute.
        """
    
    async def fetch_stock_metadata(
        self,
        ticker: str,
        article_time: datetime
    ) -> Optional[StockMetadata]:
        """
        Fetch stock metadata (industry, sector, exchange, market cap, price).
        
        Uses:
        - Alpaca for exchange info (from TradingClient.get_all_assets)
        - yfinance for industry, sector, market cap
        - Alpaca for price at article time
        """
    
    async def fetch_post_exit_bars(
        self,
        ticker: str,
        exit_time: datetime,
        duration_minutes: int = 15
    ) -> List[PriceBar]:
        """
        Fetch minute bars for N minutes after exit time.
        
        Polls every 60 seconds for duration_minutes.
        Returns list of PriceBar objects.
        """
```

**Tasks**:
- [ ] Implement Alpaca API calls (with error handling)
- [ ] Implement yfinance metadata fetching
- [ ] Add retry logic with exponential backoff
- [ ] Handle API failures gracefully (return None, log error)

---

### 2.2 Price Tracker

**Goal**: Background task to track 15-minute post-exit price action

**File**: `services/statistics/price_tracker.py`

**Methods to Implement**:

```python
class PriceTracker:
    """Tracks post-exit price action for trades."""
    
    def __init__(
        self,
        data_fetcher: StatisticsDataFetcher,
        event_bus: AsyncEventBus
    ):
        self.data_fetcher = data_fetcher
        self.event_bus = event_bus
        self._tracking_tasks: Dict[str, asyncio.Task] = {}
        self._lock = threading.Lock()
    
    async def start_tracking(
        self,
        trade_id: str,
        ticker: str,
        exit_time: datetime
    ) -> None:
        """
        Start background task to track price for 15 minutes after exit.
        
        Creates async task that:
        1. Polls every 60 seconds
        2. Fetches minute bars
        3. Updates SignalRecord.post_exit_price_action
        4. Completes after 15 minutes
        """
    
    async def _track_price_action(
        self,
        trade_id: str,
        ticker: str,
        exit_time: datetime
    ) -> None:
        """Background task implementation."""
    
    def _calculate_exit_quality(
        self,
        exit_price: float,
        post_exit_bars: List[PriceBar]
    ) -> Tuple[str, Optional[float], Optional[datetime]]:
        """
        Calculate exit quality metrics.
        
        Returns:
        - exit_quality: "early", "optimal", "late"
        - max_potential_pnl: Maximum PnL if held to optimal exit
        - optimal_exit_time: When optimal exit would have been
        """
```

**Tasks**:
- [ ] Implement async background tracking task
- [ ] Use `asyncio.sleep()` for polling interval
- [ ] Handle task cancellation gracefully
- [ ] Update SignalRecord in thread-safe manner
- [ ] Handle API failures (mark as incomplete, continue)

---

## Chapter 3: Real-Time Collectors

### 3.1 Recall Collector

**Goal**: Collect recall records from ArticleReceived events

**File**: `services/statistics/recall_engine/recall_collector.py`

**Methods to Implement**:

```python
class RecallCollector:
    """Collects recall statistics in real-time."""
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        data_fetcher: StatisticsDataFetcher,
        storage_query_service: StorageQueryService  # For fetching articles
    ):
        self.event_bus = event_bus
        self.data_fetcher = data_fetcher
        self.storage_query_service = storage_query_service
        
        # Thread-safe in-memory storage
        self._recall_records: Dict[str, List[RecallRecord]] = {}  # session -> records
        self._lock = threading.Lock()
    
    async def start(self) -> None:
        """Subscribe to domain events."""
        # Subscribe to:
        # - Domain.ArticleReceived (to track all articles with tickers)
        # - Domain.ArticleClassified (to know classification result)
        # - Domain.TradeExecuted (to know if article was traded)
        # - Infrastructure.ClassificationSkipped (to know filter reasons)
    
    async def _handle_article_received(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """
        Handle ArticleReceived event.
        
        Steps:
        1. Check if article has tickers (skip if not)
        2. Determine current session
        3. Fetch article from storage (with lock to avoid race conditions)
        4. Create RecallRecord
        5. Fetch stock metadata for all tickers
        6. Store in memory (thread-safe)
        """
    
    async def _fetch_article_with_lock(self, article_id: str) -> Optional[Article]:
        """Fetch article from storage using thread-safe lock."""
        # Use threading.Lock() when calling storage_query_service
    
    async def _determine_filter_reasons(
        self,
        article_id: str
    ) -> List[str]:
        """
        Determine why article wasn't traded.
        
        Checks:
        - Was it classified? (check ArticleClassified events)
        - Was it traded? (check TradeExecuted events)
        - What filters blocked it? (check ClassificationSkipped events)
        
        Returns list like: ["low_price", "nbbo_unavailable", "not_classified_imminent"]
        """
    
    def get_recall_records(self, session: MarketSession) -> List[RecallRecord]:
        """Get recall records for a session (thread-safe)."""
        with self._lock:
            return self._recall_records.get(session.value, []).copy()
    
    def clear_recall_records(self, session: MarketSession) -> None:
        """Clear records for a session after report generation."""
        with self._lock:
            self._recall_records[session.value] = []
```

**Tasks**:
- [ ] Subscribe to required events
- [ ] Implement thread-safe article fetching
- [ ] Implement filter reason detection (track events throughout session)
- [ ] Implement metadata fetching for all tickers
- [ ] Use threading.Lock() for all shared state access

---

### 3.2 Signal Collector

**Goal**: Collect signal records from TradeExecuted events

**File**: `services/statistics/signal_engine/signal_collector.py`

**Methods to Implement**:

```python
class SignalCollector:
    """Collects signal statistics in real-time."""
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        data_fetcher: StatisticsDataFetcher,
        price_tracker: PriceTracker
    ):
        self.event_bus = event_bus
        self.data_fetcher = data_fetcher
        self.price_tracker = price_tracker
        
        # Thread-safe in-memory storage
        self._signal_records: List[SignalRecord] = []
        self._open_trades: Dict[str, SignalRecord] = {}  # trade_id -> record
        self._lock = threading.Lock()
    
    async def start(self) -> None:
        """Subscribe to domain events."""
        # Subscribe to:
        # - Domain.TradeExecuted (entry and exit)
        # - Domain.TradeFailed (failed trades)
    
    async def _handle_trade_executed(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """
        Handle TradeExecuted event.
        
        Steps:
        1. Check if BUY (entry) or SELL (exit)
        2. If BUY:
           - Create new SignalRecord
           - Extract entry details (including ladder_time_seconds)
           - Fetch stock metadata for all tickers
           - Store in _open_trades
        3. If SELL:
           - Find matching entry in _open_trades
           - Extract exit details (including ladder_time_seconds)
           - Calculate outcome
           - Start 15-minute post-exit tracking
           - Move to _signal_records
        """
    
    async def _extract_ladder_time(
        self,
        trade_result: TradeResult
    ) -> float:
        """
        Extract ladder time from trade result.
        
        Check instrument_details.ladder_attempts_detail for:
        - First attempt timestamp
        - Last attempt timestamp (or fill timestamp)
        - Return difference in seconds
        """
    
    async def _extract_phase1_filters_passed(
        self,
        article_id: str,
        trade_request: TradeRequest
    ) -> Phase1FilterStatus:
        """
        Extract which phase 1 filters passed.
        
        Check:
        - Had tickers (from article)
        - Price > $5 (from trade request or metadata)
        - Market cap > $500M (from metadata)
        - NBBO available (from trade request metadata)
        - Classification result (from ArticleClassified events)
        """
    
    def get_signal_records(self, session: MarketSession) -> List[SignalRecord]:
        """Get signal records for a session (thread-safe)."""
        with self._lock:
            return [
                r for r in self._signal_records
                if r.session == session
            ]
    
    def clear_signal_records(self, session: MarketSession) -> None:
        """Clear records for a session after report generation."""
        with self._lock:
            self._signal_records = [
                r for r in self._signal_records
                if r.session != session
            ]
```

**Tasks**:
- [ ] Subscribe to TradeExecuted events
- [ ] Implement entry/exit matching logic
- [ ] Extract ladder time from trade results
- [ ] Extract phase 1 filter status
- [ ] Integrate with PriceTracker for post-exit tracking
- [ ] Use threading.Lock() for all shared state access

---

## Chapter 4: Session-End Analysis

### 4.1 Recall Analyzer

**Goal**: Analyze recall records and generate session report

**File**: `services/statistics/recall_engine/recall_analyzer.py`

**Methods to Implement**:

```python
class RecallAnalyzer:
    """Analyzes recall records and generates session reports."""
    
    def __init__(
        self,
        data_fetcher: StatisticsDataFetcher,
        llm_analyzer: StatisticsLLMAnalyzer,
        statistics_repository: StatisticsRepository,
        event_bus: AsyncEventBus
    ):
        self.data_fetcher = data_fetcher
        self.llm_analyzer = llm_analyzer
        self.statistics_repository = statistics_repository
        self.event_bus = event_bus
    
    async def analyze_session(
        self,
        session: MarketSession,
        date: str,
        recall_records: List[RecallRecord]
    ) -> str:
        """
        Analyze recall records for a session and generate report.
        
        Steps:
        1. For each record, fetch session price bars for all tickers
        2. Calculate session price action metrics
        3. Determine if 5% threshold achieved
        4. Generate summary statistics
        5. Request LLM analysis (batch + aggregate)
        6. Create RecallSessionReport
        7. Save to JSON file
        8. Publish StatisticsReportGeneratedEvent
        9. Return file path
        """
    
    async def _analyze_price_action(
        self,
        record: RecallRecord,
        session_start: datetime,
        session_end: datetime
    ) -> RecallRecord:
        """
        Analyze price action for all tickers in record.
        
        For each ticker:
        1. Fetch session bars
        2. Calculate: open, high, low, close
        3. Calculate: max_move_percent = (high - open) / open * 100
        4. Determine: achieved_5_percent = max_move_percent >= 5.0
        5. Calculate: time_to_high_minutes
        6. Update record.session_price_action[ticker]
        """
    
    async def _generate_summary(
        self,
        records: List[RecallRecord]
    ) -> RecallSummary:
        """
        Generate summary statistics.
        
        Calculate:
        - total_articles_tracked
        - articles_with_5_percent_move (count tickers, not articles)
        - articles_traded
        - missed_opportunities
        - filter_breakdown (count by filter reason)
        - industry/sector breakdown
        """
    
    async def _request_llm_analysis(
        self,
        records: List[RecallRecord],
        session: MarketSession,
        date: str
    ) -> LLMAnalysis:
        """
        Request LLM analysis for recall records.
        
        Steps:
        1. Batch records (e.g., 10 per batch)
        2. For each batch, call llm_analyzer.analyze_recall_batch()
        3. Aggregate batch results with llm_analyzer.aggregate_recall_analysis()
        4. Return LLMAnalysis
        """
```

**Tasks**:
- [ ] Implement session price action analysis
- [ ] Calculate 5% threshold detection
- [ ] Generate summary statistics
- [ ] Integrate with LLM analyzer
- [ ] Handle API failures gracefully (continue without LLM analysis if needed)

---

### 4.2 Signal Analyzer

**Goal**: Analyze signal records and generate session report

**File**: `services/statistics/signal_engine/signal_analyzer.py`

**Methods to Implement**:

```python
class SignalAnalyzer:
    """Analyzes signal records and generates session reports."""
    
    def __init__(
        self,
        llm_analyzer: StatisticsLLMAnalyzer,
        statistics_repository: StatisticsRepository,
        event_bus: AsyncEventBus
    ):
        self.llm_analyzer = llm_analyzer
        self.statistics_repository = statistics_repository
        self.event_bus = event_bus
    
    async def analyze_session(
        self,
        session: MarketSession,
        date: str,
        signal_records: List[SignalRecord]
    ) -> str:
        """
        Analyze signal records for a session and generate report.
        
        Steps:
        1. Ensure all post-exit tracking is complete (wait if needed)
        2. Calculate trade outcome metrics
        3. Generate summary statistics
        4. Request LLM analysis (batch + aggregate)
        5. Create SignalSessionReport
        6. Save to JSON file
        7. Publish StatisticsReportGeneratedEvent
        8. Return file path
        """
    
    async def _generate_summary(
        self,
        records: List[SignalRecord]
    ) -> SignalSummary:
        """
        Generate summary statistics.
        
        Calculate:
        - total_trades
        - profitable_trades / losing_trades
        - average_pnl
        - exit_quality_distribution (early/optimal/late)
        - ladder_time_analysis (avg entry ladder time, avg exit ladder time)
        - phase1_filter_effectiveness (which filters passed for losing trades)
        """
    
    async def _request_llm_analysis(
        self,
        records: List[SignalRecord],
        session: MarketSession,
        date: str
    ) -> LLMAnalysis:
        """
        Request LLM analysis for signal records.
        
        Steps:
        1. Batch records (e.g., 5 per batch)
        2. For each batch, call llm_analyzer.analyze_signal_batch()
        3. Aggregate batch results with llm_analyzer.aggregate_signal_analysis()
        4. Return LLMAnalysis
        """
```

**Tasks**:
- [ ] Implement trade outcome analysis
- [ ] Calculate ladder time statistics
- [ ] Generate summary statistics
- [ ] Integrate with LLM analyzer
- [ ] Handle incomplete post-exit tracking gracefully

---

## Chapter 5: LLM Analysis Engine

### 5.1 Statistics LLM Analyzer

**Goal**: LLM analysis for recall and signal reports

**File**: `infra/statistics/llm_analyzer.py`

**Methods to Implement**:

```python
class StatisticsLLMAnalyzer:
    """LLM analysis for statistics reports."""
    
    def __init__(self, groq_api_key: str, groq_model: str):
        # Create separate Groq client instance
        self.groq_client = Groq(api_key=groq_api_key)
        self.model = groq_model
    
    async def analyze_recall_batch(
        self,
        records: List[RecallRecord]
    ) -> str:
        """
        Analyze a batch of recall records.
        
        Prompt template:
        - List of articles with tickers that were missed
        - Filter reasons for each
        - Price action (did ticker hit 5%+?)
        - Stock metadata (industry, sector, etc.)
        
        Questions:
        - Which articles should have been traded?
        - Why were they filtered out?
        - Are the filters too strict?
        - What patterns do you see?
        """
    
    async def aggregate_recall_analysis(
        self,
        batch_analyses: List[str],
        summary: RecallSummary
    ) -> Tuple[str, List[str]]:
        """
        Aggregate batch analyses into final summary.
        
        Prompt template:
        - All batch analyses
        - Summary statistics
        - Overall patterns
        
        Questions:
        - Overall assessment of recall performance
        - Key missed opportunities
        - Filter improvement suggestions
        - Areas of strength/weakness
        """
    
    async def analyze_signal_batch(
        self,
        records: List[SignalRecord]
    ) -> str:
        """
        Analyze a batch of signal records.
        
        Prompt template:
        - Trade details (entry, exit, outcome)
        - Ladder times
        - Phase 1 filters that passed
        - Post-exit price action
        - Stock metadata
        
        Questions:
        - Why did trades lose money?
        - Were entries/exits optimal?
        - Are phase 1 filters too lenient?
        - What patterns do you see?
        """
    
    async def aggregate_signal_analysis(
        self,
        batch_analyses: List[str],
        summary: SignalSummary
    ) -> Tuple[str, List[str]]:
        """
        Aggregate batch analyses into final summary.
        
        Prompt template:
        - All batch analyses
        - Summary statistics
        - Overall patterns
        
        Questions:
        - Overall assessment of signal quality
        - Key issues (entry/exit/filters)
        - Filter improvement suggestions
        - Areas of strength/weakness
        """
```

**Tasks**:
- [ ] Create Groq client instance
- [ ] Implement batch analysis prompts
- [ ] Implement aggregation prompts
- [ ] Add error handling with retries
- [ ] Handle API failures gracefully

---

## Chapter 6: Session Manager & Scheduling

### 6.1 Session Manager

**Goal**: Manage session timing and trigger report generation

**File**: `services/statistics/session_manager.py`

**Methods to Implement**:

```python
class StatisticsSessionManager:
    """Manages session timing and report generation."""
    
    def __init__(
        self,
        recall_collector: RecallCollector,
        signal_collector: SignalCollector,
        recall_analyzer: RecallAnalyzer,
        signal_analyzer: SignalAnalyzer,
        event_bus: AsyncEventBus,
        main_event_loop: asyncio.AbstractEventLoop
    ):
        self.recall_collector = recall_collector
        self.signal_collector = signal_collector
        self.recall_analyzer = recall_analyzer
        self.signal_analyzer = signal_analyzer
        self.event_bus = event_bus
        self._main_event_loop = main_event_loop
        
        # Use Python schedule library (not asyncio)
        self._scheduler = schedule.Scheduler()
    
    async def run(self) -> None:
        """
        Main loop running in separate thread.
        
        Steps:
        1. Schedule report generation for each session end:
           - Premarket: 2:30 PM UK (9:30 AM ET)
           - Market: 9:00 PM UK (4:00 PM ET)
           - Postmarket: 1:00 AM UK (8:00 PM ET)
        2. On startup, catch up on current session (if started mid-session)
        3. Run scheduler loop
        4. At scheduled times, trigger report generation
        """
    
    def _schedule_reports(self) -> None:
        """Schedule daily report generation."""
        # Use schedule library:
        # schedule.every().day.at("14:30").do(...)  # Premarket
        # schedule.every().day.at("21:00").do(...)  # Market
        # schedule.every().day.at("01:00").do(...)  # Postmarket
    
    async def _generate_session_reports(
        self,
        session: MarketSession
    ) -> None:
        """
        Generate reports for a completed session.
        
        Steps:
        1. Get current date
        2. Get recall records for session
        3. Get signal records for session
        4. Generate recall report (async)
        5. Generate signal report (async)
        6. Wait for both to complete
        7. Publish completion events (thread-safe to main loop)
        """
    
    async def _catchup_current_session(self) -> None:
        """
        On startup, catch up on current session.
        
        If server started mid-session, collect any missed articles/trades
        from the current session start time until now.
        """
    
    def _publish_to_main_thread(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """
        Publish events to main thread event bus (thread-safe).
        
        Uses call_soon_threadsafe pattern.
        """
```

**Tasks**:
- [ ] Implement schedule-based timing (using `schedule` library)
- [ ] Implement session catchup on startup
- [ ] Implement thread-safe event publishing
- [ ] Handle timezone conversions (UK time to ET)
- [ ] Handle edge cases (weekends, holidays)

---

## Chapter 7: Infrastructure Service & Domain Listener

### 7.1 Statistics Infrastructure Service

**Goal**: Infrastructure layer service for statistics microservice

**File**: `infra/statistics/service.py`

**Methods to Implement**:

```python
class StatisticsInfrastructureService:
    """Infrastructure service for statistics microservice."""
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        statistics_repository: StatisticsRepository,
        llm_analyzer: StatisticsLLMAnalyzer
    ):
        self.event_bus = event_bus
        self.statistics_repository = statistics_repository
        self.llm_analyzer = llm_analyzer
    
    async def start(self) -> None:
        """Start infrastructure service."""
        # No-op (event-driven)
    
    async def stop(self) -> None:
        """Stop infrastructure service."""
        # No-op
```

**Tasks**:
- [ ] Implement infrastructure service following existing patterns
- [ ] Ensure stateless design

---

### 7.2 Statistics Domain Listener

**Goal**: Bridge infrastructure and domain events

**File**: `domain/statistics/listener.py`

**Methods to Implement**:

```python
class StatisticsDomainListener(BaseDomainListener):
    """Bridge infrastructure ↔ domain for statistics."""
    
    def __init__(
        self,
        event_bus: AsyncEventBus
    ):
        super().__init__(event_bus)
    
    async def start(self) -> None:
        """Subscribe to events."""
        # Subscribe to:
        # - StatisticsReportGeneratedEvent (to trigger notifications)
    
    async def _handle_report_generated(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """Handle report generation completion."""
        # Forward to notification service via domain event
```

**Tasks**:
- [ ] Implement domain listener following existing patterns
- [ ] Subscribe to statistics events
- [ ] Forward events to notification service

---

## Chapter 8: Microservice Integration

### 8.1 Statistics Microservice Container

**Goal**: Create microservice container following existing patterns

**File**: `services/statistics/__init__.py`

**Classes to Implement**:

```python
@dataclass
class StatisticsMicroservice:
    """Statistics microservice container."""
    
    infra: StatisticsInfrastructureService
    domain_listener: StatisticsDomainListener
    recall_collector: RecallCollector
    signal_collector: SignalCollector
    recall_analyzer: RecallAnalyzer
    signal_analyzer: SignalAnalyzer
    session_manager: StatisticsSessionManager
    price_tracker: PriceTracker
    data_fetcher: StatisticsDataFetcher
    
    _stats_thread: Optional[threading.Thread] = None
    _main_event_loop: Optional[asyncio.AbstractEventLoop] = None
    
    async def start(self) -> None:
        """
        Start statistics microservice.
        
        Steps:
        1. Store reference to main event loop
        2. Start infrastructure service
        3. Start domain listener
        4. Start collectors (in main thread)
        5. Start price tracker
        6. Start session manager in separate thread
        """
    
    async def stop(self) -> None:
        """Stop statistics microservice."""
        # Stop all components
        # Cancel background tasks
        # Join thread if running

async def initialize_statistics_microservice(
    event_bus: AsyncEventBus,
    market_data_client: StockHistoricalDataClient,
    trading_client: TradingClient,
    storage_query_service: StorageQueryService,
    groq_api_key: str,
    groq_model: str,
    metrics_service
) -> StatisticsMicroservice:
    """Initialize statistics microservice with DI."""
```

**Tasks**:
- [ ] Implement microservice container
- [ ] Implement initialization function
- [ ] Follow existing microservice patterns
- [ ] Ensure proper lifecycle management

---

### 8.2 Dependency Injection Updates

**Goal**: Integrate statistics microservice into DI container

**Files to Update**:
- `services/containers/application.py`
- `services/composition_root.py`
- `services/service_initialization.py`
- `services/lifecycle_manager.py`

**Tasks**:

1. **Add to ApplicationContainer**:
   - [ ] Add statistics microservice factory
   - [ ] Wire dependencies (market_data_client, trading_client, etc.)

2. **Update Composition Root**:
   - [ ] Initialize statistics microservice
   - [ ] Wire cross-microservice dependencies (storage_query_service)

3. **Update Services Container**:
   - [ ] Add `statistics: StatisticsMicroservice` field

4. **Update Lifecycle Manager**:
   - [ ] Add statistics microservice to start/stop sequences

---

### 8.3 API Routes (Optional)

**Goal**: Add API endpoints for statistics (future use)

**File**: `api/routes/statistics/reports.py`

**Endpoints to Implement**:

```python
@router.get("/statistics/recall/{date}/{session}")
async def get_recall_report(date: str, session: str):
    """Get recall report for a session."""
    
@router.get("/statistics/signal/{date}/{session}")
async def get_signal_report(date: str, session: str):
    """Get signal report for a session."""
```

**Tasks**:
- [ ] Create API routes (optional for now)
- [ ] Add to main FastAPI app if needed

---

## Chapter 9: Notification Integration

### 9.1 Notification Use Case for Statistics

**Goal**: Send Telegram notifications when reports are complete

**File**: `use_cases/notification/notify_statistics_report_use_case.py`

**Methods to Implement**:

```python
class NotifyStatisticsReportUseCase:
    """Sends Telegram notifications for statistics reports."""
    
    async def _handle_report_generated(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """
        Handle StatisticsReportGeneratedEvent.
        
        Message format:
        - Top TLDR from LLM aggregated analysis
        - Confirmation that processing completed
        - File path to JSON report
        """
    
    def _format_report_notification(
        self,
        report_type: str,
        session: MarketSession,
        date: str,
        file_path: str,
        llm_analysis: Optional[LLMAnalysis]
    ) -> str:
        """
        Format Telegram notification message.
        
        Include:
        - Report type (Recall/Signal)
        - Session and date
        - Top insights (from aggregated LLM analysis)
        - File path
        """
```

**Tasks**:
- [ ] Implement notification use case
- [ ] Subscribe to StatisticsReportGeneratedEvent
- [ ] Format concise Telegram messages
- [ ] Handle thread-safe event publishing

---

## Chapter 10: Error Handling & Resilience

### 10.1 Error Handling Strategy

**Goal**: Implement robust error handling throughout

**Principles**:
- Retry with exponential backoff for API calls
- Circuit breakers for persistent failures
- Graceful degradation (continue without LLM analysis if needed)
- Never crash the main application loop

**Files to Update**:
- All statistics service files

**Tasks**:
- [ ] Add retry logic to all API calls (Alpaca, yfinance, Groq)
- [ ] Implement circuit breakers for persistent failures
- [ ] Add comprehensive error logging
- [ ] Ensure errors don't propagate to main thread
- [ ] Handle partial data gracefully (incomplete reports are better than no reports)

---

## Chapter 11: Testing & Validation

### 11.1 Unit Tests

**Goal**: Test individual components

**Files to Create**:
- `tests/unit/statistics/test_recall_collector.py`
- `tests/unit/statistics/test_signal_collector.py`
- `tests/unit/statistics/test_recall_analyzer.py`
- `tests/unit/statistics/test_signal_analyzer.py`
- `tests/unit/statistics/test_session_manager.py`

**Tasks**:
- [ ] Write unit tests for collectors
- [ ] Write unit tests for analyzers
- [ ] Write unit tests for session manager
- [ ] Mock external dependencies (Alpaca, yfinance, Groq)

---

### 11.2 Integration Tests

**Goal**: Test end-to-end flow

**Files to Create**:
- `tests/integration/test_statistics_engine_flow.py`

**Tasks**:
- [ ] Test full session report generation
- [ ] Test LLM analysis integration
- [ ] Test file persistence
- [ ] Test thread safety

---

## Chapter 12: Final Integration & Deployment

### 12.1 Final Checklist

**Tasks**:
- [ ] All components implemented
- [ ] DI container updated
- [ ] Lifecycle manager updated
- [ ] Event subscriptions working
- [ ] Thread safety verified
- [ ] Error handling comprehensive
- [ ] Tests passing
- [ ] Documentation updated

---

## Implementation Order

1. **Chapter 1**: Foundation (models, events, storage)
2. **Chapter 2**: Data fetching infrastructure
3. **Chapter 3**: Real-time collectors
4. **Chapter 4**: Session-end analyzers
5. **Chapter 5**: LLM analysis engine
6. **Chapter 6**: Session manager & scheduling
7. **Chapter 7**: Infrastructure & domain layers
8. **Chapter 8**: Microservice integration
9. **Chapter 9**: Notification integration
10. **Chapter 10**: Error handling
11. **Chapter 11**: Testing
12. **Chapter 12**: Final integration

---

## Key Design Principles

1. **Stateless**: No shared mutable state between components
2. **Event-Driven**: Use event bus for all communication
3. **Thread-Safe**: Use locks for all shared data access
4. **Error-Resilient**: Never crash main application
5. **Type-Safe**: Comprehensive type hints throughout
6. **DI-Compliant**: Follow existing dependency injection patterns
7. **Separation of Concerns**: Clear boundaries between layers

---

## Notes

- **Alpaca Minute Bars**: Verify free tier access (likely available for historical data)
- **Session Catchup**: On startup, only catch up current session (don't reprocess old sessions)
- **Thread Safety**: All collectors use `threading.Lock()` for in-memory buffers
- **LLM Analysis**: Runs in separate thread, doesn't block main application
- **File Paths**: Use `tmp/statistics/` directory (create if not exists)
- **Telegram Notifications**: Concise TLDR only, with file path

This plan provides clear implementation goals organized by chapter. Each subchapter represents a specific coding milestone.
