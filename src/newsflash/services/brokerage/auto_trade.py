"""
Auto-trade service - subscribes to domain events and handles trading logic.

AI-BASED POSITION SIZING (Immediate entry on classification):
- SMALL: $20 position (weak headline, vague, unknown partner)
- MODERATE: $30 position (decent headline, some specificity)
- LARGE: $50 position (strong headline, specific details)
- MAX: $70 position (transformational headline, >50% of market cap deal)

The AI determines position size based on:
1. Headline concreteness (specific $ amounts, named parties, definitive terms)
2. Deal value relative to market cap (>50% = transformational)
3. Catalyst strength for the industry
4. Counterparty quality (Fortune 100, major pharma, etc.)

Confluence scoring is still collected for statistical research but does NOT gate trades.
Trades execute IMMEDIATELY on AI classification to capture the move before volume arrives.

Stop loss: 5% below actual entry price - caps max loss per trade.
Chase filter: 7% max ask change from reception.

Pure functions for trade processing logic, with minimal service class for event subscriptions.
"""
from decimal import Decimal
from datetime import datetime, timezone, timedelta
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

# ============================================================
# RECALL ENGINE INTEGRATION (for recording post-AI skip reasons)
# ============================================================
# When an IMMINENT article is skipped due to post-AI checks, we record
# the reason in the recall engine for statistical analysis.
_recall_engine = None  # Set by composition_root


def set_recall_engine(engine) -> None:
    """Set the recall engine reference for recording post-AI skips."""
    global _recall_engine
    _recall_engine = engine


async def _record_postfilter_skip(article_id: str, reason: str) -> None:
    """Record a post-AI skip reason in the recall engine."""
    if _recall_engine:
        try:
            await _recall_engine.record_postfilter_skip(article_id, reason)
        except Exception as e:
            logger.debug(f"Failed to record postfilter skip: {e}")


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


# ============================================================
# POSITION SIZING - AI-BASED (TESTING MODE - 10x REDUCED)
# ============================================================
# Position size determined by AI based on headline quality, deal size vs market cap,
# catalyst strength, and counterparty quality.
# REDUCED 10x from normal sizes while testing/validating filters
# Paper shadow trades use 50x these amounts for meaningful stats
POSITION_SIZES_USD = {
    ConvictionLevel.MINIMUM: Decimal("20.00"),        # AI: SMALL - weak/vague headline
    ConvictionLevel.STANDARD: Decimal("30.00"),       # AI: MODERATE - decent specificity
    ConvictionLevel.HIGH: Decimal("50.00"),           # AI: LARGE - strong, specific headline
    ConvictionLevel.VERY_HIGH: Decimal("70.00"),      # AI: MAX - transformational (>50% mkt cap)
}

# Map AI position size strings to ConvictionLevel
AI_SIZE_TO_CONVICTION = {
    "SMALL": ConvictionLevel.MINIMUM,
    "MODERATE": ConvictionLevel.STANDARD,
    "LARGE": ConvictionLevel.HIGH,
    "MAX": ConvictionLevel.VERY_HIGH,
}

# Paper shadow multiplier - paper trades use this multiplier for comparison
PAPER_SHADOW_MULTIPLIER = 50  # Paper trades at 50x live size

# Thresholds for 2-second observation window (publication-anchored)
OBSERVATION_WINDOW_SECONDS = 2.0      # 2-second window after article publication
PRICE_EXCURSION_THRESHOLD_PCT = 0.01  # 1% price move = +1 point
VOLUME_SURGE_THRESHOLD = 2000         # 2000+ shares in 2s = volume surge (+1 point)
BUYING_PRESSURE_THRESHOLD = 0.80      # 80% buying pressure = +1 point

# ============================================================
# CONFLUENCE METRICS (For statistical analysis only - NOT used for gating)
# ============================================================
# These thresholds are used to collect microstructure data for research.
# Trades are NO LONGER gated on confluence - AI determines entry immediately.
# Data shows: headlines are harder to manipulate than microstructure (gap & trap).
LAST_CHANCE_WINDOW_SECONDS = 8        # Total window for surge monitoring
LAST_CHANCE_POLL_INTERVAL = 0.1       # Check every 100ms (WebSocket data is instant)
SURGE_PRICE_ACTION_PCT = 5.0          # 5% price move required for surge
SURGE_BUYING_PRESSURE = 0.80          # 80% buying pressure required for surge
FAST_SURGE_THRESHOLD_SECONDS = 3.0    # If surge found within 3s → STANDARD sizing, else MINIMUM
SURGE_VOLUME_MULTIPLIER = 3.0         # Volume must be 3x prior 10min average
SURGE_TRADE_COUNT_MULTIPLIER = 3.0    # Trade count must be 3x prior 10min average
MIN_ABSOLUTE_VOLUME = 2000            # Absolute minimum volume (even if prior is 0)
MIN_ABSOLUTE_TRADES = 20              # Absolute minimum trades (even if prior is 0)


async def monitor_for_last_chance_surge(
    market_data_client: Optional["StockHistoricalDataClient"],
    quote_fetcher,  # AlpacaQuoteFetcher for NBBO snapshots
    ticker: str,
    publication_time: datetime,
    initial_nbbo_mid: Optional[float],
    article_id: str,
    initial_ask_at_publication: Optional[float] = None,  # CRITICAL: Ask price at publication time
    prior_avg_volume: Optional[float] = None,  # Prior 10min avg volume for relative threshold
    prior_avg_trade_count: Optional[float] = None,  # Prior 10min avg trade count for relative threshold
) -> Optional[dict]:
    """
    Monitor for 8 seconds after MINIMUM conviction for a qualifying surge.

    This is the "last chance" mechanism - when the 2-second confluence check fails,
    we give the trade 8 more seconds to prove itself with ALL criteria met:
    - Volume ≥ 2000 shares AND ≥ 3x prior 10min avg
    - Trade count ≥ 3x prior 10min avg (minimum 20)
    - Price action ≥ 5% (max excursion from initial ask)
    - Buying pressure ≥ 80% (buy volume / total volume)

    FAST IMPLEMENTATION: Uses WebSocket cached data only (no REST API calls).
    Each check takes milliseconds instead of 8+ seconds.

    If ALL criteria are met within 3s → STANDARD conviction ($300).
    If ALL criteria are met after 3s → MINIMUM conviction ($200).
    If 8 seconds pass without qualifying → SKIP.

    Args:
        market_data_client: Alpaca market data client (unused - kept for signature compatibility)
        quote_fetcher: Quote fetcher with stream_manager for WebSocket data
        ticker: Stock ticker to monitor
        publication_time: When the article was published
        initial_nbbo_mid: Initial NBBO mid for reference
        article_id: Article ID for logging
        prior_avg_volume: Prior 10min average volume (for 3x multiplier check)
        prior_avg_trade_count: Prior 10min average trade count (for 3x multiplier check)

    Returns:
        Dict with surge data and NBBO if qualifying surge found, None otherwise
    """
    import asyncio

    # Get stream manager for WebSocket data
    stream_manager = getattr(quote_fetcher, 'stream_manager', None) if quote_fetcher else None
    if not stream_manager:
        logger.debug("Last chance surge monitor skipped - no WebSocket stream manager", ticker=ticker)
        return None

    # Use publication-time ask price for price excursion calculation
    # CRITICAL: We must use the ask price at publication time, NOT current ask.
    # By the time we enter surge monitoring (after confluence check), the price may have
    # already moved significantly. Using current ask would make the 5% threshold impossible
    # to reach for moves that have already happened.
    initial_ask = initial_ask_at_publication

    if not initial_ask:
        logger.debug("Last chance surge monitor skipped - no publication-time ask price", ticker=ticker)
        return None

    # Ensure publication_time is timezone-aware
    pub_time_utc = publication_time
    if pub_time_utc.tzinfo is None:
        pub_time_utc = pub_time_utc.replace(tzinfo=timezone.utc)

    # Fetch prior averages if not provided (one-time API call)
    # This adds ~100ms but gives us accurate baselines for relative thresholds
    prior_vol = prior_avg_volume
    prior_trades = prior_avg_trade_count

    if prior_vol is None or prior_trades is None:
        try:
            if market_data_client:
                volume_analysis = await analyze_volume_around_event(
                    client=market_data_client,
                    symbol=ticker,
                    event_time=pub_time_utc,
                    received_at=pub_time_utc,
                    reference_nbbo={"ask": initial_ask} if initial_ask else None,
                )
                if volume_analysis:
                    prior_vol = volume_analysis.prior_avg_10min_volume or 0
                    prior_trades = volume_analysis.prior_avg_10min_trade_count or 0
                    logger.debug(
                        "Fetched prior averages for surge monitoring",
                        ticker=ticker,
                        prior_avg_volume=prior_vol,
                        prior_avg_trade_count=prior_trades,
                    )
        except Exception as e:
            logger.debug(f"Could not fetch prior averages: {e}")
            prior_vol = 0
            prior_trades = 0

    prior_vol = prior_vol or 0
    prior_trades = prior_trades or 0

    # Calculate dynamic thresholds based on prior averages
    # Volume: max of (absolute minimum, 3x prior)
    volume_threshold = max(MIN_ABSOLUTE_VOLUME, prior_vol * SURGE_VOLUME_MULTIPLIER)

    # Trade count: max of (absolute minimum, 3x prior)
    trades_threshold = max(MIN_ABSOLUTE_TRADES, prior_trades * SURGE_TRADE_COUNT_MULTIPLIER)

    logger.info(
        "🔍 LAST CHANCE: Starting 8-second surge monitoring",
        ticker=ticker,
        article_id=article_id,
        initial_ask_at_publication=initial_ask,
        prior_avg_volume=prior_vol,
        prior_avg_trade_count=prior_trades,
        volume_threshold=volume_threshold,
        trades_threshold=int(trades_threshold),
        criteria=f"{int(volume_threshold)}+ vol, {int(trades_threshold)}+ trades, 5% price, 80% buy pressure",
    )

    # Surge thresholds for price and pressure
    MIN_PRICE_PCT = SURGE_PRICE_ACTION_PCT  # 5%
    MIN_BUY_PRESSURE = SURGE_BUYING_PRESSURE  # 80%

    num_checks = int(LAST_CHANCE_WINDOW_SECONDS / LAST_CHANCE_POLL_INTERVAL)

    for check_num in range(num_checks):
        try:
            # Wait before checking (except first iteration - check immediately)
            if check_num > 0:
                await asyncio.sleep(LAST_CHANCE_POLL_INTERVAL)

            # Get cached trades from WebSocket (INSTANT - no API call)
            trades = await stream_manager.get_recent_trades(ticker, max_trades=1000)
            if not trades:
                logger.debug(f"LAST CHANCE check #{check_num + 1}: No cached trades yet", ticker=ticker)
                continue

            # Filter trades to those after publication time
            window_trades = []
            for trade in trades:
                trade_ts = trade.get("timestamp")
                if trade_ts:
                    # Handle both datetime and string timestamps
                    if isinstance(trade_ts, str):
                        try:
                            trade_ts = datetime.fromisoformat(trade_ts.replace('Z', '+00:00'))
                        except:
                            continue
                    if trade_ts.tzinfo is None:
                        trade_ts = trade_ts.replace(tzinfo=timezone.utc)
                    if trade_ts >= pub_time_utc:
                        window_trades.append(trade)

            if not window_trades:
                logger.debug(f"LAST CHANCE check #{check_num + 1}: No trades after publication", ticker=ticker)
                continue

            # Calculate metrics from cached trades
            total_volume = sum(t.get("size", 0) for t in window_trades)
            trade_count = len(window_trades)
            max_price = max((t.get("price", 0) for t in window_trades), default=0)

            # Calculate price excursion
            price_excursion_pct = 0.0
            if initial_ask and max_price > 0:
                price_excursion_pct = ((max_price - initial_ask) / initial_ask) * 100

            # Classify trades as buy/sell using tick rule
            # If price > previous price = buy, if price < previous = sell
            buy_volume = 0
            sell_volume = 0
            prev_price = initial_ask  # Start with initial ask as reference

            for trade in sorted(window_trades, key=lambda t: t.get("timestamp", datetime.min)):
                price = trade.get("price", 0)
                size = trade.get("size", 0)
                if price > prev_price:
                    buy_volume += size
                elif price < prev_price:
                    sell_volume += size
                else:
                    # Price unchanged - split evenly
                    buy_volume += size // 2
                    sell_volume += size - (size // 2)
                prev_price = price

            # Calculate buying pressure
            buying_pressure = 0.0
            if total_volume > 0:
                buying_pressure = buy_volume / total_volume

            # Check ALL criteria (using dynamic thresholds)
            vol_ok = total_volume >= volume_threshold
            trades_ok = trade_count >= trades_threshold
            price_ok = price_excursion_pct >= MIN_PRICE_PCT
            pressure_ok = buying_pressure >= MIN_BUY_PRESSURE

            logger.debug(
                f"LAST CHANCE check #{check_num + 1}/{num_checks}",
                ticker=ticker,
                volume=total_volume,
                trade_count=trade_count,
                price_excursion_pct=round(price_excursion_pct, 2),
                buying_pressure_pct=round(buying_pressure * 100, 1),
                vol_ok=vol_ok,
                trades_ok=trades_ok,
                price_ok=price_ok,
                pressure_ok=pressure_ok
            )

            if vol_ok and trades_ok and price_ok and pressure_ok:
                # Get current NBBO for entry price
                current_nbbo = await quote_fetcher.get_nbbo_snapshot(ticker) if quote_fetcher else None

                logger.info(
                    "🚀 LAST CHANCE SURGE FOUND: ALL criteria met!",
                    ticker=ticker,
                    article_id=article_id,
                    check_number=check_num + 1,
                    seconds_elapsed=round((check_num + 1) * LAST_CHANCE_POLL_INTERVAL, 1),
                    volume=total_volume,
                    trade_count=trade_count,
                    price_excursion_pct=round(price_excursion_pct, 2),
                    buying_pressure_pct=round(buying_pressure * 100, 1),
                    surge_ask=current_nbbo.get("ask") if current_nbbo else None,
                    surge_bid=current_nbbo.get("bid") if current_nbbo else None
                )

                # Convert to imbalance_ratio format for compatibility (-1 to 1 scale)
                imbalance_ratio = (buying_pressure * 2) - 1

                return {
                    "surge_found": True,
                    "surge_nbbo": current_nbbo,
                    "surge_nbbo_mid": current_nbbo.get("mid") if current_nbbo else None,
                    "check_number": check_num + 1,
                    "seconds_elapsed": round((check_num + 1) * LAST_CHANCE_POLL_INTERVAL, 1),
                    "imbalance_ratio": imbalance_ratio,
                    "surge_multiplier": total_volume / 100 if total_volume > 0 else 0,  # Approximate
                    "trade_count_multiplier": trade_count,
                    "max_excursion_pct": price_excursion_pct,
                    "buying_pressure_pct": buying_pressure * 100,
                    "volume": total_volume,
                    "buy_volume": buy_volume,
                    "sell_volume": sell_volume,
                }

            # Log progress for partially-met criteria
            criteria_met = sum([vol_ok, trades_ok, price_ok, pressure_ok])
            if criteria_met >= 2:
                logger.debug(
                    f"LAST CHANCE: {criteria_met}/4 criteria met",
                    ticker=ticker,
                    check_number=check_num + 1,
                )

        except Exception as e:
            logger.debug(f"Error in surge monitoring check #{check_num + 1}: {e}")
            continue

    logger.info(
        "⏭️ LAST CHANCE: No qualifying surge in 8-second window",
        ticker=ticker,
        article_id=article_id,
        checks_performed=num_checks,
        volume_threshold=int(volume_threshold),
        trades_threshold=int(trades_threshold),
        criteria=f"{int(volume_threshold)}+ vol, {int(trades_threshold)}+ trades, 5% price, 80% buy pressure"
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
    - Volume surge (2000+ shares in 2s) → +1 point
    - Price excursion >1% → +1 point
    - Buying pressure >80% → +1 point

    Position sizing by score:
    - Score 0: MINIMUM → 8-second surge window (STANDARD $15 if fast, MINIMUM $10 if slow, else SKIP)
    - Score 1: STANDARD → $300
    - Score 2: HIGH → $500
    - Score 3: VERY_HIGH → $700

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
            metadata["initial_bid"] = initial_nbbo.get("bid")

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

                # ============================================================
                # CONFLUENCE WINDOW METRICS (0-2 seconds after publication)
                # All metrics prefixed with confluence_ for clear identification
                # ============================================================

                # Basic price/volume metrics
                first_price = trade_list[0].price
                first_trade_time = trade_list[0].timestamp
                max_price = max(t.price for t in trade_list)
                min_price = min(t.price for t in trade_list)
                total_volume = sum(t.size for t in trade_list)
                trade_count = len(trade_list)

                # VWAP calculation (volume-weighted average price)
                total_dollar_volume = sum(t.price * t.size for t in trade_list)
                vwap = total_dollar_volume / total_volume if total_volume > 0 else first_price

                # Average trade size
                avg_trade_size = total_volume / trade_count if trade_count > 0 else 0

                # First trade latency (ms from publication to first trade)
                first_trade_latency_ms = None
                if first_trade_time:
                    # Ensure both are timezone-aware for comparison
                    pub_time_utc = publication_time.replace(tzinfo=timezone.utc) if publication_time.tzinfo is None else publication_time
                    first_trade_utc = first_trade_time.replace(tzinfo=timezone.utc) if first_trade_time.tzinfo is None else first_trade_time
                    first_trade_latency_ms = (first_trade_utc - pub_time_utc).total_seconds() * 1000

                # Max trade gap (longest time between consecutive trades, in ms)
                max_trade_gap_ms = 0
                if trade_count > 1:
                    for i in range(1, trade_count):
                        gap = (trade_list[i].timestamp - trade_list[i-1].timestamp).total_seconds() * 1000
                        max_trade_gap_ms = max(max_trade_gap_ms, gap)

                # Max excursion from first trade
                max_move_up = (max_price - first_price) / first_price if first_price else 0
                max_move_down = (first_price - min_price) / first_price if first_price else 0
                max_move_pct = max(max_move_up, max_move_down)

                # Additional market physics for long-term statistical analysis
                last_price = trade_list[-1].price
                price_direction = 1 if last_price > first_price else (-1 if last_price < first_price else 0)
                dollar_volume = total_dollar_volume  # Already calculated for VWAP
                max_single_trade = max(t.size for t in trade_list)

                # Median trade size (requires sorting)
                trade_sizes = sorted([t.size for t in trade_list])
                median_idx = len(trade_sizes) // 2
                if len(trade_sizes) % 2 == 0:
                    median_trade_size = (trade_sizes[median_idx - 1] + trade_sizes[median_idx]) / 2
                else:
                    median_trade_size = trade_sizes[median_idx]

                # Large trade percentage (>= 500 shares = likely institutional)
                large_trade_volume = sum(t.size for t in trade_list if t.size >= 500)
                large_trade_pct = (large_trade_volume / total_volume * 100) if total_volume > 0 else 0

                # Uptick/downtick count
                uptick_count = 0
                downtick_count = 0
                prev_price = first_price
                for t in trade_list[1:]:
                    if t.price > prev_price:
                        uptick_count += 1
                    elif t.price < prev_price:
                        downtick_count += 1
                    prev_price = t.price

                # Store ALL confluence metrics with confluence_ prefix
                metadata["confluence_volume"] = total_volume
                metadata["confluence_trade_count"] = trade_count
                metadata["confluence_first_price"] = round(first_price, 4)
                metadata["confluence_max_price"] = round(max_price, 4)
                metadata["confluence_min_price"] = round(min_price, 4)
                metadata["confluence_vwap"] = round(vwap, 4)
                metadata["confluence_avg_trade_size"] = round(avg_trade_size, 1)
                metadata["confluence_first_trade_latency_ms"] = round(first_trade_latency_ms, 1) if first_trade_latency_ms else None
                metadata["confluence_max_trade_gap_ms"] = round(max_trade_gap_ms, 1)
                metadata["confluence_price_excursion_pct"] = round(max_move_pct * 100, 2)
                # Additional market physics for long-term analysis
                metadata["confluence_last_price"] = round(last_price, 4)
                metadata["confluence_price_direction"] = price_direction
                metadata["confluence_dollar_volume"] = round(dollar_volume, 2)
                metadata["confluence_max_single_trade"] = max_single_trade
                metadata["confluence_median_trade_size"] = round(median_trade_size, 1)
                metadata["confluence_large_trade_pct"] = round(large_trade_pct, 1)
                metadata["confluence_uptick_count"] = uptick_count
                metadata["confluence_downtick_count"] = downtick_count

                # Legacy fields (for backwards compatibility)
                metadata["price_excursion_pct"] = metadata["confluence_price_excursion_pct"]
                metadata["volume"] = total_volume
                metadata["trade_count"] = trade_count

                # ============================================================
                # CRITERION 1: Volume surge (2000+ shares in 2s)
                # ============================================================
                has_volume_surge = total_volume >= VOLUME_SURGE_THRESHOLD
                metadata["confluence_has_volume_surge"] = has_volume_surge
                metadata["volume_surge"] = has_volume_surge  # Legacy

                if has_volume_surge:
                    confluence_score += 1
                    logger.info(
                        f"📈 VOLUME SURGE: +1 point ({total_volume} shares in 2s)",
                        ticker=ticker,
                        volume=total_volume,
                        threshold=VOLUME_SURGE_THRESHOLD
                    )

                # ============================================================
                # CRITERION 2: Price excursion >1%
                # ============================================================
                has_price_excursion = max_move_pct >= PRICE_EXCURSION_THRESHOLD_PCT
                metadata["confluence_has_price_excursion"] = has_price_excursion
                metadata["has_price_excursion"] = has_price_excursion  # Legacy

                if has_price_excursion:
                    confluence_score += 1
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
                # Imbalance ratio: (buy - sell) / (buy + sell), range -1 to +1
                imbalance_ratio = (buy_volume - sell_volume) / (buy_volume + sell_volume) if (buy_volume + sell_volume) > 0 else 0

                # Store buying pressure metrics with confluence_ prefix
                metadata["confluence_buy_volume"] = int(buy_volume)
                metadata["confluence_sell_volume"] = int(sell_volume)
                metadata["confluence_buying_pressure_pct"] = round(buying_pressure * 100, 1)
                metadata["confluence_imbalance_ratio"] = round(imbalance_ratio, 3)

                # Legacy fields
                metadata["buying_pressure_pct"] = metadata["confluence_buying_pressure_pct"]
                metadata["buy_volume"] = int(buy_volume)
                metadata["sell_volume"] = int(sell_volume)

                has_buying_pressure = buying_pressure >= BUYING_PRESSURE_THRESHOLD
                metadata["confluence_has_buying_pressure"] = has_buying_pressure
                metadata["has_buying_pressure"] = has_buying_pressure  # Legacy

                if has_buying_pressure:
                    confluence_score += 1
                    logger.info(
                        f"📈 BUYING PRESSURE: +1 point ({buying_pressure*100:.1f}% buy-sided)",
                        ticker=ticker,
                        buying_pressure=f"{buying_pressure*100:.1f}%",
                        buy_volume=int(buy_volume),
                        sell_volume=int(sell_volume),
                        imbalance_ratio=round(imbalance_ratio, 3)
                    )

                # Store spread metrics with confluence_ prefix
                metadata["confluence_initial_spread"] = metadata.get("initial_spread")
                metadata["confluence_final_spread"] = metadata.get("final_spread")
                metadata["confluence_spread_compression_pct"] = metadata.get("spread_compression_pct")

                # ============================================================
                # BUILD STRUCTURED CONFLUENCE WINDOW (8 x 250ms sub-slices)
                # For ML feature extraction and micro-trajectory analysis
                # ============================================================
                try:
                    from ...shared.statistics.slice_analyzer import build_confluence_window

                    # Convert Alpaca trades to dict format for analyzer
                    trades_for_analysis = [
                        {
                            "timestamp": t.timestamp,
                            "price": t.price,
                            "size": t.size
                        }
                        for t in trade_list
                    ]

                    # Build initial and final NBBO dicts
                    initial_nbbo_dict = metadata.get("initial_nbbo") or {
                        "bid": metadata.get("initial_bid"),
                        "ask": metadata.get("initial_ask"),
                        "spread": metadata.get("initial_spread"),
                    }
                    final_nbbo_dict = {
                        "bid": metadata.get("final_bid"),
                        "ask": metadata.get("final_ask"),
                        "spread": metadata.get("final_spread"),
                    }

                    # TODO: Fetch baseline stats (5s before news) for ratio calculation
                    # For now, leave as None - will be added when we have pre-news monitoring

                    confluence_window = build_confluence_window(
                        trades=trades_for_analysis,
                        window_start=publication_time,
                        initial_nbbo=initial_nbbo_dict,
                        final_nbbo=final_nbbo_dict,
                    )

                    # Store as dict for JSON serialization
                    metadata["confluence_window"] = confluence_window.model_dump()

                    # Log key ML features
                    logger.debug(
                        "Confluence window built with sub-slices",
                        ticker=ticker,
                        slices=len(confluence_window.slices),
                        pressure_first_half=confluence_window.pressure_first_half,
                        pressure_second_half=confluence_window.pressure_second_half,
                        pressure_consistent=confluence_window.pressure_consistent,
                        volume_in_first_500ms=confluence_window.volume_in_first_500ms,
                    )

                except Exception as e:
                    logger.warning(f"Failed to build confluence window: {e}")

        # ============================================================
        # STEP 4: Determine conviction level from confluence score
        # Score 0 = MINIMUM (surge window $200), 1 = STANDARD ($300), 2 = HIGH ($500), 3 = VERY_HIGH ($700)
        # ============================================================
        metadata["confluence_score"] = confluence_score

        # Flag that surge window will be triggered if score is 0
        metadata["surge_triggered"] = (confluence_score == 0)

        if confluence_score >= 3:
            conviction = ConvictionLevel.VERY_HIGH
            logger.info(
                f"🔥🔥 VERY HIGH CONVICTION (score {confluence_score}): All 3 criteria met → $700 position",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                volume_surge=metadata.get("volume_surge"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                buying_pressure_pct=metadata.get("buying_pressure_pct")
            )
        elif confluence_score == 2:
            conviction = ConvictionLevel.HIGH
            logger.info(
                f"🔥 HIGH CONVICTION (score {confluence_score}): 2 criteria met → $500 position",
                ticker=ticker,
                position_size=f"${POSITION_SIZES_USD[conviction]}",
                volume_surge=metadata.get("volume_surge"),
                price_excursion_pct=metadata.get("price_excursion_pct"),
                buying_pressure_pct=metadata.get("buying_pressure_pct")
            )
        elif confluence_score == 1:
            conviction = ConvictionLevel.STANDARD
            logger.info(
                f"📊 STANDARD CONVICTION (score {confluence_score}): 1 criterion met → $300 position",
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
    - MINIMUM: $200 (surge trade)
    - STANDARD: $300 (1 criterion met)
    - HIGH: $500 (2 criteria met)
    - VERY_HIGH: $700 (all 3 criteria met)

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
            shares=float(shares),  # Explicit shares calculated from position size
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
    # Article metadata from event to avoid storage fetch delay
    event_tickers: Optional[list] = None,
    event_title: Optional[str] = None,
    event_published_at: Optional[datetime] = None,
    # AI-determined position size for immediate entry
    event_position_size: Optional[str] = None,
) -> None:
    """
    Process an IMMINENT classification result and publish trade request if valid.

    Pure function that orchestrates the auto-trade workflow.

    IMPORTANT: Trades are executed IMMEDIATELY on AI classification.
    Position size is determined by the AI based on headline quality.
    Confluence metrics are still collected for research but do NOT gate trades.

    Args:
        event_bus: Event bus instance for publishing events
        storage_service: Storage query service (kept for backward compatibility, now unused)
        classification_result: Classification result to process
        enabled: Whether auto-trading is enabled
        market_data_client: Optional Alpaca market data client
        quote_fetcher: Optional AlpacaQuoteFetcher for NBBO snapshots
        event_tickers: Tickers from the classification event (avoids storage delay)
        event_title: Title from the classification event (for logging)
        event_published_at: Published_at from the classification event (for stats collection)
        event_position_size: AI-determined position size (SMALL, MODERATE, LARGE, MAX)
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
            await _record_postfilter_skip(article_id, "postfilter_active_position")
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
            await _record_postfilter_skip(article_id, "postfilter_cooldown")
            return

        # ============================================================
        # 🚀 AI-BASED POSITION SIZING: Immediate entry on classification
        # ============================================================
        # Position size determined by AI based on headline quality.
        # NO 2-second confluence delay - trades execute immediately.
        # Rationale: Headlines are harder to manipulate than microstructure.
        # The "gap and trap" pattern exploits confluence scoring, not headline quality.

        # Map AI position size to conviction level
        ai_position_size = event_position_size or "MODERATE"  # Default to MODERATE if not specified
        conviction = AI_SIZE_TO_CONVICTION.get(ai_position_size.upper(), ConvictionLevel.STANDARD)

        logger.info(
            f"🚀 AI POSITION SIZE: {ai_position_size} → {conviction.value}",
            ticker=ticker,
            ai_position_size=ai_position_size,
            conviction=conviction.value,
            position_size=f"${POSITION_SIZES_USD[conviction]}",
            article_id=article_id,
        )

        # Initialize confluence_metadata for stats collection (non-blocking)
        # We still collect this data for research, but it doesn't gate trades
        confluence_metadata = {
            "confluence_checked": False,
            "ai_position_size": ai_position_size,
            "initial_ask": None,
            "initial_bid": None,
            "initial_spread": None,
        }

        # Get initial NBBO immediately (for stats and chase filter)
        if quote_fetcher:
            try:
                initial_nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
                if initial_nbbo:
                    confluence_metadata["initial_nbbo_mid"] = initial_nbbo.get("mid")
                    confluence_metadata["initial_spread"] = initial_nbbo.get("spread")
                    confluence_metadata["initial_ask"] = initial_nbbo.get("ask")
                    confluence_metadata["initial_bid"] = initial_nbbo.get("bid")
            except Exception as e:
                logger.debug(f"Could not get initial NBBO: {e}")

        # ============================================================
        # 🛡️ SAFETY FILTERS: Market cap and biotech price checks
        # ============================================================
        # These filters prevent trading manipulated or low-quality stocks.

        # Filter 1: Market cap check (minimum $2M to avoid manipulated stocks)
        MIN_MARKET_CAP_MILLIONS = 2.0  # $2M minimum
        MIN_BIOTECH_PRICE = 30.0  # Biotechs must be $30+ (data shows sub-$30 biotechs have poor risk/reward)
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
                        await _record_postfilter_skip(article_id, f"postfilter_market_cap:{market_cap_millions:.1f}M")
                        return

                    # Filter 1b: Biotech price filter - only trade $30+ biotechs
                    # Data shows: $30+ biotechs move 100-300%, sub-$5 biotechs only 5-18%
                    # Sub-$30 biotechs have weak catalysts (IND, early phase, offerings) and high failure rate
                    industry = ticker_metadata.get("industry", "")
                    ticker_price = ticker_metadata.get("price", 0)
                    if industry == "Biotechnology" and ticker_price < MIN_BIOTECH_PRICE:
                        logger.info(
                            "⏭️ AUTO-TRADE SKIPPED: Biotech below $30 price threshold",
                            ticker=ticker,
                            industry=industry,
                            price=round(ticker_price, 2),
                            min_biotech_price=MIN_BIOTECH_PRICE,
                            article_id=article_id,
                            reason="Only trade quality biotechs ($30+) with real drugs and revenue"
                        )
                        await _record_postfilter_skip(article_id, f"postfilter_biotech_price:${ticker_price:.0f}")
                        return
            except Exception as e:
                logger.debug(f"Could not check market cap/biotech filter: {e}")

        # ============================================================
        # 📊 SPREAD FILTER: Reject wide spreads (>10% of mid)
        # ============================================================
        # Wide spreads cause massive slippage and are manipulation targets.
        # IINN lesson: 35% spread = untradeable.
        # IINN lesson: 35% spread = untradeable. This should never happen.
        # Any spread > 10% of mid price is a hard skip regardless of other factors.
        MAX_SPREAD_PCT = 10.0  # Absolute maximum spread as % of mid price
        initial_spread = confluence_metadata.get("initial_spread")
        initial_ask = confluence_metadata.get("initial_ask")
        initial_bid = confluence_metadata.get("initial_bid", 0)

        if initial_spread and initial_ask:
            # Calculate mid price (use bid if available, otherwise estimate from ask and spread)
            if initial_bid and initial_bid > 0:
                initial_mid = (initial_ask + initial_bid) / 2
            else:
                initial_mid = initial_ask - (initial_spread / 2)

            if initial_mid > 0:
                spread_pct_of_mid = (initial_spread / initial_mid) * 100

                # HARD STOP: Spread > 10% = untradeable, period.
                if spread_pct_of_mid > MAX_SPREAD_PCT:
                    logger.info(
                        "⏭️ AUTO-TRADE SKIPPED: Spread too wide (>10% of mid) - untradeable",
                        ticker=ticker,
                        spread_pct_of_mid=round(spread_pct_of_mid, 2),
                        max_allowed=MAX_SPREAD_PCT,
                        initial_spread=initial_spread,
                        initial_bid=initial_bid,
                        initial_ask=initial_ask,
                        initial_mid=round(initial_mid, 4),
                        article_id=article_id,
                        reason="Wide spreads cause massive slippage and are manipulation targets"
                    )
                    await _record_postfilter_skip(article_id, f"postfilter_spread_too_wide:{spread_pct_of_mid:.0f}%")
                    return

        logger.info(
            "✅ SAFETY FILTERS PASSED - Proceeding to trade",
            ticker=ticker,
            ai_position_size=ai_position_size,
            conviction=conviction.value,
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
                    await _record_postfilter_skip(article_id, "postfilter_zero_volume")
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
        # NOTE: AI-based flow doesn't use surge trading - trades execute immediately
        # on classification. The surge bypass logic is kept for future reference.
        MAX_CHASE_PCT = 7.0
        initial_ask = confluence_metadata.get("initial_ask")
        is_surge_trade = False  # AI-based flow doesn't use surge trading
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
                            await _record_postfilter_skip(article_id, f"postfilter_chase:{chase_pct:.0f}%")
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
            position_size=domain_event.position_size,
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
            # AI-determined position size for immediate entry
            event_position_size=domain_event.position_size,
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

