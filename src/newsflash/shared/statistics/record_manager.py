"""
Record manager module - handles record updates, metadata, and event processing.

Extracted from RecallStatsEngine to separate record management from monitoring logic.
"""
import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, Protocol

from ...utils.logging_config import get_logger
from ...utils.brokerage.session_detector import get_market_session_from_timestamp

logger = get_logger(__name__)


def calculate_volume_float_pct(volume: Optional[int], float_shares: Optional[int]) -> Optional[float]:
    """
    Calculate volume as percentage of float.

    Float-normalized volume helps compare activity across different sized companies.
    A 10M float stock trading 100k shares (1%) is more significant than
    a 100M float stock trading 100k shares (0.1%).
    """
    if not volume or not float_shares or float_shares <= 0:
        return None
    pct = (volume / float_shares) * 100
    return round(pct, 4)


class QuoteFetcherProtocol(Protocol):
    """Protocol for quote fetching."""
    async def get_nbbo_snapshot(self, ticker: str) -> Optional[Dict[str, Any]]: ...


class MetadataFetcherProtocol(Protocol):
    """Protocol for metadata fetching."""
    async def fetch_metadata(
        self,
        ticker: str,
        timeout: float = 30.0,
        queue_on_failure: bool = False,
        callback: Optional[Any] = None
    ) -> Optional[Dict[str, Any]]: ...

    async def queue_metadata_fetch(self, ticker: str, callback: Any) -> None: ...


class RepositoryProtocol(Protocol):
    """Protocol for record persistence."""
    async def update_recall_record(
        self,
        article_id: str,
        updates: Dict[str, Any],
        session: str,
        date: datetime
    ) -> bool: ...


class RecordManager:
    """
    Manages recall record updates, metadata fetching, and event processing.

    Responsibilities:
    - Fetch and update ticker metadata
    - Handle classification events
    - Handle trade execution events
    - Retry pending updates
    - Run finalization loop

    Design:
    - Receives dependencies via protocols (testable)
    - Manages pending update state
    - Provides retry logic for failed updates
    """

    def __init__(
        self,
        repository: RepositoryProtocol,
        quote_fetcher: QuoteFetcherProtocol,
        metadata_fetcher: MetadataFetcherProtocol,
        trading_client: Optional[Any] = None,
        metadata_cache: Optional[Any] = None  # MetadataCache for float shares
    ):
        """
        Initialize record manager.

        Args:
            repository: Statistics repository for record persistence
            quote_fetcher: Quote fetcher for NBBO snapshots
            metadata_fetcher: Yahoo Finance coordinator for metadata
            trading_client: Optional Alpaca trading client for exchange info
            metadata_cache: Optional metadata cache for float shares (instant lookups)
        """
        self.repository = repository
        self.quote_fetcher = quote_fetcher
        self.metadata_fetcher = metadata_fetcher
        self.trading_client = trading_client
        self.metadata_cache = metadata_cache

        # Pending updates state
        self._pending_metadata: Dict[str, tuple[list[str], str, datetime, asyncio.Task]] = {}
        self._metadata_lock = asyncio.Lock()

        self._pending_filter_reasons: Dict[str, str] = {}
        self._filter_reasons_lock = asyncio.Lock()

        self._pending_classifications: Dict[str, str] = {}
        self._classification_lock = asyncio.Lock()

        # Record locations cache (article_id -> (session, date))
        self._record_locations: Dict[str, tuple[str, datetime]] = {}

        # Finalization task
        self._finalization_task: Optional[asyncio.Task] = None

    def register_record_location(self, article_id: str, session: str, received_at: datetime) -> None:
        """Register where a record was created for later updates."""
        self._record_locations[article_id] = (session, received_at)

    async def apply_pending_updates(self, article_id: str, session: str, received_at: datetime) -> None:
        """
        Apply any pending classifications/filter_reasons that arrived before the record was created.

        This fixes the race condition where classification event arrives before recall record exists.
        Call this AFTER the record has been appended to the repository.
        """
        # Apply any pending classification for this article
        pending_classification = None
        async with self._classification_lock:
            pending_classification = self._pending_classifications.get(article_id)

        if pending_classification:
            try:
                updated = await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={"ai_classification": pending_classification},
                    session=session,
                    date=received_at
                )
                if updated:
                    async with self._classification_lock:
                        self._pending_classifications.pop(article_id, None)
                    logger.info(
                        "RecordManager: Applied pending classification after record creation",
                        article_id=article_id,
                        classification=pending_classification
                    )
            except Exception as e:
                logger.warning(
                    "RecordManager: Failed to apply pending classification",
                    article_id=article_id,
                    error=str(e)
                )

        # Also apply any pending filter_reason
        pending_filter_reason = None
        async with self._filter_reasons_lock:
            pending_filter_reason = self._pending_filter_reasons.get(article_id)

        if pending_filter_reason:
            try:
                updated = await self.repository.update_recall_record(
                    article_id=article_id,
                    updates={"filter_reason": pending_filter_reason},
                    session=session,
                    date=received_at
                )
                if updated:
                    async with self._filter_reasons_lock:
                        self._pending_filter_reasons.pop(article_id, None)
                    logger.debug(
                        "RecordManager: Applied pending filter_reason after record creation",
                        article_id=article_id
                    )
            except Exception as e:
                logger.warning(
                    "RecordManager: Failed to apply pending filter_reason",
                    article_id=article_id,
                    error=str(e)
                )

    def get_record_location(self, article_id: str) -> Optional[tuple[str, datetime]]:
        """Get registered record location."""
        return self._record_locations.get(article_id)

    async def start_finalization_loop(self) -> None:
        """Start background finalization loop."""
        if self._finalization_task and not self._finalization_task.done():
            return
        self._finalization_task = asyncio.create_task(self._finalization_loop())

    async def stop_finalization_loop(self) -> None:
        """Stop background finalization loop."""
        if self._finalization_task:
            self._finalization_task.cancel()
            try:
                await self._finalization_task
            except asyncio.CancelledError:
                pass

    # ==================== Metadata Management ====================

    async def fetch_and_update_metadata(
        self,
        article_id: str,
        tickers: list[str],
        session: str,
        received_at: datetime
    ) -> None:
        """
        Fetch ticker metadata and update record.

        Fire-and-forget background task with retry logic.
        """
        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            try:
                metadata_dict = {}
                metadata_errors = {}

                for ticker in tickers:
                    ticker_meta = await self._fetch_ticker_metadata(
                        ticker, article_id, session, received_at
                    )
                    if ticker_meta:
                        metadata_dict[ticker] = ticker_meta
                    else:
                        metadata_errors[ticker] = "fetch_failed_queued_for_retry"

                # Update record even with partial metadata
                if metadata_dict or attempt == max_retries - 1:
                    updates = {"ticker_metadata": metadata_dict}
                    if metadata_errors:
                        updates["metadata_errors"] = metadata_errors

                    # Calculate float-normalized volumes if we have float_shares
                    # Get float_shares from metadata_cache (instant) or from fetched metadata
                    float_shares = None
                    for ticker in tickers:
                        if self.metadata_cache:
                            try:
                                float_shares = await self.metadata_cache.get_float(ticker)
                            except Exception:
                                pass
                        if not float_shares and ticker in metadata_dict:
                            float_shares = metadata_dict[ticker].get("float_shares")
                        if float_shares:
                            updates["float_shares"] = int(float_shares)
                            break

                    updated = await self.repository.update_recall_record(
                        article_id=article_id,
                        updates=updates,
                        session=session,
                        date=received_at
                    )

                    if metadata_dict:
                        if updated:
                            logger.info(
                                "RecordManager: Updated metadata",
                                article_id=article_id,
                                tickers=list(metadata_dict.keys()),
                                has_float=float_shares is not None
                            )
                        async with self._metadata_lock:
                            self._pending_metadata.pop(article_id, None)
                        break

                # Retry after delay if no metadata
                if not metadata_dict and attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2

            except Exception as e:
                logger.error(
                    "RecordManager: Error fetching metadata",
                    article_id=article_id,
                    attempt=attempt + 1,
                    error=str(e)
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2

    async def _fetch_ticker_metadata(
        self,
        ticker: str,
        article_id: str,
        session: str,
        received_at: datetime
    ) -> Optional[Dict[str, Any]]:
        """Fetch metadata for a single ticker."""
        # Get price from NBBO
        price = None
        try:
            nbbo = await self.quote_fetcher.get_nbbo_snapshot(ticker)
            if nbbo:
                price = nbbo.get("mid") or nbbo.get("ask") or nbbo.get("bid")
        except Exception:
            pass

        # Get exchange from trading client (use to_thread to avoid blocking event loop)
        exchange = None
        if self.trading_client:
            try:
                asset = await asyncio.to_thread(self.trading_client.get_asset, ticker)
                if asset:
                    exchange = asset.exchange
            except Exception:
                pass

        # Callback for background retry
        async def update_callback(t: str, meta: Optional[Dict[str, Any]]) -> None:
            if meta:
                if price is not None:
                    meta["price"] = price
                if exchange:
                    meta["exchange"] = exchange
                asyncio.create_task(
                    self.repository.update_recall_record(
                        article_id, {"ticker_metadata": {t: meta}}, session, received_at
                    )
                )
                logger.info(
                    "RecordManager: Metadata updated via callback",
                    article_id=article_id,
                    ticker=t
                )

        try:
            ticker_meta = await self.metadata_fetcher.fetch_metadata(
                ticker, timeout=30.0, queue_on_failure=True, callback=update_callback
            )
            if ticker_meta:
                if price is not None:
                    ticker_meta["price"] = price
                if exchange:
                    ticker_meta["exchange"] = exchange
                return ticker_meta
            elif price is not None or exchange:
                # Partial metadata
                return {
                    "industry": None,
                    "sector": None,
                    "market_cap_millions": None,
                    "price": price,
                    "exchange": exchange
                }
        except asyncio.TimeoutError:
            await self.metadata_fetcher.queue_metadata_fetch(ticker, update_callback)
            if price is not None or exchange:
                return {
                    "industry": None,
                    "sector": None,
                    "market_cap_millions": None,
                    "price": price,
                    "exchange": exchange
                }
        except Exception as e:
            logger.debug("RecordManager: Metadata fetch error", ticker=ticker, error=str(e))
            await self.metadata_fetcher.queue_metadata_fetch(ticker, update_callback)

        return None

    # ==================== Classification Updates ====================

    async def update_classification(
        self,
        article_id: str,
        classification: str,
        filter_reason: Optional[str] = None
    ) -> bool:
        """
        Update record with classification result.

        Returns:
            True if updated immediately, False if queued for retry
        """
        # Store in pending first (race condition prevention)
        async with self._classification_lock:
            self._pending_classifications[article_id] = classification

        if filter_reason:
            async with self._filter_reasons_lock:
                self._pending_filter_reasons[article_id] = filter_reason

        # Try to update
        record_loc = self._record_locations.get(article_id)
        if not record_loc:
            logger.debug(
                "RecordManager: No record location, classification pending",
                article_id=article_id
            )
            return False

        updates = {"ai_classification": classification}
        if filter_reason:
            updates["filter_reason"] = filter_reason

        updated = await self.repository.update_recall_record(
            article_id=article_id,
            updates=updates,
            session=record_loc[0],
            date=record_loc[1]
        )

        if updated:
            async with self._classification_lock:
                self._pending_classifications.pop(article_id, None)
            if filter_reason:
                async with self._filter_reasons_lock:
                    self._pending_filter_reasons.pop(article_id, None)
            logger.info(
                "RecordManager: Updated classification",
                article_id=article_id,
                classification=classification
            )

        return updated

    async def update_postfilter_reason(
        self,
        article_id: str,
        postfilter_reason: str,
    ) -> bool:
        """
        Update record with post-AI filter reason.

        Called when an IMMINENT article is skipped due to post-AI checks
        (e.g., no surge, low volume, spread too wide, etc.)

        Returns:
            True if updated immediately, False if failed
        """
        record_loc = self._record_locations.get(article_id)
        if not record_loc:
            logger.debug(
                "RecordManager: No record location for postfilter update",
                article_id=article_id,
                postfilter_reason=postfilter_reason
            )
            return False

        updated = await self.repository.update_recall_record(
            article_id=article_id,
            updates={"postfilter_reason": postfilter_reason},
            session=record_loc[0],
            date=record_loc[1]
        )

        if updated:
            logger.info(
                "RecordManager: Updated postfilter reason",
                article_id=article_id,
                postfilter_reason=postfilter_reason
            )

        return updated

    async def update_headline_type(
        self,
        article_id: str,
        headline_type: str,
    ) -> bool:
        """
        Update record with headline type classification.

        Called for IMMINENT articles to store the catalyst type
        (e.g., contract, fda, partnership, earnings, etc.)

        Returns:
            True if updated immediately, False if failed
        """
        record_loc = self._record_locations.get(article_id)
        if not record_loc:
            logger.debug(
                "RecordManager: No record location for headline_type update",
                article_id=article_id,
                headline_type=headline_type
            )
            return False

        updated = await self.repository.update_recall_record(
            article_id=article_id,
            updates={"headline_type": headline_type},
            session=record_loc[0],
            date=record_loc[1]
        )

        if updated:
            logger.debug(
                "RecordManager: Updated headline_type",
                article_id=article_id,
                headline_type=headline_type
            )

        return updated

    # ==================== Trade Updates ====================

    async def update_trade_executed(
        self,
        article_id: str,
        ticker: str,
        execution_data: Dict[str, Any]
    ) -> bool:
        """Update record with trade execution result."""
        record_loc = self._record_locations.get(article_id)
        if not record_loc:
            logger.warning(
                "RecordManager: No record location for trade update",
                article_id=article_id
            )
            return False

        return await self.repository.update_recall_record(
            article_id=article_id,
            updates={
                "traded": True,
                "trade_ticker": ticker,
                "trade_execution": execution_data
            },
            session=record_loc[0],
            date=record_loc[1]
        )

    async def update_trade_failed(
        self,
        article_id: str,
        ticker: str,
        error: str
    ) -> bool:
        """Update record with trade failure."""
        record_loc = self._record_locations.get(article_id)
        if not record_loc:
            return False

        return await self.repository.update_recall_record(
            article_id=article_id,
            updates={
                "traded": False,
                "trade_ticker": ticker,
                "trade_error": error
            },
            session=record_loc[0],
            date=record_loc[1]
        )

    # ==================== Finalization & Retry ====================

    async def _finalization_loop(self) -> None:
        """Background task that retries pending updates every 5 minutes."""
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                await self._retry_pending_filter_reasons()
                await self._retry_pending_classifications()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("RecordManager: Error in finalization loop", error=str(e))

    async def _retry_pending_filter_reasons(self) -> None:
        """Retry pending filter_reason updates."""
        async with self._filter_reasons_lock:
            pending = dict(self._pending_filter_reasons)

        for article_id, filter_reason in pending.items():
            try:
                record_loc = self._record_locations.get(article_id)
                if record_loc:
                    updated = await self.repository.update_recall_record(
                        article_id=article_id,
                        updates={"filter_reason": filter_reason},
                        session=record_loc[0],
                        date=record_loc[1]
                    )
                    if updated:
                        async with self._filter_reasons_lock:
                            self._pending_filter_reasons.pop(article_id, None)
                else:
                    # Search recent sessions
                    await self._search_and_update(
                        article_id, {"filter_reason": filter_reason},
                        self._pending_filter_reasons, self._filter_reasons_lock
                    )
            except Exception as e:
                logger.error("RecordManager: Error retrying filter_reason", article_id=article_id, error=str(e))

    async def _retry_pending_classifications(self) -> None:
        """Retry pending classification updates."""
        async with self._classification_lock:
            pending = dict(self._pending_classifications)

        for article_id, classification in pending.items():
            try:
                record_loc = self._record_locations.get(article_id)
                if record_loc:
                    updated = await self.repository.update_recall_record(
                        article_id=article_id,
                        updates={"ai_classification": classification},
                        session=record_loc[0],
                        date=record_loc[1]
                    )
                    if updated:
                        async with self._classification_lock:
                            self._pending_classifications.pop(article_id, None)
                else:
                    await self._search_and_update(
                        article_id, {"ai_classification": classification},
                        self._pending_classifications, self._classification_lock
                    )
            except Exception as e:
                logger.error("RecordManager: Error retrying classification", article_id=article_id, error=str(e))

    async def _search_and_update(
        self,
        article_id: str,
        updates: Dict[str, Any],
        pending_dict: Dict[str, str],
        pending_lock: asyncio.Lock
    ) -> None:
        """Search recent sessions and update record."""
        current_time = datetime.now()
        for hours_ago in [0, 1, 2]:
            past_time = current_time - timedelta(hours=hours_ago)
            session_name, _ = get_market_session_from_timestamp(past_time)
            if session_name != "closed":
                updated = await self.repository.update_recall_record(
                    article_id=article_id,
                    updates=updates,
                    session=session_name,
                    date=past_time
                )
                if updated:
                    async with pending_lock:
                        pending_dict.pop(article_id, None)
                    break
