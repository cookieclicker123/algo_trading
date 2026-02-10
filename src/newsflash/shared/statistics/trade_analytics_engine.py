"""
Trade Analytics Engine - Captures detailed exit timing and tape degradation data.

PURPOSE: Build dataset to answer:
1. When are we exiting too early? (price continued higher)
2. When are we exiting too late? (missed the peak)
3. What tape characteristics predict optimal exit timing?

DATA COLLECTED:
- Entry conditions (price, tape stats, industry, market cap, headline type)
- Tape snapshots every 5 seconds during hold
- Peak detection (price and tape at peak)
- Exit conditions (actual exit vs optimal exit)
- Post-exit monitoring (did we leave money on table?)

ANALYSIS GOALS:
- Find tape signatures that predict "hold longer" vs "exit now"
- Segment by industry, market cap, headline type
- Build intelligent take-profit rules from empirical data
"""

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any
from decimal import Decimal

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TapeSnapshot:
    """Point-in-time tape microstructure snapshot."""
    timestamp: datetime
    seconds_since_entry: float

    # Price data
    price: float
    bid: float
    ask: float
    spread: float
    spread_pct: float  # spread as % of mid

    # Volume data (cumulative since entry)
    cumulative_volume: int
    cumulative_trades: int
    volume_rate_per_sec: float  # volume in last interval
    trade_rate_per_sec: float   # trades in last interval

    # Order flow
    buy_volume: int
    sell_volume: int
    imbalance_ratio: float      # (buy - sell) / (buy + sell), range -1 to +1
    buying_pressure_pct: float  # buy / total * 100

    # Derived metrics
    profit_pct: float           # current profit from entry
    distance_from_peak_pct: float  # how far below the peak we are

    # Momentum indicators
    price_velocity: float       # price change since last snapshot
    volume_acceleration: float  # volume rate change from prior snapshot
    spread_change_pct: float    # spread change from entry

    def to_dict(self) -> dict:
        d = asdict(self)
        d['timestamp'] = self.timestamp.isoformat()
        return d


@dataclass
class TapePrintCounts:
    """
    Counts of prints (trades) at various microstructure levels.

    This captures HOW LONG the tape stayed in certain states,
    not just point-in-time snapshots. Critical for understanding
    the time dimension of tape degradation.
    """
    total_prints: int = 0
    total_volume: int = 0

    # Prints by imbalance level
    prints_imbalance_very_positive: int = 0   # > 0.5
    prints_imbalance_positive: int = 0         # 0.2 to 0.5
    prints_imbalance_neutral: int = 0          # -0.2 to 0.2
    prints_imbalance_negative: int = 0         # -0.5 to -0.2
    prints_imbalance_very_negative: int = 0    # < -0.5

    # Prints by spread level (as % of mid)
    prints_spread_tight: int = 0      # < 1%
    prints_spread_normal: int = 0     # 1-3%
    prints_spread_wide: int = 0       # 3-5%
    prints_spread_very_wide: int = 0  # > 5%

    # Prints by volume rate (prints per second)
    prints_volume_surge: int = 0      # > 50 prints/sec
    prints_volume_high: int = 0       # 20-50 prints/sec
    prints_volume_normal: int = 0     # 5-20 prints/sec
    prints_volume_low: int = 0        # < 5 prints/sec

    # Prints by profit level (relative to entry)
    prints_profit_high: int = 0       # > 10%
    prints_profit_good: int = 0       # 5-10%
    prints_profit_small: int = 0      # 0-5%
    prints_loss: int = 0              # < 0%

    # Prints after peak (degradation tracking)
    prints_after_peak: int = 0
    prints_after_peak_imbalance_negative: int = 0  # Selling pressure after peak

    # Time spent in each state (seconds)
    time_imbalance_positive: float = 0.0
    time_imbalance_negative: float = 0.0
    time_above_5pct_profit: float = 0.0
    time_above_10pct_profit: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradeAnalyticsRecord:
    """Complete analytics record for a single trade."""

    # Identity
    trade_id: str
    article_id: str
    ticker: str

    # Context (for segmentation)
    industry: str
    sector: str
    market_cap_millions: float
    float_shares: Optional[int]
    headline: str
    headline_type: str  # e.g., "acquisition", "fda_approval", "partnership"
    conviction_level: str

    # Entry conditions
    entry_time: datetime
    entry_price: float
    entry_spread_pct: float
    entry_imbalance: float
    entry_buying_pressure: float
    entry_volume_rate: float
    position_size_usd: float
    shares: int

    # Peak tracking (updated throughout hold)
    peak_price: float = 0.0
    peak_time: Optional[datetime] = None
    peak_profit_pct: float = 0.0
    seconds_to_peak: float = 0.0

    # Tape at peak (what did healthy tape look like?)
    tape_at_peak: Optional[TapeSnapshot] = None

    # Degradation tracking (tape snapshots after peak)
    # These help us learn what "degrading" looks like
    snapshots_after_peak: List[TapeSnapshot] = field(default_factory=list)

    # All tape snapshots (for full analysis)
    tape_snapshots: List[TapeSnapshot] = field(default_factory=list)

    # Print counts - how many prints at each level (time dimension)
    print_counts: TapePrintCounts = field(default_factory=TapePrintCounts)

    # Exit conditions
    exit_time: Optional[datetime] = None
    exit_price: float = 0.0
    exit_reason: str = ""  # "tier_10pct", "tier_15pct", "floor", "stop_loss", "time_exit"
    exit_profit_pct: float = 0.0
    seconds_held: float = 0.0

    # Tape at exit
    tape_at_exit: Optional[TapeSnapshot] = None

    # Exit quality metrics (computed after trade)
    exit_vs_peak_pct: float = 0.0      # how much we missed the peak by
    exit_timing_quality: str = ""       # "optimal", "early", "late", "very_late"
    money_left_on_table_pct: float = 0.0  # peak - exit as % of entry

    # Post-exit monitoring (did price continue after we left?)
    post_exit_high: float = 0.0
    post_exit_high_time: Optional[datetime] = None
    post_exit_low: float = 0.0
    price_5min_after_exit: float = 0.0
    continued_higher: bool = False      # did it go higher after we exited?

    # Final assessment
    optimal_exit_price: float = 0.0     # what we should have exited at
    optimal_exit_time: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        d = {
            'trade_id': self.trade_id,
            'article_id': self.article_id,
            'ticker': self.ticker,
            'industry': self.industry,
            'sector': self.sector,
            'market_cap_millions': self.market_cap_millions,
            'float_shares': self.float_shares,
            'headline': self.headline,
            'headline_type': self.headline_type,
            'conviction_level': self.conviction_level,
            'entry_time': self.entry_time.isoformat(),
            'entry_price': self.entry_price,
            'entry_spread_pct': self.entry_spread_pct,
            'entry_imbalance': self.entry_imbalance,
            'entry_buying_pressure': self.entry_buying_pressure,
            'entry_volume_rate': self.entry_volume_rate,
            'position_size_usd': self.position_size_usd,
            'shares': self.shares,
            'peak_price': self.peak_price,
            'peak_time': self.peak_time.isoformat() if self.peak_time else None,
            'peak_profit_pct': self.peak_profit_pct,
            'seconds_to_peak': self.seconds_to_peak,
            'tape_at_peak': self.tape_at_peak.to_dict() if self.tape_at_peak else None,
            'snapshots_after_peak_count': len(self.snapshots_after_peak),
            'tape_snapshots_count': len(self.tape_snapshots),
            'tape_snapshots': [s.to_dict() for s in self.tape_snapshots],
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'exit_price': self.exit_price,
            'exit_reason': self.exit_reason,
            'exit_profit_pct': self.exit_profit_pct,
            'seconds_held': self.seconds_held,
            'tape_at_exit': self.tape_at_exit.to_dict() if self.tape_at_exit else None,
            'exit_vs_peak_pct': self.exit_vs_peak_pct,
            'exit_timing_quality': self.exit_timing_quality,
            'money_left_on_table_pct': self.money_left_on_table_pct,
            'post_exit_high': self.post_exit_high,
            'post_exit_high_time': self.post_exit_high_time.isoformat() if self.post_exit_high_time else None,
            'post_exit_low': self.post_exit_low,
            'price_5min_after_exit': self.price_5min_after_exit,
            'continued_higher': self.continued_higher,
            'optimal_exit_price': self.optimal_exit_price,
            'optimal_exit_time': self.optimal_exit_time.isoformat() if self.optimal_exit_time else None,
            'print_counts': self.print_counts.to_dict(),
        }
        return d


class TradeAnalyticsEngine:
    """
    Engine to collect detailed trade analytics for exit optimization.

    Monitors active trades, captures tape snapshots, and tracks exit quality.
    Data is stored for later analysis to find patterns in:
    - Industry-specific exit timing
    - Market cap effects on price action
    - Tape degradation signatures
    - Headline type impact on hold duration
    """

    # REAL-TIME MONITORING - Trade management is the most critical moment
    # Use 100ms intervals to capture every meaningful tape change
    SNAPSHOT_INTERVAL_SECONDS = 0.1   # 100ms - real-time WebSocket monitoring
    SNAPSHOT_SAVE_INTERVAL = 1.0      # Save snapshots every 1 second (not every 100ms)
    POST_EXIT_MONITOR_SECONDS = 300   # Monitor for 5 minutes after exit

    def __init__(
        self,
        event_bus,
        quote_fetcher=None,
        stream_manager=None,
        metadata_cache=None,
        storage_path: Path = None,
    ):
        self.event_bus = event_bus
        self.quote_fetcher = quote_fetcher
        self.stream_manager = stream_manager
        self.metadata_cache = metadata_cache
        self.storage_path = storage_path or Path("tmp/statistics/trade_analytics")

        # Active trade tracking
        self._active_records: Dict[str, TradeAnalyticsRecord] = {}  # trade_id -> record
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._post_exit_tasks: Dict[str, asyncio.Task] = {}

        # Cumulative tape stats per ticker (reset on new trade)
        self._tape_accumulators: Dict[str, Dict] = {}

        self._running = False

    async def start(self):
        """Start the analytics engine."""
        self._running = True

        # Subscribe to trade events
        # TradeExecutedDomainEvent contains trade_result with action (buy/sell)
        self.event_bus.subscribe(
            "Domain.TradeExecuted",
            self._handle_trade_executed
        )

        # Ensure storage directory exists
        self.storage_path.mkdir(parents=True, exist_ok=True)

        logger.info(
            "TradeAnalyticsEngine started",
            storage_path=str(self.storage_path),
            snapshot_interval=self.SNAPSHOT_INTERVAL_SECONDS,
        )

    async def stop(self):
        """Stop the analytics engine."""
        self._running = False

        # Cancel all monitoring tasks
        for task in self._monitoring_tasks.values():
            task.cancel()
        for task in self._post_exit_tasks.values():
            task.cancel()

        # Save any remaining records
        for record in self._active_records.values():
            await self._save_record(record)

        logger.info("TradeAnalyticsEngine stopped")

    async def _handle_trade_executed(self, event_data: dict):
        """Handle trade executed event - dispatch to entry or exit handler."""
        try:
            # Extract trade_result from event
            trade_result = event_data.get("trade_result", {})
            if not trade_result:
                # Fallback for direct event structure
                trade_result = event_data

            action = trade_result.get("action", "").lower()
            ticker = trade_result.get("ticker")

            if not ticker:
                return

            if action == "buy":
                await self._handle_trade_entry(event_data, trade_result)
            elif action == "sell":
                await self._handle_trade_exit(event_data, trade_result)

        except Exception as e:
            logger.error(f"Error handling trade executed: {e}", exc_info=True)

    async def _handle_trade_entry(self, event_data: dict, trade_result: dict):
        """Handle new trade entry - start monitoring."""
        try:
            trade_id = trade_result.get("trade_id") or trade_result.get("order_id")
            ticker = trade_result.get("ticker")
            article_id = trade_result.get("article_id")

            if not trade_id or not ticker:
                return

            # Skip if already tracking this ticker (avoid duplicates)
            for record in self._active_records.values():
                if record.ticker == ticker:
                    logger.debug(f"Already tracking {ticker}, skipping duplicate entry")
                    return

            # Get metadata for segmentation
            metadata = {}
            if self.metadata_cache:
                metadata = await self.metadata_cache.get_permanent(ticker) or {}

            # Classify headline type
            headline = trade_result.get("headline", "") or event_data.get("headline", "")
            headline_type = self._classify_headline(headline)

            # Get entry tape stats
            entry_tape = await self._get_current_tape_stats(ticker, 0.0, 0.0)

            # Extract entry price and shares from trade_result
            entry_price = float(trade_result.get("filled_avg_price") or trade_result.get("entry_price") or 0)
            shares = int(float(trade_result.get("filled_qty") or trade_result.get("shares") or 0))
            position_size = entry_price * shares if entry_price and shares else 0

            # Get entry time from event
            executed_at = event_data.get("executed_at")
            if executed_at:
                if isinstance(executed_at, str):
                    entry_time = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
                else:
                    entry_time = executed_at
            else:
                entry_time = datetime.now()

            # Create analytics record
            record = TradeAnalyticsRecord(
                trade_id=trade_id,
                article_id=article_id,
                ticker=ticker,
                industry=metadata.get("industry", "Unknown"),
                sector=metadata.get("sector", "Unknown"),
                market_cap_millions=metadata.get("market_cap_millions", 0),
                float_shares=metadata.get("float_shares"),
                headline=headline[:200] if headline else "",
                headline_type=headline_type,
                conviction_level=trade_result.get("conviction") or event_data.get("conviction", "standard"),
                entry_time=entry_time,
                entry_price=entry_price,
                entry_spread_pct=entry_tape.spread_pct if entry_tape else 0,
                entry_imbalance=entry_tape.imbalance_ratio if entry_tape else 0,
                entry_buying_pressure=entry_tape.buying_pressure_pct if entry_tape else 0,
                entry_volume_rate=entry_tape.volume_rate_per_sec if entry_tape else 0,
                position_size_usd=position_size,
                shares=shares,
                peak_price=entry_price,
                peak_time=entry_time,
            )

            # Initialize tape accumulator
            self._tape_accumulators[ticker] = {
                "cumulative_volume": 0,
                "cumulative_trades": 0,
                "cumulative_buy_volume": 0,
                "cumulative_sell_volume": 0,
                "last_snapshot_time": datetime.now(),
                "last_volume": 0,
                "last_trades": 0,
            }

            self._active_records[trade_id] = record

            # Start monitoring task
            task = asyncio.create_task(self._monitor_trade(trade_id))
            self._monitoring_tasks[trade_id] = task

            logger.info(
                "TradeAnalyticsEngine: Started monitoring trade",
                trade_id=trade_id,
                ticker=ticker,
                industry=record.industry,
                market_cap=record.market_cap_millions,
                headline_type=headline_type,
            )

        except Exception as e:
            logger.error(f"Error handling trade executed: {e}", exc_info=True)

    async def _handle_trade_exit(self, event_data: dict, trade_result: dict):
        """Handle trade exit - finalize record and start post-exit monitoring."""
        try:
            ticker = trade_result.get("ticker")

            # Find active record for this ticker (exits may have different trade_id)
            record = None
            original_trade_id = None
            for tid, rec in self._active_records.items():
                if rec.ticker == ticker:
                    record = rec
                    original_trade_id = tid
                    break

            if not record:
                logger.debug(f"No active analytics record for {ticker} exit")
                return

            # Cancel monitoring task
            if original_trade_id in self._monitoring_tasks:
                self._monitoring_tasks[original_trade_id].cancel()
                del self._monitoring_tasks[original_trade_id]

            # Extract exit data from trade_result
            exit_price = float(trade_result.get("filled_avg_price") or trade_result.get("exit_price") or 0)

            # Get exit time from event
            executed_at = event_data.get("executed_at")
            if executed_at:
                if isinstance(executed_at, str):
                    exit_time = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
                else:
                    exit_time = executed_at
            else:
                exit_time = datetime.now()

            # Get exit reason from metadata if available
            trade_metadata = trade_result.get("metadata", {}) or {}
            exit_reason = trade_metadata.get("exit_reason") or trade_result.get("exit_reason") or "unknown"

            # Update exit data
            record.exit_time = exit_time
            record.exit_price = exit_price
            record.exit_reason = exit_reason
            record.seconds_held = (record.exit_time - record.entry_time).total_seconds()

            # Calculate exit profit
            if record.entry_price > 0 and exit_price > 0:
                record.exit_profit_pct = (exit_price - record.entry_price) / record.entry_price * 100

            # Get tape at exit
            record.tape_at_exit = await self._get_current_tape_stats(
                record.ticker,
                record.seconds_held,
                record.peak_price
            )

            # Calculate exit quality vs peak
            if record.peak_price > 0 and record.entry_price > 0:
                record.exit_vs_peak_pct = (record.peak_price - record.exit_price) / record.entry_price * 100
                record.money_left_on_table_pct = record.peak_profit_pct - record.exit_profit_pct

                # Classify exit timing
                if record.exit_vs_peak_pct < 1:
                    record.exit_timing_quality = "optimal"  # Within 1% of peak
                elif record.exit_vs_peak_pct < 3:
                    record.exit_timing_quality = "good"     # Within 3% of peak
                elif record.exit_vs_peak_pct < 5:
                    record.exit_timing_quality = "late"     # 3-5% below peak
                else:
                    record.exit_timing_quality = "very_late"  # >5% below peak

            logger.info(
                "TradeAnalyticsEngine: Trade exited",
                trade_id=original_trade_id,
                ticker=record.ticker,
                exit_reason=record.exit_reason,
                exit_profit_pct=f"{record.exit_profit_pct:.1f}%",
                peak_profit_pct=f"{record.peak_profit_pct:.1f}%",
                exit_timing_quality=record.exit_timing_quality,
                money_left_on_table=f"{record.money_left_on_table_pct:.1f}%",
            )

            # Start post-exit monitoring
            task = asyncio.create_task(self._monitor_post_exit(original_trade_id))
            self._post_exit_tasks[original_trade_id] = task

        except Exception as e:
            logger.error(f"Error handling trade exit: {e}", exc_info=True)

    async def _monitor_trade(self, trade_id: str):
        """
        Monitor active trade with real-time WebSocket data.

        - 100ms intervals for print counting (captures every tape change)
        - 1 second intervals for full snapshot saving (avoids data bloat)
        - Tracks print counts at various levels for time dimension analysis
        """
        try:
            record = self._active_records.get(trade_id)
            if not record:
                return

            last_snapshot_save = datetime.now()
            last_print_count = 0
            passed_peak = False

            while self._running and trade_id in self._active_records:
                await asyncio.sleep(self.SNAPSHOT_INTERVAL_SECONDS)  # 100ms

                if trade_id not in self._active_records:
                    break

                record = self._active_records[trade_id]
                now = datetime.now()
                seconds_since_entry = (now - record.entry_time).total_seconds()

                # Get current tape snapshot
                snapshot = await self._get_current_tape_stats(
                    record.ticker,
                    seconds_since_entry,
                    record.peak_price
                )

                if not snapshot:
                    continue

                # Count new prints since last check
                new_prints = snapshot.cumulative_trades - last_print_count
                last_print_count = snapshot.cumulative_trades

                if new_prints > 0:
                    # Update print counts by level
                    pc = record.print_counts
                    pc.total_prints += new_prints
                    pc.total_volume += int(snapshot.volume_rate_per_sec * self.SNAPSHOT_INTERVAL_SECONDS)

                    # By imbalance level
                    if snapshot.imbalance_ratio > 0.5:
                        pc.prints_imbalance_very_positive += new_prints
                    elif snapshot.imbalance_ratio > 0.2:
                        pc.prints_imbalance_positive += new_prints
                    elif snapshot.imbalance_ratio > -0.2:
                        pc.prints_imbalance_neutral += new_prints
                    elif snapshot.imbalance_ratio > -0.5:
                        pc.prints_imbalance_negative += new_prints
                    else:
                        pc.prints_imbalance_very_negative += new_prints

                    # By spread level
                    if snapshot.spread_pct < 1.0:
                        pc.prints_spread_tight += new_prints
                    elif snapshot.spread_pct < 3.0:
                        pc.prints_spread_normal += new_prints
                    elif snapshot.spread_pct < 5.0:
                        pc.prints_spread_wide += new_prints
                    else:
                        pc.prints_spread_very_wide += new_prints

                    # By volume rate (prints per second)
                    prints_per_sec = new_prints / self.SNAPSHOT_INTERVAL_SECONDS
                    if prints_per_sec > 50:
                        pc.prints_volume_surge += new_prints
                    elif prints_per_sec > 20:
                        pc.prints_volume_high += new_prints
                    elif prints_per_sec > 5:
                        pc.prints_volume_normal += new_prints
                    else:
                        pc.prints_volume_low += new_prints

                    # By profit level
                    if snapshot.profit_pct > 10:
                        pc.prints_profit_high += new_prints
                    elif snapshot.profit_pct > 5:
                        pc.prints_profit_good += new_prints
                    elif snapshot.profit_pct > 0:
                        pc.prints_profit_small += new_prints
                    else:
                        pc.prints_loss += new_prints

                    # After peak tracking
                    if passed_peak:
                        pc.prints_after_peak += new_prints
                        if snapshot.imbalance_ratio < 0:
                            pc.prints_after_peak_imbalance_negative += new_prints

                # Update time spent in states
                interval = self.SNAPSHOT_INTERVAL_SECONDS
                pc = record.print_counts
                if snapshot.imbalance_ratio > 0:
                    pc.time_imbalance_positive += interval
                else:
                    pc.time_imbalance_negative += interval
                if snapshot.profit_pct > 5:
                    pc.time_above_5pct_profit += interval
                if snapshot.profit_pct > 10:
                    pc.time_above_10pct_profit += interval

                # Update peak tracking
                if snapshot.price > record.peak_price:
                    record.peak_price = snapshot.price
                    record.peak_time = now
                    record.peak_profit_pct = snapshot.profit_pct
                    record.seconds_to_peak = seconds_since_entry
                    record.tape_at_peak = snapshot
                    # Clear post-peak snapshots since we have a new peak
                    record.snapshots_after_peak = []
                    passed_peak = False
                else:
                    passed_peak = True

                # Save full snapshot every 1 second (not every 100ms)
                if (now - last_snapshot_save).total_seconds() >= self.SNAPSHOT_SAVE_INTERVAL:
                    record.tape_snapshots.append(snapshot)
                    last_snapshot_save = now

                    if passed_peak:
                        record.snapshots_after_peak.append(snapshot)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error monitoring trade {trade_id}: {e}", exc_info=True)

    async def _monitor_post_exit(self, trade_id: str):
        """Monitor price action after exit to assess exit quality."""
        try:
            record = self._active_records.get(trade_id)
            if not record or not record.exit_time:
                return

            record.post_exit_high = record.exit_price
            record.post_exit_low = record.exit_price

            start_time = datetime.now()

            while self._running:
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > self.POST_EXIT_MONITOR_SECONDS:
                    break

                await asyncio.sleep(5.0)  # Check every 5 seconds

                # Get current price
                current_price = await self._get_current_price(record.ticker)
                if current_price is None:
                    continue

                # Track post-exit extremes
                if current_price > record.post_exit_high:
                    record.post_exit_high = current_price
                    record.post_exit_high_time = datetime.now()
                    record.continued_higher = True

                if current_price < record.post_exit_low:
                    record.post_exit_low = current_price

                # Capture 5-min price
                if 295 <= elapsed <= 305:
                    record.price_5min_after_exit = current_price

            # Determine optimal exit (hindsight)
            if record.post_exit_high > record.exit_price:
                record.optimal_exit_price = record.post_exit_high
                record.optimal_exit_time = record.post_exit_high_time
            else:
                record.optimal_exit_price = record.exit_price
                record.optimal_exit_time = record.exit_time

            # Update exit timing quality if we left money on table
            if record.continued_higher:
                additional_missed = (record.post_exit_high - record.exit_price) / record.entry_price * 100
                if additional_missed > 5:
                    record.exit_timing_quality = "too_early"

            # Save final record
            await self._save_record(record)

            # Clean up
            del self._active_records[trade_id]
            if trade_id in self._post_exit_tasks:
                del self._post_exit_tasks[trade_id]
            if record.ticker in self._tape_accumulators:
                del self._tape_accumulators[record.ticker]

            logger.info(
                "TradeAnalyticsEngine: Post-exit monitoring complete",
                trade_id=trade_id,
                ticker=record.ticker,
                continued_higher=record.continued_higher,
                post_exit_high_vs_exit=f"+{(record.post_exit_high/record.exit_price - 1)*100:.1f}%" if record.exit_price > 0 else "N/A",
                final_exit_quality=record.exit_timing_quality,
            )

        except asyncio.CancelledError:
            # Save record even if cancelled
            if trade_id in self._active_records:
                await self._save_record(self._active_records[trade_id])
        except Exception as e:
            logger.error(f"Error in post-exit monitoring {trade_id}: {e}", exc_info=True)

    async def _get_current_tape_stats(
        self,
        ticker: str,
        seconds_since_entry: float,
        peak_price: float,
    ) -> Optional[TapeSnapshot]:
        """Get current tape microstructure snapshot."""
        try:
            if not self.stream_manager:
                return None

            # Get NBBO
            nbbo = None
            if self.quote_fetcher:
                nbbo = await self.quote_fetcher.get_quote(ticker)

            if not nbbo:
                return None

            bid = nbbo.get("bid", 0)
            ask = nbbo.get("ask", 0)
            mid = (bid + ask) / 2 if bid and ask else ask or bid
            spread = ask - bid if bid and ask else 0
            spread_pct = (spread / mid * 100) if mid > 0 else 0

            # Get accumulated trade stats from stream manager
            tape_stats = {}
            if hasattr(self.stream_manager, 'get_ticker_stats'):
                tape_stats = self.stream_manager.get_ticker_stats(ticker) or {}

            # Get accumulator for this ticker
            acc = self._tape_accumulators.get(ticker, {})

            # Calculate rates
            now = datetime.now()
            time_delta = (now - acc.get("last_snapshot_time", now)).total_seconds()
            time_delta = max(time_delta, 1.0)  # Avoid division by zero

            current_volume = tape_stats.get("total_volume", 0)
            current_trades = tape_stats.get("trade_count", 0)

            volume_delta = current_volume - acc.get("last_volume", 0)
            trade_delta = current_trades - acc.get("last_trades", 0)

            volume_rate = volume_delta / time_delta
            trade_rate = trade_delta / time_delta

            # Get order flow stats
            buy_volume = tape_stats.get("buy_volume", 0)
            sell_volume = tape_stats.get("sell_volume", 0)
            total_volume = buy_volume + sell_volume

            imbalance = (buy_volume - sell_volume) / total_volume if total_volume > 0 else 0
            buying_pressure = (buy_volume / total_volume * 100) if total_volume > 0 else 50

            # Calculate profit and distance from peak
            entry_record = None
            for record in self._active_records.values():
                if record.ticker == ticker:
                    entry_record = record
                    break

            entry_price = entry_record.entry_price if entry_record else mid
            profit_pct = ((mid - entry_price) / entry_price * 100) if entry_price > 0 else 0
            distance_from_peak = ((peak_price - mid) / entry_price * 100) if peak_price > 0 and entry_price > 0 else 0

            # Calculate velocity (price change since last snapshot)
            last_price = acc.get("last_price", mid)
            price_velocity = (mid - last_price) / time_delta if time_delta > 0 else 0

            # Calculate volume acceleration
            last_volume_rate = acc.get("last_volume_rate", volume_rate)
            volume_acceleration = volume_rate - last_volume_rate

            # Calculate spread change from entry
            entry_spread = entry_record.entry_spread_pct if entry_record else spread_pct
            spread_change = spread_pct - entry_spread

            # Update accumulator
            self._tape_accumulators[ticker] = {
                "cumulative_volume": current_volume,
                "cumulative_trades": current_trades,
                "cumulative_buy_volume": buy_volume,
                "cumulative_sell_volume": sell_volume,
                "last_snapshot_time": now,
                "last_volume": current_volume,
                "last_trades": current_trades,
                "last_price": mid,
                "last_volume_rate": volume_rate,
            }

            return TapeSnapshot(
                timestamp=now,
                seconds_since_entry=seconds_since_entry,
                price=mid,
                bid=bid,
                ask=ask,
                spread=spread,
                spread_pct=spread_pct,
                cumulative_volume=current_volume,
                cumulative_trades=current_trades,
                volume_rate_per_sec=volume_rate,
                trade_rate_per_sec=trade_rate,
                buy_volume=buy_volume,
                sell_volume=sell_volume,
                imbalance_ratio=imbalance,
                buying_pressure_pct=buying_pressure,
                profit_pct=profit_pct,
                distance_from_peak_pct=distance_from_peak,
                price_velocity=price_velocity,
                volume_acceleration=volume_acceleration,
                spread_change_pct=spread_change,
            )

        except Exception as e:
            logger.error(f"Error getting tape stats for {ticker}: {e}")
            return None

    async def _get_current_price(self, ticker: str) -> Optional[float]:
        """Get current mid price for ticker."""
        try:
            if self.quote_fetcher:
                nbbo = await self.quote_fetcher.get_quote(ticker)
                if nbbo:
                    bid = nbbo.get("bid", 0)
                    ask = nbbo.get("ask", 0)
                    return (bid + ask) / 2 if bid and ask else ask or bid
            return None
        except Exception:
            return None

    def _classify_headline(self, headline: str) -> str:
        """Classify headline into type for segmentation."""
        headline_lower = headline.lower()

        # M&A
        if any(word in headline_lower for word in ["acquisition", "acquire", "merger", "buyout", "to be acquired"]):
            return "acquisition"

        # FDA/Regulatory
        if any(word in headline_lower for word in ["fda approval", "fda approves", "fda clears", "breakthrough therapy"]):
            return "fda_approval"
        if "fda" in headline_lower:
            return "fda_other"

        # Clinical trials
        if any(word in headline_lower for word in ["phase 3", "phase 2", "clinical trial", "primary endpoint"]):
            return "clinical_trial"

        # Partnerships
        if any(word in headline_lower for word in ["partnership", "partners with", "collaboration", "agreement"]):
            return "partnership"

        # Contracts
        if any(word in headline_lower for word in ["contract", "awarded", "wins"]):
            return "contract"

        # Offerings
        if any(word in headline_lower for word in ["offering", "placement", "financing"]):
            return "offering"

        # Earnings
        if any(word in headline_lower for word in ["earnings", "revenue", "eps", "quarterly"]):
            return "earnings"

        return "other"

    async def _save_record(self, record: TradeAnalyticsRecord):
        """Save analytics record to storage."""
        try:
            # Organize by date
            date_str = record.entry_time.strftime("%Y/%m/%d")
            file_path = self.storage_path / date_str / f"{record.trade_id}.json"
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(file_path, 'w') as f:
                json.dump(record.to_dict(), f, indent=2, default=str)

            logger.debug(f"Saved trade analytics record: {file_path}")

        except Exception as e:
            logger.error(f"Error saving analytics record: {e}", exc_info=True)
