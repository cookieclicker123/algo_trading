"""
Auto-trade service - subscribes to domain events and handles trading logic.

Confluence Scoring System (2-second observation window after publication):
- Volume surge (1000+ shares in 2s) → +1 point
- Price excursion >1% → +1 point
- Buying pressure >80% → +1 point

Position sizing by score:
- Score 0: 8-second surge window (if surge found → $5k, else SKIP)
- Score 1: $7,500 (STANDARD)
- Score 2: $10,000 (HIGH)
- Score 3: $15,000 (VERY_HIGH)

Surge window (8s, stricter criteria - ALL required):
- Volume multiplier ≥ 10x
- Trade count multiplier ≥ 10x
- Price action ≥ 5%
- Buying pressure ≥ 80%

Stop loss: 5% below actual entry price - caps max loss per trade.
Chase filter: 7% max ask change from reception (only for immediate entries, not surge).

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
from ...shared.statistics.volume_analyzer import analyze_volume_around_event
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


# ============================================================
# DUPLICATE POSITION & COOLDOWN PROTECTION
# ============================================================
# Track active positions and recently exited tickers to prevent:
# 1. Entering same ticker twice from different article IDs
# 2. Re-entering a ticker immediately after time-based exit

# Active tickers with open positions (set by composition_root on TradeExecuted)
_active_positions: set = set()

# Recently exited tickers with cooldown (ticker -> exit_time)
_exited_tickers: dict = {}
TICKER_COOLDOWN_MINUTES = 30  # Don't re-enter for 30 minutes after exit

# ============================================================
# RECENTLY SKIPPED TICKER TRACKING (Risk Reduction)
# ============================================================
# If we skipped a headline for a ticker recently, subsequent headlines
# are higher risk (the first headline already moved the stock).
# Cap position size at $5k for these "second wave" entries.

_skipped_tickers: dict = {}  # ticker -> skip_time
SKIPPED_TICKER_WINDOW_MINUTES = 10  # Remember skips for 10 minutes
SKIPPED_TICKER_MAX_POSITION_USD = 5000  # Cap at $5k for recently skipped tickers


def register_skipped_ticker(ticker: str) -> None:
    """Register that we skipped a headline for this ticker."""
    ticker_upper = ticker.upper()
    _skipped_tickers[ticker_upper] = datetime.now(timezone.utc)
    logger.info(
        f"Ticker skip registered: {ticker_upper}",
        window_minutes=SKIPPED_TICKER_WINDOW_MINUTES,
        max_position_if_traded=f"${SKIPPED_TICKER_MAX_POSITION_USD}"
    )


def was_recently_skipped(ticker: str) -> bool:
    """Check if we skipped a headline for this ticker recently."""
    ticker_upper = ticker.upper()
    if ticker_upper not in _skipped_tickers:
        return False

    skip_time = _skipped_tickers[ticker_upper]
    window_end = skip_time + timedelta(minutes=SKIPPED_TICKER_WINDOW_MINUTES)

    if datetime.now(timezone.utc) > window_end:
        # Window expired, remove from tracking
        del _skipped_tickers[ticker_upper]
        return False

    return True


def get_skip_age_seconds(ticker: str) -> Optional[float]:
    """Get how long ago we skipped this ticker, or None if not skipped recently."""
    ticker_upper = ticker.upper()
    if ticker_upper not in _skipped_tickers:
        return None

    skip_time = _skipped_tickers[ticker_upper]
    return (datetime.now(timezone.utc) - skip_time).total_seconds()


def register_active_position(ticker: str) -> None:
    """Register a ticker as having an active position."""
    _active_positions.add(ticker.upper())
    logger.info(f"Position registered: {ticker.upper()}", active_count=len(_active_positions))


def unregister_active_position(ticker: str) -> None:
    """Unregister a ticker when position is closed."""
    ticker_upper = ticker.upper()
    _active_positions.discard(ticker_upper)
    # Add to cooldown tracking
    _exited_tickers[ticker_upper] = datetime.now(timezone.utc)
    logger.info(
        f"Position unregistered, cooldown started: {ticker_upper}",
        active_count=len(_active_positions),
        cooldown_minutes=TICKER_COOLDOWN_MINUTES
    )


def has_active_position(ticker: str) -> bool:
    """Check if we already have an active position in this ticker."""
    return ticker.upper() in _active_positions


def is_ticker_in_cooldown(ticker: str) -> bool:
    """Check if ticker is in cooldown period after recent exit."""
    ticker_upper = ticker.upper()
    if ticker_upper not in _exited_tickers:
        return False

    exit_time = _exited_tickers[ticker_upper]
    cooldown_end = exit_time + timedelta(minutes=TICKER_COOLDOWN_MINUTES)

    if datetime.now(timezone.utc) > cooldown_end:
        # Cooldown expired, remove from tracking
        del _exited_tickers[ticker_upper]
        return False

    return True


def get_cooldown_remaining(ticker: str) -> Optional[float]:
    """Get remaining cooldown time in minutes, or None if not in cooldown."""
    ticker_upper = ticker.upper()
    if ticker_upper not in _exited_tickers:
        return None

    exit_time = _exited_tickers[ticker_upper]
    cooldown_end = exit_time + timedelta(minutes=TICKER_COOLDOWN_MINUTES)
    remaining = (cooldown_end - datetime.now(timezone.utc)).total_seconds() / 60

    return max(0, remaining)


# Position sizing based on confluence score
POSITION_SIZES_USD = {
    ConvictionLevel.MINIMUM: Decimal("5000.00"),      # Score 0: Surge window → $5k if surge found
    ConvictionLevel.STANDARD: Decimal("7500.00"),     # Score 1: $7.5k position
    ConvictionLevel.HIGH: Decimal("10000.00"),        # Score 2: $10k position
    ConvictionLevel.VERY_HIGH: Decimal("15000.00"),   # Score 3: $15k position (max confluence)
}

# Thresholds for 2-second observation window (publication-anchored)
OBSERVATION_WINDOW_SECONDS = 2.0      # 2-second window after article publication
PRICE_EXCURSION_THRESHOLD_PCT = 0.01  # 1% price move = +1 point
VOLUME_SURGE_THRESHOLD = 1000         # 1000+ shares in 2s = volume surge (+1 point)
BUYING_PRESSURE_THRESHOLD = 0.80      # 80% buying pressure = +1 point

# ============================================================
# 8-SECOND "LAST CHANCE" SURGE MONITORING CONFIGURATION
# ============================================================
# When 2-second confluence check fails (MINIMUM conviction), we give the trade
# an 8-second "last chance" window to prove itself with ALL criteria met:
# - Volume multiplier ≥ 10x
# - Trade count multiplier ≥ 10x
# - Price action ≥ 5%
# - Buying pressure ≥ 80%
# Trade with MINIMUM conviction ($5k) - conservative sizing for late entries
LAST_CHANCE_WINDOW_SECONDS = 8        # Total window for surge monitoring
LAST_CHANCE_POLL_INTERVAL = 1.0       # Check every 1 second
SURGE_VOLUME_MULTIPLIER = 10.0        # 10x volume required for surge
SURGE_TRADE_COUNT_MULTIPLIER = 10.0   # 10x trade count required for surge
SURGE_PRICE_ACTION_PCT = 5.0          # 5% price move required for surge
SURGE_BUYING_PRESSURE = 0.80          # 80% buying pressure required for surge


async def monitor_for_last_chance_surge(
    market_data_client: Optional["StockHistoricalDataClient"],
    quote_fetcher,  # AlpacaQuoteFetcher for NBBO snapshots
    ticker: str,
    publication_time: datetime,
    initial_nbbo_mid: Optional[float],
    article_id: str,
) -> Optional[dict]:
    """
    Monitor for 8 seconds after MINIMUM conviction for a qualifying surge.

    This is the "last chance" mechanism - when the 2-second confluence check fails,
    we give the trade 8 more seconds to prove itself with ALL criteria met:
    - Volume multiplier ≥ 10x (vs prior 10-min avg)
    - Trade count multiplier ≥ 10x (vs prior 10-min avg)
    - Price action ≥ 5% (max excursion from pub ask)
    - Buying pressure ≥ 80% (imbalance_ratio >= 0.60)

    If ALL criteria are met, we trade with MINIMUM conviction ($5k).
    If 8 seconds pass without qualifying, we skip.

    Args:
        market_data_client: Alpaca market data client for volume analysis
        quote_fetcher: Quote fetcher for NBBO snapshots
        ticker: Stock ticker to monitor
        publication_time: When the article was published
        initial_nbbo_mid: Initial NBBO mid for reference
        article_id: Article ID for logging

    Returns:
        Dict with surge data and NBBO if qualifying surge found, None otherwise
    """
    import asyncio

    if not market_data_client:
        logger.debug("Last chance surge monitor skipped - no market data client", ticker=ticker)
        return None

    logger.info(
        "🔍 LAST CHANCE: Starting 8-second surge monitoring (strict: 10x vol, 10x trades, 5% price, 80% buy pressure)",
        ticker=ticker,
        article_id=article_id,
        initial_nbbo_mid=initial_nbbo_mid
    )

    # Convert buying pressure threshold to imbalance_ratio
    # buying_pressure = (imbalance_ratio + 1) / 2
    # imbalance_ratio = 2 * buying_pressure - 1
    min_imbalance_ratio = 2 * SURGE_BUYING_PRESSURE - 1  # 0.60 for 80%

    num_checks = int(LAST_CHANCE_WINDOW_SECONDS / LAST_CHANCE_POLL_INTERVAL)

    for check_num in range(num_checks):
        try:
            # Wait before checking (except first iteration - check immediately)
            if check_num > 0:
                await asyncio.sleep(LAST_CHANCE_POLL_INTERVAL)

            # Get current NBBO
            current_nbbo = None
            if quote_fetcher:
                try:
                    current_nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
                except Exception as e:
                    logger.debug(f"Could not get NBBO for surge check: {e}")
                    continue

            if not current_nbbo:
                continue

            # Run volume analysis at current time
            event_time = datetime.now(timezone.utc)

            try:
                volume_analysis = await analyze_volume_around_event(
                    client=market_data_client,
                    symbol=ticker,
                    event_time=event_time,
                    received_at=event_time,
                    reference_nbbo=current_nbbo,
                    stream_manager=quote_fetcher.stream_manager if hasattr(quote_fetcher, 'stream_manager') else None
                )
            except Exception as e:
                logger.debug(f"Volume analysis error in surge check: {e}")
                continue

            # Extract metrics for strict ALL-criteria check
            surge_multiplier = volume_analysis.surge_multiplier or 0
            trade_count_multiplier = volume_analysis.trade_count_multiplier or 0
            max_excursion_pct = volume_analysis.max_excursion_pct or 0
            imbalance_ratio = volume_analysis.imbalance_ratio or 0
            buying_pressure_pct = (imbalance_ratio + 1) / 2 * 100

            logger.debug(
                f"LAST CHANCE check #{check_num + 1}/{num_checks}",
                ticker=ticker,
                surge_multiplier=round(surge_multiplier, 1),
                trade_count_multiplier=round(trade_count_multiplier, 1),
                max_excursion_pct=round(max_excursion_pct, 2),
                buying_pressure_pct=round(buying_pressure_pct, 1),
                imbalance_ratio=round(imbalance_ratio, 3)
            )

            # ALL criteria must be met (strict AND):
            vol_ok = surge_multiplier >= SURGE_VOLUME_MULTIPLIER
            trades_ok = trade_count_multiplier >= SURGE_TRADE_COUNT_MULTIPLIER
            price_ok = max_excursion_pct >= SURGE_PRICE_ACTION_PCT
            pressure_ok = imbalance_ratio >= min_imbalance_ratio

            if vol_ok and trades_ok and price_ok and pressure_ok:
                logger.info(
                    "🚀 LAST CHANCE SURGE FOUND: ALL strict criteria met!",
                    ticker=ticker,
                    article_id=article_id,
                    check_number=check_num + 1,
                    seconds_elapsed=round((check_num + 1) * LAST_CHANCE_POLL_INTERVAL, 1),
                    surge_multiplier=round(surge_multiplier, 1),
                    trade_count_multiplier=round(trade_count_multiplier, 1),
                    max_excursion_pct=round(max_excursion_pct, 2),
                    buying_pressure_pct=round(buying_pressure_pct, 1),
                    surge_ask=current_nbbo.get("ask"),
                    surge_bid=current_nbbo.get("bid")
                )

                return {
                    "surge_found": True,
                    "surge_nbbo": current_nbbo,
                    "surge_nbbo_mid": current_nbbo.get("mid"),
                    "volume_analysis": volume_analysis,
                    "check_number": check_num + 1,
                    "seconds_elapsed": round((check_num + 1) * LAST_CHANCE_POLL_INTERVAL, 1),
                    "imbalance_ratio": imbalance_ratio,
                    "surge_multiplier": surge_multiplier,
                    "trade_count_multiplier": trade_count_multiplier,
                    "max_excursion_pct": max_excursion_pct,
                    "buying_pressure_pct": buying_pressure_pct,
                }

            # Log progress for partially-met criteria
            criteria_met = sum([vol_ok, trades_ok, price_ok, pressure_ok])
            if criteria_met >= 2:
                logger.debug(
                    f"LAST CHANCE: {criteria_met}/4 criteria met",
                    ticker=ticker,
                    check_number=check_num + 1,
                    vol_ok=vol_ok,
                    trades_ok=trades_ok,
                    price_ok=price_ok,
                    pressure_ok=pressure_ok
                )

        except Exception as e:
            logger.debug(f"Error in surge monitoring check #{check_num + 1}: {e}")
            continue

    logger.info(
        "⏭️ LAST CHANCE: No qualifying surge in 8-second window (requires ALL: 10x vol, 10x trades, 5% price, 80% buy)",
        ticker=ticker,
        article_id=article_id,
        checks_performed=num_checks
    )
    return None


async def check_confluence_signals(
    market_data_client: Optional["StockHistoricalDataClient"],
    quote_fetcher,  # AlpacaQuoteFetcher for NBBO snapshots
    ticker: str,
    publication_time: datetime,
    baseline_volume: Optional[float] = None,
) -> tuple[ConvictionLevel, dict]:
    """
    Check confluence signals in 2-second window after article publication.

    Confluence Scoring (3 criteria, max 3 points):
    - Volume surge (1000+ shares in 2s) → +1 point
    - Price excursion >1% → +1 point
    - Buying pressure >80% → +1 point

    Position sizing by score:
    - Score 0: MINIMUM → 8-second surge window ($5k if surge, else SKIP)
    - Score 1: STANDARD → $7,500
    - Score 2: HIGH → $10,000
    - Score 3: VERY_HIGH → $15,000

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
        "price_excursion_pct": 0.0,
        "has_price_excursion": False,
        "volume": 0,
        "volume_surge": False,
        "buying_pressure_pct": 0.0,
        "has_buying_pressure": False,
        "conviction": ConvictionLevel.MINIMUM.value,
        # Fields for early-entry filters
        "initial_ask": None,
        "final_ask": None,
        "ask_change_pct": 0.0,
        "initial_spread": None,
        "final_spread": None,
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

        # Calculate ask_change_pct and spread_compression_pct for early-entry filters
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
                spread_compression = ((initial_spread - final_spread) / initial_spread) * 100
                metadata["spread_compression_pct"] = round(spread_compression, 2)

        # ============================================================
        # STEP 3: Fetch trades in 2-second window for volume/price/pressure analysis
        # ============================================================
        confluence_score = 0

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
                # CRITERION 1: Volume surge (1000+ shares in 2s)
                # ============================================================
                if total_volume >= VOLUME_SURGE_THRESHOLD:
                    confluence_score += 1
                    metadata["volume_surge"] = True
                    logger.info(
                        f"📈 VOLUME SURGE: +1 point ({total_volume} shares in 2s)",
                        ticker=ticker,
                        volume=total_volume,
                        threshold=VOLUME_SURGE_THRESHOLD
                    )

                # ============================================================
                # CRITERION 2: Price excursion >1%
                # ============================================================
                if max_move_pct >= PRICE_EXCURSION_THRESHOLD_PCT:
                    confluence_score += 1
                    metadata["has_price_excursion"] = True
                    logger.info(
                        f"📈 PRICE EXCURSION: +1 point ({max_move_pct*100:.2f}%)",
                        ticker=ticker,
                        max_move_pct=f"{max_move_pct*100:.2f}%"
                    )

                # ============================================================
                # CRITERION 3: Buying pressure >80% (tick rule)
                # ============================================================
                # Classify each trade as buy/sell using tick rule:
                # price > prev_price → buy, price < prev_price → sell, same → split
                buy_volume = 0
                sell_volume = 0
                prev_price = None

                for t in trade_list:
                    if prev_price is not None:
                        if t.price > prev_price:
                            buy_volume += t.size
                        elif t.price < prev_price:
                            sell_volume += t.size
                        else:
                            # Same price - split evenly
                            buy_volume += t.size / 2
                            sell_volume += t.size / 2
                    else:
                        # First trade - classify as buy (conservative for uptick bias)
                        buy_volume += t.size
                    prev_price = t.price

                buying_pressure = buy_volume / total_volume if total_volume > 0 else 0
                metadata["buying_pressure_pct"] = round(buying_pressure * 100, 1)
                metadata["buy_volume"] = int(buy_volume)
                metadata["sell_volume"] = int(sell_volume)

                if buying_pressure >= BUYING_PRESSURE_THRESHOLD:
                    confluence_score += 1
                    metadata["has_buying_pressure"] = True
                    logger.info(
                        f"📈 BUYING PRESSURE: +1 point ({buying_pressure*100:.1f}% buy-sided)",
                        ticker=ticker,
                        buying_pressure=f"{buying_pressure*100:.1f}%",
                        buy_volume=int(buy_volume),
                        sell_volume=int(sell_volume)
                    )

        # ============================================================
        # STEP 4: Determine conviction level from confluence score
        # Score 0 = MINIMUM (surge window), 1 = STANDARD ($7.5k), 2 = HIGH ($10k), 3 = VERY_HIGH ($15k)
        # ============================================================
        metadata["confluence_score"] = confluence_score

        if confluence_score >= 3:
            conviction = ConvictionLevel.VERY_HIGH
            logger.info(
                f"🔥🔥 VERY HIGH CONVICTION (score {confluence_score}): All 3 criteria met → $15k position",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                volume_surge=metadata.get("volume_surge"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                buying_pressure_pct=metadata.get("buying_pressure_pct")
            )
        elif confluence_score == 2:
            conviction = ConvictionLevel.HIGH
            logger.info(
                f"🔥 HIGH CONVICTION (score {confluence_score}): 2 criteria met → $10k position",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                volume_surge=metadata.get("volume_surge"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                buying_pressure_pct=metadata.get("buying_pressure_pct")
            )
        elif confluence_score == 1:
            conviction = ConvictionLevel.STANDARD
            logger.info(
                f"📊 STANDARD CONVICTION (score {confluence_score}): 1 criterion met → $7.5k position",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                volume_surge=metadata.get("volume_surge"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                buying_pressure_pct=metadata.get("buying_pressure_pct")
            )
        else:
            conviction = ConvictionLevel.MINIMUM
            logger.info(
                f"⚠️ MINIMUM CONVICTION (score {confluence_score}): No criteria met → surge window or SKIP",
                ticker=ticker,
                volume=metadata.get("volume"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                buying_pressure_pct=metadata.get("buying_pressure_pct"),
                reason="No volume/price/pressure signals in 2s window"
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

    Note: This version doesn't have access to quote_fetcher for NBBO tracking.
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
    - MINIMUM: $5,000 (surge trade)
    - STANDARD: $7,500 (1 criterion met)
    - HIGH: $10,000 (2 criteria met)
    - VERY_HIGH: $15,000 (all 3 criteria met)

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

    # Risk reduction: If we skipped a headline for this ticker recently, cap position
    # The first headline already moved the stock - we're entering as "second wave" exit liquidity
    ticker_to_check = ticker or (next(iter(article.tickers), None) if article.tickers else None)
    if ticker_to_check and was_recently_skipped(ticker_to_check):
        skip_age = get_skip_age_seconds(ticker_to_check)
        original_size = TRADE_SIZE_USD
        TRADE_SIZE_USD = min(TRADE_SIZE_USD, Decimal(str(SKIPPED_TICKER_MAX_POSITION_USD)))
        logger.warning(
            f"⚠️ RISK REDUCTION: Ticker was skipped {skip_age:.0f}s ago - capping position at ${SKIPPED_TICKER_MAX_POSITION_USD}",
            ticker=ticker_to_check,
            original_size=float(original_size),
            capped_size=float(TRADE_SIZE_USD),
            skip_age_seconds=skip_age,
            reason="Second headline for same ticker - higher risk of fade"
        )

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

        # ============================================================
        # 🛡️ DUPLICATE POSITION GUARD: Don't enter if already have position
        # ============================================================
        if has_active_position(ticker):
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: Already have active position in ticker",
                ticker=ticker,
                article_id=article_id,
                reason="Duplicate position prevention"
            )
            return

        # ============================================================
        # 🕐 TICKER COOLDOWN: Don't re-enter too soon after exit
        # ============================================================
        if is_ticker_in_cooldown(ticker):
            remaining_min = get_cooldown_remaining(ticker)
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: Ticker in cooldown after recent exit",
                ticker=ticker,
                article_id=article_id,
                cooldown_remaining_min=round(remaining_min, 1) if remaining_min else 0,
                cooldown_total_min=TICKER_COOLDOWN_MINUTES,
                reason="Preventing re-entry after time-based exit"
            )
            return

        # 🔥 CONFLUENCE SCORING: 2-second observation window after publication
        # 3 criteria (max 3 points):
        # - Volume surge (1000+ shares) → +1 point
        # - Price excursion >1% → +1 point
        # - Buying pressure >80% → +1 point
        #
        # Position sizing: 0=$5k(surge), 1=$7.5k, 2=$10k, 3=$15k
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
            volume_surge=confluence_metadata.get("volume_surge", False),
            price_excursion_pct=confluence_metadata.get("price_excursion_pct", 0),
            buying_pressure_pct=confluence_metadata.get("buying_pressure_pct", 0),
            initial_nbbo_mid=confluence_metadata.get("initial_nbbo_mid"),
            ask_change_pct=confluence_metadata.get("ask_change_pct", 0),
        )

        # ============================================================
        # 🚫 MINIMUM CONVICTION: 8-SECOND "LAST CHANCE" SURGE MONITORING
        # ============================================================
        # When no confluence criteria are met (score 0), give the trade 8 more seconds
        # to prove itself with ALL strict surge criteria:
        # - Volume ≥ 10x, Trades ≥ 10x, Price ≥ 5%, Buying pressure ≥ 80%
        #
        # If ALL met → trade with MINIMUM conviction ($5k, conservative late entry)
        # If 8 seconds pass without qualifying → SKIP
        is_surge_trade = False
        if conviction == ConvictionLevel.MINIMUM:
            logger.info(
                "🔍 MINIMUM CONVICTION: Starting 8-second last chance surge monitor",
                ticker=ticker,
                confluence_score=confluence_metadata.get("confluence_score", 0),
                volume_surge=confluence_metadata.get("volume_surge", False),
                buying_pressure_pct=confluence_metadata.get("buying_pressure_pct", 0),
                article_id=article_id,
                reason="No confluence criteria met - checking for late surge before skipping"
            )

            # Run the 8-second last chance surge monitoring
            surge_result = await monitor_for_last_chance_surge(
                market_data_client=market_data_client,
                quote_fetcher=quote_fetcher,
                ticker=ticker,
                publication_time=published_at,
                initial_nbbo_mid=confluence_metadata.get("initial_nbbo_mid"),
                article_id=article_id,
            )

            if surge_result is None:
                # No qualifying surge found - skip the trade
                logger.info(
                    "⏭️ SKIPPING TRADE: No qualifying surge in 8-second window",
                    ticker=ticker,
                    article_id=article_id,
                    reason="MINIMUM conviction and no late surge detected"
                )
                return

            # Qualifying surge found! Trade with MINIMUM conviction ($5k)
            is_surge_trade = True
            logger.info(
                "🚀 LAST CHANCE SURGE: Trading with MINIMUM conviction ($5k)",
                ticker=ticker,
                article_id=article_id,
                surge_seconds=surge_result.get("seconds_elapsed"),
                surge_multiplier=surge_result.get("surge_multiplier"),
                trade_count_multiplier=surge_result.get("trade_count_multiplier"),
                max_excursion_pct=surge_result.get("max_excursion_pct"),
                buying_pressure_pct=surge_result.get("buying_pressure_pct"),
                surge_ask=surge_result.get("surge_nbbo", {}).get("ask"),
            )

            # Keep conviction at MINIMUM ($5k) for late-entry surge trades

            # Update confluence_metadata with surge data
            confluence_metadata["last_chance_surge"] = True
            confluence_metadata["surge_seconds_elapsed"] = surge_result.get("seconds_elapsed")
            confluence_metadata["surge_imbalance_ratio"] = surge_result.get("imbalance_ratio")
            confluence_metadata["surge_multiplier"] = surge_result.get("surge_multiplier")
            confluence_metadata["surge_trade_count_multiplier"] = surge_result.get("trade_count_multiplier")
            confluence_metadata["surge_max_excursion_pct"] = surge_result.get("max_excursion_pct")
            confluence_metadata["surge_buying_pressure_pct"] = surge_result.get("buying_pressure_pct")
            if surge_result.get("surge_nbbo"):
                surge_nbbo = surge_result["surge_nbbo"]
                confluence_metadata["surge_ask"] = surge_nbbo.get("ask")
                confluence_metadata["surge_bid"] = surge_nbbo.get("bid")
                confluence_metadata["surge_mid"] = surge_nbbo.get("mid")

        # ============================================================
        # 📊 MINIMUM VOLUME FILTER: 2000 shares required (except NEW_ACTIVITY)
        # ============================================================
        # Skip if volume too low to ensure reliable entry/exit liquidity.
        # Exception: NEW_ACTIVITY (stock went from dormant to active) is allowed
        # because any activity on a previously dead stock is significant.
        MIN_WINDOW_VOLUME = 2000
        window_volume = confluence_metadata.get("volume", 0)
        # Note: move_type would come from volume_analyzer if integrated
        # For now, check if volume_surge indicates NEW_ACTIVITY pattern
        has_volume_surge = confluence_metadata.get("volume_surge", False)

        if window_volume < MIN_WINDOW_VOLUME and not has_volume_surge and not is_surge_trade:
            logger.info(
                "⏭️ SKIPPING TRADE: Window volume too low for reliable entry",
                ticker=ticker,
                window_volume=window_volume,
                min_required=MIN_WINDOW_VOLUME,
                article_id=article_id,
                reason="Insufficient liquidity - need 2000+ shares in observation window"
            )
            return

        # ============================================================
        # 🛡️ EARLY-ENTRY FILTERS: Ensure we're catching moves early, not chasing
        # ============================================================
        # These filters prevent us from becoming exit liquidity:
        # 1. Market cap >= $1M (tiny caps too manipulable)
        # 2. Ask price stable (ask_change_pct ~0% means we're early)
        # 3. Spread compression < 50% (heavy compression = MMs pulling liquidity)

        # Filter 1: Market cap check (minimum $3M to avoid manipulated sub-penny stocks)
        MIN_MARKET_CAP_MILLIONS = 2.0  # $2M minimum
        if metadata_cache:
            try:
                ticker_metadata = await metadata_cache.get_permanent(ticker)
                if ticker_metadata:
                    market_cap_millions = ticker_metadata.get("market_cap_millions", 0)
                    if market_cap_millions and market_cap_millions < MIN_MARKET_CAP_MILLIONS:
                        logger.info(
                            "⏭️ AUTO-TRADE SKIPPED: Market cap below $2M threshold",
                            ticker=ticker,
                            market_cap_millions=round(market_cap_millions, 2),
                            min_required=MIN_MARKET_CAP_MILLIONS,
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

        # ============================================================
        # 🛡️ CHASE FILTER: Don't enter if ask moved >7% from reception
        # ============================================================
        # Data shows ALL winners entered within 6% of reception ask.
        # Entries at 7%+ above reception ask are tail entries (exit liquidity).
        # EXCEPTION: Surge trades bypass this filter - they already passed
        # strict criteria (10x vol, 10x trades, 5% price, 80% buy pressure).
        MAX_CHASE_PCT = 7.0
        initial_ask = confluence_metadata.get("initial_ask")
        if is_surge_trade:
            logger.info(
                "✅ CHASE FILTER BYPASSED: Surge trade (strict criteria already met)",
                ticker=ticker,
                article_id=article_id
            )
        elif quote_fetcher and initial_ask and initial_ask > 0:
            try:
                pre_entry_nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
                if pre_entry_nbbo:
                    current_ask = pre_entry_nbbo.get("ask", 0)
                    if current_ask and current_ask > 0:
                        chase_pct = ((current_ask - initial_ask) / initial_ask) * 100
                        if chase_pct > MAX_CHASE_PCT:
                            logger.info(
                                "⏭️ AUTO-TRADE SKIPPED: Chasing tail - ask moved >7% from reception",
                                ticker=ticker,
                                initial_ask=initial_ask,
                                current_ask=current_ask,
                                chase_pct=round(chase_pct, 1),
                                max_allowed=MAX_CHASE_PCT,
                                article_id=article_id,
                                reason="Winners enter <6.5% above recv ask. 7%+ = exit liquidity."
                            )
                            return
                        logger.debug(
                            "✅ CHASE CHECK PASSED",
                            ticker=ticker,
                            chase_pct=round(chase_pct, 1),
                            initial_ask=initial_ask,
                            current_ask=current_ask
                        )
            except Exception as e:
                logger.debug(f"Chase check failed (proceeding anyway): {e}")

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

