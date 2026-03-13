"""
Auto-trade service - subscribes to domain events and handles trading logic.

AI-BASED POSITION SIZING (Immediate entry on classification):
Regular path:
- SMALL: $1,000 position (weak headline, vague, unknown partner)
- MODERATE: $1,250 position (decent headline, some specificity)
- LARGE: $1,500 position (strong headline, specific details)
- MAX: $2,000 position (transformational headline, >50% of market cap deal)
High-conviction headline types (gov/military contracts, major commercial contracts):
- SMALL/MODERATE: $3,000 position
- LARGE: $4,000 position
- MAX: $5,000 position

The AI determines position size based on:
1. Headline concreteness (specific $ amounts, named parties, definitive terms)
2. Deal value relative to market cap (>50% = transformational)
3. Catalyst strength for the industry
4. Counterparty quality (Fortune 100, major pharma, etc.)

Confluence scoring is still collected for statistical research but does NOT gate trades.
Trades execute IMMEDIATELY on AI classification to capture the move before volume arrives.

Stop loss: 5% below actual entry price - caps max loss per trade.
Price movement filter: 3% max ask change per leg (pub→recv, recv→fill).

Pure functions for trade processing logic, with minimal service class for event subscriptions.
"""
from decimal import Decimal
from datetime import datetime, timezone, timedelta, date
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
from ...utils.async_alpaca import run_sync_alpaca_call
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
# HIGH-CONVICTION HEADLINE TYPES (bypass/relax postfilters)
# ============================================================
# Government/military contracts AND major commercial contracts with concrete
# deal specifics reliably produce sustained moves.
# Defense: GXAI +37%, MTEK +49%, MOBX +25%
# Commercial: RITR +35%, ASNS +17%, PLBY +17%, SWVL +11%
# All blocked by microstructure filters despite being highly profitable.
# When headline_type matches, most postfilters are SKIPPED — the edge is the headline signal:
#   circuit_breaker: SKIP (don't let unrelated losses block proven patterns)
#   initial_spread: 10% (matches prefilter, defense stocks have wider spreads)
#   selling_pressure: SKIP (trust the headline signal)
#   pub_to_recv: SKIP (legitimate market reaction, not front-running)
#   recv_to_fill: SKIP (fast price movement IS the signal)
#   fill_spread: 10% (spreads widen during fast moves but settle)
#   momentum_exhaustion: SKIP (these headlines sustain momentum)
#   late_entry: 25s (10s extra over normal, pump-and-dump filter protects)
#   pre_news_runup: 10% (some pre-positioning is normal)
# KEPT at normal thresholds: pump-and-dump (5.5% ask vs VWAP), pre-news runup (10%)
#
# These match the universal triage prompt types (prompts/headline_types/universal_triage.txt).
# Broad categories — the LLM handles specifics (Navy, Army, DARPA all → military_contract).
HIGH_CONVICTION_HEADLINE_TYPES = frozenset({
    "government_contract",   # Any government deal (federal, state, NASA, DARPA, agency awards)
    "military_contract",     # Any military/defense deal (Army, Navy, Air Force, weapons, drones, munitions)
    "defense_order",         # Orders from defense companies or for defense products
    "major_contract",        # Commercial contracts — micro-cap + material deal size = sustained moves
})

# Headline types that should NEVER be traded.
# Acquiring company = cash outflow, dilution risk, typically sells off.
# Only the acquisition TARGET (acquired company) should be traded.
BLOCKED_HEADLINE_TYPES = frozenset({
    "acquisition_announced",  # Company ACQUIRING another — cash outflow, stock usually drops
})

# AI breakthrough headlines get price-tiered spread leniency (only spread, nothing else).
# Cheap stocks with genuine AI breakthroughs have structurally wide spreads that thin rapidly.
AI_BREAKTHROUGH_HEADLINE_TYPES = frozenset({"ai_breakthrough"})


def _ai_breakthrough_spread_threshold(price: float) -> float:
    """Price-tiered spread threshold for AI breakthrough headlines."""
    if price < 0.30:
        return 10.0
    return 7.5


# ============================================================
# DUPLICATE POSITION & COOLDOWN PROTECTION
# ============================================================
# Track active positions and recently exited tickers to prevent:
# 1. Entering same ticker twice from different article IDs
# 2. Re-entering a ticker immediately after time-based exit

# Active tickers with open positions (set by composition_root on TradeExecuted)
_active_positions: set = set()

# Recently exited tickers with cooldown (ticker -> {exit_time, was_profitable})
_exited_tickers: dict = {}

# Dynamic cooldown: shorter for profitable exits, longer for losing exits
TICKER_COOLDOWN_PROFIT_MINUTES = 5    # Re-enter after 5 min if exited profitably
TICKER_COOLDOWN_LOSS_MINUTES = 30     # Wait 30 min after a loss

# ============================================================
# RECENTLY SKIPPED TICKER TRACKING (Risk Reduction)
# ============================================================
# If we skipped a headline for a ticker recently, subsequent headlines
# are higher risk (the first headline already moved the stock).
# Cap position size at $5k for these "second wave" entries.

_skipped_tickers: dict = {}  # ticker -> skip_time
SKIPPED_TICKER_WINDOW_MINUTES = 10  # Remember skips for 10 minutes
SKIPPED_TICKER_MAX_POSITION_USD = 5000  # Cap at $5k for recently skipped tickers

# Housekeeping: last time we swept stale entries from module-level dicts
_last_cleanup_time: Optional[datetime] = None
_CLEANUP_INTERVAL_MINUTES = 30  # Sweep every 30 minutes

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
            logger.warning(f"Failed to record postfilter skip: {e}", article_id=article_id, reason=reason)
    else:
        logger.warning("No recall engine set — postfilter reason lost", article_id=article_id, reason=reason)


# ============================================================
# DAILY P&L CIRCUIT BREAKER
# ============================================================
# Shuts down trading for the day if losses exceed threshold.
# Protects against systematic failures (bad data, broken filters).
# Threshold should be scaled with position sizes.

DAILY_MAX_LOSS_USD = 5.00  # Shut down after $5 loss (scale up as positions increase)
DAILY_MAX_LOSS_ENABLED = False  # Disabled — will re-enable with appropriate threshold later

_daily_pnl_usd: float = 0.0  # Running P&L for the day
_daily_pnl_trades: list = []  # List of (ticker, pnl) for logging
_daily_pnl_date: Optional[date] = None  # Date of current tracking period
_circuit_breaker_triggered: bool = False  # True if we've hit the limit
_circuit_breaker_trigger_time: Optional[datetime] = None


def _reset_daily_pnl_if_new_day() -> None:
    """Reset daily P&L tracking if it's a new trading day."""
    global _daily_pnl_usd, _daily_pnl_trades, _daily_pnl_date, _circuit_breaker_triggered, _circuit_breaker_trigger_time

    today = date.today()
    if _daily_pnl_date != today:
        if _daily_pnl_date is not None:
            logger.info(
                f"📊 DAILY P&L RESET: New trading day",
                previous_date=_daily_pnl_date.isoformat() if _daily_pnl_date else None,
                previous_pnl=f"${_daily_pnl_usd:.2f}",
                previous_trades=len(_daily_pnl_trades),
                circuit_breaker_was_triggered=_circuit_breaker_triggered,
            )
        _daily_pnl_usd = 0.0
        _daily_pnl_trades = []
        _daily_pnl_date = today
        _circuit_breaker_triggered = False
        _circuit_breaker_trigger_time = None


def record_trade_pnl(ticker: str, pnl_usd: float) -> None:
    """
    Record P&L from a closed trade.

    Called when a trade exits (from position_manager or exit handler).
    Checks if circuit breaker should be triggered.
    """
    global _daily_pnl_usd, _daily_pnl_trades, _circuit_breaker_triggered, _circuit_breaker_trigger_time

    _reset_daily_pnl_if_new_day()

    _daily_pnl_usd += pnl_usd
    _daily_pnl_trades.append((ticker, pnl_usd, datetime.now()))

    logger.info(
        f"📊 DAILY P&L UPDATE: {'+' if pnl_usd >= 0 else ''}{pnl_usd:.2f}",
        ticker=ticker,
        trade_pnl=f"${pnl_usd:.2f}",
        daily_pnl=f"${_daily_pnl_usd:.2f}",
        daily_trades=len(_daily_pnl_trades),
        max_loss_threshold=f"-${DAILY_MAX_LOSS_USD:.2f}",
    )

    # Check circuit breaker
    if DAILY_MAX_LOSS_ENABLED and not _circuit_breaker_triggered:
        if _daily_pnl_usd <= -DAILY_MAX_LOSS_USD:
            _circuit_breaker_triggered = True
            _circuit_breaker_trigger_time = datetime.now()

            # Build trade history for logging
            trade_history = "\n".join([
                f"  {i+1}. {t[0]}: ${t[1]:+.2f} at {t[2].strftime('%H:%M:%S')}"
                for i, t in enumerate(_daily_pnl_trades)
            ])

            logger.error(
                f"🚨🚨🚨 CIRCUIT BREAKER TRIGGERED: Daily loss exceeded ${DAILY_MAX_LOSS_USD:.2f} 🚨🚨🚨\n"
                f"═══════════════════════════════════════════════════════════════\n"
                f"SYSTEM SHUTDOWN - NO NEW TRADES WILL BE EXECUTED\n"
                f"═══════════════════════════════════════════════════════════════\n"
                f"Daily P&L: ${_daily_pnl_usd:.2f}\n"
                f"Threshold: -${DAILY_MAX_LOSS_USD:.2f}\n"
                f"Trades today: {len(_daily_pnl_trades)}\n"
                f"Trade history:\n{trade_history}\n"
                f"═══════════════════════════════════════════════════════════════\n"
                f"To resume trading:\n"
                f"  1. Restart the system (resets for new day), OR\n"
                f"  2. Increase DAILY_MAX_LOSS_USD threshold, OR\n"
                f"  3. Set DAILY_MAX_LOSS_ENABLED = False\n"
                f"═══════════════════════════════════════════════════════════════",
                daily_pnl=f"${_daily_pnl_usd:.2f}",
                threshold=f"-${DAILY_MAX_LOSS_USD:.2f}",
                trades_today=len(_daily_pnl_trades),
            )


def is_circuit_breaker_triggered() -> bool:
    """Check if the daily loss circuit breaker has been triggered."""
    _reset_daily_pnl_if_new_day()
    return _circuit_breaker_triggered


def get_daily_pnl_status() -> dict:
    """Get current daily P&L status for monitoring/display."""
    _reset_daily_pnl_if_new_day()
    return {
        "daily_pnl_usd": _daily_pnl_usd,
        "daily_trades": len(_daily_pnl_trades),
        "max_loss_threshold": DAILY_MAX_LOSS_USD,
        "circuit_breaker_enabled": DAILY_MAX_LOSS_ENABLED,
        "circuit_breaker_triggered": _circuit_breaker_triggered,
        "circuit_breaker_trigger_time": _circuit_breaker_trigger_time.isoformat() if _circuit_breaker_trigger_time else None,
        "remaining_before_shutoff": max(0, DAILY_MAX_LOSS_USD + _daily_pnl_usd) if not _circuit_breaker_triggered else 0,
    }


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


def unregister_active_position(ticker: str, was_profitable: bool = False) -> None:
    """
    Unregister a ticker when position is closed.

    Dynamic cooldown:
    - Profitable exit: 5 min cooldown (shorter, allow catching second wave)
    - Loss exit: 30 min cooldown (longer, avoid re-entering bad setups)
    """
    ticker_upper = ticker.upper()
    _active_positions.discard(ticker_upper)
    # Add to cooldown tracking with profit info
    _exited_tickers[ticker_upper] = {
        "exit_time": datetime.now(timezone.utc),
        "was_profitable": was_profitable
    }
    cooldown_minutes = TICKER_COOLDOWN_PROFIT_MINUTES if was_profitable else TICKER_COOLDOWN_LOSS_MINUTES
    logger.info(
        f"Position unregistered, dynamic cooldown started: {ticker_upper}",
        active_count=len(_active_positions),
        was_profitable=was_profitable,
        cooldown_minutes=cooldown_minutes
    )


def has_active_position(ticker: str) -> bool:
    """Check if we already have an active position in this ticker."""
    return ticker.upper() in _active_positions


def is_ticker_in_cooldown(ticker: str) -> bool:
    """Check if ticker is in cooldown period after recent exit."""
    ticker_upper = ticker.upper()
    if ticker_upper not in _exited_tickers:
        return False

    exit_data = _exited_tickers[ticker_upper]
    exit_time = exit_data["exit_time"]
    was_profitable = exit_data.get("was_profitable", False)

    # Dynamic cooldown based on exit outcome
    cooldown_minutes = TICKER_COOLDOWN_PROFIT_MINUTES if was_profitable else TICKER_COOLDOWN_LOSS_MINUTES
    cooldown_end = exit_time + timedelta(minutes=cooldown_minutes)

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

    exit_data = _exited_tickers[ticker_upper]
    exit_time = exit_data["exit_time"]
    was_profitable = exit_data.get("was_profitable", False)

    # Dynamic cooldown based on exit outcome
    cooldown_minutes = TICKER_COOLDOWN_PROFIT_MINUTES if was_profitable else TICKER_COOLDOWN_LOSS_MINUTES
    cooldown_end = exit_time + timedelta(minutes=cooldown_minutes)
    remaining = (cooldown_end - datetime.now(timezone.utc)).total_seconds() / 60

    return max(0, remaining)


def cleanup_stale_tracking() -> None:
    """
    Sweep stale entries from module-level tracking dicts.

    Prevents unbounded memory growth from tickers that are seen once
    and never re-checked (lazy cleanup only fires on re-access).
    Called periodically — gated by _CLEANUP_INTERVAL_MINUTES.
    """
    global _last_cleanup_time

    now = datetime.now(timezone.utc)
    if _last_cleanup_time and (now - _last_cleanup_time) < timedelta(minutes=_CLEANUP_INTERVAL_MINUTES):
        return
    _last_cleanup_time = now

    # Sweep _exited_tickers: remove entries past their cooldown
    max_cooldown = timedelta(minutes=max(TICKER_COOLDOWN_PROFIT_MINUTES, TICKER_COOLDOWN_LOSS_MINUTES))
    stale_exited = [t for t, data in _exited_tickers.items()
                    if now - data["exit_time"] > max_cooldown]
    for t in stale_exited:
        del _exited_tickers[t]

    # Sweep _skipped_tickers: remove entries past their window
    skip_window = timedelta(minutes=SKIPPED_TICKER_WINDOW_MINUTES)
    stale_skipped = [t for t, skip_time in _skipped_tickers.items()
                     if now - skip_time > skip_window]
    for t in stale_skipped:
        del _skipped_tickers[t]

    # Sweep _daily_pnl_trades: only keep today's trades
    today = date.today()
    global _daily_pnl_trades
    if _daily_pnl_date and _daily_pnl_date != today:
        _daily_pnl_trades = []

    if stale_exited or stale_skipped:
        logger.info(
            "Tracking cleanup sweep",
            evicted_exited=len(stale_exited),
            evicted_skipped=len(stale_skipped),
            remaining_exited=len(_exited_tickers),
            remaining_skipped=len(_skipped_tickers),
            active_positions=len(_active_positions),
        )


# ============================================================
# POSITION SIZING - AI-BASED (TESTING MODE - 10x REDUCED)
# ============================================================
# Position size determined by AI based on headline quality, deal size vs market cap,
# catalyst strength, and counterparty quality.
# REDUCED 10x from normal sizes while testing/validating filters
# Paper shadow trades use 50x these amounts for meaningful stats
POSITION_SIZES_USD = {
    ConvictionLevel.MINIMUM: Decimal("1000.00"),      # AI: SMALL - $1,000
    ConvictionLevel.STANDARD: Decimal("1250.00"),     # AI: MODERATE - $1,250
    ConvictionLevel.HIGH: Decimal("1500.00"),         # AI: LARGE - $1,500
    ConvictionLevel.VERY_HIGH: Decimal("2000.00"),    # AI: MAX - $2,000
}

# High-conviction headline types (gov/military contracts, major commercial contracts):
# Statistically proven edge — sized up accordingly.
HC_POSITION_SIZES_USD = {
    ConvictionLevel.MINIMUM: Decimal("3000.00"),      # AI: SMALL → overridden to MODERATE ($3,000)
    ConvictionLevel.STANDARD: Decimal("3000.00"),     # AI: MODERATE - $3,000
    ConvictionLevel.HIGH: Decimal("4000.00"),         # AI: LARGE - $4,000
    ConvictionLevel.VERY_HIGH: Decimal("5000.00"),    # AI: MAX - $5,000
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

# ============================================================
# IMPROVED CONFLUENCE CRITERIA (v2 - scales with price)
# ============================================================
# Old criteria were naive (2000 shares doesn't scale with stock price).
# New criteria use dollar volume and trade count for robustness.
#
# FULL CONFLUENCE (4-5 criteria): 1.0x multiplier
# PARTIAL CONFLUENCE (2-3 criteria): 0.75x multiplier
# NO VOLUME (0-1 criteria): 0.5x multiplier (early entry, await confirmation)
# NEGATIVE (selling pressure): SKIP

# Criterion 1: Dollar volume (scales with stock price)
DOLLAR_VOLUME_THRESHOLD = 2500        # $2,500 in 2s = meaningful activity

# Criterion 2: Trade count (activity indicator)
TRADE_COUNT_THRESHOLD = 5             # 5+ trades = real activity, not one big order

# Criterion 3: Imbalance ratio (buy pressure)
IMBALANCE_RATIO_THRESHOLD = 0.5       # ≥0.5 means ≥60% buy-sided (more lenient than 80%)

# Criterion 4: Price excursion from recv_ask (what we'd pay)
PRICE_EXCURSION_THRESHOLD_PCT = 0.01  # 1% move from reception ask

# Criterion 5: First trade latency (fast reaction = informed traders)
FIRST_TRADE_LATENCY_THRESHOLD_MS = 1500  # First trade within 1.5s of publication

# Legacy thresholds (kept for compatibility)
VOLUME_SURGE_THRESHOLD = 2000         # Legacy: 2000 shares
BUYING_PRESSURE_THRESHOLD = 0.80      # Legacy: 80% buying pressure

# Microstructure multipliers applied to AI base size
CONFLUENCE_MULTIPLIERS = {
    "full": 1.0,       # 4-5 criteria met → full AI size
    "partial": 0.75,   # 2-3 criteria met → 75% of AI size
    "no_volume": 0.5,  # 0-1 criteria met → 50% (await confirmation)
}

# ============================================================
# CONFLUENCE METRICS (For statistical analysis only - NOT used for gating)
# ============================================================
# These thresholds are used to collect microstructure data for research.
# Trades are NO LONGER gated on confluence - AI determines entry immediately.
# Data shows: headlines are harder to manipulate than microstructure (gap & trap).
LAST_CHANCE_WINDOW_SECONDS = 8        # Total window for surge monitoring
LAST_CHANCE_POLL_INTERVAL = 0.1       # Check every 100ms (WebSocket data is instant)
SURGE_PRICE_ACTION_PCT = 2.0          # 2% POSITIVE price move required for surge (consistent with 5% runup filter)
SURGE_BUYING_PRESSURE = 0.80          # 80% buying pressure required for surge
SURGE_VOLUME_MULTIPLIER = 3.0         # Volume must be 3x prior 10min average
SURGE_TRADE_COUNT_MULTIPLIER = 3.0    # Trade count must be 3x prior 10min average
MIN_ABSOLUTE_VOLUME = 2000            # Absolute minimum volume (even if prior is 0)
MIN_ABSOLUTE_TRADES = 20              # Absolute minimum trades (even if prior is 0)

# Late entry monitoring: extended window after initial STRENGTH/SURGE checks fail
LATE_ENTRY_MAX_SECONDS = 90.0         # Max seconds from publication for late entry
LATE_ENTRY_POLL_INTERVAL = 1.0        # Check every 1s (WebSocket data is instant)
LATE_STRENGTH_MIN_TRADES = 5          # Min trades to confirm real activity


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


async def monitor_for_late_entry(
    quote_fetcher,
    ticker: str,
    publication_time: datetime,
    initial_ask_at_publication: Optional[float],
    article_id: str,
) -> Optional[dict]:
    """
    Monitor for late STRENGTH or SURGE up to 30 seconds from publication.

    Called when both the 2-second STRENGTH check and 8-second SURGE check fail.
    Polls WebSocket trade data every 1 second, looking for either:
    - STRENGTH: price excursion >= 0.5% AND trade count >= 5
    - SURGE: all 4 criteria (volume, trades, price >= 2%, pressure >= 80%)

    Args:
        quote_fetcher: Quote fetcher with stream_manager for WebSocket data
        ticker: Stock ticker to monitor
        publication_time: When the article was published
        initial_ask_at_publication: Ask price at publication time
        article_id: Article ID for logging

    Returns:
        Dict with late entry metadata if signal found, None otherwise
    """
    import asyncio

    stream_manager = getattr(quote_fetcher, 'stream_manager', None) if quote_fetcher else None
    if not stream_manager:
        return None

    initial_ask = initial_ask_at_publication
    if not initial_ask:
        return None

    pub_time_utc = publication_time
    if pub_time_utc.tzinfo is None:
        pub_time_utc = pub_time_utc.replace(tzinfo=timezone.utc)

    # Calculate how much time remains until 30s from publication
    now_utc = datetime.now(timezone.utc)
    elapsed = (now_utc - pub_time_utc).total_seconds()
    remaining = LATE_ENTRY_MAX_SECONDS - elapsed

    if remaining <= 0:
        return None

    num_checks = int(remaining / LATE_ENTRY_POLL_INTERVAL)
    if num_checks <= 0:
        return None

    logger.info(
        "🔍 LATE ENTRY: Starting extended monitoring",
        ticker=ticker,
        article_id=article_id,
        seconds_elapsed=round(elapsed, 1),
        remaining_seconds=round(remaining, 1),
        num_checks=num_checks,
    )

    for check_num in range(num_checks):
        try:
            await asyncio.sleep(LATE_ENTRY_POLL_INTERVAL)

            trades = await stream_manager.get_recent_trades(ticker, max_trades=1000)
            if not trades:
                continue

            # Filter trades to those after publication time
            window_trades = []
            for trade in trades:
                trade_ts = trade.get("timestamp")
                if trade_ts:
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
                continue

            # Calculate metrics
            total_volume = sum(t.get("size", 0) for t in window_trades)
            trade_count = len(window_trades)
            max_price = max((t.get("price", 0) for t in window_trades), default=0)

            price_excursion_pct = 0.0
            if initial_ask and max_price > 0:
                price_excursion_pct = ((max_price - initial_ask) / initial_ask) * 100

            # Classify trades as buy/sell using tick rule
            buy_volume = 0
            sell_volume = 0
            prev_price = initial_ask

            for trade in sorted(window_trades, key=lambda t: t.get("timestamp", datetime.min)):
                price = trade.get("price", 0)
                size = trade.get("size", 0)
                if price > prev_price:
                    buy_volume += size
                elif price < prev_price:
                    sell_volume += size
                else:
                    buy_volume += size // 2
                    sell_volume += size - (size // 2)
                prev_price = price

            buying_pressure = buy_volume / total_volume if total_volume > 0 else 0.0

            seconds_elapsed = (datetime.now(timezone.utc) - pub_time_utc).total_seconds()

            # Check STRENGTH: excursion >= 0.5% AND trades >= 5
            has_strength = (
                price_excursion_pct >= 0.5
                and trade_count >= LATE_STRENGTH_MIN_TRADES
            )

            # Check SURGE: all 4 criteria
            has_surge = (
                total_volume >= MIN_ABSOLUTE_VOLUME
                and trade_count >= MIN_ABSOLUTE_TRADES
                and price_excursion_pct >= SURGE_PRICE_ACTION_PCT
                and buying_pressure >= SURGE_BUYING_PRESSURE
            )

            if has_strength or has_surge:
                entry_type = "late_surge" if has_surge else "late_strength"
                current_nbbo = await quote_fetcher.get_nbbo_snapshot(ticker) if quote_fetcher else None

                logger.info(
                    f"🚀 LATE ENTRY FOUND: {entry_type.upper()} at {seconds_elapsed:.1f}s",
                    ticker=ticker,
                    article_id=article_id,
                    entry_type=entry_type,
                    seconds_elapsed=round(seconds_elapsed, 1),
                    check_number=check_num + 1,
                    volume=total_volume,
                    trade_count=trade_count,
                    price_excursion_pct=round(price_excursion_pct, 2),
                    buying_pressure_pct=round(buying_pressure * 100, 1),
                )

                imbalance_ratio = (buying_pressure * 2) - 1

                return {
                    "late_entry_type": entry_type,
                    "late_entry_seconds_elapsed": round(seconds_elapsed, 1),
                    "late_entry_check_number": check_num + 1,
                    "late_entry_volume": total_volume,
                    "late_entry_trade_count": trade_count,
                    "late_entry_price_excursion_pct": round(price_excursion_pct, 2),
                    "late_entry_buying_pressure_pct": round(buying_pressure * 100, 1),
                    "late_entry_imbalance_ratio": round(imbalance_ratio, 3),
                    "late_entry_buy_volume": buy_volume,
                    "late_entry_sell_volume": sell_volume,
                    "surge_nbbo": current_nbbo,
                    "surge_nbbo_mid": current_nbbo.get("mid") if current_nbbo else None,
                }

            # Log progress periodically
            if check_num % 5 == 4:
                logger.debug(
                    f"LATE ENTRY check #{check_num + 1}: no signal yet",
                    ticker=ticker,
                    volume=total_volume,
                    trade_count=trade_count,
                    price_excursion_pct=round(price_excursion_pct, 2),
                    seconds_elapsed=round(seconds_elapsed, 1),
                )

        except Exception as e:
            logger.debug(f"Error in late entry check #{check_num + 1}: {e}")
            continue

    logger.info(
        "⏭️ LATE ENTRY: No signal in extended window",
        ticker=ticker,
        article_id=article_id,
        total_seconds=round(LATE_ENTRY_MAX_SECONDS, 0),
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
            # Order book depth at decision time (for liquidity analysis)
            metadata["initial_bid_size"] = initial_nbbo.get("bid_size")
            metadata["initial_ask_size"] = initial_nbbo.get("ask_size")

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

        # Use async wrapper to avoid blocking event loop
        trades = await run_sync_alpaca_call(
            market_data_client.get_stock_trades,
            StockTradesRequest(
                symbol_or_symbols=ticker,
                start=trades_start,
                end=trades_end,
                feed=DataFeed.SIP
            )
        )

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
                # IMPROVED CONFLUENCE CRITERIA v2 (scales with price)
                # ============================================================
                # Classify each trade as buy/sell using tick rule first
                buy_volume = 0
                sell_volume = 0
                prev_price_for_tick = None

                for t in trade_list:
                    if prev_price_for_tick is not None:
                        if t.price > prev_price_for_tick:
                            buy_volume += t.size
                        elif t.price < prev_price_for_tick:
                            sell_volume += t.size
                        else:
                            buy_volume += t.size / 2
                            sell_volume += t.size / 2
                    else:
                        buy_volume += t.size
                    prev_price_for_tick = t.price

                buying_pressure = buy_volume / total_volume if total_volume > 0 else 0
                imbalance_ratio = (buy_volume - sell_volume) / (buy_volume + sell_volume) if (buy_volume + sell_volume) > 0 else 0

                # Store buying pressure metrics
                metadata["confluence_buy_volume"] = int(buy_volume)
                metadata["confluence_sell_volume"] = int(sell_volume)
                metadata["confluence_buying_pressure_pct"] = round(buying_pressure * 100, 1)
                metadata["confluence_imbalance_ratio"] = round(imbalance_ratio, 3)

                # ============================================================
                # VOLUME DISTRIBUTION ANALYSIS (Manipulation Detection)
                # ============================================================
                # Build trade classifications for distribution analysis
                trade_classifications = []
                prev_price_for_class = None
                for t in trade_list:
                    is_buy = None
                    if prev_price_for_class is not None:
                        if t.price > prev_price_for_class:
                            is_buy = True
                        elif t.price < prev_price_for_class:
                            is_buy = False
                        else:
                            is_buy = True  # Tie: assume buy (conservative for distribution detection)
                    else:
                        is_buy = True  # First trade: assume buy
                    trade_classifications.append({"size": t.size, "is_buy": is_buy})
                    prev_price_for_class = t.price

                # Analyze distribution
                if trade_classifications and total_volume > 0:
                    # Find largest trade
                    largest_trade = max(trade_classifications, key=lambda x: x["size"])
                    largest_size = largest_trade["size"]

                    # Single trade dominance percentage
                    single_trade_dominance_pct = round((largest_size / total_volume) * 100, 1)
                    metadata["single_trade_dominance_pct"] = single_trade_dominance_pct

                    # Analyze remaining trades (excluding largest)
                    remaining = [t for t in trade_classifications if t != largest_trade]
                    metadata["remaining_trade_count"] = len(remaining)

                    if remaining:
                        remaining_buy = sum(t["size"] for t in remaining if t["is_buy"])
                        remaining_sell = sum(t["size"] for t in remaining if not t["is_buy"])
                        remaining_total = remaining_buy + remaining_sell

                        if remaining_total > 0:
                            metadata["remaining_flow_imbalance"] = round(
                                (remaining_buy - remaining_sell) / remaining_total, 3
                            )
                            remaining_sell_count = sum(1 for t in remaining if not t["is_buy"])
                            metadata["remaining_sell_pct"] = round(
                                (remaining_sell_count / len(remaining)) * 100, 1
                            )
                        else:
                            metadata["remaining_flow_imbalance"] = 0.0
                            metadata["remaining_sell_pct"] = 0.0
                    else:
                        metadata["remaining_flow_imbalance"] = 0.0
                        metadata["remaining_sell_pct"] = 0.0

                    # Classification logic
                    remaining_sell_pct = metadata.get("remaining_sell_pct", 0)
                    remaining_trade_count = metadata.get("remaining_trade_count", 0)

                    if single_trade_dominance_pct >= 50.0:
                        metadata["volume_distribution_class"] = "SUSPICIOUS"
                    elif single_trade_dominance_pct >= 30.0 and remaining_sell_pct >= 50.0:
                        metadata["volume_distribution_class"] = "DISTRIBUTION"
                    elif single_trade_dominance_pct >= 30.0 and remaining_sell_pct < 40.0:
                        metadata["volume_distribution_class"] = "INSTITUTIONAL"
                    elif single_trade_dominance_pct < 30.0 and remaining_trade_count >= 5:
                        metadata["volume_distribution_class"] = "ORGANIC"
                    elif single_trade_dominance_pct < 30.0:
                        metadata["volume_distribution_class"] = "ORGANIC"
                    else:
                        metadata["volume_distribution_class"] = "INSTITUTIONAL"

                # ============================================================
                # CRITERION 1: Dollar volume (scales with stock price)
                # ============================================================
                has_dollar_volume = dollar_volume >= DOLLAR_VOLUME_THRESHOLD
                metadata["confluence_has_dollar_volume"] = has_dollar_volume

                if has_dollar_volume:
                    confluence_score += 1
                    logger.info(
                        f"📈 DOLLAR VOLUME: +1 point (${dollar_volume:,.0f} in 2s)",
                        ticker=ticker,
                        dollar_volume=f"${dollar_volume:,.0f}",
                        threshold=f"${DOLLAR_VOLUME_THRESHOLD:,}"
                    )

                # ============================================================
                # CRITERION 2: Trade count (activity indicator)
                # ============================================================
                has_trade_activity = trade_count >= TRADE_COUNT_THRESHOLD
                metadata["confluence_has_trade_activity"] = has_trade_activity

                if has_trade_activity:
                    confluence_score += 1
                    logger.info(
                        f"📈 TRADE ACTIVITY: +1 point ({trade_count} trades in 2s)",
                        ticker=ticker,
                        trade_count=trade_count,
                        threshold=TRADE_COUNT_THRESHOLD
                    )

                # ============================================================
                # CRITERION 3: Imbalance ratio (buying pressure)
                # ============================================================
                has_buy_pressure = imbalance_ratio >= IMBALANCE_RATIO_THRESHOLD
                metadata["confluence_has_buying_pressure"] = has_buy_pressure
                metadata["has_buying_pressure"] = has_buy_pressure  # Legacy

                if has_buy_pressure:
                    confluence_score += 1
                    logger.info(
                        f"📈 BUY PRESSURE: +1 point (imbalance {imbalance_ratio:.2f}, {buying_pressure*100:.0f}% buy)",
                        ticker=ticker,
                        imbalance_ratio=round(imbalance_ratio, 2),
                        buying_pressure_pct=round(buying_pressure * 100, 1)
                    )
                elif imbalance_ratio < -0.3:
                    # Negative signal: significant selling pressure
                    metadata["confluence_has_selling_pressure"] = True
                    logger.warning(
                        f"⚠️ SELLING PRESSURE: imbalance {imbalance_ratio:.2f} ({buying_pressure*100:.0f}% buy)",
                        ticker=ticker
                    )

                # ============================================================
                # CRITERION 4: Price excursion from recv_ask (what we'd pay)
                # ============================================================
                # Use excursion calculated from max_price vs first_price (conservative)
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
                # CRITERION 5: First trade latency (fast reaction = informed)
                # ============================================================
                has_fast_reaction = (first_trade_latency_ms is not None and
                                     first_trade_latency_ms <= FIRST_TRADE_LATENCY_THRESHOLD_MS)
                metadata["confluence_has_fast_reaction"] = has_fast_reaction

                if has_fast_reaction:
                    confluence_score += 1
                    logger.info(
                        f"📈 FAST REACTION: +1 point (first trade at {first_trade_latency_ms:.0f}ms)",
                        ticker=ticker,
                        first_trade_latency_ms=round(first_trade_latency_ms, 0),
                        threshold_ms=FIRST_TRADE_LATENCY_THRESHOLD_MS
                    )

                # ============================================================
                # DETERMINE CONFLUENCE LEVEL
                # ============================================================
                # 4-5 criteria = full, 2-3 = partial, 0-1 = no_volume
                if confluence_score >= 4:
                    confluence_level = "full"
                elif confluence_score >= 2:
                    confluence_level = "partial"
                else:
                    confluence_level = "no_volume"

                metadata["confluence_level"] = confluence_level
                metadata["confluence_multiplier"] = CONFLUENCE_MULTIPLIERS[confluence_level]

                # Legacy compatibility
                has_volume_surge = total_volume >= VOLUME_SURGE_THRESHOLD
                metadata["confluence_has_volume_surge"] = has_volume_surge
                metadata["volume_surge"] = has_volume_surge

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
    # Check circuit breaker FIRST - this is critical safety
    if is_circuit_breaker_triggered():
        logger.warning(
            f"🚨 AUTO-TRADE BLOCKED: Circuit breaker triggered (daily loss exceeded ${DAILY_MAX_LOSS_USD:.2f})",
            article_id=result.article_id,
            daily_pnl=f"${_daily_pnl_usd:.2f}",
            message="System shutdown due to daily loss limit. No new trades until tomorrow or manual reset."
        )
        return False

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
    confluence_multiplier: float = 1.0,
    is_high_conviction: bool = False,
) -> Optional[TradeRequest]:
    """
    Build a trade request from an article with conviction-based position sizing.

    Position sizes: AI_BASE × CONFLUENCE_MULTIPLIER

    AI Base (headline quality - your edge):
    - MAX: $4 base (transformational)
    - LARGE: $3 base (strong, specific)
    - MODERATE: $2 base (decent)
    - SMALL: $1 base (weak)

    Confluence Multiplier (microstructure confirmation):
    - full (4-5 criteria): 1.0x
    - partial (2-3 criteria): 0.75x
    - no_volume (0-1 criteria): 0.5x

    Args:
        article: Domain Article model
        current_price: Current ask price for buying
        ticker: Specific ticker to trade
        conviction: AI-determined conviction level
        confluence_multiplier: Microstructure confirmation multiplier (0.5-1.0)

    Returns:
        Domain TradeRequest model or None if invalid
    """
    import math
    import os

    # Use conviction-based position sizing with confluence multiplier
    size_dict = HC_POSITION_SIZES_USD if is_high_conviction else POSITION_SIZES_USD
    BASE_SIZE_USD = size_dict.get(conviction, size_dict[ConvictionLevel.STANDARD])
    TRADE_SIZE_USD = Decimal(str(float(BASE_SIZE_USD) * confluence_multiplier))

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
            f"💰 AUTO-TRADE: Building ${TRADE_SIZE_USD:.2f} trade (${BASE_SIZE_USD} base × {confluence_multiplier}x)",
            article_id=article.id,
            shares=shares,
            current_price=current_price,
            total_notional=float(shares * Decimal(str(current_price))),
            base_size_usd=float(BASE_SIZE_USD),
            confluence_multiplier=confluence_multiplier,
            final_size_usd=float(TRADE_SIZE_USD),
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

        # Scale-in tracking for no_volume entries (0.5x initial position)
        # If we entered at half size, track full target for scale-in on confirmation
        confluence_level = confluence_metadata.get("confluence_level", "full")
        confluence_multiplier = confluence_metadata.get("confluence_multiplier", 1.0)
        if confluence_level == "no_volume" and confluence_multiplier < 1.0:
            # Calculate full shares (without the 0.5x multiplier)
            current_shares = trade_request.shares if trade_request.shares else 0
            if current_shares > 0 and confluence_multiplier > 0:
                full_shares = int(current_shares / confluence_multiplier)
                metadata["awaiting_confirmation"] = True
                metadata["target_full_shares"] = full_shares
                metadata["scale_in_shares"] = full_shares - int(current_shares)

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
    # Headline type for high-conviction bypass
    event_headline_type: Optional[str] = None,
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
        event_headline_type: Headline type from HeadlineTypeClassifier (for high-conviction bypass)
    """
    try:
        # High-conviction trades bypass circuit breaker — the edge is in the headline signal.
        is_hc_early = event_headline_type in HIGH_CONVICTION_HEADLINE_TYPES if event_headline_type else False

        # Check if we should process this classification
        if not should_process_classification(classification_result, enabled):
            # HC trades bypass circuit breaker (but not disabled/non-IMMINENT)
            if is_hc_early and is_circuit_breaker_triggered() and enabled and classification_result.classification == ClassificationCategory.IMMINENT:
                logger.info(
                    "🎖️ HIGH-CONVICTION BYPASS: Circuit breaker overridden for proven headline pattern",
                    article_id=classification_result.article_id,
                    headline_type=event_headline_type,
                )
                # Fall through to continue processing
            else:
                # Record why for IMMINENT articles (non-IMMINENT handled by classification system)
                if classification_result.classification == ClassificationCategory.IMMINENT:
                    if is_circuit_breaker_triggered():
                        await _record_postfilter_skip(classification_result.article_id, "postfilter_circuit_breaker")
                    elif not enabled:
                        await _record_postfilter_skip(classification_result.article_id, "postfilter_trading_disabled")
                return

        article_id = classification_result.article_id
        processing_start = datetime.now(timezone.utc)

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
            await _record_postfilter_skip(article_id, "postfilter_no_tickers")
            return

        if not published_at:
            logger.warning(
                "⏭️ AUTO-TRADE SKIPPED: No published_at in classification event",
                article_id=article_id,
            )
            await _record_postfilter_skip(article_id, "postfilter_no_published_at")
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
            await _record_postfilter_skip(article_id, "postfilter_no_ticker")
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
            # Get cooldown type for logging
            ticker_upper = ticker.upper()
            exit_data = _exited_tickers.get(ticker_upper, {})
            was_profitable = exit_data.get("was_profitable", False)
            cooldown_min = TICKER_COOLDOWN_PROFIT_MINUTES if was_profitable else TICKER_COOLDOWN_LOSS_MINUTES
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: Ticker in dynamic cooldown after recent exit",
                ticker=ticker,
                article_id=article_id,
                cooldown_remaining_min=round(remaining_min, 1) if remaining_min else 0,
                cooldown_total_min=cooldown_min,
                cooldown_type="profit" if was_profitable else "loss",
                reason=f"{'Short' if was_profitable else 'Long'} cooldown after {'profitable' if was_profitable else 'losing'} exit"
            )
            await _record_postfilter_skip(article_id, "postfilter_cooldown")
            return

        # ============================================================
        # 🚫 TICKER BLACKLIST: Don't trade serial pump-and-dump tickers
        # ============================================================
        from .ticker_blacklist import is_ticker_blacklisted
        if await is_ticker_blacklisted(ticker):
            logger.info(
                "⏭️ AUTO-TRADE SKIPPED: Ticker is blacklisted (3+ consecutive FPs)",
                ticker=ticker,
                article_id=article_id,
                reason="Serial pump-and-dump ticker"
            )
            await _record_postfilter_skip(article_id, "postfilter_blacklisted")
            return

        # ============================================================
        # 🚫 BLOCKED HEADLINE TYPE CHECK
        # ============================================================
        # Some headline types are never worth trading (e.g. acquirer = cash outflow).
        headline_type = event_headline_type
        if headline_type in BLOCKED_HEADLINE_TYPES:
            logger.info(
                f"⏭️ AUTO-TRADE SKIPPED: Blocked headline type '{headline_type}'",
                ticker=ticker,
                headline_type=headline_type,
                article_id=article_id,
                reason="Acquiring company trades are cash outflow — only targets are tradeable",
            )
            await _record_postfilter_skip(article_id, f"postfilter_blocked_headline_type:{headline_type}")
            return

        # ============================================================
        # 🎖️ HIGH-CONVICTION HEADLINE TYPE CHECK
        # ============================================================
        # If headline_type matches HIGH_CONVICTION_HEADLINE_TYPES, relax postfilters.
        # These headline types (gov/military contracts) reliably produce sustained moves.
        is_high_conviction = headline_type in HIGH_CONVICTION_HEADLINE_TYPES if headline_type else False
        is_ai_breakthrough = headline_type in AI_BREAKTHROUGH_HEADLINE_TYPES if headline_type else False

        if is_ai_breakthrough:
            logger.info(
                "🤖 AI BREAKTHROUGH HEADLINE: Spread filters will use price-tiered thresholds",
                ticker=ticker,
                headline_type=headline_type,
                article_id=article_id,
            )

        if is_high_conviction:
            logger.info(
                "🎖️ HIGH-CONVICTION HEADLINE: Postfilters will be relaxed",
                ticker=ticker,
                headline_type=headline_type,
                article_id=article_id,
                relaxed_filters="circuit_breaker=SKIP, spread=10%, selling_pressure=SKIP, pub_to_recv=SKIP, recv_to_fill=SKIP, fill_spread=10%, momentum_exhaustion=SKIP, late_entry=25s, pre_news_runup=10%",
            )

        # ============================================================
        # 🚀 AI + CONFLUENCE GATING: Require STRENGTH or SURGE to trade
        # ============================================================
        # Position size determined by AI based on headline quality.
        # REINSTATED: 2-second confluence check gates trades on market activity.
        # Rationale: AI determines WHAT to trade, confluence confirms activity.
        # STRENGTH = at least 1 criterion met + 0.5% excursion (any direction)
        # SURGE = full surge criteria (2%+ positive move, 80% buying pressure, volume spike)

        ai_position_size = event_position_size or "MAX"  # What the sector LLM sent

        if is_high_conviction:
            # HIGH-CONVICTION DEFENSE: Use AI position size with minimum MODERATE
            # Sized up: MODERATE=$750, LARGE=$1000, MAX=$1500
            if ai_position_size == "SMALL":
                ai_position_size = "MODERATE"
            ai_conviction = AI_SIZE_TO_CONVICTION.get(ai_position_size, ConvictionLevel.STANDARD)
            size_dict = HC_POSITION_SIZES_USD
            logger.info(
                f"🎯 HC DEFENSE SIZE: ${size_dict[ai_conviction]} (AI: {ai_position_size})",
                ticker=ticker,
                ai_position_size=ai_position_size,
                conviction=ai_conviction.value,
                position_size=f"${size_dict[ai_conviction]}",
                article_id=article_id,
            )
        else:
            # NORMAL TRADES: Use AI-determined position size
            # AI sizes: SMALL=$1000, MODERATE=$1250, LARGE=$1500, MAX=$2000
            # Modified by confluence multiplier (0.5x-1.0x)
            ai_conviction = AI_SIZE_TO_CONVICTION.get(ai_position_size, ConvictionLevel.STANDARD)
            size_dict = POSITION_SIZES_USD
            logger.info(
                f"🚀 BASE SIZE: ${size_dict[ai_conviction]} (AI: {ai_position_size})",
                ticker=ticker,
                ai_position_size=ai_position_size,
                conviction=ai_conviction.value,
                position_size=f"${size_dict[ai_conviction]}",
                article_id=article_id,
            )

        # ============================================================
        # 📊 CONFLUENCE CHECK: 2-second window for STRENGTH verification
        # ============================================================
        # Check confluence signals in the 2-second window after publication.
        # STRENGTH requires: confluence_score >= 1 AND max_excursion >= 0.5%
        MIN_STRENGTH_EXCURSION_PCT = 0.5  # Minimum price movement to confirm activity

        confluence_conviction, confluence_metadata = await check_confluence_signals(
            market_data_client=market_data_client,
            quote_fetcher=quote_fetcher,
            ticker=ticker,
            publication_time=published_at,
        )

        # Add AI position size to metadata for logging
        confluence_metadata["ai_position_size"] = ai_position_size

        # Get confluence score and excursion for gating decision
        confluence_score = confluence_metadata.get("confluence_score", 0)
        max_excursion_pct = confluence_metadata.get("confluence_price_excursion_pct", 0.0)
        confluence_trade_count = confluence_metadata.get("confluence_trade_count", 0)

        # Minimum trade count: 1-2 trades is not "confluence" — it's one person.
        # Require at least 3 independent trades to confirm real market interest.
        # EXCEPTION: HC trades bypass this — the headline type IS the confirmation.
        MIN_CONFLUENCE_TRADES = 3
        if confluence_trade_count < MIN_CONFLUENCE_TRADES and not is_high_conviction:
            logger.info(
                f"⏭️ TOO FEW TRADES: Only {confluence_trade_count} trade(s) in confluence window — not real activity",
                ticker=ticker,
                confluence_trade_count=confluence_trade_count,
                confluence_score=confluence_score,
                article_id=article_id,
            )
            confluence_score = 0  # Override score — single trade can't confirm anything
        elif confluence_trade_count < MIN_CONFLUENCE_TRADES and is_high_conviction:
            logger.info(
                f"🎖️ HC BYPASS: Only {confluence_trade_count} trade(s) but high-conviction headline — keeping score {confluence_score}",
                ticker=ticker,
                confluence_trade_count=confluence_trade_count,
                confluence_score=confluence_score,
                headline_type=headline_type,
                article_id=article_id,
            )

        # STRENGTH check: score >= 1 AND excursion >= 0.5%
        has_strength = confluence_score >= 1 and max_excursion_pct >= MIN_STRENGTH_EXCURSION_PCT

        # HIGH CONFLUENCE check: score >= 3 AND price must have moved >= 0.5%
        # Without price movement, high score can be faked by a few trades at same price (SPAI pattern)
        # Stocks that don't move in 2s but move later get caught by surge (8s) or late (30s) monitoring
        HIGH_CONFLUENCE_SCORE = 3
        has_high_confluence = confluence_score >= HIGH_CONFLUENCE_SCORE and max_excursion_pct >= MIN_STRENGTH_EXCURSION_PCT

        # HC BYPASS: High-conviction headlines (gov/military contracts) don't need activity confirmation.
        # The headline type IS the catalyst — 12% stop loss protects us. Any activity (score >= 1) is enough.
        has_hc_bypass = is_high_conviction and confluence_score >= 1

        if has_strength or has_high_confluence or has_hc_bypass:
            # Activity confirmed - use AI conviction for position sizing
            conviction = ai_conviction
            entry_reason = "HC_BYPASS" if has_hc_bypass and not has_strength and not has_high_confluence else ("STRENGTH" if has_strength else "HIGH_CONFLUENCE")
            logger.info(
                f"✅ {entry_reason} CONFIRMED: Confluence score {confluence_score}, excursion {max_excursion_pct:.2f}%",
                ticker=ticker,
                confluence_score=confluence_score,
                max_excursion_pct=max_excursion_pct,
                entry_reason=entry_reason,
                conviction=conviction.value,
                article_id=article_id
            )
        elif confluence_score >= 1:
            # Score >= 1 but excursion too low - no strength
            logger.info(
                f"⏭️ NO STRENGTH: Score {confluence_score} but excursion {max_excursion_pct:.2f}% < {MIN_STRENGTH_EXCURSION_PCT}% required",
                ticker=ticker,
                confluence_score=confluence_score,
                max_excursion_pct=max_excursion_pct,
                reason="Activity detected but price movement insufficient"
            )
            # Fall through to surge check
            has_strength = False

        # If no STRENGTH or HIGH CONFLUENCE, check for SURGE (8-second window with strict criteria)
        is_surge_trade = False
        is_late_trade = False
        if not has_strength and not has_high_confluence:
            logger.info(
                f"🔍 CHECKING SURGE: No STRENGTH found (score={confluence_score}, excursion={max_excursion_pct:.2f}%)",
                ticker=ticker,
                article_id=article_id
            )

            # Get initial ask for surge monitoring
            initial_ask_at_pub = confluence_metadata.get("initial_ask")

            surge_result = await monitor_for_last_chance_surge(
                market_data_client=market_data_client,
                quote_fetcher=quote_fetcher,
                ticker=ticker,
                publication_time=published_at,
                initial_nbbo_mid=confluence_metadata.get("initial_nbbo_mid"),
                article_id=article_id,
                initial_ask_at_publication=initial_ask_at_pub,
            )

            if surge_result:
                # SURGE found - use same sizing logic as STRENGTH trades
                # Same $4 base, same confluence multiplier, same safety filters
                # Rationale: If safety filters pass, ticker hasn't run away
                is_surge_trade = True
                conviction = ai_conviction  # Use AI conviction, not STANDARD override
                surge_timing = surge_result.get("surge_timing_seconds", 0)
                logger.info(
                    f"🚀 SURGE CONFIRMED: All criteria met in {surge_timing:.1f}s",
                    ticker=ticker,
                    surge_timing_seconds=surge_timing,
                    conviction=conviction.value,
                    article_id=article_id
                )
                # Update metadata with surge info
                confluence_metadata.update(surge_result)
            else:
                # Neither STRENGTH nor SURGE in initial windows - try late entry (up to 30s)
                late_result = await monitor_for_late_entry(
                    quote_fetcher=quote_fetcher,
                    ticker=ticker,
                    publication_time=published_at,
                    initial_ask_at_publication=initial_ask_at_pub,
                    article_id=article_id,
                )

                if late_result:
                    is_late_trade = True
                    conviction = ai_conviction
                    late_type = late_result.get("late_entry_type", "late_strength")
                    late_secs = late_result.get("late_entry_seconds_elapsed", 0)
                    logger.info(
                        f"🚀 LATE ENTRY CONFIRMED: {late_type.upper()} at {late_secs:.1f}s",
                        ticker=ticker,
                        late_entry_type=late_type,
                        seconds_elapsed=late_secs,
                        conviction=conviction.value,
                        article_id=article_id
                    )
                    confluence_metadata.update(late_result)
                else:
                    # No STRENGTH, SURGE, or late entry - SKIP
                    logger.info(
                        f"⏭️ AUTO-TRADE SKIPPED: No STRENGTH, SURGE, or late entry detected",
                        ticker=ticker,
                        confluence_score=confluence_score,
                        max_excursion_pct=max_excursion_pct,
                        reason="AI classified IMMINENT but no activity confirmation within 30s",
                        article_id=article_id
                    )
                    await _record_postfilter_skip(article_id, f"postfilter_no_strength_or_surge_or_late:score={confluence_score},excursion={max_excursion_pct:.1f}%")
                    return

        # ============================================================
        # 🔥 MEGA TRADE DETECTION: When ALL signals overwhelmingly align
        # ============================================================
        # These are the highest-conviction trades — the edge-creating opportunities.
        # Mega trades get relaxed front-running thresholds and wider 10% stop loss.
        #
        # ADIL lesson: score 4 without buying pressure is NOT mega-quality.
        # ADIL had 66.6% buying pressure (a third was selling) and lost -5.3%.
        # MOBX had score 5, 78.4% pressure, 93ms latency, 3378x surge → +120%.
        # Mega trade must mean ALL signals firing, no exceptions.
        confluence_buying_pressure = confluence_metadata.get("confluence_buying_pressure_pct", 0.0)
        is_mega_trade = (
            ai_position_size in ("LARGE", "MAX") and        # LLM says strong headline
            confluence_score >= 5 and                         # ALL 5 criteria met (not 4 — ADIL had 4 and lost -5.3%)
            confluence_metadata.get("confluence_has_volume_surge", False) and  # Extreme volume
            confluence_metadata.get("confluence_has_price_excursion", False) and  # Strong price move
            max_excursion_pct >= 2.0 and                      # Significant excursion (not noise)
            confluence_buying_pressure >= 70.0                 # Majority buying (MOBX 78.4% yes, ADIL 66.6% no)
            # NOTE: Not requiring confluence_has_buying_pressure bool (80% threshold)
            # because MOBX was at 78.4% which is below the 80% bool but clearly strong.
            # The 70% raw check is the real gate.
        )
        confluence_metadata["is_mega_trade"] = is_mega_trade
        confluence_metadata["is_high_conviction"] = is_high_conviction
        if headline_type:
            confluence_metadata["headline_type"] = headline_type

        if is_mega_trade:
            logger.info(
                "MEGA TRADE DETECTED: All signals overwhelmingly bullish — relaxed front-running, wider stop",
                ticker=ticker,
                ai_position_size=ai_position_size,
                confluence_score=confluence_score,
                max_excursion_pct=max_excursion_pct,
                buying_pressure_pct=confluence_buying_pressure,
                article_id=article_id,
            )

        # ============================================================
        # 🛡️ SAFETY FILTERS: Market cap check
        # ============================================================
        # These filters prevent trading manipulated or low-quality stocks.
        # Biotech price filter removed — momentum exhaustion handles bad cheap biotechs.

        # Filter 1: Market cap check (minimum $1.5M to avoid manipulated stocks)
        MIN_MARKET_CAP_MILLIONS = 1.5  # $1.5M minimum (lowered from $2M)
        ticker_sector = None  # Track sector for statistics
        ticker_industry = None  # Track industry for statistics
        if metadata_cache:
            try:
                ticker_metadata = await metadata_cache.get(ticker)
                if ticker_metadata:
                    # Extract sector and industry for tracking
                    ticker_sector = ticker_metadata.get("sector")
                    ticker_industry = ticker_metadata.get("industry", "")
                    confluence_metadata["sector"] = ticker_sector
                    confluence_metadata["industry"] = ticker_industry

                    # Check if sector is hot (logging only, not blocking yet)
                    from .sector_tracker import is_sector_hot
                    sector_is_hot = await is_sector_hot(ticker_sector)
                    confluence_metadata["sector_is_hot"] = sector_is_hot
                    if sector_is_hot:
                        logger.info(
                            f"⚠️ SECTOR HOT: {ticker_sector} has 3+ FPs today (proceeding with caution)",
                            ticker=ticker,
                            sector=ticker_sector,
                            article_id=article_id
                        )

                    market_cap_millions = ticker_metadata.get("market_cap_millions", 0)
                    if market_cap_millions and market_cap_millions < MIN_MARKET_CAP_MILLIONS:
                        logger.info(
                            "⏭️ AUTO-TRADE SKIPPED: Market cap below $1.5M threshold",
                            ticker=ticker,
                            market_cap_millions=round(market_cap_millions, 2),
                            min_required=MIN_MARKET_CAP_MILLIONS,
                            article_id=article_id
                        )
                        await _record_postfilter_skip(article_id, f"postfilter_market_cap:{market_cap_millions:.1f}M")
                        return

            except Exception as e:
                logger.debug(f"Could not check market cap/biotech filter: {e}")

        # ============================================================
        # 📊 SPREAD FILTER: Reject wide spreads (>5% of mid)
        # ============================================================
        # 🎯 WIDE SPREAD TRAP FILTER
        # ============================================================
        # Wide spreads cause instant losses. If bid-ask spread is 5%, you're
        # already down 5% the moment you buy at ask and would sell at bid.
        # IINN lesson: 35% spread = untradeable.
        # Tightened to 5% - even 5% spread means instant -5% on entry.
        if is_high_conviction:
            MAX_SPREAD_PCT = 10.0
        elif is_ai_breakthrough:
            _ab_price = confluence_metadata.get("initial_ask") or 0
            MAX_SPREAD_PCT = _ai_breakthrough_spread_threshold(_ab_price)
        else:
            MAX_SPREAD_PCT = 4.5
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
                confluence_metadata["initial_spread_pct"] = round(spread_pct_of_mid, 2)

                if spread_pct_of_mid > MAX_SPREAD_PCT:
                    logger.info(
                        "⏭️ AUTO-TRADE SKIPPED: Spread too wide (>4.5% of mid) - instant loss trap",
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

        # ============================================================
        # 🎯 SELLING PRESSURE FILTER: Block heavy selling
        # ============================================================
        # If imbalance ratio is strongly negative, there's more selling than buying.
        # This is a red flag - someone knows something we don't.
        SELLING_PRESSURE_THRESHOLD = -0.3  # More than 65% selling = block
        confluence_imbalance = confluence_metadata.get("confluence_imbalance_ratio")
        if confluence_imbalance is not None and confluence_imbalance < SELLING_PRESSURE_THRESHOLD:
            if is_high_conviction:
                logger.info(
                    "🎖️ HIGH-CONVICTION BYPASS: selling_pressure filter skipped (trusting headline signal)",
                    ticker=ticker,
                    imbalance_ratio=round(confluence_imbalance, 3),
                    headline_type=headline_type,
                    article_id=article_id,
                )
            else:
                buying_pct = ((confluence_imbalance + 1) / 2) * 100  # Convert to buying %
                logger.info(
                    "⏭️ AUTO-TRADE SKIPPED: Heavy selling pressure detected",
                    ticker=ticker,
                    imbalance_ratio=round(confluence_imbalance, 3),
                    buying_pressure_pct=round(buying_pct, 1),
                    threshold=SELLING_PRESSURE_THRESHOLD,
                    article_id=article_id,
                    reason="Imbalance strongly negative - more sellers than buyers, someone knows something"
                )
                await _record_postfilter_skip(article_id, f"postfilter_selling_pressure:{confluence_imbalance:.2f}")
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
        # Position = AI_BASE × CONFLUENCE_MULTIPLIER
        current_price = None  # Executor will fetch current price
        confluence_multiplier = confluence_metadata.get("confluence_multiplier", 1.0)
        confluence_level = confluence_metadata.get("confluence_level", "partial")

        logger.info(
            f"💰 POSITION SIZING: AI {ai_position_size} × {confluence_level} ({confluence_multiplier}x)",
            ticker=ticker,
            ai_position_size=ai_position_size,
            confluence_level=confluence_level,
            confluence_multiplier=confluence_multiplier,
            confluence_score=confluence_metadata.get("confluence_score", 0),
            article_id=article_id
        )

        trade_request = build_trade_request_for_article(
            minimal_article,
            current_price=current_price,
            ticker=ticker,
            conviction=conviction,
            confluence_multiplier=confluence_multiplier,
            is_high_conviction=is_high_conviction,
        )

        if not trade_request:
            await _record_postfilter_skip(article_id, "postfilter_trade_request_build_failed")
            return

        # 🚀 MICROSTRUCTURE CHECK: Ensure there is actually trading activity
        # "Truly big moves always have volume." - User
        if market_data_client and StockTradesRequest:
            try:
                # Check for any trades since publication
                trades_start = published_at
                trades_end = datetime.now(timezone.utc)

                # Use async wrapper to avoid blocking event loop
                trades = await run_sync_alpaca_call(
                    market_data_client.get_stock_trades,
                    StockTradesRequest(
                        symbol_or_symbols=trade_request.ticker,
                        start=trades_start,
                        end=trades_end,
                        feed=DataFeed.SIP
                    )
                )

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
        # 📊 PRE-ENTRY SPREAD CHECK: Final spread verification before trade
        # ============================================================
        # CRITICAL: Check spread at FILL TIME, not just at reception.
        # APUS disaster: Reception spread was 6%, but by fill time it was 10.27%.
        # This check prevents entering trades with wide spreads that developed during SURGE monitoring.
        #
        # JZXN lesson: On penny stocks, the spread temporarily widens during initial
        # volatility (bid drops as sells hit). JZXN had 2.11% initial spread but the
        # bid likely dipped causing ~5% fill spread — blocking a +69% winner.
        # Fix: If the initial spread was tight (< 3%) and widening is modest (< 3pp),
        # allow it — this is temporary noise, not genuine spread deterioration.
        if is_high_conviction:
            MAX_FILL_SPREAD_PCT = 10.0
        elif is_ai_breakthrough:
            _ab_fill_price = confluence_metadata.get("initial_ask") or 0
            MAX_FILL_SPREAD_PCT = _ai_breakthrough_spread_threshold(_ab_fill_price)
        else:
            MAX_FILL_SPREAD_PCT = 4.5
        SPREAD_TIGHT_INITIAL = 3.0  # Initial spread considered "tight"
        SPREAD_WIDENING_TOLERANCE = 3.0  # Max acceptable widening from initial (percentage points)

        pre_entry_nbbo = None
        if quote_fetcher:
            try:
                pre_entry_nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
                if pre_entry_nbbo:
                    current_bid = pre_entry_nbbo.get("bid", 0)
                    current_ask = pre_entry_nbbo.get("ask", 0)
                    if current_bid and current_ask and current_ask > current_bid:
                        current_spread = current_ask - current_bid
                        current_mid = (current_ask + current_bid) / 2
                        fill_spread_pct = (current_spread / current_mid) * 100 if current_mid > 0 else 0

                        if fill_spread_pct > MAX_FILL_SPREAD_PCT:
                            # Check if this is temporary volatility on a normally liquid stock
                            initial_spread_pct_val = confluence_metadata.get("initial_spread_pct", 0)
                            spread_widening = fill_spread_pct - initial_spread_pct_val if initial_spread_pct_val > 0 else fill_spread_pct
                            initial_was_tight = initial_spread_pct_val > 0 and initial_spread_pct_val < SPREAD_TIGHT_INITIAL
                            widening_is_modest = spread_widening < SPREAD_WIDENING_TOLERANCE

                            if initial_was_tight and widening_is_modest:
                                # Temporary volatility on a liquid stock — allow it
                                logger.info(
                                    "✅ FILL-TIME SPREAD: Exceeds 4.5% but initial was tight and widening modest — temporary volatility",
                                    ticker=ticker,
                                    fill_spread_pct=round(fill_spread_pct, 2),
                                    initial_spread_pct=round(initial_spread_pct_val, 2),
                                    spread_widening_pp=round(spread_widening, 2),
                                    tolerance_pp=SPREAD_WIDENING_TOLERANCE,
                                    current_bid=current_bid,
                                    current_ask=current_ask,
                                )
                                confluence_metadata["fill_spread_pct"] = round(fill_spread_pct, 2)
                            else:
                                # Genuine deterioration or already wide — block
                                logger.info(
                                    "⏭️ AUTO-TRADE SKIPPED: Spread widened beyond threshold at fill time",
                                    ticker=ticker,
                                    fill_spread_pct=round(fill_spread_pct, 2),
                                    initial_spread_pct=round(initial_spread_pct_val, 2) if initial_spread_pct_val else None,
                                    spread_widening_pp=round(spread_widening, 2),
                                    max_allowed=MAX_FILL_SPREAD_PCT,
                                    current_bid=current_bid,
                                    current_ask=current_ask,
                                    current_spread=round(current_spread, 4),
                                    article_id=article_id,
                                    reason="Spread genuinely deteriorated or initial already wide"
                                )
                                await _record_postfilter_skip(article_id, f"postfilter_fill_spread_too_wide:{fill_spread_pct:.1f}%")
                                return
                        else:
                            logger.info(
                                "✅ FILL-TIME SPREAD CHECK PASSED",
                                ticker=ticker,
                                fill_spread_pct=round(fill_spread_pct, 2),
                                max_allowed=MAX_FILL_SPREAD_PCT,
                            )

                        confluence_metadata["fill_spread_pct"] = round(fill_spread_pct, 2)
            except Exception as e:
                logger.warning(f"Fill-time spread check failed: {e} - proceeding with caution")

        # ============================================================
        # 🏃 TWO-LEG PRICE MOVEMENT FILTER: Prevent front-run / pumped entries
        # ============================================================
        # VRME lesson: Ask moved from $1.15 (pub) to $1.33 (recv) = 15.6% BEFORE we even saw it.
        # This means the move already happened - we're entering as exit liquidity.
        #
        # Two-leg filter:
        # 1. pub → recv: Max 3% ask change (if already moved, we're late)
        # 2. recv → fill: Max 3% ask change (if moving during our checks, too volatile)
        #
        # This catches both front-running (pub→recv) and chase scenarios (recv→fill).
        MAX_ASK_CHANGE_PER_LEG_PCT = 3.0
        MIN_ABSOLUTE_ASK_MOVE = 0.05  # $0.05 minimum move to trigger filter (penny stock protection)
        initial_ask = confluence_metadata.get("initial_ask")  # Ask at reception/confluence time

        # LEG 1: Publication → Reception price change
        # Try WebSocket cache first (instant), fall back to REST API (100-300ms)
        pub_time_ask = None
        pub_quote_source = None

        # FAST PATH: Try WebSocket cache first
        stream_manager = getattr(quote_fetcher, 'stream_manager', None) if quote_fetcher else None
        if stream_manager and published_at:
            try:
                cached_quotes = await stream_manager.get_recent_quotes(ticker, max_quotes=500)
                if cached_quotes:
                    # Find quote closest to publication time
                    pub_time_utc = published_at.replace(tzinfo=timezone.utc) if published_at.tzinfo is None else published_at
                    best_quote = None
                    best_delta = float('inf')

                    for quote in cached_quotes:
                        quote_ts = quote.get("timestamp")
                        if quote_ts:
                            if quote_ts.tzinfo is None:
                                quote_ts = quote_ts.replace(tzinfo=timezone.utc)
                            delta = abs((quote_ts - pub_time_utc).total_seconds())
                            # Only consider quotes within 2 seconds of publication
                            if delta < 2.0 and delta < best_delta:
                                best_delta = delta
                                best_quote = quote

                    if best_quote and best_quote.get("ask"):
                        pub_time_ask = best_quote["ask"]
                        pub_quote_source = "websocket_cache"
                        logger.debug(
                            "Got publication-time ask from WebSocket cache (FAST)",
                            ticker=ticker,
                            pub_time_ask=pub_time_ask,
                            delta_seconds=round(best_delta, 3)
                        )
            except Exception as e:
                logger.debug(f"WebSocket cache lookup failed: {e}")

        # SLOW PATH: Fall back to REST API if cache miss
        if not pub_time_ask and market_data_client and published_at:
            try:
                from alpaca.data.requests import StockQuotesRequest

                # Get quote at publication time (1-second window)
                pub_quotes = await run_sync_alpaca_call(
                    market_data_client.get_stock_quotes,
                    StockQuotesRequest(
                        symbol_or_symbols=ticker,
                        start=published_at - timedelta(seconds=1),
                        end=published_at + timedelta(seconds=1),
                        feed=DataFeed.SIP
                    )
                )

                if pub_quotes and pub_quotes.data and ticker in pub_quotes.data:
                    quotes_list = pub_quotes.data[ticker]
                    if quotes_list:
                        pub_time_ask = quotes_list[-1].ask_price if quotes_list else None
                        pub_quote_source = "rest_api"
                        logger.debug(
                            "Fetched publication-time ask from REST API (SLOW)",
                            ticker=ticker,
                            pub_time_ask=pub_time_ask,
                            quotes_count=len(quotes_list)
                        )
            except Exception as e:
                logger.debug(f"REST API quote fetch failed: {e}")

        # Mega trades get slightly relaxed front-running thresholds
        # MOBX had 12% LEG 1 front-running but passes via $0.05 absolute floor anyway.
        # The percentage only matters for non-penny stocks where absolute > $0.05.
        # 8% is conservative but still above normal 3% — absolute floor handles penny stocks.
        is_mega_trade = confluence_metadata.get("is_mega_trade", False)
        MEGA_MAX_ASK_CHANGE_PER_LEG_PCT = 8.0  # 8% vs 3% normal (MOBX was 12% but passes via $0.05 floor)
        effective_max_pct = MEGA_MAX_ASK_CHANGE_PER_LEG_PCT if is_mega_trade else MAX_ASK_CHANGE_PER_LEG_PCT

        # Check pub → recv change if we have both prices
        if pub_time_ask and initial_ask and pub_time_ask > 0:
            pub_to_recv_pct = ((initial_ask - pub_time_ask) / pub_time_ask) * 100
            absolute_move = abs(initial_ask - pub_time_ask)

            if pub_to_recv_pct > effective_max_pct and absolute_move >= MIN_ABSOLUTE_ASK_MOVE:
                # High-conviction headlines: skip pub_to_recv filter (legitimate market reaction)
                if is_high_conviction:
                    logger.info(
                        "🎖️ HIGH-CONVICTION BYPASS: pub_to_recv filter skipped (legitimate market reaction to gov/mil contract)",
                        ticker=ticker,
                        pub_to_recv_pct=round(pub_to_recv_pct, 2),
                        normal_max=effective_max_pct,
                        headline_type=headline_type,
                        article_id=article_id,
                    )
                else:
                    logger.info(
                        "⏭️ AUTO-TRADE SKIPPED: Ask moved too much between publication and reception",
                        ticker=ticker,
                        pub_time_ask=round(pub_time_ask, 4),
                        recv_time_ask=round(initial_ask, 4),
                        pub_to_recv_pct=round(pub_to_recv_pct, 2),
                        absolute_move=round(absolute_move, 4),
                        max_allowed_pct=effective_max_pct,
                        min_absolute=MIN_ABSOLUTE_ASK_MOVE,
                        is_mega_trade=is_mega_trade,
                        article_id=article_id,
                        reason="Front-running detected: move happened BEFORE we received the article"
                    )
                    await _record_postfilter_skip(article_id, f"postfilter_pub_to_recv:{pub_to_recv_pct:.1f}%")
                    return

            logger.info(
                "✅ PUB→RECV CHECK PASSED",
                ticker=ticker,
                pub_time_ask=round(pub_time_ask, 4),
                recv_time_ask=round(initial_ask, 4),
                pub_to_recv_pct=round(pub_to_recv_pct, 2),
                absolute_move=round(absolute_move, 4),
                max_allowed_pct=effective_max_pct,
                is_mega_trade=is_mega_trade,
            )

            # Store pub_time_ask in metadata for statistics
            confluence_metadata["pub_time_ask"] = pub_time_ask
            confluence_metadata["pub_to_recv_pct"] = round(pub_to_recv_pct, 2)

        # LEG 2: Reception → Fill price change
        # Check if ask moved too much since reception (chase/volatility filter)
        if pre_entry_nbbo and initial_ask and initial_ask > 0:
            # Use already-fetched NBBO for chase check
            current_ask = pre_entry_nbbo.get("ask", 0)
            if current_ask and current_ask > 0:
                recv_to_fill_pct = ((current_ask - initial_ask) / initial_ask) * 100
                absolute_move_leg2 = abs(current_ask - initial_ask)

                if recv_to_fill_pct > effective_max_pct and absolute_move_leg2 >= MIN_ABSOLUTE_ASK_MOVE:
                    if is_high_conviction:
                        logger.info(
                            "🎖️ HIGH-CONVICTION BYPASS: recv_to_fill filter skipped (fast price movement is the signal)",
                            ticker=ticker,
                            recv_to_fill_pct=round(recv_to_fill_pct, 2),
                            normal_max=effective_max_pct,
                            headline_type=headline_type,
                            article_id=article_id,
                        )
                    else:
                        logger.info(
                            "⏭️ AUTO-TRADE SKIPPED: Ask moved too much between reception and fill",
                            ticker=ticker,
                            recv_time_ask=round(initial_ask, 4),
                            fill_time_ask=round(current_ask, 4),
                            recv_to_fill_pct=round(recv_to_fill_pct, 2),
                            absolute_move=round(absolute_move_leg2, 4),
                            max_allowed_pct=effective_max_pct,
                            min_absolute=MIN_ABSOLUTE_ASK_MOVE,
                            is_mega_trade=is_mega_trade,
                            article_id=article_id,
                            reason="Price too volatile during our checks - likely pump in progress"
                        )
                        await _record_postfilter_skip(article_id, f"postfilter_recv_to_fill:{recv_to_fill_pct:.1f}%")
                        return

                logger.info(
                    "✅ RECV→FILL CHECK PASSED",
                    ticker=ticker,
                    recv_time_ask=round(initial_ask, 4),
                    fill_time_ask=round(current_ask, 4),
                    recv_to_fill_pct=round(recv_to_fill_pct, 2),
                    absolute_move=round(absolute_move_leg2, 4),
                    max_allowed_pct=effective_max_pct,
                    is_mega_trade=is_mega_trade,
                )

                # Store recv_to_fill_pct in metadata for statistics
                confluence_metadata["recv_to_fill_pct"] = round(recv_to_fill_pct, 2)

        # ============================================================
        # 🎯 PUMP-AND-DUMP FILTER: Entry ask vs confluence VWAP
        # ============================================================
        # Pump-and-dump pattern: Ask held high while trades happen at lower prices.
        # We'd enter at the inflated ask and the price crashes to where trades actually are.
        #
        # Uses VWAP (not first_price) because first_price can be a stale pre-news tick
        # (e.g. GFAI $0.44 stale → VWAP $0.49 → ask $0.503 = 2.6% premium, not 14.3%).
        #
        # JZXN lesson: On sub-$1 stocks, the bid-ask spread is structurally wide (2-4%),
        # so VWAP (trades near bid/mid) is naturally 3-5% below the ask. This is normal
        # microstructure, not pump manipulation. Require BOTH percentage AND absolute dollar
        # thresholds to avoid false positives on penny stocks (same fix as front-running).
        MAX_ASK_VS_VWAP_PCT = 12.0 if is_ai_breakthrough else 5.5  # AI breakthrough: 12% (early volatility dips depress VWAP), normal: 5.5%
        MIN_ABSOLUTE_ASK_VS_VWAP = 0.08  # $0.08 minimum gap to trigger (penny stock protection)
        # Higher than front-running's $0.05 because this checks a LEVEL gap (ask vs VWAP),
        # not a DELTA (how much ask moved). On sub-$1 stocks, VWAP naturally sits 5-8¢
        # below ask due to spread structure. Empirical: filter has 0 real catches and 2
        # false positives (JZXN $0.051 gap +69%, OneMedNet $0.079 gap +26%). EPOW (the
        # inspiration) had actual VWAP $0.9641 vs ask $1.00 = 3.7% — wouldn't even trigger.
        confluence_vwap = confluence_metadata.get("confluence_vwap")
        fill_ask = pre_entry_nbbo.get("ask") if pre_entry_nbbo else None

        if confluence_vwap and fill_ask and confluence_vwap > 0:
            ask_vs_vwap_pct = ((fill_ask - confluence_vwap) / confluence_vwap) * 100
            absolute_gap = abs(fill_ask - confluence_vwap)

            if ask_vs_vwap_pct > MAX_ASK_VS_VWAP_PCT and absolute_gap >= MIN_ABSOLUTE_ASK_VS_VWAP:
                logger.info(
                    "⏭️ AUTO-TRADE SKIPPED: Entry ask too far above VWAP (pump-and-dump pattern)",
                    ticker=ticker,
                    confluence_vwap=round(confluence_vwap, 4),
                    fill_ask=round(fill_ask, 4),
                    ask_vs_vwap_pct=round(ask_vs_vwap_pct, 2),
                    absolute_gap=round(absolute_gap, 4),
                    max_allowed_pct=MAX_ASK_VS_VWAP_PCT,
                    min_absolute=MIN_ABSOLUTE_ASK_VS_VWAP,
                    article_id=article_id,
                    reason="Entry ask is above average trading price - paying the pump premium"
                )
                await _record_postfilter_skip(article_id, f"postfilter_pump_and_dump:{ask_vs_vwap_pct:.1f}%")
                return

            logger.info(
                "✅ PUMP-AND-DUMP CHECK PASSED",
                ticker=ticker,
                confluence_vwap=round(confluence_vwap, 4),
                fill_ask=round(fill_ask, 4),
                ask_vs_vwap_pct=round(ask_vs_vwap_pct, 2),
                absolute_gap=round(absolute_gap, 4),
                max_allowed_pct=MAX_ASK_VS_VWAP_PCT,
                min_absolute=MIN_ABSOLUTE_ASK_VS_VWAP,
            )

            # Store for statistics
            confluence_metadata["ask_vs_vwap_pct"] = round(ask_vs_vwap_pct, 2)

        # ============================================================
        # 🎯 PRE-NEWS RUNUP FILTER
        # ============================================================
        # Check if stock already moved significantly BEFORE the news.
        # If it ran 5%+ in the 30 minutes prior, the news may already be priced in.
        # Could indicate: insider buying, leaked news, or technical breakout unrelated to news.
        PRE_NEWS_LOOKBACK_SECONDS = 1800  # 30 minutes
        PRE_NEWS_RUNUP_THRESHOLD = 10.0 if is_high_conviction else 5.0  # High-conviction: 10% (pre-positioning normal for defense)

        pre_news_change_pct = None
        stream_manager = getattr(quote_fetcher, 'stream_manager', None) if quote_fetcher else None

        if stream_manager and published_at:
            try:
                # Get historical quotes from WebSocket cache
                cached_quotes = await stream_manager.get_recent_quotes(ticker, max_quotes=3000)

                if cached_quotes and len(cached_quotes) > 10:
                    pub_time_utc = published_at.replace(tzinfo=timezone.utc) if published_at.tzinfo is None else published_at
                    target_time = pub_time_utc - timedelta(seconds=PRE_NEWS_LOOKBACK_SECONDS)

                    # Find quote closest to 30 min ago
                    price_30min_ago = None
                    price_at_pub = None

                    for quote in cached_quotes:
                        quote_time = quote.get("timestamp")
                        if quote_time:
                            if isinstance(quote_time, str):
                                quote_time = datetime.fromisoformat(quote_time.replace('Z', '+00:00'))

                            # Find price around 30 min before publication
                            time_diff_30min = abs((quote_time - target_time).total_seconds())
                            if time_diff_30min < 120 and price_30min_ago is None:  # Within 2 min of target
                                price_30min_ago = quote.get("ask") or quote.get("mid")

                            # Find price around publication time
                            time_diff_pub = abs((quote_time - pub_time_utc).total_seconds())
                            if time_diff_pub < 5 and price_at_pub is None:  # Within 5s of pub
                                price_at_pub = quote.get("ask") or quote.get("mid")

                    if price_30min_ago and price_at_pub and price_30min_ago > 0:
                        pre_news_change_pct = ((price_at_pub - price_30min_ago) / price_30min_ago) * 100

                        if pre_news_change_pct > PRE_NEWS_RUNUP_THRESHOLD:
                            logger.info(
                                "⏭️ AUTO-TRADE SKIPPED: Pre-news runup detected (stock already moved >5% before news)",
                                ticker=ticker,
                                price_30min_ago=round(price_30min_ago, 4),
                                price_at_pub=round(price_at_pub, 4),
                                pre_news_change_pct=round(pre_news_change_pct, 2),
                                max_allowed=PRE_NEWS_RUNUP_THRESHOLD,
                                article_id=article_id,
                                reason="Stock already ran before news - could be priced in or leaked"
                            )
                            await _record_postfilter_skip(article_id, f"postfilter_pre_news_runup:{pre_news_change_pct:.1f}%")
                            return

                        logger.debug(
                            "✅ PRE-NEWS CHECK PASSED",
                            ticker=ticker,
                            pre_news_change_pct=round(pre_news_change_pct, 2),
                            max_allowed=PRE_NEWS_RUNUP_THRESHOLD
                        )

                        # Store for statistics
                        confluence_metadata["pre_news_30min_change_pct"] = round(pre_news_change_pct, 2)

            except Exception as e:
                logger.debug(f"Pre-news runup check failed (non-blocking): {e}")

        # ============================================================
        # 🎯 MOMENTUM EXHAUSTION FILTER
        # ============================================================
        # If the max trade price in confluence is X% above our actual entry price (initial_ask),
        # the move already happened. We'd be buying at the peak.
        # Uses initial_ask (NBBO ask at reception) as base — NOT first_trade_price which
        # can be a stale pre-news tick (e.g. GFAI $0.44 stale → $0.50 real = false 13.6% runup).
        MAX_CONFLUENCE_RUNUP_PCT = 5.0  # If max price ran 5% above our entry price, we're late
        confluence_max_price = confluence_metadata.get("confluence_max_price")
        entry_reference_price = confluence_metadata.get("initial_ask") or confluence_first_price

        if entry_reference_price and confluence_max_price and entry_reference_price > 0:
            confluence_runup_pct = ((confluence_max_price - entry_reference_price) / entry_reference_price) * 100

            if confluence_runup_pct > MAX_CONFLUENCE_RUNUP_PCT:
                # High-conviction headlines: skip momentum exhaustion (these sustain momentum)
                if is_high_conviction:
                    logger.info(
                        "🎖️ HIGH-CONVICTION BYPASS: momentum_exhaustion filter skipped (gov/mil contracts sustain momentum)",
                        ticker=ticker,
                        confluence_runup_pct=round(confluence_runup_pct, 2),
                        normal_max=MAX_CONFLUENCE_RUNUP_PCT,
                        headline_type=headline_type,
                        article_id=article_id,
                    )
                else:
                    logger.info(
                        "⏭️ AUTO-TRADE SKIPPED: Momentum exhausted (max price >5% above entry)",
                        ticker=ticker,
                        entry_reference_price=round(entry_reference_price, 4),
                        confluence_max_price=round(confluence_max_price, 4),
                        confluence_runup_pct=round(confluence_runup_pct, 2),
                        max_allowed=MAX_CONFLUENCE_RUNUP_PCT,
                        article_id=article_id,
                        reason="Max confluence price is above our entry price - entering at the top"
                    )
                    await _record_postfilter_skip(article_id, f"postfilter_momentum_exhausted:{confluence_runup_pct:.1f}%")
                    return

            logger.info(
                "✅ MOMENTUM CHECK PASSED",
                ticker=ticker,
                entry_reference_price=round(entry_reference_price, 4),
                confluence_max_price=round(confluence_max_price, 4),
                confluence_runup_pct=round(confluence_runup_pct, 2),
                max_allowed=MAX_CONFLUENCE_RUNUP_PCT
            )

            # Store for statistics
            confluence_metadata["confluence_runup_pct"] = round(confluence_runup_pct, 2)

        # ============================================================
        # 🎯 LATE ENTRY FILTER
        # ============================================================
        # If we're trying to trade too late after publication, skip.
        # For late trades (confirmed via monitor_for_late_entry), allow up to 35s.
        # For normal trades, max 15s from publication (many arrive in 10-15s batches).
        if is_high_conviction:
            max_entry_delay = 25.0  # High-conviction: 25s (10s extra over normal — moves last long enough, pump-and-dump protects)
        elif is_late_trade:
            max_entry_delay = 95.0
        else:
            max_entry_delay = 15.0
        now_utc = datetime.now(timezone.utc)
        pub_time_utc = published_at.replace(tzinfo=timezone.utc) if published_at.tzinfo is None else published_at
        entry_delay_seconds = (now_utc - pub_time_utc).total_seconds()

        if entry_delay_seconds > max_entry_delay:
            logger.info(
                f"⏭️ AUTO-TRADE SKIPPED: Too late (>{max_entry_delay:.0f}s since publication)",
                ticker=ticker,
                entry_delay_seconds=round(entry_delay_seconds, 2),
                max_allowed_seconds=max_entry_delay,
                is_late_trade=is_late_trade,
                published_at=pub_time_utc.isoformat(),
                now=now_utc.isoformat(),
                article_id=article_id,
                reason="Late to the party - early buyers ready to dump on you"
            )
            await _record_postfilter_skip(article_id, f"postfilter_late_entry:{entry_delay_seconds:.1f}s")
            return

        logger.info(
            "✅ ENTRY TIMING CHECK PASSED",
            ticker=ticker,
            entry_delay_seconds=round(entry_delay_seconds, 2),
            max_allowed_seconds=max_entry_delay,
            is_late_trade=is_late_trade,
        )

        # Store for statistics
        confluence_metadata["entry_delay_seconds"] = round(entry_delay_seconds, 2)

        # Determine entry timing classification
        if is_late_trade:
            entry_timing = confluence_metadata.get("late_entry_type", "late_strength")
        elif is_surge_trade:
            entry_timing = "early_surge"
        elif has_high_confluence and not has_strength:
            entry_timing = "high_confluence"
        else:
            entry_timing = "early_strength"
        confluence_metadata["entry_timing"] = entry_timing
        confluence_metadata["is_late_trade"] = is_late_trade

        # ============================================================
        # 📊 FILTER CHECKPOINT VALUES (for hit rate analysis)
        # ============================================================
        # Capture all filter values for TP/FP comparison
        # Extract hour for time-of-day analysis
        hour = now_utc.hour if now_utc else None

        filter_values = {
            "spread_pct": confluence_metadata.get("initial_spread_pct"),
            "fill_spread_pct": confluence_metadata.get("fill_spread_pct"),
            "pub_to_recv_pct": confluence_metadata.get("pub_to_recv_pct"),
            "recv_to_fill_pct": confluence_metadata.get("recv_to_fill_pct"),
            "ask_vs_first_trade_pct": confluence_metadata.get("ask_vs_first_trade_pct"),
            "confluence_runup_pct": confluence_metadata.get("confluence_runup_pct"),
            "pre_news_30min_change_pct": confluence_metadata.get("pre_news_30min_change_pct"),
            "entry_delay_seconds": round(entry_delay_seconds, 2),
            "confluence_score": confluence_metadata.get("confluence_score"),
            "max_excursion_pct": confluence_metadata.get("confluence_price_excursion_pct"),
            "imbalance_ratio": confluence_metadata.get("confluence_imbalance_ratio"),
            "buying_pressure_pct": confluence_metadata.get("confluence_buying_pressure_pct"),
            "dollar_volume": confluence_metadata.get("confluence_dollar_volume"),
            "trade_count": confluence_metadata.get("confluence_trade_count"),
            "first_trade_latency_ms": confluence_metadata.get("confluence_first_trade_latency_ms"),
            "entry_timing": entry_timing,
            "is_late_trade": is_late_trade,
            "hour": hour,
            "sector": confluence_metadata.get("sector"),
            "industry": confluence_metadata.get("industry"),
            "sector_is_hot": confluence_metadata.get("sector_is_hot", False),
            "is_high_conviction": is_high_conviction,
            "headline_type": headline_type,
        }
        # All filters passed for executed trades
        filters_checked = {
            "circuit_breaker": True,
            "duplicate_position": True,
            "cooldown": True,
            "blacklist": True,
            "strength_or_surge": True,
            "market_cap": True,
            "spread": True,
            "pub_to_recv": True,
            "recv_to_fill": True,
            "pump_and_dump": True,
            "pre_news_runup": True,
            "momentum_exhaustion": True,
            "late_entry": True,
        }
        confluence_metadata["filter_values"] = filter_values
        confluence_metadata["filters_checked"] = filters_checked

        # ============================================================
        # ⏱️ LATENCY METRICS (observational - for future optimization)
        # ============================================================
        decision_time = datetime.now(timezone.utc)
        pub_to_reception_ms = (processing_start - pub_time_utc).total_seconds() * 1000
        reception_to_decision_ms = (decision_time - processing_start).total_seconds() * 1000
        pub_to_decision_ms = (decision_time - pub_time_utc).total_seconds() * 1000
        confluence_metadata["pub_to_reception_ms"] = round(pub_to_reception_ms, 1)
        confluence_metadata["reception_to_decision_ms"] = round(reception_to_decision_ms, 1)
        confluence_metadata["pub_to_decision_ms"] = round(pub_to_decision_ms, 1)

        # Publish trade request with conviction metadata
        logger.info(
            "🚀 AUTO-TRADING: Publishing trade request domain event",
            ticker=trade_request.ticker,
            article_id=article_id,
            conviction=conviction.value,
            position_size=f"${POSITION_SIZES_USD[conviction]}",
            pub_to_reception_ms=round(pub_to_reception_ms, 1),
            reception_to_decision_ms=round(reception_to_decision_ms, 1),
            pub_to_decision_ms=round(pub_to_decision_ms, 1),
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
        # Record the exception as the postfilter reason so it's never silently lost
        try:
            await _record_postfilter_skip(
                classification_result.article_id,
                f"postfilter_exception:{type(e).__name__}:{str(e)[:100]}"
            )
        except Exception:
            pass  # Last resort — don't let recording failure mask the real error


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
        # Periodic housekeeping — sweep stale entries from tracking dicts
        cleanup_stale_tracking()

        logger.info(
            "🎯 AUTO-TRADE: Received ArticleClassified event (with article metadata)",
            article_id=domain_event.result.article_id,
            classification=domain_event.result.classification.value,
            tickers=domain_event.tickers,
            has_published_at=domain_event.published_at is not None,
            position_size=domain_event.position_size,
            headline_type=domain_event.headline_type,
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
            # Headline type for high-conviction bypass
            event_headline_type=domain_event.headline_type,
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

