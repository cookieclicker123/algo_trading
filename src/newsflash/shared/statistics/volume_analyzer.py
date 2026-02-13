"""
Volume Analyzer - Stateless utility for volume surge analysis.

This module provides functions to analyze volume and quote data around
a specific event time (e.g., when a news article was received).

The analysis captures:
- Volume at 3 min, 2 min, 1 min, 30 sec before, and at event time
- Bid/Ask/Spread at each interval
- Computed surge metrics (for future filtering)

NO FILTERING IS PERFORMED HERE - this is purely data collection.
Thresholds will be derived from 30+ days of statistics.
"""
import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockQuotesRequest, StockTradesRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
import yfinance as yf

from ...utils.logging_config import get_logger
from ...utils.async_alpaca import run_sync_alpaca_call

if TYPE_CHECKING:
    from ...infra.brokerage.stream_manager import AlpacaMarketDataStreamManager

logger = get_logger(__name__)


@dataclass
class VolumeStats:
    """Volume and quote statistics at a specific timestamp."""
    timestamp: str  # ISO format for serialization
    volume: Optional[int] = None
    trade_count: Optional[int] = None
    range: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    normalized_minute_volume: Optional[float] = None
    normalized_minute_range: Optional[float] = None
    window_seconds: Optional[float] = None
    # Order Flow (only for trade windows)
    buy_volume: Optional[int] = None
    sell_volume: Optional[int] = None
    imbalance_ratio: Optional[float] = None
    max_price: Optional[float] = None  # Highest price hit in window
    
    # Shadow Tracking
    total_dollar_volume: Optional[float] = None
    block_trade_pct: Optional[float] = None
    tape_acceleration_pct: Optional[float] = None
    first_trade_ts: Optional[datetime] = None
    max_trade_gap: Optional[float] = None  # Liveness Metric: Max seconds between trades
    tape_quality_score: Optional[float] = None  # New Metric: 0-100 Score
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class VolumeSurgeAnalysis:
    """
    Lean Four-Pillar Confluence Model for Trade Entry.
    Focuses strictly on identifying sudden, high-quality surges.
    """
    symbol: str
    event_time: str  # ISO format (published_at)
    move_type: str = "NORMAL_ACTIVITY"  # Classification result
    
    # 1. PILLAR: SIZE (Volume)
    window_volume: int = 0
    prior_avg_10min_volume: float = 0.0
    surge_multiplier: float = 0.0
    window_volume_normalised: Optional[float] = None
    volume_accel_pct: Optional[float] = None
    
    # 2. PILLAR: FREQUENCY (Trade Count)
    trade_count: int = 0
    trade_count_normalised: Optional[float] = None
    prior_avg_10min_trade_count: Optional[float] = None
    trade_count_multiplier: Optional[float] = None
    
    # 3. PILLAR: MOMENTUM (Price Action)
    max_excursion_pct: Optional[float] = None  # Peak move in window vs Pub Ask
    spread_compression_pct: Optional[float] = None # How much the spread narrowed
    pub_price: Optional[float] = None # Price @ Publication time (Ask)
    recv_price: Optional[float] = None # Price @ Reception time (Ask)
    ask_change_pct: Optional[float] = None 
    
    # 4. PILLAR: CONVICTION (Order Flow)
    buy_volume: Optional[int] = None
    sell_volume: Optional[int] = None
    imbalance_ratio: Optional[float] = None  # -1.0 to 1.0 (Buying Pressure)

    # 5. PILLAR: SHADOW TRACKING (Alpha Research)
    block_trade_pct: Optional[float] = None
    price_impact_bps: Optional[float] = None
    tape_acceleration_pct: Optional[float] = None
    latency_to_first_trade: Optional[float] = None
    post_trade_bid_ratio: Optional[float] = None
    max_trade_gap: Optional[float] = None  # Liveness Metric
    tape_quality_score: Optional[float] = None  # 0-100 Score
    float_shares: Optional[int] = None  # From YFinance
    
    # EARLY MOMENTUM (1-second window - matches auto_trade.py conviction check)
    # These fields track the first 1 second after article publication for conviction-based sizing
    early_1s_move_pct: Optional[float] = None       # Max price excursion in first 1 second
    early_1s_volume: Optional[int] = None           # Volume in first 1 second
    early_1s_trade_count: Optional[int] = None      # Trade count in first 1 second
    moved_1_percent_in_1s: bool = False             # True if price moved 1%+ in first 1 second
    early_1s_volume_surge: bool = False             # True if volume surge (500+ shares) in first 1 second
    conviction_level: Optional[str] = None          # "standard", "high", "very_high" based on early signals

    # METADATA & LEGACY
    pub_to_recv_seconds: float = 0.0
    volatility_surge_ratio: Optional[float] = None
    prior_avg_minute_range: Optional[float] = None
    prior_avg_10min_spread: Optional[float] = None
    pub_to_recv_range: Optional[float] = None
    pub_ask: Optional[float] = None # Legacy
    recv_ask: Optional[float] = None # Legacy
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)



def _get_float_shares(symbol: str) -> Optional[int]:
    """
    Fetch shares outstanding from YFinance.
    Note: This is a synchronous blocking call (~500ms-1s).
    """
    try:
        # Suppress yfinance logging spam if possible, or just call
        ticker = yf.Ticker(symbol)
        # accessing .info triggers the request
        info = ticker.info
        # Prefer floatShares, fallback to sharesOutstanding
        return info.get("floatShares") or info.get("sharesOutstanding")
    except Exception as e:
        logger.warning(f"⚠️ Failed to fetch float for {symbol}: {e}")
        return None


def _calculate_tape_quality(
    trade_list: List[Any],
    buy_vol: int,
    sell_vol: int,
    imbalance: float,
    block_pct: float
) -> float:
    """
    Calculate Tape Quality Score (0-100).
    A high score indicates specific, high-conviction order flow (clean tape).
    A low score indicates choppy, indecisive, or retail-heavy flow.
    """
    if not trade_list:
        return 0.0
    
    # 1. Conviction Score (Imbalance)
    # 0.8 imbalance -> 1.0 score. 0.0 imbalance -> 0.0 score.
    conviction_score = abs(imbalance) * 100
    
    # 2. Institutional Presence (Block Trade %)
    # 50% blocks -> 100 score. 0% -> 0 score.
    # We cap at 50% being "Perfect" (don't need 100% blocks)
    inst_score = min(block_pct * 2, 100.0)
    
    # 3. Participation Consistency (Trade Count per Second)
    # We want valid density, not 1 trade then silence.
    # But simple logic for now: If we have > 10 trades, good.
    density_score = min(len(trade_list) * 5, 100.0)
    
    # Weighted Average
    # Conviction is King (50%)
    # Institutions (30%)
    # Density (20%)
    total_score = (conviction_score * 0.5) + (inst_score * 0.3) + (density_score * 0.2)
    return round(total_score, 1)


async def _fetch_trades_in_window_async(
    client: StockHistoricalDataClient,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    reference_nbbo: Optional[Dict[str, Any]] = None,
    stream_manager: Optional["AlpacaMarketDataStreamManager"] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch all trades in a time window using WebSocket cache if available, else REST API.
    
    Uses high-precision tick data to separate Buy volume from Sell volume.
    """
    # Ensure UTC
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)
    
    # Try WebSocket cache first (if available and window is recent)
    now = datetime.now(timezone.utc)
    window_age_seconds = (now - window_end).total_seconds()
    
    # Use WebSocket cache if:
    # 1. Stream manager is available
    # 2. Window is recent (within last 60 seconds - cache only has recent trades)
    # 3. Window is in the past (not future)
    if stream_manager and -60 <= window_age_seconds <= 60:
        try:
            cached_trades = await stream_manager.get_recent_trades(symbol, max_trades=1000)
            
            # Filter trades by time window
            filtered_trades = [
                t for t in cached_trades
                if isinstance(t, dict) and 
                "timestamp" in t and
                window_start <= t["timestamp"] <= window_end
            ]
            
            if filtered_trades:
                logger.debug(
                    f"✅ Using WebSocket cache for trades: {symbol}",
                    cached_count=len(filtered_trades),
                    window_start=window_start.isoformat(),
                    window_end=window_end.isoformat()
                )
                # Convert dict trades to format expected by aggregation logic
                # Create simple objects with .timestamp, .price, .size attributes
                class TradeObj:
                    def __init__(self, d: Dict[str, Any]):
                        self.timestamp = d.get("timestamp")
                        self.price = d.get("price")
                        self.size = d.get("size")
                
                trade_list = [TradeObj(t) for t in filtered_trades]
                
                # Try to get quotes from WebSocket cache too
                cached_quotes = []
                try:
                    cached_quotes_raw = await stream_manager.get_recent_quotes(symbol, max_quotes=1000)
                    # Filter quotes by time window
                    cached_quotes = [
                        q for q in cached_quotes_raw
                        if isinstance(q, dict) and
                        "timestamp" in q and
                        window_start <= q["timestamp"] <= window_end
                    ]
                except Exception as e:
                    logger.debug(f"WebSocket quote cache miss, will use REST API: {symbol}", error=str(e))
                
                # Continue with aggregation (reuse existing logic)
                # Pass cached quotes if available, otherwise will fetch from REST API
                return await asyncio.to_thread(
                    _aggregate_trades_data,
                    trade_list=trade_list,
                    symbol=symbol,
                    window_start=window_start,
                    window_end=window_end,
                    client=client,
                    reference_nbbo=reference_nbbo,
                    cached_quotes=cached_quotes if cached_quotes else None
                )
        except Exception as e:
            logger.debug(
                f"WebSocket cache miss for trades, falling back to REST API: {symbol}",
                error=str(e)
            )
            # Fall through to REST API
    
    # REST API fallback (original implementation)
    return await asyncio.to_thread(
        _fetch_trades_in_window,
        client=client,
        symbol=symbol,
        window_start=window_start,
        window_end=window_end,
        reference_nbbo=reference_nbbo
    )


def _aggregate_trades_data(
    trade_list: List[Any],
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    client: StockHistoricalDataClient,
    reference_nbbo: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Aggregate trade data (shared logic for WebSocket cache and REST API paths).
    """
    if not trade_list:
        return None


def _fetch_trades_in_window(
    client: StockHistoricalDataClient,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    reference_nbbo: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch all trades in a time window and aggregate volume + order flow.

    Uses high-precision tick data to separate Buy volume from Sell volume.
    REST API implementation (fallback when WebSocket cache unavailable).

    NOTE: This is a SYNC function. Callers should use asyncio.to_thread() to avoid blocking.
    """
    try:
        # Ensure UTC
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)

        # 1. Fetch trades for the window
        trade_request = StockTradesRequest(
            symbol_or_symbols=[symbol],
            start=window_start,
            end=window_end,
            feed=DataFeed.SIP
        )
        trades = client.get_stock_trades(trade_request)
        if symbol not in trades.data or not trades[symbol]:
            return None
        trade_list = list(trades[symbol])
        
        # Aggregate trades (reuse existing logic)
        return _aggregate_trades_data(
            trade_list=trade_list,
            symbol=symbol,
            window_start=window_start,
            window_end=window_end,
            client=client,
            reference_nbbo=reference_nbbo
        )
    except Exception as e:
        logger.error(f"Error fetching trades in window: {e}", exc_info=True)
        return None


def _aggregate_trades_data(
    trade_list: List[Any],
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    client: StockHistoricalDataClient,
    reference_nbbo: Optional[Dict[str, Any]] = None,
    cached_quotes: Optional[List[Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """
    Aggregate trade data (shared logic for WebSocket cache and REST API paths).
    
    Args:
        cached_quotes: Optional list of quote dicts from WebSocket cache (if available)
    """
    if not trade_list:
        return None
    
    try:
        # 2. Fetch all quotes in this window to build a 'Moving Ruler'
        # Use WebSocket cache if available, otherwise fetch from REST API
        if cached_quotes:
            # Convert cached quote dicts to format expected by aggregation logic
            class QuoteObj:
                def __init__(self, d: Dict[str, Any]):
                    self.timestamp = d.get("timestamp")
                    self.bid_price = d.get("bid")
                    self.ask_price = d.get("ask")
            
            quote_list = [QuoteObj(q) for q in cached_quotes]
            logger.debug(f"✅ Using WebSocket cache for quotes: {symbol}", cached_count=len(quote_list))
        else:
            # REST API fallback
            quote_request = StockQuotesRequest(
                symbol_or_symbols=[symbol],
                start=window_start,
                end=window_end,
                feed=DataFeed.SIP
            )
            quotes_data = client.get_stock_quotes(quote_request)
            quote_list = list(quotes_data[symbol]) if symbol in quotes_data.data else []
        
        # Aggregate volume and balance
        total_volume = 0
        total_dollar_volume = 0
        trade_count = 0
        buy_vol = 0
        sell_vol = 0
        
        # Block participation tracking ($10k+)
        block_volume = 0
        
        # Tape Rhythm tracking (Splitting 4s into 2s chunks)
        mid_time = window_start + (window_end - window_start) / 2
        trades_first_half = 0
        trades_second_half = 0
        
        prices = [t.price for t in trade_list]
        max_p = max(prices) if prices else 0
        min_p = min(prices) if prices else 0
        range_p = round(max_p - min_p, 4)
        
        # Latency to first trade
        first_trade_ts = trade_list[0].timestamp if trade_list else None
        
        quote_idx = 0
        prev_price = None
        
        # Liveness Tracking (Max Gap)
        # Note: We track last_trade_ts but initialize it to first_trade_ts for inter-trade gaps
        # We DO NOT count start latency (0 -> first trade) as a gap, per user request (latency != fakeout)
        last_trade_ts = first_trade_ts
        max_trade_gap = 0.0
        
        # User Feedback: "I don't mind if the first trade takes time... extend the window"
        # So we skip start_gap penalty.
        
        for t in trade_list:
            size = t.size
            price = t.price
            trade_dollar_vol = size * price
            
            total_volume += size
            total_dollar_volume += trade_dollar_vol
            trade_count += 1
            
            # Block Tracking
            if trade_dollar_vol >= 10000:
                block_volume += size
            
            # Tape Rhythm
            if t.timestamp < mid_time:
                trades_first_half += 1
            else:
                trades_second_half += 1
            
            # Find the quote active at this trade's timestamp
            active_quote = None
            while quote_idx < len(quote_list) and quote_list[quote_idx].timestamp <= t.timestamp:
                active_quote = quote_list[quote_idx]
                quote_idx += 1
            
            # If we found a quote, use Lee-Ready (Quote Test)
            if active_quote and active_quote.bid_price and active_quote.ask_price:
                mid = (float(active_quote.bid_price) + float(active_quote.ask_price)) / 2
                if price > mid:
                    buy_vol += size
                elif price < mid:
                    sell_vol += size
                else:
                    # Mid-point tie: Fallback to Tick Test
                    if prev_price is not None:
                        if price > prev_price: buy_vol += size
                        elif price < prev_price: sell_vol += size
                        else:
                            buy_vol += size / 2
                            sell_vol += size / 2
                    else:
                        buy_vol += size / 2
                        sell_vol += size / 2
            else:
                # No quote found or dead premarket: Use Tick Test
                if prev_price is not None:
                    if price > prev_price: buy_vol += size
                    elif price < prev_price: sell_vol += size
                    else:
                        buy_vol += size / 2
                        sell_vol += size / 2
                else:
                    # First trade with no quote: Split
                    buy_vol += size / 2
                    sell_vol += size / 2
            
            # Liveness: Update Gap
            current_gap = (t.timestamp - last_trade_ts).total_seconds()
            max_trade_gap = max(max_trade_gap, current_gap)
            last_trade_ts = t.timestamp
            
            prev_price = price
        
        # Check Gap to Window End (did it die at the end?)
        if last_trade_ts:
            end_gap = (window_end - last_trade_ts).total_seconds()
            max_trade_gap = max(max_trade_gap, end_gap)
        elif not last_trade_ts:
            # No trades at all? Gap is the whole window
            max_trade_gap = (window_end - window_start).total_seconds()
        
        imbalance = 0.0
        if total_volume > 0:
            imbalance = round((buy_vol - sell_vol) / total_volume, 3)
        
        # Calculate Block Participation %
        block_pct = round((block_volume / total_volume) * 100, 1) if total_volume > 0 else 0
        
        # Calculate Tape Acceleration
        # (Trades in 2nd half / Trades in 1st half) - 1
        tape_accel = 0.0
        if trades_first_half > 0:
            tape_accel = round(((trades_second_half / trades_first_half) - 1) * 100, 1)
        elif trades_second_half > 0:
            tape_accel = 100.0 # From 0 to something
            
        # Calculate Tape Quality
        tape_quality = _calculate_tape_quality(trade_list, buy_vol, sell_vol, imbalance, block_pct)
            
        window_seconds = (window_end - window_start).total_seconds()
        
        # Normalization factor (e.g. if window is 3s, multiplier is 20x to reach a minute)
        norm_factor = 60 / window_seconds if window_seconds > 0 else 0
        
        normalized_minute_volume = total_volume * norm_factor
        normalized_minute_range = range_p * norm_factor
        
        return {
            "raw_volume": int(total_volume),
            "trade_count": int(trade_count),
            "buy_volume": int(buy_vol),
            "sell_volume": int(sell_vol),
            "tape_quality_score": tape_quality,
            "imbalance_ratio": imbalance,
            "range": range_p,
            "normalized_minute_range": round(normalized_minute_range, 4),
            "window_seconds": round(window_seconds, 2),
            "normalized_minute_volume": round(normalized_minute_volume, 0),
            "max_price": float(max_p),
            "total_dollar_volume": total_dollar_volume,
            "block_trade_pct": block_pct,
            "tape_acceleration_pct": tape_accel,
            "first_trade_ts": first_trade_ts,
            "max_trade_gap": round(max_trade_gap, 2)
        }
    except Exception as e:
        logger.debug(f"Error aggregating trades for {symbol} in window: {e}")
        return None
        
def _fetch_prior_history_stats(
    client: StockHistoricalDataClient,
    symbol: str,
    event_time: datetime,
    lookback_minutes: int = 10
) -> Optional[Dict[str, Any]]:
    """Fetch 10-minute baseline stats for volume and volatility."""
    try:
        start = event_time - timedelta(minutes=lookback_minutes)
        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Minute,
            start=start,
            end=event_time,
            feed=DataFeed.SIP
        )
        bars = client.get_stock_bars(request)
        if symbol not in bars.data or not bars[symbol]:
            return None
            
        bar_list = list(bars[symbol])
        volumes = [int(b.volume) for b in bar_list if b.volume]
        ranges = [float(b.high - b.low) for b in bar_list if b.high and b.low]
        trades = [int(b.trade_count) for b in bar_list if hasattr(b, 'trade_count') and b.trade_count]
        
        # Calculate avg spread over 10 mins (if quotes are available at minute boundaries)
        # For simplicity, we'll just return the basics for now, but we'll fetch a baseline spread below.
        
        return {
            "avg_volume": sum(volumes) / len(volumes) if volumes else 0,
            "avg_range": sum(ranges) / len(ranges) if ranges else 0,
            "avg_trade_count": sum(trades) / len(trades) if trades else 0,
            "has_data": len(volumes) > 0
        }
    except Exception as e:
        logger.debug(f"Error fetching prior history for {symbol}: {e}")
        return None


def _fetch_minute_bar(
    client: StockHistoricalDataClient,
    symbol: str,
    minute_start: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a single minute bar for a specific minute.
    
    Args:
        client: Alpaca market data client
        symbol: Ticker symbol
        minute_start: Start of the minute to fetch
        
    Returns:
        Bar data dict or None if no data
    """
    try:
        # Ensure UTC
        if minute_start.tzinfo is None:
            minute_start = minute_start.replace(tzinfo=timezone.utc)
        
        minute_end = minute_start + timedelta(minutes=1)
        
        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Minute,
            start=minute_start,
            end=minute_end,
            feed=DataFeed.SIP
        )
        
        bars = client.get_stock_bars(request)
        
        if symbol not in bars.data:
            return None
        
        bar_list = list(bars[symbol])
        if not bar_list:
            return None
        
        bar = bar_list[0]
        return {
            "volume": int(bar.volume) if bar.volume else None,
            "trade_count": int(bar.trade_count) if hasattr(bar, 'trade_count') and bar.trade_count else None,
            "close": float(bar.close) if bar.close else None,
            "vwap": float(bar.vwap) if bar.vwap else None,
        }
        
    except Exception as e:
        logger.debug(f"Error fetching minute bar for {symbol} at {minute_start}: {e}")
        return None


def _fetch_quote_at_time(
    client: StockHistoricalDataClient,
    symbol: str,
    target_time: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Fetch the quote closest to (at or before) the target time.
    
    Args:
        client: Alpaca market data client
        symbol: Ticker symbol
        target_time: Target timestamp
        
    Returns:
        Quote data dict or None if no data
    """
    try:
        # Ensure UTC
        if target_time.tzinfo is None:
            target_time = target_time.replace(tzinfo=timezone.utc)
        
        # Fetch quotes in a window around target time
        # Use 60 seconds for illiquid stocks that may not have frequent quotes
        start = target_time - timedelta(seconds=60)
        end = target_time + timedelta(seconds=5)
        
        request = StockQuotesRequest(
            symbol_or_symbols=[symbol],
            start=start,
            end=end,
            feed=DataFeed.SIP
        )
        
        quotes = client.get_stock_quotes(request)
        
        if symbol not in quotes.data:
            return None
        
        quote_list = list(quotes[symbol])
        if not quote_list:
            return None
        
        # Find quote closest to target_time (at or before)
        closest_quote = None
        for quote in quote_list:
            qt = quote.timestamp
            if qt.tzinfo is None:
                qt = qt.replace(tzinfo=timezone.utc)
            if qt <= target_time:
                closest_quote = quote
        
        if not closest_quote:
            closest_quote = quote_list[0]  # Fallback to first available
        
        bid = float(closest_quote.bid_price) if closest_quote.bid_price else None
        ask = float(closest_quote.ask_price) if closest_quote.ask_price else None
        bid_size = int(closest_quote.bid_size) if closest_quote.bid_size else None
        ask_size = int(closest_quote.ask_size) if closest_quote.ask_size else None
        
        spread = None
        spread_pct = None
        mid = None
        
        if bid and ask:
            spread = round(ask - bid, 4)
            mid = round((bid + ask) / 2, 4)
            if bid > 0:
                spread_pct = round((spread / bid) * 100, 3)
        
        return {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "spread_pct": spread_pct,
            "bid_size": bid_size,
            "ask_size": ask_size,
        }
        
    except Exception as e:
        logger.debug(f"Error fetching quote for {symbol} at {target_time}: {e}")
        return None


async def _get_stats_at_time(
    client: StockHistoricalDataClient,
    symbol: str,
    target_time: datetime,
    use_realtime_window: bool = False,
    window_end: datetime = None,
    reference_nbbo: Optional[Dict[str, Any]] = None,
    stream_manager: Optional["AlpacaMarketDataStreamManager"] = None
) -> Optional[VolumeStats]:
    """
    Get combined volume and quote stats at a specific time.
    
    Args:
        client: Alpaca market data client
        symbol: Ticker symbol
        target_time: Target timestamp (window start for real-time mode)
        use_realtime_window: If True, fetch trades in a window instead of minute bar
        window_end: End of window for real-time mode (e.g., received_at)
        stream_manager: Optional WebSocket stream manager for cached trades
        
    Returns:
        VolumeStats or None if no data
    """
    bar_data = None
    
    if use_realtime_window:
        # For stats_at_event: use real-time trades in a precise window
        # Use window_end if provided (published_at → received_at), else fallback to +5s
        if window_end is None:
            window_end = target_time + timedelta(seconds=5)
        
        # Ensure window_end is UTC
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)
        if target_time.tzinfo is None:
            target_time = target_time.replace(tzinfo=timezone.utc)
        
        trades_data = await _fetch_trades_in_window_async(
            client, symbol, target_time, window_end, 
            reference_nbbo=reference_nbbo,
            stream_manager=stream_manager
        )
        
        if trades_data:
            bar_data = {
                "volume": trades_data.get("raw_volume"),
                "trade_count": trades_data.get("trade_count"),
                "buy_volume": trades_data.get("buy_volume"),
                "sell_volume": trades_data.get("sell_volume"),
                "imbalance_ratio": trades_data.get("imbalance_ratio"),
                "range": trades_data.get("range"),
                "normalized_minute_range": trades_data.get("normalized_minute_range"),
                "normalized_minute_volume": trades_data.get("normalized_minute_volume"),
                "window_seconds": trades_data.get("window_seconds"),
                "max_price": trades_data.get("max_price"),
                "total_dollar_volume": trades_data.get("total_dollar_volume"),
                "block_trade_pct": trades_data.get("block_trade_pct"),
                "tape_acceleration_pct": trades_data.get("tape_acceleration_pct"),
                "first_trade_ts": trades_data.get("first_trade_ts"),
                "max_trade_gap": trades_data.get("max_trade_gap"),
                "tape_quality_score": trades_data.get("tape_quality_score"),
            }
    else:
        # Get minute bar for this minute
        minute_start = target_time.replace(second=0, microsecond=0)
        # Use to_thread to avoid blocking event loop
        bar_data = await asyncio.to_thread(_fetch_minute_bar, client, symbol, minute_start)
    
    # Get quote at Publication time (Widen window for dead stocks)
    # Search up to 5 minutes back to find the last valid Ask price before news
    start_lookup = target_time - timedelta(minutes=5)
    end_lookup = target_time # Strictly before or at news time
    
    quote_data = None
    try:
        request = StockQuotesRequest(
            symbol_or_symbols=[symbol],
            start=start_lookup,
            end=end_lookup,
            feed=DataFeed.SIP
        )
        # Use to_thread to avoid blocking event loop
        quotes = await asyncio.to_thread(client.get_stock_quotes, request)
        if symbol in quotes.data and quotes[symbol]:
            closest_quote = list(quotes[symbol])[-1] # Take the most recent quote before/at target
            quote_data = {
                "bid": float(closest_quote.bid_price),
                "ask": float(closest_quote.ask_price),
                "mid": round((float(closest_quote.bid_price) + float(closest_quote.ask_price)) / 2, 4),
                "spread": round(float(closest_quote.ask_price) - float(closest_quote.bid_price), 4),
                "bid_size": int(closest_quote.bid_size) if hasattr(closest_quote, 'bid_size') else None,
                "ask_size": int(closest_quote.ask_size) if hasattr(closest_quote, 'ask_size') else None
            }
    except: pass
    
    # If historical lookup failed, use injected reference NBBO
    if quote_data is None and use_realtime_window and reference_nbbo:
        quote_data = reference_nbbo
    
    if bar_data is None and quote_data is None:
        return None
    
    stats = VolumeStats(
        timestamp=target_time.isoformat(),
        volume=bar_data.get("volume") if bar_data else None,
        trade_count=bar_data.get("trade_count") if bar_data else None,
        buy_volume=bar_data.get("buy_volume") if bar_data else None,
        sell_volume=bar_data.get("sell_volume") if bar_data else None,
        imbalance_ratio=bar_data.get("imbalance_ratio") if bar_data else None,
        range=bar_data.get("range") if bar_data else None,
        bid=quote_data.get("bid") if quote_data else None,
        ask=quote_data.get("ask") if quote_data else None,
        mid=quote_data.get("mid") if quote_data else None,
        spread=quote_data.get("spread") if quote_data else None,
        bid_size=quote_data.get("bid_size") if quote_data else None,
        ask_size=quote_data.get("ask_size") if quote_data else None,
        normalized_minute_volume=bar_data.get("normalized_minute_volume") if bar_data else None,
        normalized_minute_range=bar_data.get("normalized_minute_range") if bar_data else None,
        window_seconds=bar_data.get("window_seconds") if bar_data else None,
        max_price=bar_data.get("max_price") if bar_data else None,
        total_dollar_volume=bar_data.get("total_dollar_volume") if bar_data else None,
        block_trade_pct=bar_data.get("block_trade_pct") if bar_data else None,
        tape_acceleration_pct=bar_data.get("tape_acceleration_pct") if bar_data else None,
        first_trade_ts=bar_data.get("first_trade_ts") if bar_data else None,
        max_trade_gap=bar_data.get("max_trade_gap") if bar_data else None,
        tape_quality_score=bar_data.get("tape_quality_score") if bar_data else None
    )
    
    return stats


async def _assess_surge_snapshot(
    client: StockHistoricalDataClient,
    symbol: str,
    event_time: datetime,
    window_end: datetime,
    prior_history: Optional[Dict[str, Any]],  # Can be None if fetch fails
    reference_nbbo: Optional[Dict[str, Any]],
    sector: Optional[str],
    stream_manager: Optional["AlpacaMarketDataStreamManager"] = None
) -> tuple[Optional[VolumeStats], Dict[str, Any], str]:
    """
    Fetch stats, calculate metrics, and classify surge for a specific window end.
    Returns: (stats_now, metrics_dict, move_type)
    """
    stats_now = await _get_stats_at_time(
        client=client, 
        symbol=symbol, 
        target_time=event_time, 
        use_realtime_window=True, 
        window_end=window_end, 
        reference_nbbo=reference_nbbo,
        stream_manager=stream_manager
    )
    
    # Extract Prior History (defensive - ensure prior_history is dict before calling .get())
    prior_avg_vol = prior_history.get("avg_volume", 0) if isinstance(prior_history, dict) else 0
    prior_avg_trades = prior_history.get("avg_trade_count", 0) if isinstance(prior_history, dict) else 0
    prior_avg_range = prior_history.get("avg_range", 0) if isinstance(prior_history, dict) else 0
    
    # Default outputs
    metrics = {
        "surge_multiplier": 0.0,
        "trade_count_multiplier": 0.0,
        "max_excursion_pct": 0.0,
        "imbalance_ratio": 0.0,
        "buy_volume": None,
        "sell_volume": None,
        "tape_quality_score": None,
        "trade_count_normalised": 0.0, 
        "block_trade_pct": 0.0,
        "tape_acceleration_pct": 0.0,
        "price_impact_bps": 0.0,
        "post_trade_bid_ratio": 0.0
    }
    move_type = "INACTIVE"
    
    current_vol = stats_now.volume if stats_now else 0
    norm_vol = stats_now.normalized_minute_volume if stats_now else None
    
    if current_vol is None or current_vol == 0:
        return stats_now, metrics, "INACTIVE"

    # 1. PILLAR: SIZE (Volume Multiplier)
    surge_score = 0.0
    if prior_avg_vol > 0 and norm_vol is not None:
        surge_score = round(norm_vol / prior_avg_vol, 2)
    elif norm_vol is not None:
        surge_score = round(norm_vol, 0)
    metrics["surge_multiplier"] = surge_score

    # 2. PILLAR: FREQUENCY (Trade Count Multiplier)
    actual_duration = (window_end - event_time).total_seconds()
    norm_factor = (60 / actual_duration) if actual_duration > 0 else 0
    norm_trades = ((stats_now.trade_count or 0) * norm_factor) if stats_now else 0
    metrics["trade_count_normalised"] = round(norm_trades, 1)

    trade_count_multiplier = 0.0
    if prior_avg_trades > 0:
        trade_count_multiplier = round(norm_trades / prior_avg_trades, 2)
    else:
        trade_count_multiplier = round(norm_trades, 1)
    metrics["trade_count_multiplier"] = trade_count_multiplier

    # 3. PILLAR: MOMENTUM (Max Excursion)
    pub_ask = stats_now.ask if stats_now else None 
    max_seen = stats_now.max_price if stats_now else None
    max_excursion = 0.0
    if pub_ask and max_seen and pub_ask > 0:
        max_excursion = round(((max_seen - pub_ask) / pub_ask) * 100, 3)
    metrics["max_excursion_pct"] = max_excursion

    # 4. PILLAR: CONVICTION (Buying Pressure)
    imbalance = stats_now.imbalance_ratio if stats_now else 0
    metrics["imbalance_ratio"] = imbalance
    pressure_pct = (imbalance + 1) / 2 * 100 if imbalance is not None else 50.0
    buy_volume = stats_now.buy_volume if stats_now else 0
    metrics["buy_volume"] = buy_volume
    metrics["sell_volume"] = stats_now.sell_volume

    # Tape Metrics
    metrics["tape_quality_score"] = getattr(stats_now, 'tape_quality_score', None)
    metrics["block_trade_pct"] = getattr(stats_now, 'block_trade_pct', 0.0)
    metrics["tape_acceleration_pct"] = getattr(stats_now, 'tape_acceleration_pct', 0.0)

    # Price Impact & Post Trade Bid Logic (Simplified copy)
    total_usd_vol = getattr(stats_now, 'total_dollar_volume', 0.0) or 0.0
    if stats_now and total_usd_vol > 0 and stats_now.mid:
        ref_mid = reference_nbbo.get("mid") if reference_nbbo else stats_now.ask
        if ref_mid and ref_mid > 0:
            price_move_pct = abs(stats_now.mid - ref_mid) / ref_mid
            metrics["price_impact_bps"] = round((price_move_pct * 10000) / (total_usd_vol / 100000), 2)
            
    bid_sz = getattr(stats_now, 'bid_size', 0.0) or 0.0
    ask_sz = getattr(stats_now, 'ask_size', 0.0) or 0.0
    if stats_now and bid_sz and ask_sz:
        metrics["post_trade_bid_ratio"] = round(bid_sz / ask_sz, 2)

    # --- CLASSIFICATION ---
    # Thresholds for surge detection
    MAX_EXCURSION_THRESHOLD = 1.0
    BUYING_PRESSURE_THRESHOLD = 70.0
    MIN_WINDOW_VOLUME_THRESHOLD = 5000
    MIN_TRADE_COUNT_THRESHOLD = 10  # Absolute threshold for dormant stocks

    if prior_avg_vol == 0:
        # DORMANT STOCK SURGE LOGIC
        # When there's no prior volume, we can't use relative multipliers.
        # Instead, check absolute thresholds. A dormant stock suddenly getting
        # massive activity (like JFBR: 19,673 shares, 43 trades, 6% excursion,
        # 86.6% buying pressure) IS a surge - arguably more significant.
        is_mom_ok = max_excursion >= MAX_EXCURSION_THRESHOLD
        has_sufficient_buying_pressure = pressure_pct >= BUYING_PRESSURE_THRESHOLD
        has_minimum_volume = (current_vol >= MIN_WINDOW_VOLUME_THRESHOLD)
        current_trade_count = stats_now.trade_count if stats_now and stats_now.trade_count else 0
        has_minimum_trades = (current_trade_count >= MIN_TRADE_COUNT_THRESHOLD)

        if is_mom_ok and has_sufficient_buying_pressure and has_minimum_volume and has_minimum_trades:
            # Dormant stock with strong absolute metrics = SURGE
            move_type = "SURGE"
        elif has_minimum_volume or has_minimum_trades:
            move_type = "NEW_ACTIVITY"
        else:
            move_type = "NEW_ACTIVITY"
    else:
        # NORMAL SURGE LOGIC (with prior volume for relative comparison)
        VOLUME_SURGE_THRESHOLD = 3.0
        TRADE_COUNT_THRESHOLD = 2.0

        is_size_ok = surge_score >= VOLUME_SURGE_THRESHOLD
        is_freq_ok = trade_count_multiplier >= TRADE_COUNT_THRESHOLD
        is_mom_ok = max_excursion >= MAX_EXCURSION_THRESHOLD

        # All sectors treated equally - no preferred sector lenience
        has_sufficient_buying_pressure = pressure_pct >= BUYING_PRESSURE_THRESHOLD
        # 70% buying pressure (imbalance ratio >= 0.4) - minimum buy volume removed (redundant with buying pressure + window volume)
        is_conv_ok = has_sufficient_buying_pressure

        # All sectors require minimum volume
        has_minimum_volume = (current_vol >= MIN_WINDOW_VOLUME_THRESHOLD)

        # Liveness metric completely removed - no longer used in surge detection

        # Single path: All surges require 5 signals (size, frequency, momentum, conviction, minimum volume)
        # 70% buying pressure is REQUIRED for all surges (no high volume exception)
        if is_size_ok and is_freq_ok and is_mom_ok and is_conv_ok and has_minimum_volume:
            # Surge path: size, frequency, momentum, conviction (70% buying pressure), minimum volume (no liveness, no sector lenience)
            move_type = "SURGE"
        elif is_size_ok and is_freq_ok and is_mom_ok and is_conv_ok and not has_minimum_volume:
            move_type = "STRENGTH"
        elif surge_score >= 1.5:
            move_type = "STRENGTH"
        elif surge_score >= 1.0:
            move_type = "NORMAL_ACTIVITY"
        else:
            move_type = "LOW_ACTIVITY"

    return stats_now, metrics, move_type


async def analyze_volume_around_event(
    client: StockHistoricalDataClient,
    symbol: str,
    event_time: datetime,
    received_at: datetime = None,
    reference_nbbo: Optional[Dict[str, Any]] = None,
    sector: Optional[str] = None,
    stream_manager: Optional[Any] = None  # AlpacaMarketDataStreamManager (optional for WebSocket cache)
) -> VolumeSurgeAnalysis:
    """
    Analyze volume/order flow with FAST POLLING (0.5s) to detect surges early.
    Non-blocking execution via asyncio.to_thread for all I/O bound synchronous calls.
    """
    logger.debug("Analyzing volume around event (Polling)", symbol=symbol, event_time=event_time.isoformat())
    
    # 1. Setup Windows (calculate immediately - no I/O)
    shock_window_seconds = 4.0
    shock_end_time = event_time + timedelta(seconds=shock_window_seconds)
    
    # Real-world reception latency
    real_window_seconds = 0
    if received_at:
        received_at_utc = received_at.replace(tzinfo=timezone.utc)
        real_window_seconds = (received_at_utc - event_time).total_seconds()

    # 2. Fetch Float Data and Prior History in PARALLEL (non-blocking)
    # Both are independent I/O operations - fetch concurrently to reduce latency
    float_shares_task = asyncio.create_task(asyncio.to_thread(_get_float_shares, symbol))
    prior_history_task = asyncio.create_task(asyncio.to_thread(_fetch_prior_history_stats, client, symbol, event_time, lookback_minutes=10))
    
    # Wait for both to complete (they run in parallel)
    float_shares, prior_history = await asyncio.gather(float_shares_task, prior_history_task, return_exceptions=True)
    
    # Handle exceptions gracefully
    if isinstance(float_shares, Exception):
        float_shares = None
    if isinstance(prior_history, Exception):
        prior_history = None
    
    # Shadow Spread Logic (calculated once)
    prior_avg_spread = 0.0
    
    def _fetch_shadow_spread():
        try:
            spread_start = event_time - timedelta(minutes=10)
            spread_req = StockQuotesRequest(symbol_or_symbols=[symbol], start=spread_start, end=event_time, feed=DataFeed.SIP)
            spread_quotes = client.get_stock_quotes(spread_req)
            if symbol in spread_quotes.data and spread_quotes[symbol]:
                all_spreads = [(float(q.ask_price) - float(q.bid_price)) for q in spread_quotes[symbol] if q.ask_price and q.bid_price]
                return sum(all_spreads) / len(all_spreads) if all_spreads else 0.0
        except: return 0.0
        return 0.0

    prior_avg_spread = await asyncio.to_thread(_fetch_shadow_spread)
    
    # 2.5. EARLY MOMENTUM ANALYSIS (1-second window - matches auto_trade.py conviction check)
    # This tracks the first 1 second after article publication for conviction-based sizing
    early_1s_move_pct = None
    early_1s_volume = None
    early_1s_trade_count = None
    moved_1_percent_in_1s = False
    early_1s_volume_surge = False
    conviction_level = "standard"

    early_1s_end = event_time + timedelta(seconds=1.0)
    try:
        early_stats = await _fetch_trades_in_window_async(
            client, symbol, event_time, early_1s_end,
            reference_nbbo=reference_nbbo,
            stream_manager=stream_manager
        )
        if early_stats:
            early_1s_volume = early_stats.get("raw_volume", 0)
            early_1s_trade_count = early_stats.get("trade_count", 0)

            # Calculate early move % (max excursion from first trade price)
            max_price = early_stats.get("max_price", 0)
            pub_ask = reference_nbbo.get("ask") if reference_nbbo else None

            if pub_ask and max_price and pub_ask > 0:
                early_1s_move_pct = round(((max_price - pub_ask) / pub_ask) * 100, 2)

                # Check 1% threshold for conviction
                if early_1s_move_pct >= 1.0:
                    moved_1_percent_in_1s = True

            # Check volume surge (500+ shares in 1 second = surge heuristic)
            if early_1s_volume and early_1s_volume >= 500:
                early_1s_volume_surge = True

            # Determine conviction level (mirrors auto_trade.py logic)
            if moved_1_percent_in_1s and early_1s_volume_surge:
                conviction_level = "very_high"  # $10k position
            elif moved_1_percent_in_1s:
                conviction_level = "high"  # $7.5k position
            else:
                conviction_level = "standard"  # $5k position

            logger.debug(
                "Early 1s momentum analysis",
                symbol=symbol,
                early_1s_move_pct=early_1s_move_pct,
                early_1s_volume=early_1s_volume,
                moved_1_percent_in_1s=moved_1_percent_in_1s,
                early_1s_volume_surge=early_1s_volume_surge,
                conviction_level=conviction_level
            )
    except Exception as e:
        logger.debug(f"Error in early 1s momentum analysis for {symbol}: {e}")

    # 3. CATCH-UP WINDOW ANALYSIS (Bottleneck #3)
    # If article arrived late (received_at > event_time), analyze the entire catch-up window first
    # This allows us to detect surges that occurred during the reception delay
    stats_now = None
    metrics = {}
    move_type = "INACTIVE"
    last_window_end = None
    use_catchup_window = False
    catchup_window_end = None
    
    if received_at:
        received_at_utc = received_at.replace(tzinfo=timezone.utc) if received_at.tzinfo is None else received_at
        event_time_utc = event_time.replace(tzinfo=timezone.utc) if event_time.tzinfo is None else event_time
        catchup_delay = (received_at_utc - event_time_utc).total_seconds()
        
        # Only use catch-up window if article arrived late (received_at > event_time)
        # And only if we're analyzing a reasonable window (0-60 seconds, not hours in the past from tests)
        # Allow 0-60 second windows: sometimes things are very quick, and in practice most articles
        # have a meaningful period (a few seconds) before we receive them where we can find signal
        if 0 < catchup_delay <= 60:  # Reasonable delay window: 0-60 seconds (exclude 0 since no window to analyze)
            use_catchup_window = True
            catchup_window_end = received_at_utc
            
            # Analyze entire catch-up window as one period
            logger.debug(
                "Analyzing catch-up window",
                symbol=symbol,
                event_time=event_time_utc.isoformat(),
                catchup_window_end=catchup_window_end.isoformat(),
                catchup_delay_seconds=round(catchup_delay, 2)
            )
            
            catchup_stats_now, catchup_metrics, catchup_move_type = await _assess_surge_snapshot(
                client=client,
                symbol=symbol,
                event_time=event_time_utc,
                window_end=catchup_window_end,
                prior_history=prior_history,
                reference_nbbo=reference_nbbo,
                sector=sector,
                stream_manager=stream_manager
            )
            
            stats_now = catchup_stats_now
            metrics = catchup_metrics
            move_type = catchup_move_type
            last_window_end = catchup_window_end
            
            # If surge detected in catch-up window, return immediately
            if move_type == "SURGE":
                logger.info(
                    f"⚡ SURGE DETECTED in catch-up window for {symbol} (delay: {catchup_delay:.2f}s)!",
                    multiplier=metrics.get("surge_multiplier"),
                    catchup_delay_seconds=round(catchup_delay, 2)
                )
                # Skip polling loop - surge already found
            else:
                # No surge in catch-up window - continue with normal polling
                # But only if we haven't already passed the normal 4-second window
                if catchup_window_end >= shock_end_time:
                    # We've already analyzed past the normal 4-second window
                    # Skip polling loop - use catch-up window results
                    logger.debug(
                        "Catch-up window extends past normal window, using catch-up results",
                        symbol=symbol,
                        catchup_window_end=catchup_window_end.isoformat(),
                        shock_end_time=shock_end_time.isoformat()
                    )
                else:
                    # Continue with normal polling from catchup_window_end forward
                    # The polling loop will start from where we left off
                    logger.debug(
                        "No surge in catch-up window, continuing with normal polling",
                        symbol=symbol,
                        catchup_window_end=catchup_window_end.isoformat()
                    )
    
    # 4. POLLING LOOP (only if no surge detected in catch-up window and we haven't passed the normal window)
    if move_type != "SURGE" and (not use_catchup_window or (use_catchup_window and catchup_window_end < shock_end_time)):
        while True:
            now = datetime.now(timezone.utc)
            
            # Check if we are past shock window
            if now >= shock_end_time:
                current_check_time = shock_end_time
                is_final = True
            else:
                current_check_time = now
                is_final = False
            
            # If we used catch-up window, start polling from catchup_window_end (not event_time)
            # Otherwise, start from event_time as normal
            if use_catchup_window and last_window_end:
                # Don't re-analyze the catch-up window
                if current_check_time <= last_window_end:
                    # Wait until we're past the catch-up window
                    if not is_final:
                        await asyncio.sleep(0.1)
                        continue
            
            # Don't check meaningful stats if < 0.5s of data unless it's final
            duration = (current_check_time - event_time).total_seconds()
            if duration < 0.5 and not is_final:
                await asyncio.sleep(0.1)
                continue
                
            # Assess Surge - Now async to support WebSocket cache
            stats_now, metrics, move_type = await _assess_surge_snapshot(
                client=client,
                symbol=symbol,
                event_time=event_time,
                window_end=current_check_time,
                prior_history=prior_history,
                reference_nbbo=reference_nbbo,
                sector=sector,
                stream_manager=stream_manager
            )
            last_window_end = current_check_time
            
            # EARLY EXIT: If Surge criteria met, break immediately
            if move_type == "SURGE":
                logger.info(f"⚡ EARLY SURGE DETECTED for {symbol} at T+{duration:.2f}s!", 
                            multiplier=metrics.get("surge_multiplier"))
                break
                
            # If final pass, break
            if is_final:
                break
                
            # Wait 0.1s before next poll (faster polling for early detection)
            # But ensure we don't overshoot shock_end too much
            remaining = (shock_end_time - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                continue # Loop back to hit is_final
            
            sleep_time = min(0.1, remaining)  # Changed from 0.5s to 0.1s for faster detection
            if sleep_time > 0.05:
                await asyncio.sleep(sleep_time)
            else:
                # yield to event loop
                await asyncio.sleep(0)  

    # 5. Populate Final Result (using stats from the loop)
    # LATE START LOGIC: If we finished (Final) but failed to surge, check for late start
    if move_type != "SURGE" and stats_now and stats_now.first_trade_ts:
        time_since_start = (stats_now.first_trade_ts - event_time).total_seconds()
        time_left_in_window = (shock_end_time - stats_now.first_trade_ts).total_seconds()
        
        # If activity started late (< 1.0s remaining) and we are at the end
        if time_left_in_window < 1.0 and last_window_end >= shock_end_time:
             # Extend!
             extension_seconds = 1.0
             logger.debug(f"🔄 LATE START DETECTED (Started at {time_since_start:.2f}s). Extending...", symbol=symbol)
             
             extended_end = shock_end_time + timedelta(seconds=extension_seconds)
             # Wait for extension
             now_ext = datetime.now(timezone.utc)
             wait_ext = (extended_end - now_ext).total_seconds()
             if wait_ext > 0:
                 await asyncio.sleep(wait_ext)
             
             # Final Re-Assess (async - supports WebSocket cache)
             stats_now, metrics, move_type = await _assess_surge_snapshot(
                 client=client,
                 symbol=symbol,
                 event_time=event_time,
                 window_end=extended_end,
                 prior_history=prior_history,
                 reference_nbbo=reference_nbbo,
                 sector=sector,
                 stream_manager=stream_manager
             )
             last_window_end = extended_end

    # 6. Additional Calculations for Dataclass
    current_vol = stats_now.volume if stats_now else 0
    prior_avg_vol = prior_history.get("avg_volume", 0) if isinstance(prior_history, dict) else 0
    prior_avg_range = prior_history.get("avg_range", 0) if isinstance(prior_history, dict) else 0
    
    current_spread = stats_now.spread if stats_now else 0.0
    spread_compression = 0.0
    if prior_avg_spread > 0 and current_spread is not None:
        spread_compression = round((1 - (current_spread / prior_avg_spread)) * 100, 1)

    vol_accel = None
    if metrics.get("surge_multiplier") and prior_avg_vol > 0:
        vol_accel = round((metrics["surge_multiplier"] - 1) * 100, 1)

    pub_ask = stats_now.ask if stats_now else None
    recv_ask = reference_nbbo.get("ask") if reference_nbbo else None
    ask_change = None
    if pub_ask is not None and recv_ask is not None and pub_ask > 0:
        ask_change = round(((recv_ask - pub_ask) / pub_ask) * 100, 3)

    window_range = stats_now.range if stats_now else 0
    norm_range = stats_now.normalized_minute_range if stats_now else 0
    vol_surge_ratio = None
    if norm_range and prior_avg_range > 0:
        vol_surge_ratio = round(norm_range / prior_avg_range, 2)

    return VolumeSurgeAnalysis(
        symbol=symbol,
        event_time=event_time.isoformat(),
        move_type=move_type,
        window_volume=current_vol,
        prior_avg_10min_volume=round(prior_avg_vol, 1),
        surge_multiplier=metrics.get("surge_multiplier"),
        window_volume_normalised=stats_now.normalized_minute_volume if stats_now else None,
        volume_accel_pct=vol_accel,
        trade_count=stats_now.trade_count if stats_now else 0,
        trade_count_normalised=metrics.get("trade_count_normalised"),
        prior_avg_10min_trade_count=round(prior_history.get("avg_trade_count", 0), 1) if isinstance(prior_history, dict) else 0.0,
        trade_count_multiplier=metrics.get("trade_count_multiplier"),
        max_excursion_pct=metrics.get("max_excursion_pct"),
        spread_compression_pct=spread_compression,
        pub_price=pub_ask,
        recv_price=recv_ask,
        ask_change_pct=ask_change,
        buy_volume=metrics.get("buy_volume"),
        sell_volume=metrics.get("sell_volume"),
        imbalance_ratio=metrics.get("imbalance_ratio"),
        block_trade_pct=metrics.get("block_trade_pct"),
        price_impact_bps=metrics.get("price_impact_bps"),
        tape_acceleration_pct=metrics.get("tape_acceleration_pct"),
        latency_to_first_trade=round((stats_now.first_trade_ts - event_time).total_seconds(), 3) if stats_now and stats_now.first_trade_ts else None,
        post_trade_bid_ratio=metrics.get("post_trade_bid_ratio"),
        # Early momentum (1-second window - matches auto_trade.py conviction check)
        early_1s_move_pct=early_1s_move_pct,
        early_1s_volume=early_1s_volume,
        early_1s_trade_count=early_1s_trade_count,
        moved_1_percent_in_1s=moved_1_percent_in_1s,
        early_1s_volume_surge=early_1s_volume_surge,
        conviction_level=conviction_level,
        # Legacy metadata
        pub_to_recv_seconds=round(real_window_seconds, 3),
        volatility_surge_ratio=vol_surge_ratio,
        prior_avg_minute_range=round(prior_avg_range, 4),
        prior_avg_10min_spread=round(prior_avg_spread, 4),
        pub_to_recv_range=window_range,
        pub_ask=pub_ask,
        recv_ask=recv_ask,
        max_trade_gap=stats_now.max_trade_gap if stats_now else None,
        tape_quality_score=metrics.get("tape_quality_score"),
        float_shares=float_shares,
        error=None
    )
def format_volume_stats_for_notification(analysis: VolumeSurgeAnalysis) -> List[str]:
    """Format the Four-Pillar Confluence for Telegram."""
    if not analysis or getattr(analysis, 'move_type', None) == "ERROR":
        err_msg = analysis.error if analysis else 'Unknown'
        return [f"   ⚠️ Analysis failed: {err_msg}"]
    
    move_type = analysis.move_type
    lines = ["", "📊 **FOUR-PILLAR CONFLUENCE:**"]
    
    # 1. VOLUME (Size)
    if move_type == "INACTIVE":
        lines.append(f"   💤 **Vol:** INACTIVE (No trades tracked)")
    elif move_type == "NEW_ACTIVITY":
        lines.append(f"   🚀 **Vol:** NEW ACTIVITY ({analysis.window_volume:,} shares)")
    else:
        # Determine emoji and Label for Size
        if move_type == "SURGE": label, emj = "SURGE", "🔥"
        elif move_type == "STRENGTH": label, emj = "STRENGTH", "📈"
        elif move_type == "NORMAL_ACTIVITY": label, emj = "NORMAL", "📊"
        else: label, emj = "LOW", "📉"
        
        lines.append(f"   {emj} **Size:** {analysis.surge_multiplier}x Vol ({analysis.window_volume:,} vs {analysis.prior_avg_10min_volume:,} avg)")

    # 2. FREQUENCY (Trades)
    if move_type not in ["INACTIVE", "NEW_ACTIVITY"]:
        lines.append(f"   🎟 **Freq:** {analysis.trade_count_multiplier}x Trades ({analysis.trade_count_normalised:.1f}/min vs {analysis.prior_avg_10min_trade_count:,}/min avg)")

    # 3. MOMENTUM (Max Excursion)
    if analysis.max_excursion_pct is not None:
        mom_emj = "⚡️" if analysis.max_excursion_pct >= 0.5 else "➡️"
        mom_line = f"   {mom_emj} **Mom:** {analysis.max_excursion_pct:+.2f}% Max Peak (vs Pub Ask)"
        lines.append(mom_line)

    # 4. CONVICTION (Buying Pressure)
    if analysis.imbalance_ratio is not None:
        pressure_pct = (analysis.imbalance_ratio + 1) / 2 * 100
        # Use same threshold as SURGE check (70% currently)
        SURGE_BUYING_PRESSURE_THRESHOLD = 70.0
        conv_emj = "🟢" if pressure_pct >= SURGE_BUYING_PRESSURE_THRESHOLD else "🟡" if pressure_pct >= 50 else "🔴"
        lines.append(f"   {conv_emj} **Convict:** {pressure_pct:.1f}% BUY ({analysis.buy_volume:,} shares)")

    return lines
    
    # 3. Volatility Surge
    if analysis.volatility_surge_ratio is not None:
        v_emoji = "⚡" if analysis.volatility_surge_ratio > 2 else "📊"
        lines.append(f"   {v_emoji} **Vol Spike:** {analysis.volatility_surge_ratio}x range vs 10m avg")

    # 4. Entry Clip (Slippage)
    if analysis.ask_change_pct is not None:
        lines.append(f"   💸 **Entry Clip:** +{analysis.ask_change_pct}% on Ask (in {analysis.pub_to_recv_seconds}s)")

    return lines
