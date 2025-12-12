# Statistics Engines Implementation Plan

## Overview

Implementation of two lightweight statistics engines (Recall & Signal) that run alongside the main trading system to collect performance metrics. Both engines are event-driven, stateless, use dependency injection, and write records in real-time to JSON files using a repository pattern.

**Key Principles:**
- ✅ **Stateless Design**: All state in external files (repository pattern)
- ✅ **Event-Driven**: Subscribe to existing domain events
- ✅ **Dependency Injection**: All dependencies injected via constructor
- ✅ **Real-Time Append**: Write records immediately, no in-memory batching
- ✅ **Type-Safe**: Full type hints, Pydantic models
- ✅ **No Duplication**: Reuse existing code patterns (BaseRepository, event bus, etc.)

---

## Architecture

### File Structure

```
src/newsflash/
├── shared/statistics/
│   ├── __init__.py
│   ├── recall_engine.py          # RecallStatsEngine (singleton service)
│   ├── signal_engine.py           # SignalStatsEngine (singleton service)
│   └── models.py                  # Shared Pydantic models
├── infra/statistics/
│   ├── __init__.py
│   └── repository.py             # StatisticsRepository (file I/O)
└── domain/statistics/
    └── __init__.py                # (No domain layer needed - pure infrastructure)

tmp/statistics/
├── recall/
│   └── {year}/
│       └── {month}/
│           └── week_{week}/
│               └── {day}/
│                   ├── premarket/
│                   │   └── premarket.json
│                   ├── market_hours/
│                   │   └── market_hours.json
│                   └── postmarket/
│                       └── postmarket.json
└── signal/
    └── {year}/
        └── {month}/
            └── week_{week}/
                └── {day}/
                    ├── premarket/
                    │   └── premarket.json
                    ├── market_hours/
                    │   └── market_hours.json
                    └── postmarket/
                        └── postmarket.json
```

---

## Part 1: Data Models

### File: `shared/statistics/models.py`

```python
"""
Statistics data models - shared between recall and signal engines.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from ...domain.brokerage.models import MarketSession


# ===== Recall Engine Models =====

class RecallRecord(BaseModel):
    """Record of an article that could have been traded (missed opportunity)."""
    article_id: str
    title: str
    tickers: List[str]  # All tickers from article
    session: MarketSession
    published_at: datetime
    received_at: datetime
    
    # Initial NBBO snapshot (when article received)
    initial_nbbo: Optional[Dict[str, Any]] = Field(None, description="bid, ask, spread, mid")
    
    # 5-minute price check result
    price_check_5min: Optional[Dict[str, Any]] = Field(None, description="final_mid, percent_change, moved_1_percent")
    
    # Ticker metadata (fetched via yfinance)
    ticker_metadata: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="ticker -> {industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Filter reasons (why wasn't it traded?)
    filter_reasons: List[str] = Field(
        default_factory=list,
        description="e.g., ['not_classified_imminent', 'no_nbbo_available', 'ticker_not_tradeable_extended_hours']"
    )
    
    # Tracking metadata
    tracked_at: datetime = Field(default_factory=datetime.now)
    price_checked_at: Optional[datetime] = None
    
    model_config = {"frozen": False}  # Allow updates for price_check_5min


class RecallSessionFile(BaseModel):
    """JSON file structure for a recall session."""
    session: MarketSession
    date: str  # YYYY-MM-DD
    session_start: datetime
    session_end: datetime
    file_created_at: datetime = Field(default_factory=datetime.now)
    last_updated_at: datetime = Field(default_factory=datetime.now)
    
    # Real-time summary (updated on each append)
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_articles_tracked": 0,
            "articles_with_1_percent_move": 0,
            "articles_traded": 0,
            "missed_opportunities": 0,
            "filter_breakdown": {},
            "ticker_breakdown": {}
        }
    )
    
    # List of records (appended in real-time)
    records: List[RecallRecord] = Field(default_factory=list)
    
    model_config = {"frozen": False}  # Allow updates


# ===== Signal Engine Models =====

class SignalRecord(BaseModel):
    """Record of an actual trade execution."""
    trade_id: str
    article_id: Optional[str]
    ticker: str
    session: MarketSession
    executed_at: datetime
    
    # Entry details (from TradeResult)
    entry_price: float
    entry_shares: int
    entry_amount_usd: float
    entry_nbbo: Optional[Dict[str, Any]] = Field(None, description="bid, ask, spread, mid at entry")
    
    # Ticker metadata (fetched via yfinance)
    ticker_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="{industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Trade outcome (if available)
    exit_price: Optional[float] = None
    exit_shares: Optional[int] = None
    exit_amount_usd: Optional[float] = None
    profit_loss_usd: Optional[float] = None
    profit_loss_percent: Optional[float] = None
    
    # Tracking metadata
    recorded_at: datetime = Field(default_factory=datetime.now)
    
    model_config = {"frozen": False}  # Allow updates for exit data


class SignalSessionFile(BaseModel):
    """JSON file structure for a signal session."""
    session: MarketSession
    date: str  # YYYY-MM-DD
    session_start: datetime
    session_end: datetime
    file_created_at: datetime = Field(default_factory=datetime.now)
    last_updated_at: datetime = Field(default_factory=datetime.now)
    
    # Real-time summary (updated on each append)
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_trades": 0,
            "profitable_trades": 0,
            "losing_trades": 0,
            "total_profit_loss_usd": 0.0,
            "average_spread_at_entry": 0.0,
            "ticker_breakdown": {},
            "industry_breakdown": {},
            "sector_breakdown": {}
        }
    )
    
    # List of records (appended in real-time)
    records: List[SignalRecord] = Field(default_factory=list)
    
    model_config = {"frozen": False}  # Allow updates
```

---

## Part 2: Repository (File I/O)

### File: `infra/statistics/repository.py`

```python
"""
Statistics repository - handles file I/O for statistics records.
Pure infrastructure - stateless, uses BaseRepository pattern.
"""
import json
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import aiofiles
import pytz

from ...utils.logging_config import get_logger
from ...shared.statistics.models import RecallSessionFile, SignalSessionFile, RecallRecord, SignalRecord
from ...utils.brokerage.session_detector import get_market_session

logger = get_logger(__name__)


class StatisticsRepository:
    """
    Repository for statistics file operations.
    
    Responsibilities:
    - Append records to session JSON files in real-time
    - Load existing session files
    - Update summary statistics
    - Handle file path calculation
    
    Stateless: All state in files, no in-memory storage.
    """
    
    def __init__(self, tmp_dir: Path):
        """
        Initialize statistics repository.
        
        Args:
            tmp_dir: Base tmp directory (e.g., Path("tmp"))
        """
        self.tmp_dir = tmp_dir
        self.statistics_dir = tmp_dir / "statistics"
        self._file_lock = asyncio.Lock()  # Serialize file access
        
        # Ensure statistics directory exists
        self.statistics_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("StatisticsRepository initialized", tmp_dir=str(tmp_dir))
    
    def _get_session_file_path(
        self,
        engine_type: str,  # "recall" or "signal"
        session: str,  # "premarket", "market_hours", "postmarket"
        date: datetime
    ) -> Path:
        """
        Calculate file path for a session file.
        
        Path: tmp/statistics/{engine_type}/{year}/{month}/week_{week}/{day}/{session}/{session}.json
        
        Args:
            engine_type: "recall" or "signal"
            session: Session name
            date: Date to use for path calculation
            
        Returns:
            Path to JSON file
        """
        et_tz = pytz.timezone("US/Eastern")
        date_et = date.astimezone(et_tz) if date.tzinfo else et_tz.localize(date)
        
        year = date_et.year
        month = date_et.month
        day = date_et.day
        
        # Calculate week number (ISO week)
        week = date_et.isocalendar()[1]
        
        # Map session name to directory name
        session_dir_map = {
            "premarket": "premarket",
            "market_hours": "market_hours",
            "postmarket": "postmarket"
        }
        session_dir = session_dir_map.get(session, session)
        
        file_path = (
            self.statistics_dir /
            engine_type /
            str(year) /
            f"{month:02d}" /
            f"week_{week}" /
            f"{day:02d}" /
            session_dir /
            f"{session_dir}.json"
        )
        
        return file_path
    
    async def append_recall_record(
        self,
        record: RecallRecord,
        session: str,
        date: datetime
    ) -> None:
        """
        Append a recall record to the session file and update summary.
        
        Real-time operation: Loads file, appends record, updates summary, saves.
        
        Args:
            record: RecallRecord to append
            session: Session name
            date: Date for file path calculation
        """
        async with self._file_lock:
            file_path = self._get_session_file_path("recall", session, date)
            
            # Load existing file or create new
            session_file = await self._load_recall_file(file_path, session, date)
            
            # Append record
            session_file.records.append(record)
            
            # Update summary
            session_file.summary["total_articles_tracked"] = len(session_file.records)
            
            if record.price_check_5min and record.price_check_5min.get("moved_1_percent"):
                session_file.summary["articles_with_1_percent_move"] += 1
                if record.filter_reasons:  # If filtered, it's a missed opportunity
                    session_file.summary["missed_opportunities"] += 1
            
            # Update filter breakdown
            for reason in record.filter_reasons:
                session_file.summary["filter_breakdown"][reason] = \
                    session_file.summary["filter_breakdown"].get(reason, 0) + 1
            
            # Update last_updated_at
            session_file.last_updated_at = datetime.now()
            
            # Save file
            await self._save_recall_file(file_path, session_file)
            
            logger.debug(
                "Appended recall record",
                article_id=record.article_id,
                file_path=str(file_path)
            )
    
    async def append_signal_record(
        self,
        record: SignalRecord,
        session: str,
        date: datetime
    ) -> None:
        """
        Append a signal record to the session file and update summary.
        
        Real-time operation: Loads file, appends record, updates summary, saves.
        
        Args:
            record: SignalRecord to append
            session: Session name
            date: Date for file path calculation
        """
        async with self._file_lock:
            file_path = self._get_session_file_path("signal", session, date)
            
            # Load existing file or create new
            session_file = await self._load_signal_file(file_path, session, date)
            
            # Append record
            session_file.records.append(record)
            
            # Update summary
            session_file.summary["total_trades"] = len(session_file.records)
            
            if record.profit_loss_usd is not None:
                if record.profit_loss_usd > 0:
                    session_file.summary["profitable_trades"] += 1
                else:
                    session_file.summary["losing_trades"] += 1
                session_file.summary["total_profit_loss_usd"] += record.profit_loss_usd
            
            # Update average spread
            if record.entry_nbbo and record.entry_nbbo.get("spread"):
                spreads = [
                    r.entry_nbbo.get("spread")
                    for r in session_file.records
                    if r.entry_nbbo and r.entry_nbbo.get("spread")
                ]
                if spreads:
                    session_file.summary["average_spread_at_entry"] = sum(spreads) / len(spreads)
            
            # Update last_updated_at
            session_file.last_updated_at = datetime.now()
            
            # Save file
            await self._save_signal_file(file_path, session_file)
            
            logger.debug(
                "Appended signal record",
                trade_id=record.trade_id,
                file_path=str(file_path)
            )
    
    async def _load_recall_file(
        self,
        file_path: Path,
        session: str,
        date: datetime
    ) -> RecallSessionFile:
        """Load recall session file or create new if doesn't exist."""
        if file_path.exists():
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    if content.strip():
                        data = json.loads(content)
                        return RecallSessionFile(**data)
            except Exception as e:
                logger.warning(
                    "Failed to load recall file, creating new",
                    file_path=str(file_path),
                    error=str(e)
                )
        
        # Create new file
        et_tz = pytz.timezone("US/Eastern")
        date_et = date.astimezone(et_tz) if date.tzinfo else et_tz.localize(date)
        
        # Calculate session start/end times
        if session == "premarket":
            session_start = date_et.replace(hour=4, minute=0, second=0, microsecond=0)
            session_end = date_et.replace(hour=9, minute=30, second=0, microsecond=0)
        elif session == "market_hours":
            session_start = date_et.replace(hour=9, minute=30, second=0, microsecond=0)
            session_end = date_et.replace(hour=16, minute=0, second=0, microsecond=0)
        elif session == "postmarket":
            session_start = date_et.replace(hour=16, minute=0, second=0, microsecond=0)
            session_end = date_et.replace(hour=20, minute=0, second=0, microsecond=0)
        else:
            session_start = date_et
            session_end = date_et
        
        return RecallSessionFile(
            session=session,
            date=date_et.strftime("%Y-%m-%d"),
            session_start=session_start,
            session_end=session_end
        )
    
    async def _load_signal_file(
        self,
        file_path: Path,
        session: str,
        date: datetime
    ) -> SignalSessionFile:
        """Load signal session file or create new if doesn't exist."""
        if file_path.exists():
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    if content.strip():
                        data = json.loads(content)
                        return SignalSessionFile(**data)
            except Exception as e:
                logger.warning(
                    "Failed to load signal file, creating new",
                    file_path=str(file_path),
                    error=str(e)
                )
        
        # Create new file (same session times as recall)
        et_tz = pytz.timezone("US/Eastern")
        date_et = date.astimezone(et_tz) if date.tzinfo else et_tz.localize(date)
        
        if session == "premarket":
            session_start = date_et.replace(hour=4, minute=0, second=0, microsecond=0)
            session_end = date_et.replace(hour=9, minute=30, second=0, microsecond=0)
        elif session == "market_hours":
            session_start = date_et.replace(hour=9, minute=30, second=0, second=0, microsecond=0)
            session_end = date_et.replace(hour=16, minute=0, second=0, microsecond=0)
        elif session == "postmarket":
            session_start = date_et.replace(hour=16, minute=0, second=0, microsecond=0)
            session_end = date_et.replace(hour=20, minute=0, second=0, microsecond=0)
        else:
            session_start = date_et
            session_end = date_et
        
        return SignalSessionFile(
            session=session,
            date=date_et.strftime("%Y-%m-%d"),
            session_start=session_start,
            session_end=session_end
        )
    
    async def _save_recall_file(self, file_path: Path, session_file: RecallSessionFile) -> None:
        """Save recall session file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            json_str = json.dumps(
                session_file.model_dump(mode='json'),
                indent=2,
                ensure_ascii=False,
                default=str
            )
            await f.write(json_str)
    
    async def _save_signal_file(self, file_path: Path, session_file: SignalSessionFile) -> None:
        """Save signal session file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            json_str = json.dumps(
                session_file.model_dump(mode='json'),
                indent=2,
                ensure_ascii=False,
                default=str
            )
            await f.write(json_str)
```

---

## Part 3: Recall Engine

### File: `shared/statistics/recall_engine.py`

```python
"""
Recall statistics engine - tracks all articles with tradable tickers.
Event-driven, stateless, runs alongside main trading system.
"""
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, Set
from pathlib import Path

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType
from ...shared.statistics.models import RecallRecord
from ...infra.statistics.repository import StatisticsRepository
from ...infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
from ...utils.brokerage.session_detector import get_market_session
from ...domain.websocket.events import ArticleReceivedDomainEvent
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.brokerage.events import TradeExecutedDomainEvent

logger = get_logger(__name__)


class RecallStatsEngine:
    """
    Recall statistics engine - tracks missed trading opportunities.
    
    Responsibilities:
    - Subscribe to Domain.ArticleReceived events
    - Check if article has tradable tickers (NBBO available + in trading session)
    - Monitor ticker price for 5 minutes after article received
    - Record missed opportunities (1%+ moves we didn't trade)
    - Append records to JSON files in real-time
    
    Stateless: All state in repository (files), no in-memory storage.
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        repository: StatisticsRepository,
        quote_fetcher: AlpacaQuoteFetcher
    ):
        """
        Initialize recall statistics engine.
        
        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
            quote_fetcher: Quote fetcher for NBBO snapshots
        """
        self.event_bus = event_bus
        self.repository = repository
        self.quote_fetcher = quote_fetcher
        
        # Track active monitoring tasks (article_id -> task)
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._monitoring_lock = asyncio.Lock()
        
        # Track which articles were traded (to exclude from recall)
        self._traded_articles: Set[str] = set()
        self._traded_lock = asyncio.Lock()
        
        logger.info("RecallStatsEngine initialized")
    
    async def start(self) -> None:
        """Start engine - subscribe to events."""
        self.event_bus.subscribe(DomainEventType.ARTICLE_RECEIVED, self._handle_article_received)
        self.event_bus.subscribe(DomainEventType.ARTICLE_CLASSIFIED, self._handle_article_classified)
        self.event_bus.subscribe(DomainEventType.TRADE_EXECUTED, self._handle_trade_executed)
        
        logger.info("RecallStatsEngine started - subscribed to events")
    
    async def stop(self) -> None:
        """Stop engine - cancel monitoring tasks."""
        async with self._monitoring_lock:
            for task in self._monitoring_tasks.values():
                task.cancel()
            self._monitoring_tasks.clear()
        
        logger.info("RecallStatsEngine stopped")
    
    async def _handle_article_received(
        self,
        event_type: str,
        event_data: Dict[str, Any]
    ) -> None:
        """Handle Domain.ArticleReceived event."""
        try:
            event = ArticleReceivedDomainEvent(**event_data)
            article = event.article
            
            # Skip if no tickers
            if not article.tickers:
                return
            
            # Check current session
            session, is_extended = get_market_session()
            if session == "closed":
                return  # Don't track closed market
            
            # Fire and forget: Check if ticker is tradable and start monitoring
            asyncio.create_task(
                self._check_and_monitor_ticker(article, session, event.received_at)
            )
            
        except Exception as e:
            logger.error(
                "Error handling article received for recall",
                error=str(e),
                exc_info=True
            )
    
    async def _check_and_monitor_ticker(
        self,
        article: Any,  # Domain Article model
        session: str,
        received_at: datetime
    ) -> None:
        """
        Check if ticker is tradable and start 5-minute monitoring.
        
        Steps:
        1. Check if article was already traded (skip if yes)
        2. For each ticker, check NBBO availability
        3. If tradable, create RecallRecord and start monitoring task
        4. Append record immediately (with initial NBBO)
        """
        try:
            # Check if already traded
            async with self._traded_lock:
                if article.id in self._traded_articles:
                    return  # Skip - we traded this
            
            # Check each ticker for tradability
            tradable_tickers = []
            initial_nbbos = {}
            
            for ticker in article.tickers:
                # Get NBBO snapshot (this checks if ticker is tradable in current session)
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                
                if nbbo:
                    # Ticker is tradable (has NBBO in current session)
                    tradable_tickers.append(ticker)
                    initial_nbbos[ticker] = nbbo
            
            # If no tradable tickers, skip
            if not tradable_tickers:
                logger.debug(
                    "Recall: No tradable tickers for article",
                    article_id=article.id,
                    tickers=list(article.tickers)
                )
                return
            
            # Create recall record
            record = RecallRecord(
                article_id=article.id,
                title=article.title,
                tickers=tradable_tickers,
                session=session,
                published_at=article.published_at,
                received_at=received_at,
                initial_nbbo=initial_nbbos.get(tradable_tickers[0]) if tradable_tickers else None,  # Use first ticker's NBBO
                filter_reasons=[]  # Will be populated later if needed
            )
            
            # Append record immediately (with initial NBBO)
            await self.repository.append_recall_record(record, session, received_at)
            
            # Start 5-minute monitoring task (fire and forget)
            monitoring_task = asyncio.create_task(
                self._monitor_ticker_price(article.id, tradable_tickers, initial_nbbos, session, received_at)
            )
            
            async with self._monitoring_lock:
                self._monitoring_tasks[article.id] = monitoring_task
            
            logger.debug(
                "Recall: Started monitoring ticker",
                article_id=article.id,
                tickers=tradable_tickers
            )
            
        except Exception as e:
            logger.error(
                "Error checking and monitoring ticker for recall",
                article_id=article.id,
                error=str(e),
                exc_info=True
            )
    
    async def _monitor_ticker_price(
        self,
        article_id: str,
        tickers: list[str],
        initial_nbbos: Dict[str, Dict[str, Any]],
        session: str,
        received_at: datetime
    ) -> None:
        """
        Monitor ticker price for 5 minutes, then check if it moved 1%+.
        
        Background task: Waits 5 minutes, then checks final price.
        """
        try:
            # Wait 5 minutes
            await asyncio.sleep(300)  # 300 seconds = 5 minutes
            
            # Check if article was traded (skip if yes)
            async with self._traded_lock:
                if article_id in self._traded_articles:
                    return  # We traded this, don't count as missed
            
            # Get final NBBO for each ticker
            final_nbbos = {}
            best_move = None
            best_ticker = None
            
            for ticker in tickers:
                nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
                if nbbo and initial_nbbos.get(ticker):
                    initial_mid = initial_nbbos[ticker].get("mid")
                    final_mid = nbbo.get("mid")
                    
                    if initial_mid and final_mid and initial_mid > 0:
                        percent_change = ((final_mid - initial_mid) / initial_mid) * 100
                        final_nbbos[ticker] = {
                            **nbbo,
                            "percent_change": percent_change,
                            "moved_1_percent": percent_change >= 1.0
                        }
                        
                        # Track best move
                        if best_move is None or percent_change > best_move:
                            best_move = percent_change
                            best_ticker = ticker
            
            # Update record with price check result
            if best_ticker and final_nbbos.get(best_ticker):
                price_check = final_nbbos[best_ticker]
                
                # Load existing file, update record, save
                # (Repository will handle this - we need to add an update method)
                # For now, we'll append a new record with updated data
                # TODO: Add update_record method to repository
                
                logger.info(
                    "Recall: 5-minute price check completed",
                    article_id=article_id,
                    best_ticker=best_ticker,
                    percent_change=best_move,
                    moved_1_percent=price_check.get("moved_1_percent")
                )
            
        except asyncio.CancelledError:
            logger.debug("Recall: Monitoring task cancelled", article_id=article_id)
        except Exception as e:
            logger.error(
                "Error monitoring ticker price for recall",
                article_id=article_id,
                error=str(e),
                exc_info=True
            )
        finally:
            # Remove from monitoring tasks
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article_id, None)
    
    async def _handle_article_classified(
        self,
        event_type: str,
        event_data: Dict[str, Any]
    ) -> None:
        """Handle Domain.ArticleClassified event - update filter reasons."""
        # TODO: If article wasn't classified as imminent, add filter reason
        # This requires checking if record exists and updating it
        pass
    
    async def _handle_trade_executed(
        self,
        event_type: str,
        event_data: Dict[str, Any]
    ) -> None:
        """Handle Domain.TradeExecuted event - mark article as traded."""
        try:
            event = TradeExecutedDomainEvent(**event_data)
            trade_result = event.trade_result
            
            if trade_result.article_id:
                async with self._traded_lock:
                    self._traded_articles.add(trade_result.article_id)
                
                # Cancel monitoring task if exists
                async with self._monitoring_lock:
                    task = self._monitoring_tasks.pop(trade_result.article_id, None)
                    if task:
                        task.cancel()
                
                logger.debug(
                    "Recall: Marked article as traded",
                    article_id=trade_result.article_id
                )
        except Exception as e:
            logger.error(
                "Error handling trade executed for recall",
                error=str(e),
                exc_info=True
            )
```

---

## Part 4: Signal Engine

### File: `shared/statistics/signal_engine.py`

```python
"""
Signal statistics engine - tracks actual trade executions.
Event-driven, stateless, runs alongside main trading system.
"""
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import DomainEventType
from ...shared.statistics.models import SignalRecord
from ...infra.statistics.repository import StatisticsRepository
from ...utils.brokerage.session_detector import get_market_session
from ...domain.brokerage.events import TradeExecutedDomainEvent
import yfinance as yf

logger = get_logger(__name__)


class SignalStatsEngine:
    """
    Signal statistics engine - tracks actual trade executions.
    
    Responsibilities:
    - Subscribe to Domain.TradeExecuted events
    - Extract trade details (price, spread, ticker metadata)
    - Append records to JSON files in real-time
    - Track profit/loss when trades exit
    
    Stateless: All state in repository (files), no in-memory storage.
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        repository: StatisticsRepository
    ):
        """
        Initialize signal statistics engine.
        
        Args:
            event_bus: Event bus for subscribing to events
            repository: Statistics repository for file I/O
        """
        self.event_bus = event_bus
        self.repository = repository
        
        logger.info("SignalStatsEngine initialized")
    
    async def start(self) -> None:
        """Start engine - subscribe to events."""
        self.event_bus.subscribe(DomainEventType.TRADE_EXECUTED, self._handle_trade_executed)
        
        logger.info("SignalStatsEngine started - subscribed to events")
    
    async def stop(self) -> None:
        """Stop engine."""
        logger.info("SignalStatsEngine stopped")
    
    async def _handle_trade_executed(
        self,
        event_type: str,
        event_data: Dict[str, Any]
    ) -> None:
        """Handle Domain.TradeExecuted event."""
        try:
            event = TradeExecutedDomainEvent(**event_data)
            trade_result = event.trade_result
            
            # Get current session
            session, _ = get_market_session()
            if session == "closed":
                session = "market_hours"  # Fallback
            
            # Extract entry details from TradeResult
            entry_price = trade_result.fill_price
            entry_shares = trade_result.filled_shares
            entry_amount_usd = trade_result.total_cost
            
            # Extract NBBO from trade result (if available)
            entry_nbbo = None
            if hasattr(trade_result, 'spread_info') and trade_result.spread_info:
                entry_nbbo = trade_result.spread_info
            
            # Fetch ticker metadata (fire and forget - non-blocking)
            ticker_metadata_task = asyncio.create_task(
                self._fetch_ticker_metadata(trade_result.ticker)
            )
            
            # Create signal record
            record = SignalRecord(
                trade_id=trade_result.order_id or f"trade_{datetime.now().timestamp()}",
                article_id=trade_result.article_id,
                ticker=trade_result.ticker,
                session=session,
                executed_at=event.executed_at,
                entry_price=entry_price,
                entry_shares=entry_shares,
                entry_amount_usd=entry_amount_usd,
                entry_nbbo=entry_nbbo
            )
            
            # Append record immediately
            await self.repository.append_signal_record(record, session, event.executed_at)
            
            # Update record with metadata when available (non-blocking)
            asyncio.create_task(
                self._update_record_with_metadata(record, ticker_metadata_task, session, event.executed_at)
            )
            
            logger.debug(
                "Signal: Recorded trade execution",
                trade_id=record.trade_id,
                ticker=trade_result.ticker
            )
            
        except Exception as e:
            logger.error(
                "Error handling trade executed for signal",
                error=str(e),
                exc_info=True
            )
    
    async def _fetch_ticker_metadata(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch ticker metadata from yfinance.
        
        Returns:
            Dict with industry, sector, market_cap_millions, price, exchange
        """
        try:
            loop = asyncio.get_event_loop()
            stock = await loop.run_in_executor(None, lambda: yf.Ticker(ticker))
            info = await loop.run_in_executor(None, lambda: stock.info)
            
            market_cap_raw = info.get('marketCap')
            market_cap_millions = market_cap_raw / 1_000_000 if market_cap_raw else None
            
            return {
                "industry": info.get('industry'),
                "sector": info.get('sector'),
                "market_cap_millions": market_cap_millions,
                "price": info.get('currentPrice') or info.get('regularMarketPrice'),
                "exchange": info.get('exchange')
            }
        except Exception as e:
            logger.warning(
                "Failed to fetch ticker metadata",
                ticker=ticker,
                error=str(e)
            )
            return None
    
    async def _update_record_with_metadata(
        self,
        record: SignalRecord,
        metadata_task: asyncio.Task,
        session: str,
        date: datetime
    ) -> None:
        """Update record with metadata when available."""
        try:
            metadata = await metadata_task
            if metadata:
                record.ticker_metadata = metadata
                # TODO: Update record in file (need update method in repository)
                logger.debug(
                    "Signal: Updated record with metadata",
                    trade_id=record.trade_id,
                    ticker=record.ticker
                )
        except Exception as e:
            logger.warning(
                "Error updating record with metadata",
                trade_id=record.trade_id,
                error=str(e)
            )
```

---

## Part 5: Integration

### File: `services/composition_root.py` (Add to existing)

```python
# Add after other service initializations:

# Initialize statistics engines
from ..shared.statistics.recall_engine import RecallStatsEngine
from ..shared.statistics.signal_engine import SignalStatsEngine
from ..infra.statistics.repository import StatisticsRepository

# Create statistics repository
tmp_dir = Path(container.config()["tmp_dir"])
statistics_repository = StatisticsRepository(tmp_dir=tmp_dir)

# Create recall engine
recall_engine = RecallStatsEngine(
    event_bus=event_bus,
    repository=statistics_repository,
    quote_fetcher=brokerage.infra.quote_fetcher
)

# Create signal engine
signal_engine = SignalStatsEngine(
    event_bus=event_bus,
    repository=statistics_repository
)

# Start engines
await recall_engine.start()
await signal_engine.start()

logger.info("Statistics engines started")
```

---

## Part 6: JSON File Structure Examples

### Recall File: `tmp/statistics/recall/2025/12/week_50/10/premarket/premarket.json`

```json
{
  "session": "premarket",
  "date": "2025-12-10",
  "session_start": "2025-12-10T04:00:00-05:00",
  "session_end": "2025-12-10T09:30:00-05:00",
  "file_created_at": "2025-12-10T04:15:23.123456Z",
  "last_updated_at": "2025-12-10T09:25:45.789012Z",
  "summary": {
    "total_articles_tracked": 247,
    "articles_with_1_percent_move": 12,
    "articles_traded": 3,
    "missed_opportunities": 9,
    "filter_breakdown": {
      "not_classified_imminent": 8,
      "no_nbbo_available": 1
    },
    "ticker_breakdown": {
      "AAPL": 5,
      "TSLA": 3,
      "NVDA": 4
    }
  },
  "records": [
    {
      "article_id": "benzinga:49304149",
      "title": "Apple Announces New Product Line",
      "tickers": ["AAPL"],
      "session": "premarket",
      "published_at": "2025-12-10T05:30:00Z",
      "received_at": "2025-12-10T05:30:05Z",
      "initial_nbbo": {
        "bid": 175.50,
        "ask": 175.55,
        "spread": 0.05,
        "mid": 175.525
      },
      "price_check_5min": {
        "final_mid": 186.20,
        "percent_change": 6.08,
        "moved_1_percent": true
      },
      "ticker_metadata": {
        "AAPL": {
          "industry": "Consumer Electronics",
          "sector": "Technology",
          "market_cap_millions": 2800000.0,
          "price": 175.52,
          "exchange": "NASDAQ"
        }
      },
      "filter_reasons": ["not_classified_imminent"],
      "tracked_at": "2025-12-10T05:30:05Z",
      "price_checked_at": "2025-12-10T05:35:05Z"
    }
  ]
}
```

### Signal File: `tmp/statistics/signal/2025/12/week_50/10/market_hours/market_hours.json`

```json
{
  "session": "market_hours",
  "date": "2025-12-10",
  "session_start": "2025-12-10T09:30:00-05:00",
  "session_end": "2025-12-10T16:00:00-05:00",
  "file_created_at": "2025-12-10T09:35:12.345678Z",
  "last_updated_at": "2025-12-10T15:45:30.987654Z",
  "summary": {
    "total_trades": 15,
    "profitable_trades": 9,
    "losing_trades": 6,
    "total_profit_loss_usd": 1250.50,
    "average_spread_at_entry": 0.08,
    "ticker_breakdown": {
      "AAPL": 3,
      "TSLA": 2,
      "NVDA": 4
    },
    "industry_breakdown": {
      "Consumer Electronics": 3,
      "Automotive": 2,
      "Semiconductors": 4
    },
    "sector_breakdown": {
      "Technology": 7,
      "Consumer Cyclical": 2
    }
  },
  "records": [
    {
      "trade_id": "order_abc123",
      "article_id": "benzinga:49304150",
      "ticker": "AAPL",
      "session": "market_hours",
      "executed_at": "2025-12-10T10:15:30Z",
      "entry_price": 175.50,
      "entry_shares": 10,
      "entry_amount_usd": 1755.00,
      "entry_nbbo": {
        "bid": 175.48,
        "ask": 175.52,
        "spread": 0.04,
        "mid": 175.50
      },
      "ticker_metadata": {
        "industry": "Consumer Electronics",
        "sector": "Technology",
        "market_cap_millions": 2800000.0,
        "price": 175.50,
        "exchange": "NASDAQ"
      },
      "exit_price": 178.20,
      "exit_shares": 10,
      "exit_amount_usd": 1782.00,
      "profit_loss_usd": 27.00,
      "profit_loss_percent": 1.54,
      "recorded_at": "2025-12-10T10:15:30Z"
    }
  ]
}
```

---

## Part 7: Implementation Checklist

### Phase 1: Foundation
- [ ] Create `shared/statistics/models.py` with all Pydantic models
- [ ] Create `infra/statistics/repository.py` with file I/O operations
- [ ] Test repository append operations

### Phase 2: Recall Engine
- [ ] Create `shared/statistics/recall_engine.py`
- [ ] Implement event subscriptions
- [ ] Implement NBBO checking and tradability validation
- [ ] Implement 5-minute monitoring tasks
- [ ] Test with sample articles

### Phase 3: Signal Engine
- [ ] Create `shared/statistics/signal_engine.py`
- [ ] Implement event subscriptions
- [ ] Implement yfinance metadata fetching
- [ ] Test with sample trades

### Phase 4: Integration
- [ ] Add engines to `composition_root.py`
- [ ] Wire dependencies via DI container
- [ ] Test end-to-end flow

### Phase 5: Testing & Refinement
- [ ] Test file path generation
- [ ] Test concurrent append operations
- [ ] Test session boundary handling
- [ ] Verify JSON file structure matches spec

---

## Notes

1. **Repository Update Method**: The current plan appends records, but we need to update records after 5-minute price checks. We should add an `update_recall_record()` method to the repository that loads the file, finds the record by article_id, updates it, and saves.

2. **yfinance Already Added**: yfinance is already in `pyproject.toml`, so no dependency changes needed.

3. **Extended Hours Trading**: To check if a ticker trades in extended hours, we use NBBO availability - if `get_nbbo_snapshot()` returns a valid NBBO during premarket/postmarket, the ticker is tradeable in extended hours.

4. **Session Detection**: Use existing `get_market_session()` utility - no new code needed.

5. **Event Bus**: Use existing `AsyncEventBus` - no new code needed.

6. **Type Safety**: All models use Pydantic with full type hints - matches existing codebase patterns.

---

## Summary

This plan implements two lightweight, stateless statistics engines that:
- ✅ Run alongside the main trading system
- ✅ Use event-driven architecture
- ✅ Write records in real-time to JSON files
- ✅ Follow existing codebase patterns (repository, dependency injection, type safety)
- ✅ Require minimal code changes (extend composition root)
- ✅ Provide clear metrics on missed opportunities (recall) and trade performance (signal)
