"""
Auto-trade service - subscribes to domain events and handles trading logic.

Confluence Scoring System (2-second observation window after publication):
- Spread widening >15% → +2 points (market makers retreating = real catalyst)
- Spread widening 5-15% → +1 point
- Spread stable (±5%) → 0 points
- Spread tightening >5% → -1 point (market ignoring news)
- Volume surge >3x → +1 point
- Price excursion >1% → +1 point

Position sizing by score:
- Score ≤0: $2,000 (MINIMUM) - low confluence, still trade but small position
- Score 1: $5,000 (STANDARD)
- Score 2: $7,500 (HIGH)
- Score 3+: $10,000 (VERY_HIGH)

Stop loss: 5% below initial NBBO mid (not entry price) - anchored to pre-move price.

Pure functions for trade processing logic, with minimal service class for event subscriptions.
"""
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.typed_event_bus import subscribe_typed
from ...shared.event_types import DomainEventType
from ...domain.brokerage.events import TradeRequestDomainEvent
from ...domain.brokerage.models import TradeRequest, TradeAction, TradeInstrument
from ...domain.classification.events import ArticleClassifiedDomainEvent
from ...domain.classification.models import ClassificationResult, ClassificationCategory
from ...domain.websocket.models import Article
from ...services.storage import StorageQueryService
from .trade_builder import build_trade_request_from_article

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockTradesRequest
    from alpaca.data.enums import DataFeed
except ImportError:
    StockHistoricalDataClient = None
    StockTradesRequest = None
    DataFeed = None

from ..brokerage.position_manager import ConvictionLevel

logger = get_logger(__name__)


# Position sizing based on confluence score
POSITION_SIZES_USD = {
    ConvictionLevel.MINIMUM: Decimal("1000.00"),     # Score ≤0: Low confluence (tightening spread)
    ConvictionLevel.STANDARD: Decimal("5000.00"),    # Score 1: Base position
    ConvictionLevel.HIGH: Decimal("7500.00"),        # Score 2: Good confluence
    ConvictionLevel.VERY_HIGH: Decimal("10000.00"),  # Score 3+: Strong confluence
}

# Thresholds for 2-second observation window (publication-anchored)
OBSERVATION_WINDOW_SECONDS = 2.0      # 2-second window after article publication
PRICE_EXCURSION_THRESHOLD_PCT = 0.01  # 1% price move = +1 point
VOLUME_SURGE_MULTIPLIER = 3.0         # 3x normal volume = +1 point

# Spread change thresholds (widening = positive signal for microcaps)
# Market makers retreat during significant news → spread widens
SPREAD_MAJOR_WIDENING_PCT = 0.15      # >15% widening = +2 points
SPREAD_MINOR_WIDENING_PCT = 0.05      # 5-15% widening = +1 point
SPREAD_TIGHTENING_PCT = -0.05         # >5% tightening = -1 point (market ignoring news)


async def check_confluence_signals(
    market_data_client: Optional["StockHistoricalDataClient"],
    quote_fetcher,  # AlpacaQuoteFetcher for NBBO snapshots
    ticker: str,
    publication_time: datetime,
    baseline_volume: Optional[float] = None,
) -> tuple[ConvictionLevel, dict]:
    """
    Check confluence signals in 2-second window after article publication.

    Confluence Scoring System:
    - Spread widening >15% → +2 points (market makers retreating = real catalyst)
    - Spread widening 5-15% → +1 point
    - Spread stable (±5%) → 0 points
    - Spread tightening >5% → -1 point (market ignoring news)
    - Volume surge >3x → +1 point
    - Price excursion >1% → +1 point

    Position sizing by score:
    - Score ≤0: $2,000 (MINIMUM) - low confluence
    - Score 1: $5,000 (STANDARD)
    - Score 2: $7,500 (HIGH)
    - Score 3+: $10,000 (VERY_HIGH)

    Args:
        market_data_client: Alpaca market data client for trades
        quote_fetcher: Quote fetcher for NBBO snapshots
        ticker: Stock ticker to check
        publication_time: When the article was published
        baseline_volume: Average volume per second for comparison (optional)

    Returns:
        Tuple of (ConvictionLevel, metadata_dict with confluence stats)
    """
    import asyncio
    from datetime import timedelta

    metadata = {
        "confluence_checked": False,
        "confluence_score": 0,
        "initial_nbbo_mid": None,
        "initial_spread": None,
        "final_spread": None,
        "spread_change_pct": 0.0,
        "spread_widened": False,
        "spread_tightened": False,
        "price_excursion_pct": 0.0,
        "has_price_excursion": False,
        "volume": 0,
        "volume_surge": False,
        "conviction": ConvictionLevel.MINIMUM.value,
        # New fields for early-entry filters
        "initial_ask": None,
        "final_ask": None,
        "ask_change_pct": 0.0,
        "spread_compression_pct": 0.0,
    }

    # Default to MINIMUM if we can't check
    if not market_data_client or not StockTradesRequest:
        logger.debug("Confluence check skipped - no market data client")
        return ConvictionLevel.MINIMUM, metadata

    try:
        # Calculate time since publication
        now = datetime.now(timezone.utc)
        # Handle both timezone-aware and naive publication times
        if publication_time.tzinfo is None:
            publication_time = publication_time.replace(tzinfo=timezone.utc)
        time_since_publication = (now - publication_time).total_seconds()

        # If article is too old (>10 seconds), still check but log warning
        if time_since_publication > 10.0:
            logger.warning(
                "Confluence check on stale article",
                ticker=ticker,
                time_since_publication=round(time_since_publication, 2)
            )

        # ============================================================
        # STEP 1: Get NBBO IMMEDIATELY (before wait) - this is our "receipt time" NBBO
        # ============================================================
        initial_nbbo = None
        if quote_fetcher:
            try:
                initial_nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
            except Exception as e:
                logger.debug(f"Could not get initial NBBO: {e}")

        if initial_nbbo:
            metadata["initial_nbbo_mid"] = initial_nbbo.get("mid")
            metadata["initial_spread"] = initial_nbbo.get("spread")
            metadata["initial_ask"] = initial_nbbo.get("ask")

        # Wait until 2 seconds have passed since publication
        if time_since_publication < OBSERVATION_WINDOW_SECONDS:
            wait_time = OBSERVATION_WINDOW_SECONDS - time_since_publication
            await asyncio.sleep(wait_time)

        # ============================================================
        # STEP 2: Get current NBBO (2 seconds after publication)
        # ============================================================
        current_nbbo = None
        if quote_fetcher:
            try:
                current_nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
            except Exception as e:
                logger.debug(f"Could not get current NBBO: {e}")

        if current_nbbo:
            metadata["final_spread"] = current_nbbo.get("spread")
            metadata["final_ask"] = current_nbbo.get("ask")

        # ============================================================
        # STEP 2b: Calculate ask_change_pct and spread_compression_pct
        # ============================================================
        if metadata["initial_ask"] and metadata["final_ask"]:
            initial_ask = metadata["initial_ask"]
            final_ask = metadata["final_ask"]
            if initial_ask > 0:
                ask_change = ((final_ask - initial_ask) / initial_ask) * 100
                metadata["ask_change_pct"] = round(ask_change, 2)

        if metadata["initial_spread"] and metadata["final_spread"]:
            initial_spread = metadata["initial_spread"]
            final_spread = metadata["final_spread"]
            if initial_spread > 0:
                # Negative = spread compressed, Positive = spread widened
                spread_compression = ((initial_spread - final_spread) / initial_spread) * 100
                metadata["spread_compression_pct"] = round(spread_compression, 2)

        # ============================================================
        # STEP 3: Calculate spread change
        # ============================================================
        confluence_score = 0

        if metadata["initial_spread"] and metadata["final_spread"]:
            initial_spread = metadata["initial_spread"]
            final_spread = metadata["final_spread"]

            if initial_spread > 0:
                spread_change_pct = (final_spread - initial_spread) / initial_spread
                metadata["spread_change_pct"] = round(spread_change_pct * 100, 2)

                # Score spread change (widening is POSITIVE for microcap news)
                if spread_change_pct >= SPREAD_MAJOR_WIDENING_PCT:
                    # Major widening (>15%) = +2 points
                    confluence_score += 2
                    metadata["spread_widened"] = True
                    logger.info(
                        f"📈 SPREAD MAJOR WIDENING: +2 points",
                        ticker=ticker,
                        spread_change_pct=f"{spread_change_pct*100:.1f}%",
                        initial_spread=initial_spread,
                        final_spread=final_spread
                    )
                elif spread_change_pct >= SPREAD_MINOR_WIDENING_PCT:
                    # Minor widening (5-15%) = +1 point
                    confluence_score += 1
                    metadata["spread_widened"] = True
                    logger.info(
                        f"📈 SPREAD MINOR WIDENING: +1 point",
                        ticker=ticker,
                        spread_change_pct=f"{spread_change_pct*100:.1f}%"
                    )
                elif spread_change_pct <= SPREAD_TIGHTENING_PCT:
                    # Tightening (>5% decrease) = -1 point
                    confluence_score -= 1
                    metadata["spread_tightened"] = True
                    logger.info(
                        f"📉 SPREAD TIGHTENING: -1 point (market ignoring news)",
                        ticker=ticker,
                        spread_change_pct=f"{spread_change_pct*100:.1f}%"
                    )
                # else: stable (±5%) = 0 points

        # ============================================================
        # STEP 4: Fetch trades in 2-second window for volume/price analysis
        # ============================================================
        trades_start = publication_time
        trades_end = publication_time + timedelta(seconds=OBSERVATION_WINDOW_SECONDS)

        trades = market_data_client.get_stock_trades(StockTradesRequest(
            symbol_or_symbols=ticker,
            start=trades_start,
            end=trades_end,
            feed=DataFeed.SIP
        ))

        if trades and trades.data and ticker in trades.data:
            trade_list = trades.data[ticker]
            if trade_list:
                metadata["confluence_checked"] = True

                # Calculate metrics
                first_price = trade_list[0].price
                max_price = max(t.price for t in trade_list)
                min_price = min(t.price for t in trade_list)
                total_volume = sum(t.size for t in trade_list)

                # Max excursion from first trade
                max_move_up = (max_price - first_price) / first_price if first_price else 0
                max_move_down = (first_price - min_price) / first_price if first_price else 0
                max_move_pct = max(max_move_up, max_move_down)

                metadata["price_excursion_pct"] = round(max_move_pct * 100, 2)
                metadata["volume"] = total_volume
                metadata["trade_count"] = len(trade_list)

                # ============================================================
                # STEP 5: Score price excursion
                # ============================================================
                if max_move_pct >= PRICE_EXCURSION_THRESHOLD_PCT:
                    confluence_score += 1
                    metadata["has_price_excursion"] = True
                    logger.info(
                        f"📈 PRICE EXCURSION: +1 point",
                        ticker=ticker,
                        max_move_pct=f"{max_move_pct*100:.2f}%"
                    )

                # ============================================================
                # STEP 6: Score volume surge
                # ============================================================
                has_volume_surge = False
                if baseline_volume and baseline_volume > 0:
                    # Compare 2-second volume to expected (adjusted for 2s window)
                    expected_2s_volume = baseline_volume * 2
                    volume_ratio = total_volume / expected_2s_volume if expected_2s_volume > 0 else 0
                    if volume_ratio >= VOLUME_SURGE_MULTIPLIER:
                        has_volume_surge = True
                        metadata["volume_ratio"] = round(volume_ratio, 2)
                else:
                    # Simple heuristic: 1000+ shares in 2 seconds is a surge
                    if total_volume >= 1000:
                        has_volume_surge = True

                if has_volume_surge:
                    confluence_score += 1
                    metadata["volume_surge"] = True
                    logger.info(
                        f"📈 VOLUME SURGE: +1 point",
                        ticker=ticker,
                        volume=total_volume
                    )

        # ============================================================
        # STEP 7: Determine conviction level from confluence score
        # ============================================================
        metadata["confluence_score"] = confluence_score

        if confluence_score >= 3:
            conviction = ConvictionLevel.VERY_HIGH
            logger.info(
                f"🔥 VERY HIGH CONVICTION (score {confluence_score}): Strong confluence",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                spread_change_pct=metadata.get("spread_change_pct"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                volume_surge=metadata.get("volume_surge")
            )
        elif confluence_score == 2:
            conviction = ConvictionLevel.HIGH
            logger.info(
                f"⚡ HIGH CONVICTION (score {confluence_score}): Good confluence",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                spread_change_pct=metadata.get("spread_change_pct"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                volume_surge=metadata.get("volume_surge")
            )
        elif confluence_score == 1:
            conviction = ConvictionLevel.STANDARD
            logger.info(
                f"📊 STANDARD CONVICTION (score {confluence_score}): Minimal confluence",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                spread_change_pct=metadata.get("spread_change_pct"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                volume_surge=metadata.get("volume_surge")
            )
        else:
            conviction = ConvictionLevel.MINIMUM
            logger.info(
                f"⚠️ MINIMUM CONVICTION (score {confluence_score}): Low/no confluence",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                spread_change_pct=metadata.get("spread_change_pct"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                volume_surge=metadata.get("volume_surge")
            )

        metadata["conviction"] = conviction.value
        return conviction, metadata

    except Exception as e:
        logger.error(f"Error checking confluence signals: {e}", exc_info=True)
        return ConvictionLevel.MINIMUM, metadata


# Backward compatibility alias
async def check_early_momentum(
    market_data_client: Optional["StockHistoricalDataClient"],
    ticker: str,
    publication_time: datetime,
    baseline_volume: Optional[float] = None,
) -> tuple[ConvictionLevel, dict]:
    """
    Legacy wrapper for check_confluence_signals.

    Note: This version doesn't have access to quote_fetcher for spread analysis.
    Use check_confluence_signals directly for full functionality.
    """
    return await check_confluence_signals(
        market_data_client=market_data_client,
        quote_fetcher=None,
        ticker=ticker,
        publication_time=publication_time,
        baseline_volume=baseline_volume,
    )


def should_process_classification(result: ClassificationResult, enabled: bool) -> bool:
    """
    Determine if a classification result should trigger auto-trade.

    This function is called by AutoTradeService when Healthcare LLM classification
    returns IMMINENT (tradeable Healthcare headline).

    Args:
        result: Classification result to check
        enabled: Whether auto-trading is enabled

    Returns:
        True if should process, False otherwise
    """
    if not enabled:
        logger.info(f"⏭️ AUTO-TRADE SKIPPED: Auto-trading disabled", article_id=result.article_id)
        return False
    
    if result.classification != ClassificationCategory.IMMINENT:
        logger.debug(
            "AutoTradeService: Skipping non-IMMINENT classification",
            article_id=result.article_id,
            classification=result.classification.value
        )
        return False
    
    return True


async def fetch_article_for_trade(
    storage_service: StorageQueryService,
    article_id: str,
    max_retries: int = 5,  # Increased from 3 to handle race conditions better
    initial_delay: float = 0.3  # Reduced initial delay, exponential backoff will handle longer waits
) -> Optional[Article]:
    """
    Fetch an article from storage for trade processing with retry logic.
    
    Handles race condition where classification completes before storage finishes.
    
    Args:
        storage_service: Storage query service
        article_id: Article ID to fetch
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay before first retry in seconds
        
    Returns:
        Domain Article model, or None if not found after retries
    """
    import asyncio
    
    # Try fetching with exponential backoff retry
    for attempt in range(max_retries):
        domain_article = await storage_service.fetch_article(article_id)
        
        if domain_article:
            if attempt > 0:
                logger.info(
                    "AutoTradeService: Article found after retry",
                    article_id=article_id,
                    attempt=attempt + 1
                )
            return domain_article
        
        # If not found and we have retries left, wait before retrying
        if attempt < max_retries - 1:
            delay = initial_delay * (2 ** attempt)  # Exponential backoff: 0.3s, 0.6s, 1.2s, 2.4s, 4.8s
            logger.info(
                "⏳ AutoTradeService: Article not found, retrying",
                article_id=article_id,
                attempt=attempt + 1,
                max_retries=max_retries,
                delay_seconds=delay,
                total_wait_so_far=sum(initial_delay * (2 ** i) for i in range(attempt + 1))
            )
            await asyncio.sleep(delay)
    
    # All retries exhausted
    logger.warning(
        "AutoTradeService: Article not found in storage after retries",
        article_id=article_id,
        max_retries=max_retries
    )
    return None


def build_trade_request_for_article(
    article: Article,
    current_price: Optional[float] = None,
    ticker: Optional[str] = None,
    conviction: ConvictionLevel = ConvictionLevel.STANDARD,
) -> Optional[TradeRequest]:
    """
    Build a trade request from an article with conviction-based position sizing.

    Position sizes based on conviction level:
    - STANDARD: $5,000 (base position)
    - HIGH: $7,500 (1% move in 1 second)
    - VERY_HIGH: $10,000 (1% move + volume surge)

    Args:
        article: Domain Article model
        current_price: Current ask price for buying (if None, will use amount_usd and let executor calculate)
        ticker: Specific ticker to trade (if None, uses first ticker from article)
        conviction: Conviction level for position sizing

    Returns:
        Domain TradeRequest model with shares set (if price provided) or amount_usd, or None if invalid
    """
    import math
    import os

    # Use conviction-based position sizing
    TRADE_SIZE_USD = POSITION_SIZES_USD.get(conviction, POSITION_SIZES_USD[ConvictionLevel.STANDARD])

    # Allow test override via environment variable (for load tests to avoid buying power issues)
    if os.getenv("TEST_TRADE_SIZE_USD"):
        TRADE_SIZE_USD = Decimal(os.getenv("TEST_TRADE_SIZE_USD"))
    
    # If we have price, calculate shares upfront and round down
    if current_price and current_price > 0:
        shares = math.floor(TRADE_SIZE_USD / Decimal(str(current_price)))
        if shares <= 0:
            logger.warning(
                f"⏭️ AUTO-TRADE SKIPPED: Price too high for ${TRADE_SIZE_USD} trade",
                article_id=article.id,
                current_price=current_price,
                trade_size_usd=float(TRADE_SIZE_USD),
                conviction=conviction.value
            )
            return None

        logger.info(
            f"💰 AUTO-TRADE: Building ${TRADE_SIZE_USD} trade ({conviction.value})",
            article_id=article.id,
            shares=shares,
            current_price=current_price,
            total_notional=float(shares * Decimal(str(current_price))),
            conviction=conviction.value
        )
        
        # Build trade request with explicit shares (no leverage, amount_usd for reference)
        # Use provided ticker if specified, otherwise use first ticker from article
        # CRITICAL: If ticker is provided (e.g., from surge detection), use it to avoid trading wrong ticker
        if not ticker:
            # article.tickers is a frozenset, so convert to list to get first element
            ticker = next(iter(article.tickers), None) if article.tickers else None
        if not ticker:
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: Article has no tickers",
                article_id=article.id
            )
            return None
        
        # Create TradeRequest with shares calculated upfront (immutable model, so create new)
        trade_request = TradeRequest(
            ticker=ticker,
            action=TradeAction.BUY,
            shares=float(shares),  # Explicit shares calculated from $10k
            amount_usd=TRADE_SIZE_USD,  # Reference value for tracking
            leverage=None,  # No leverage - direct capital
            article_id=article.id,
            instrument=TradeInstrument.STOCK
        )
    else:
        # Fallback: Set amount_usd and let executor calculate shares (will round down via int())
        logger.info(
            f"💰 AUTO-TRADE: Building ${TRADE_SIZE_USD} trade ({conviction.value}) - executor will calculate shares",
            article_id=article.id,
            trade_size_usd=float(TRADE_SIZE_USD),
            conviction=conviction.value
        )

        trade_request = build_trade_request_from_article(
            article=article,
            amount_usd=TRADE_SIZE_USD,
            leverage=None,  # No leverage - using direct capital
            action=TradeAction.BUY
        )
    
    if not trade_request:
        logger.info(
            "⏭️ AUTO-TRADE SKIPPED: Article has no tickers or invalid for trading",
            article_id=article.id
        )
        return None
    
    return trade_request


async def publish_trade_request(
    event_bus: AsyncEventBus,
    trade_request: TradeRequest,
    article_id: str,
    conviction: ConvictionLevel = ConvictionLevel.STANDARD,
    confluence_metadata: Optional[dict] = None,
) -> None:
    """
    Publish a trade request domain event.

    Args:
        event_bus: Event bus instance
        trade_request: Domain TradeRequest to publish
        article_id: Associated article ID
        conviction: Conviction level for position sizing
        confluence_metadata: Confluence scoring metadata (spread, volume, price)
    """
    # Include conviction and initial NBBO in event metadata for position manager
    metadata = {
        "conviction": conviction.value,
        "position_size_usd": float(POSITION_SIZES_USD[conviction]),
    }
    if confluence_metadata:
        metadata.update(confluence_metadata)
        # Ensure initial_nbbo_mid is explicitly included for stop loss tracking
        if "initial_nbbo_mid" in confluence_metadata:
            metadata["initial_nbbo_mid"] = confluence_metadata["initial_nbbo_mid"]

    domain_trade_event = TradeRequestDomainEvent(
        trade_request=trade_request,
        article_id=article_id,
        requested_at=datetime.now(),
        metadata=metadata
    )

    await event_bus.publish("Domain.TradeRequested", domain_trade_event.model_dump())

    logger.info(
        "✅ AUTO-TRADE REQUEST PUBLISHED",
        ticker=trade_request.ticker,
        article_id=article_id,
        conviction=conviction.value,
        position_size=f"${POSITION_SIZES_USD[conviction]}"
    )


async def process_imminent_article(
    event_bus: AsyncEventBus,
    storage_service: StorageQueryService,
    classification_result: ClassificationResult,
    enabled: bool,
    market_data_client: Optional["StockHistoricalDataClient"] = None,
    quote_fetcher=None,  # AlpacaQuoteFetcher for NBBO snapshots
    metadata_cache=None,  # MetadataCache for market cap lookup
    # New: Article metadata from event to avoid storage fetch delay
    event_tickers: Optional[list] = None,
    event_title: Optional[str] = None,
    event_published_at: Optional[datetime] = None,
) -> None:
    """
    Process an IMMINENT classification result and publish trade request if valid.

    Pure function that orchestrates the auto-trade workflow.

    IMPORTANT: Uses article metadata directly from event (tickers, title, published_at)
    to avoid waiting for storage. Storage fetch is no longer needed since event
    contains all required data for trading.

    Args:
        event_bus: Event bus instance for publishing events
        storage_service: Storage query service (kept for backward compatibility, now unused)
        classification_result: Classification result to process
        enabled: Whether auto-trading is enabled
        market_data_client: Optional Alpaca market data client
        quote_fetcher: Optional AlpacaQuoteFetcher for NBBO snapshots
        event_tickers: Tickers from the classification event (avoids storage delay)
        event_title: Title from the classification event (for logging)
        event_published_at: Published_at from the classification event (for confluence scoring)
    """
    try:
        # Check if we should process this classification
        if not should_process_classification(classification_result, enabled):
            return

        article_id = classification_result.article_id

        # Use event data directly - NO STORAGE FETCH NEEDED
        # This eliminates the 2-minute delay caused by waiting for storage
        tickers_list = event_tickers or []
        title = event_title or ""
        published_at = event_published_at

        if not tickers_list:
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: No tickers in classification event",
                article_id=article_id,
                classification=classification_result.classification.value
            )
            return

        if not published_at:
            logger.warning(
                "⏭️ AUTO-TRADE SKIPPED: No published_at in classification event",
                article_id=article_id,
            )
            return

        # Log processing
        logger.info(
            "🤖 AUTO-TRADE: Processing IMMINENT article (using event data - no storage fetch)",
            article_id=article_id,
            title=title[:100] if title else "",
            tickers=tickers_list,
            has_tickers=len(tickers_list) > 0,
            ticker_count=len(tickers_list),
            published_at=published_at.isoformat() if published_at else None
        )

        # Get primary ticker for momentum check
        ticker = tickers_list[0] if tickers_list else None

        if not ticker:
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: Article has no tickers",
                article_id=article_id
            )
            return

        # 🔥 CONFLUENCE SCORING: 2-second observation window after publication
        # Scoring system:
        # - Spread widening >15% → +2 points (market makers retreating = real catalyst)
        # - Spread widening 5-15% → +1 point
        # - Spread stable (±5%) → 0 points
        # - Spread tightening >5% → -1 point (market ignoring news)
        # - Volume surge >3x → +1 point
        # - Price excursion >1% → +1 point
        #
        # Position sizing by score:
        # - Score ≤0: $2,000 (MINIMUM)
        # - Score 1: $5,000 (STANDARD)
        # - Score 2: $7,500 (HIGH)
        # - Score 3+: $10,000 (VERY_HIGH)
        conviction, confluence_metadata = await check_confluence_signals(
            market_data_client=market_data_client,
            quote_fetcher=quote_fetcher,
            ticker=ticker,
            publication_time=published_at,
        )

        logger.info(
            "📈 CONFLUENCE SCORE DETERMINED",
            ticker=ticker,
            conviction=conviction.value,
            confluence_score=confluence_metadata.get("confluence_score", 0),
            position_size=f"${POSITION_SIZES_USD[conviction]}",
            spread_change_pct=confluence_metadata.get("spread_change_pct", 0),
            price_excursion_pct=confluence_metadata.get("price_excursion_pct", 0),
            volume_surge=confluence_metadata.get("volume_surge", False),
            initial_nbbo_mid=confluence_metadata.get("initial_nbbo_mid"),
            ask_change_pct=confluence_metadata.get("ask_change_pct", 0),
            spread_compression_pct=confluence_metadata.get("spread_compression_pct", 0)
        )

        # ============================================================
        # 🛡️ EARLY-ENTRY FILTERS: Ensure we're catching moves early, not chasing
        # ============================================================
        # These filters prevent us from becoming exit liquidity:
        # 1. Market cap >= $10M (small caps too manipulable)
        # 2. Ask price stable (ask_change_pct ~0% means we're early)
        # 3. Spread compression < 50% (heavy compression = MMs pulling liquidity)

        # Filter 1: Market cap check
        if metadata_cache:
            try:
                ticker_metadata = await metadata_cache.get_permanent(ticker)
                if ticker_metadata:
                    market_cap_millions = ticker_metadata.get("market_cap_millions", 0)
                    if market_cap_millions and market_cap_millions < 10:
                        logger.info(
                            "⏭️ AUTO-TRADE SKIPPED: Market cap below $10M threshold",
                            ticker=ticker,
                            market_cap_millions=round(market_cap_millions, 2),
                            article_id=article_id
                        )
                        return
            except Exception as e:
                logger.debug(f"Could not check market cap: {e}")

        # Filter 2: Ask price stability check (must be ~0% change to trade)
        # If ask moved significantly, we're late to the trade
        ask_change_pct = confluence_metadata.get("ask_change_pct", 0)
        ASK_CHANGE_THRESHOLD = 3.0  # Allow up to 3% ask change (was originally 0% but need some tolerance)
        if abs(ask_change_pct) > ASK_CHANGE_THRESHOLD:
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: Ask price moved too much - we're late",
                ticker=ticker,
                ask_change_pct=ask_change_pct,
                threshold=ASK_CHANGE_THRESHOLD,
                initial_ask=confluence_metadata.get("initial_ask"),
                final_ask=confluence_metadata.get("final_ask"),
                article_id=article_id
            )
            return

        # Filter 3: Spread compression check (compression > 50% = danger)
        # Positive spread_compression_pct means spread got tighter (bad)
        # Negative means spread widened (neutral/good)
        spread_compression_pct = confluence_metadata.get("spread_compression_pct", 0)
        SPREAD_COMPRESSION_THRESHOLD = 50.0  # Max 50% compression allowed
        if spread_compression_pct > SPREAD_COMPRESSION_THRESHOLD:
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: Spread compressed too much - MMs pulling liquidity",
                ticker=ticker,
                spread_compression_pct=spread_compression_pct,
                threshold=SPREAD_COMPRESSION_THRESHOLD,
                initial_spread=confluence_metadata.get("initial_spread"),
                final_spread=confluence_metadata.get("final_spread"),
                article_id=article_id
            )
            return

        logger.info(
            "✅ EARLY-ENTRY FILTERS PASSED",
            ticker=ticker,
            ask_change_pct=ask_change_pct,
            spread_compression_pct=spread_compression_pct,
            article_id=article_id
        )

        # Build trade request - create a minimal Article-like object for build_trade_request_for_article
        # We only need: id, tickers, title for the trade request builder
        class MinimalArticle:
            """Minimal article-like object with same interface as domain Article."""
            def __init__(self, id: str, tickers: frozenset, title: str, published_at: datetime):
                self.id = id
                self.tickers = tickers
                self.title = title
                self.published_at = published_at

            def has_tickers(self) -> bool:
                return bool(self.tickers)

        minimal_article = MinimalArticle(
            id=article_id,
            tickers=frozenset(tickers_list),
            title=title,
            published_at=published_at
        )

        # Build trade request with conviction-based position sizing
        current_price = None  # Executor will fetch current price
        trade_request = build_trade_request_for_article(
            minimal_article,
            current_price=current_price,
            ticker=ticker,
            conviction=conviction
        )

        if not trade_request:
            return

        # 🚀 MICROSTRUCTURE CHECK: Ensure there is actually trading activity
        # "Truly big moves always have volume." - User
        if market_data_client and StockTradesRequest:
            try:
                # Check for any trades since publication
                trades_start = published_at
                trades_end = datetime.now(timezone.utc)

                trades = market_data_client.get_stock_trades(StockTradesRequest(
                    symbol_or_symbols=trade_request.ticker,
                    start=trades_start,
                    end=trades_end,
                    feed=DataFeed.SIP
                ))

                total_vol = 0
                if trades and trades.data and trade_request.ticker in trades.data:
                    total_vol = sum(t.size for t in trades.data[trade_request.ticker])

                if total_vol == 0:
                    logger.info(
                        "⏭️ AUTO-TRADE SKIPPED: Zero volume since publication (Dead Market)",
                        ticker=trade_request.ticker,
                        article_id=article_id,
                        latency_seconds=round((trades_end - trades_start).total_seconds(), 2)
                    )
                    return

                logger.info(
                    "📊 MICROSTRUCTURE VERIFIED: Volume detected since publication",
                    ticker=trade_request.ticker,
                    volume=total_vol,
                    article_id=article_id
                )
            except Exception as e:
                logger.error(f"Error checking volume for auto-trade gate: {e}")
                # Optional: continue anyway or skip? Let's be safe and trade if error (no gate)
                pass

        # Publish trade request with conviction metadata
        logger.info(
            "🚀 AUTO-TRADING: Publishing trade request domain event",
            ticker=trade_request.ticker,
            article_id=article_id,
            conviction=conviction.value,
            position_size=f"${POSITION_SIZES_USD[conviction]}"
        )

        await publish_trade_request(
            event_bus,
            trade_request,
            article_id,
            conviction=conviction,
            confluence_metadata=confluence_metadata
        )

    except Exception as e:
        logger.error(
            "❌ AUTO-TRADE EXCEPTION",
            error=str(e),
            article_id=classification_result.article_id,
            exc_info=True
        )


class AutoTradeService:
    """
    Trading service that subscribes to domain events and handles trade requests.
    
    Minimal wrapper for event subscription - business logic is in pure functions above.
    
    Responsibilities:
    - Subscribe to Domain.ArticleClassified events for IMMINENT articles
    - Delegate to pure functions for processing
    
    Does NOT:
    - Execute trades (brokerage microservice does that)
    - Know about infrastructure details
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,
        storage_query_service: StorageQueryService,
        enabled: bool,
        market_data_client: Optional["StockHistoricalDataClient"] = None,
        quote_fetcher=None,  # AlpacaQuoteFetcher for NBBO snapshots
        metadata_cache=None,  # MetadataCache for market cap lookup
    ):
        """
        Initialize auto-trade service.

        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            storage_query_service: Storage query service for fetching articles
            enabled: Whether auto-trading is enabled (injected via DI)
            market_data_client: Optional Alpaca market data client for volume checks
            quote_fetcher: Optional AlpacaQuoteFetcher for NBBO snapshots (for confluence scoring)
            metadata_cache: Optional MetadataCache for market cap and sector lookup
        """
        self.is_enabled = enabled
        self.event_bus = event_bus
        self.storage_query_service = storage_query_service
        self.market_data_client = market_data_client
        self.quote_fetcher = quote_fetcher
        self.metadata_cache = metadata_cache

        # Track wrapper for unsubscribe
        self._article_classified_wrapper = None

        logger.info(
            "AutoTradeService initialized - ready to start subscriptions",
            enabled=self.is_enabled,
            has_storage_query=self.storage_query_service is not None,
            has_quote_fetcher=self.quote_fetcher is not None,
            has_metadata_cache=self.metadata_cache is not None,
            observation_window_seconds=OBSERVATION_WINDOW_SECONDS
        )
    
    async def _handle_article_classified(
        self,
        domain_event: ArticleClassifiedDomainEvent,
    ) -> None:
        """
        Handle Domain.ArticleClassified event - delegate to pure function.

        This is called when classification is complete (event-driven from classification microservice).
        Uses article metadata from event directly to avoid storage fetch delay.
        """
        logger.info(
            "🎯 AUTO-TRADE: Received ArticleClassified event (with article metadata)",
            article_id=domain_event.result.article_id,
            classification=domain_event.result.classification.value,
            tickers=domain_event.tickers,
            has_published_at=domain_event.published_at is not None,
            enabled=self.is_enabled
        )
        await process_imminent_article(
            self.event_bus,
            self.storage_query_service,
            domain_event.result,
            self.is_enabled,
            self.market_data_client,
            self.quote_fetcher,
            self.metadata_cache,
            # Pass event metadata directly - no storage fetch needed
            event_tickers=domain_event.tickers,
            event_title=domain_event.title,
            event_published_at=domain_event.published_at,
        )
    
    async def start(self) -> None:
        """Start the service - subscribe to domain events."""
        if self._article_classified_wrapper:
            logger.debug("AutoTradeService already started")
            return

        # Subscribe to typed Domain.ArticleClassified events
        self._article_classified_wrapper = subscribe_typed(
            self.event_bus,
            DomainEventType.ARTICLE_CLASSIFIED,
            ArticleClassifiedDomainEvent,
            self._handle_article_classified,
        )
        logger.info("AutoTradeService started")
    
    async def stop(self) -> None:
        """Stop the service - unsubscribe from domain events."""
        if self._article_classified_wrapper:
            self.event_bus.unsubscribe(DomainEventType.ARTICLE_CLASSIFIED, self._article_classified_wrapper)
            self._article_classified_wrapper = None
            logger.info("AutoTradeService stopped")

