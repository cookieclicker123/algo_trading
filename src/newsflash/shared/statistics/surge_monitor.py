"""
Surge monitor module - handles 2-minute surge detection monitoring.

Extracted from RecallStatsEngine to separate surge detection logic.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Dict, Set, Protocol, Callable, Awaitable

from ...utils.logging_config import get_logger
from .volume_analyzer import analyze_volume_around_event

logger = get_logger(__name__)


class QuoteFetcherProtocol(Protocol):
    """Protocol for quote fetching."""
    async def get_nbbo_snapshot(self, ticker: str) -> Optional[Dict[str, Any]]: ...
    @property
    def stream_manager(self) -> Optional[Any]: ...


class MetadataFetcherProtocol(Protocol):
    """Protocol for metadata fetching."""
    async def fetch_metadata(
        self,
        ticker: str,
        timeout: float = 30.0,
        queue_on_failure: bool = False,
        callback: Optional[Any] = None
    ) -> Optional[Dict[str, Any]]: ...


class RepositoryProtocol(Protocol):
    """Protocol for record updates."""
    async def update_recall_record(
        self,
        article_id: str,
        updates: Dict[str, Any],
        session: str,
        date: datetime
    ) -> bool: ...


class SurgeMonitor:
    """
    Monitors tickers for SURGE detection over 2 minutes.

    Responsibilities:
    - Run 30 cycles of 4-second windows
    - Analyze volume/price for surge detection
    - Trigger trade callback on surge detection
    - Update record with monitoring progress

    Design:
    - Receives dependencies via protocols (testable)
    - Uses callback for trade triggering (decoupled from trade logic)
    """

    def __init__(
        self,
        market_data_client: Any,  # StockHistoricalDataClient
        quote_fetcher: QuoteFetcherProtocol,
        metadata_fetcher: MetadataFetcherProtocol,
        repository: RepositoryProtocol,
        traded_articles: Set[str],
        traded_lock: asyncio.Lock,
        monitoring_tasks: Dict[str, asyncio.Task],
        monitoring_lock: asyncio.Lock,
        on_surge_detected: Callable[[Any, str], Awaitable[None]],
        on_monitoring_complete: Optional[Callable[[list[str], str, bool], Awaitable[None]]] = None,
        metadata_cache: Optional[Any] = None  # MetadataCache for cached float shares
    ):
        """
        Initialize surge monitor.

        Args:
            market_data_client: Alpaca market data client for volume analysis
            quote_fetcher: Quote fetcher for NBBO snapshots
            metadata_fetcher: Yahoo Finance coordinator for sector data
            repository: Statistics repository for record updates
            traded_articles: Shared set of traded article IDs
            traded_lock: Lock protecting traded_articles set
            monitoring_tasks: Shared dict of monitoring tasks
            monitoring_lock: Lock protecting monitoring_tasks dict
            on_surge_detected: Callback when surge is detected (article, ticker)
            on_monitoring_complete: Callback when monitoring finishes (tickers, article_id, was_traded)
        """
        self.market_data_client = market_data_client
        self.quote_fetcher = quote_fetcher
        self.metadata_fetcher = metadata_fetcher
        self.repository = repository
        self._traded_articles = traded_articles
        self._traded_lock = traded_lock
        self._monitoring_tasks = monitoring_tasks
        self._monitoring_lock = monitoring_lock
        self._on_surge_detected = on_surge_detected
        self._on_monitoring_complete = on_monitoring_complete
        self._metadata_cache = metadata_cache

    async def monitor_for_surge(
        self,
        article: Any,
        tradable_tickers: list[str],
        initial_nbbos: Dict[str, Dict[str, Any]],
        session: str,
        received_at: datetime,
        published_at: datetime
    ) -> None:
        """
        Monitor for SURGE detection over 2 minutes (30 cycles of 4-second windows).

        Args:
            article: Domain Article model
            tradable_tickers: List of tradable ticker symbols
            initial_nbbos: Initial NBBO snapshots
            session: Market session
            received_at: When article was received
            published_at: When article was published
        """
        try:
            max_cycles = 30  # 30 cycles * 4 seconds = 120 seconds = 2 minutes
            cycle_duration = 4.0  # 4-second windows

            logger.info(
                "SurgeMonitor: Starting 2-minute monitoring",
                article_id=article.id,
                tickers=tradable_tickers,
                max_cycles=max_cycles
            )

            for cycle in range(max_cycles):
                # Check if article was already traded
                async with self._traded_lock:
                    if article.id in self._traded_articles:
                        logger.debug(
                            "SurgeMonitor: Article traded, stopping",
                            article_id=article.id,
                            cycle=cycle
                        )
                        break

                # Calculate and wait for window start time
                window_start = await self._wait_for_window(published_at, cycle, cycle_duration)

                if not self.market_data_client:
                    logger.warning("SurgeMonitor: No market data client available")
                    break

                # Analyze all tickers in parallel
                surge_result = await self._analyze_cycle(
                    article.id, tradable_tickers, initial_nbbos, window_start, cycle
                )

                # Update record with cycle progress (fire-and-forget)
                asyncio.create_task(self.repository.update_recall_record(
                    article_id=article.id,
                    updates={"monitoring_cycles_completed": cycle + 1},
                    session=session,
                    date=received_at
                ))

                if surge_result:
                    surge_ticker, surge_stats = surge_result

                    # CRITICAL: Trigger trade IMMEDIATELY (fire-and-forget)
                    asyncio.create_task(self._on_surge_detected(article, surge_ticker))

                    # Compute early/late mover classification
                    surge_detected_at = datetime.now(timezone.utc)
                    pub_time_utc = published_at.replace(tzinfo=timezone.utc) if published_at.tzinfo is None else published_at
                    time_to_surge_seconds = round((surge_detected_at - pub_time_utc).total_seconds(), 1)
                    is_first_mover = cycle <= 1  # First 2 cycles = first 8 seconds

                    # Update record with surge detection (fire-and-forget)
                    asyncio.create_task(self.repository.update_recall_record(
                        article_id=article.id,
                        updates={
                            "monitoring_status": "surge_detected",
                            "surge_detected_at": surge_detected_at,
                            "surge_detection_cycle": cycle,
                            "surge_detection_window_stats": surge_stats,
                            "monitoring_completed_at": surge_detected_at,
                            "is_first_mover": is_first_mover,
                            "time_to_surge_seconds": time_to_surge_seconds,
                        },
                        session=session,
                        date=received_at
                    ))
                    break

                # Wait for next cycle
                await self._wait_for_next_cycle(window_start, cycle_duration)

            # Monitoring completed - clean up subscriptions
            await self._finalize_monitoring(article.id, tradable_tickers, session, received_at, max_cycles)

        except asyncio.CancelledError:
            logger.debug("SurgeMonitor: Task cancelled", article_id=article.id)
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article.id, None)
            raise
        except Exception as e:
            logger.error(
                "SurgeMonitor: Error in monitoring",
                article_id=article.id,
                error=str(e),
                exc_info=True
            )
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article.id, None)

    async def _wait_for_window(
        self,
        published_at: datetime,
        cycle: int,
        cycle_duration: float
    ) -> datetime:
        """Calculate window start time and wait if needed."""
        window_start = published_at + timedelta(seconds=(cycle + 1) * cycle_duration)

        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if window_start > now:
            wait_time = (window_start - now).total_seconds()
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        return window_start

    async def _analyze_cycle(
        self,
        article_id: str,
        tickers: list[str],
        initial_nbbos: Dict[str, Dict[str, Any]],
        window_start: datetime,
        cycle: int
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        """
        Analyze all tickers in parallel for surge detection.

        Returns:
            (ticker, surge_stats) if surge detected, None otherwise
        """
        async def analyze_ticker(ticker: str):
            """Analyze a single ticker for surge."""
            try:
                # Get sector (quick fetch, queue on failure)
                ticker_sector = None
                try:
                    ticker_meta = await self.metadata_fetcher.fetch_metadata(
                        ticker, timeout=1.0, queue_on_failure=True
                    )
                    if ticker_meta:
                        ticker_sector = ticker_meta.get("sector")
                except (asyncio.TimeoutError, Exception):
                    pass

                # Get cached float_shares (instant, ~0ms)
                cached_float = None
                if self._metadata_cache:
                    try:
                        cached_float = await self._metadata_cache.get_float(ticker)
                    except Exception:
                        pass

                # Analyze 4-second window
                volume_analysis = await analyze_volume_around_event(
                    client=self.market_data_client,
                    symbol=ticker,
                    event_time=window_start,
                    received_at=window_start,
                    reference_nbbo=initial_nbbos.get(ticker),
                    sector=ticker_sector,
                    stream_manager=self.quote_fetcher.stream_manager if self.quote_fetcher else None,
                    float_shares=cached_float
                )
                return (ticker, volume_analysis)
            except Exception as e:
                logger.debug(
                    "SurgeMonitor: Error analyzing ticker",
                    article_id=article_id,
                    ticker=ticker,
                    cycle=cycle,
                    error=str(e)
                )
                return None

        # Launch all analyses in parallel
        tasks = [asyncio.create_task(analyze_ticker(t)) for t in tickers]

        # Process results as they complete
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is None:
                continue

            ticker, volume_analysis = result
            if volume_analysis and volume_analysis.move_type == "SURGE":
                # Cancel remaining tasks
                for task in tasks:
                    if not task.done():
                        task.cancel()

                # Capture surge NBBO
                surge_stats = volume_analysis.to_dict()
                await self._capture_surge_nbbo(ticker, surge_stats)

                logger.info(
                    "SurgeMonitor: SURGE detected",
                    article_id=article_id,
                    ticker=ticker,
                    cycle=cycle,
                    move_type=volume_analysis.move_type,
                    surge_multiplier=volume_analysis.surge_multiplier
                )

                return (ticker, surge_stats)

        return None

    async def _capture_surge_nbbo(self, ticker: str, surge_stats: Dict[str, Any]) -> None:
        """Capture NBBO at surge detection time."""
        try:
            surge_nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
            if surge_nbbo:
                surge_bid = surge_nbbo.get("bid")
                surge_ask = surge_nbbo.get("ask")
                surge_spread = surge_ask - surge_bid if (surge_bid and surge_ask) else None
                surge_stats["surge_bid"] = surge_bid
                surge_stats["surge_ask"] = surge_ask
                surge_stats["surge_spread"] = surge_spread
        except Exception as e:
            logger.debug(
                "SurgeMonitor: Could not fetch NBBO at surge time",
                ticker=ticker,
                error=str(e)
            )

    async def _wait_for_next_cycle(self, window_start: datetime, cycle_duration: float) -> None:
        """Wait until next cycle start time."""
        cycle_end = datetime.now(timezone.utc)
        next_cycle_start = window_start + timedelta(seconds=cycle_duration)

        if next_cycle_start.tzinfo is None:
            next_cycle_start = next_cycle_start.replace(tzinfo=timezone.utc)

        if next_cycle_start > cycle_end:
            wait_time = (next_cycle_start - cycle_end).total_seconds()
            if wait_time > 0:
                await asyncio.sleep(wait_time)

    async def _finalize_monitoring(
        self,
        article_id: str,
        tickers: list[str],
        session: str,
        received_at: datetime,
        max_cycles: int
    ) -> None:
        """Finalize monitoring - update record and clean up subscriptions."""
        async with self._traded_lock:
            was_traded = article_id in self._traded_articles

        if not was_traded:
            await self.repository.update_recall_record(
                article_id=article_id,
                updates={
                    "monitoring_status": "completed_no_surge",
                    "monitoring_completed_at": datetime.now()
                },
                session=session,
                date=received_at
            )
            logger.info(
                "SurgeMonitor: Completed monitoring, no SURGE detected",
                article_id=article_id,
                cycles_completed=max_cycles
            )

        async with self._monitoring_lock:
            self._monitoring_tasks.pop(article_id, None)

        # Clean up WebSocket subscriptions to reduce event loop load
        # Position manager maintains its own subscriptions via reference counting,
        # so this won't affect active positions
        if self._on_monitoring_complete:
            try:
                await self._on_monitoring_complete(tickers, article_id, was_traded)
            except Exception as e:
                logger.debug(
                    "SurgeMonitor: Error in monitoring complete callback",
                    article_id=article_id,
                    error=str(e)
                )
