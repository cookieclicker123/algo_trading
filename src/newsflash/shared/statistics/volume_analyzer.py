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
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
import pytz

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockQuotesRequest, StockTradesRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class VolumeStats:
    """Volume and quote statistics at a specific timestamp."""
    timestamp: str  # ISO format for serialization
    volume: Optional[int] = None
    trade_count: Optional[int] = None
    close: Optional[float] = None
    vwap: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None
    spread_pct: Optional[float] = None
    # For real-time windows (publication → reception)
    window_seconds: Optional[float] = None  # Actual window duration
    vol_per_second: Optional[float] = None  # Volume per second in window
    normalized_minute_volume: Optional[float] = None  # Volume normalized to 60s for comparison
    # Bid/Ask sizes (for microstructure analysis)
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class VolumeSurgeAnalysis:
    """
    Complete volume surge analysis result.
    
    Contains raw data at each interval plus computed metrics.
    NO FILTERING - just data for collection and future analysis.
    """
    # Ticker analyzed
    symbol: str
    
    # Event time (when article was received)
    event_time: str  # ISO format
    
    # Raw stats at each interval (None if no data)
    stats_3min_before: Optional[Dict[str, Any]] = None
    stats_2min_before: Optional[Dict[str, Any]] = None
    stats_1min_before: Optional[Dict[str, Any]] = None
    stats_30sec_before: Optional[Dict[str, Any]] = None  # Same minute as event, captured for completeness
    stats_at_event: Optional[Dict[str, Any]] = None
    
    # Computed metrics (for future filtering - NO ACTION TAKEN ON THESE NOW)
    prior_avg_volume: Optional[float] = None  # Average of 3/2/1 min before
    current_volume: Optional[int] = None  # Volume at event time
    
    # Surge type: "NEW_ACTIVITY" (was 0, now > 0) | "MULTIPLIER" (X times prior) | "NO_DATA"
    surge_type: str = "NO_DATA"
    
    # Surge score: 
    #   For NEW_ACTIVITY: current_volume (raw)
    #   For MULTIPLIER: current_volume / prior_avg_volume
    surge_score: Optional[float] = None
    
    # Did we have trading activity before the event?
    had_prior_activity: bool = False
    
    # Error information if fetch failed
    error: Optional[str] = None
    
    # Last reportable volume (even when current is NO_DATA)
    # This helps understand if ticker is illiquid or if it's an API limitation
    last_reportable_volume: Optional[int] = None
    last_reportable_volume_timestamp: Optional[str] = None  # ISO format
    
    # Average daily volume (20 trading days) - for market hours context
    avg_daily_volume_20d: Optional[int] = None
    
    # Session-specific average volume (for pre/post market context)
    # Average volume for this session type over last 20 trading days
    avg_session_volume: Optional[int] = None
    
    # Current session volume so far (real-time context)
    # Volume accumulated in current session up to event_time
    current_session_volume: Optional[int] = None
    
    # Publication → Reception window metrics (CRITICAL for detecting news-driven activity)
    pub_to_recv_seconds: Optional[float] = None  # Time between published_at and received_at
    pub_to_recv_volume: Optional[int] = None  # Actual volume in that window
    pub_to_recv_normalized_minute_volume: Optional[float] = None  # Normalized to 60s for comparison
    pub_to_recv_vol_per_second: Optional[float] = None  # Volume per second in window
    pub_to_recv_trade_count: Optional[int] = None  # Number of trades in window
    
    # Microstructure changes (spread tightening, bid/ask size changes)
    spread_tightening_pct: Optional[float] = None  # % change in spread from 3min before to at_event
    bid_size_change_pct: Optional[float] = None  # % change in bid_size
    ask_size_change_pct: Optional[float] = None  # % change in ask_size
    liquidity_ratio: Optional[float] = None  # (bid_size + ask_size) / spread (higher = more liquid)
    
    # Momentum Metrics (Price action velocity)
    momentum_3min_to_1min_pct: Optional[float] = None  # % change in mid price
    momentum_1min_to_event_pct: Optional[float] = None # % change in mid price
    momentum_3min_to_event_pct: Optional[float] = None # % change in mid price
    
    # Volume Acceleration (Rate of change of volume)
    volume_accel_3min_to_1min_pct: Optional[float] = None # % change in volume rate
    volume_accel_prior_to_event_pct: Optional[float] = None # % change from prior avg to current event volume
    
    # Order Flow / Trade Imbalance (Requires trade data)
    # Estimated from trade aggressor side (Buy vs Sell volume)
    order_flow_buy_volume: Optional[int] = None 
    order_flow_sell_volume: Optional[int] = None
    order_flow_imbalance_ratio: Optional[float] = None  # (Buy - Sell) / (Buy + Sell). Range [-1, 1].
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def _fetch_trades_in_window(
    client: StockHistoricalDataClient,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Fetch all trades in a time window and aggregate volume.
    
    This is used for real-time volume calculation when minute bars
    haven't completed yet. Returns total volume and trade count.
    
    Args:
        client: Alpaca market data client
        symbol: Ticker symbol
        window_start: Start of the window
        window_end: End of the window
        
    Returns:
        Dict with volume, trade_count, and window_seconds, or None if no data
    """
    try:
        # Ensure UTC
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)
        
        request = StockTradesRequest(
            symbol_or_symbols=[symbol],
            start=window_start,
            end=window_end,
            feed=DataFeed.SIP
        )
        
        trades = client.get_stock_trades(request)
        
        if symbol not in trades.data:
            return None
        
        trade_list = list(trades[symbol])
        if not trade_list:
            return None
        
        # Aggregate volume and count trades
        total_volume = sum(t.size for t in trade_list)
        trade_count = len(trade_list)
        
        # Calculate window duration in seconds
        window_seconds = (window_end - window_start).total_seconds()
        
        # Calculate volume per second for normalization
        vol_per_second = total_volume / window_seconds if window_seconds > 0 else 0
        
        # Normalize to 60-second equivalent (for comparison with minute bars)
        normalized_minute_volume = vol_per_second * 60
        
        return {
            "raw_volume": int(total_volume),
            "trade_count": int(trade_count),
            "window_seconds": round(window_seconds, 2),
            "vol_per_second": round(vol_per_second, 2),
            "normalized_minute_volume": round(normalized_minute_volume, 0),
        }
        
    except Exception as e:
        logger.debug(f"Error fetching trades for {symbol} in window: {e}")
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


def _get_stats_at_time(
    client: StockHistoricalDataClient,
    symbol: str,
    target_time: datetime,
    use_realtime_window: bool = False,
    window_end: datetime = None,
) -> Optional[VolumeStats]:
    """
    Get combined volume and quote stats at a specific time.
    
    Args:
        client: Alpaca market data client
        symbol: Ticker symbol
        target_time: Target timestamp (window start for real-time mode)
        use_realtime_window: If True, fetch trades in a window instead of minute bar
        window_end: End of window for real-time mode (e.g., received_at)
        
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
        
        trades_data = _fetch_trades_in_window(client, symbol, target_time, window_end)
        
        if trades_data:
            bar_data = {
                "volume": trades_data.get("raw_volume"),
                "trade_count": trades_data.get("trade_count"),
                # Store normalized volume for fair comparison with prior minute bars
                "normalized_minute_volume": trades_data.get("normalized_minute_volume"),
                "window_seconds": trades_data.get("window_seconds"),
                "vol_per_second": trades_data.get("vol_per_second"),
            }
    else:
        # Get minute bar for this minute
        minute_start = target_time.replace(second=0, microsecond=0)
        bar_data = _fetch_minute_bar(client, symbol, minute_start)
    
    # Get quote at this time
    quote_data = _fetch_quote_at_time(client, symbol, target_time)
    
    if bar_data is None and quote_data is None:
        return None
    
    stats = VolumeStats(
        timestamp=target_time.isoformat(),
        volume=bar_data.get("volume") if bar_data else None,
        trade_count=bar_data.get("trade_count") if bar_data else None,
        close=bar_data.get("close") if bar_data else None,
        vwap=bar_data.get("vwap") if bar_data else None,
        bid=quote_data.get("bid") if quote_data else None,
        ask=quote_data.get("ask") if quote_data else None,
        mid=quote_data.get("mid") if quote_data else None,
        spread=quote_data.get("spread") if quote_data else None,
        spread_pct=quote_data.get("spread_pct") if quote_data else None,
        # Real-time window metrics (for publication → reception window)
        window_seconds=bar_data.get("window_seconds") if bar_data else None,
        vol_per_second=bar_data.get("vol_per_second") if bar_data else None,
        normalized_minute_volume=bar_data.get("normalized_minute_volume") if bar_data else None,
        # Bid/Ask sizes (for microstructure analysis)
        bid_size=quote_data.get("bid_size") if quote_data else None,
        ask_size=quote_data.get("ask_size") if quote_data else None,
    )
    
    return stats


async def analyze_volume_around_event(
    client: StockHistoricalDataClient,
    symbol: str,
    event_time: datetime,
    received_at: datetime = None,
) -> VolumeSurgeAnalysis:
    """
    Analyze volume and quotes at key intervals around an event.
    
    STATELESS FUNCTION - all dependencies passed as parameters.
    NO FILTERING - just data collection for future threshold derivation.
    
    Args:
        client: Alpaca StockHistoricalDataClient (injected)
        symbol: Ticker symbol to analyze
        event_time: When the event occurred (e.g., article published_at)
        received_at: When we received the event (optional, for precise window)
        
    Returns:
        VolumeSurgeAnalysis with raw stats and computed metrics
    """
    # Ensure event_time is UTC
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    
    logger.debug(
        "Analyzing volume around event",
        symbol=symbol,
        event_time=event_time.isoformat()
    )
    
    try:
        # Define intervals
        intervals = {
            "3min_before": event_time - timedelta(minutes=3),
            "2min_before": event_time - timedelta(minutes=2),
            "1min_before": event_time - timedelta(minutes=1),
            "30sec_before": event_time - timedelta(seconds=30),
            "at_event": event_time,
        }
        
        # Fetch stats for each interval (use minute bars for prior intervals)
        stats_3min = _get_stats_at_time(client, symbol, intervals["3min_before"])
        stats_2min = _get_stats_at_time(client, symbol, intervals["2min_before"])
        stats_1min = _get_stats_at_time(client, symbol, intervals["1min_before"])
        stats_30sec = _get_stats_at_time(client, symbol, intervals["30sec_before"])
        
        # For stats_at_event, use real-time trade window: published_at → received_at
        stats_now = _get_stats_at_time(
            client, symbol, intervals["at_event"], 
            use_realtime_window=True, 
            window_end=received_at  # Precise window from publication to receipt
        )
        
        # Calculate prior average volume (from 3/2/1 min before)
        prior_volumes = []
        for stats in [stats_3min, stats_2min, stats_1min]:
            if stats and stats.volume is not None:
                prior_volumes.append(stats.volume)
        
        prior_avg = sum(prior_volumes) / len(prior_volumes) if prior_volumes else None
        current_vol = stats_now.volume if stats_now else None
        
        # Determine surge type and score
        had_prior = len(prior_volumes) > 0 and any(v > 0 for v in prior_volumes)
        
        # Find last reportable volume (even when current is NO_DATA)
        # This helps distinguish between "illiquid ticker" vs "API limitation"
        last_reportable_volume = None
        last_reportable_timestamp = None
        
        # Check all stats in reverse order (most recent first)
        for stats, label, timestamp in [
            (stats_now, "at_event", intervals["at_event"]),
            (stats_30sec, "30sec_before", intervals["30sec_before"]),
            (stats_1min, "1min_before", intervals["1min_before"]),
            (stats_2min, "2min_before", intervals["2min_before"]),
            (stats_3min, "3min_before", intervals["3min_before"]),
        ]:
            if stats and stats.volume is not None and stats.volume > 0:
                last_reportable_volume = stats.volume
                last_reportable_timestamp = timestamp.isoformat()
                break
        
        # Calculate session-specific average volumes using minute bars
        # This gives context on whether ticker is normally liquid in this session
        avg_daily_volume_20d = None
        avg_session_volume = None
        current_session_volume = None
        
        # Determine current session from event_time
        from ...utils.brokerage.session_detector import get_market_session_from_timestamp
        session_name, _ = get_market_session_from_timestamp(event_time)
        
        if session_name != "closed":
            try:
                # Calculate session-specific averages using minute bars
                # Fetch minute bars for last 30 days (to ensure we get 20 trading days)
                et_tz = pytz.timezone("US/Eastern")
                event_et = event_time.astimezone(et_tz) if event_time.tzinfo else et_tz.localize(event_time)
                
                end_date = event_et.date()
                start_date = end_date - timedelta(days=30)  # 30 days to ensure we get 20 trading days
                
                # Fetch minute bars for the entire period
                minute_bars_request = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=TimeFrame.Minute,
                    start=datetime.combine(start_date, datetime.min.time()).replace(tzinfo=et_tz),
                    end=datetime.combine(end_date, datetime.max.time()).replace(tzinfo=et_tz),
                    feed=DataFeed.SIP
                )
                minute_bars = client.get_stock_bars(minute_bars_request)
                
                if symbol in minute_bars.data:
                    bars = list(minute_bars[symbol])
                    
                    # Define session boundaries (ET timezone)
                    # Premarket: 4:00 AM - 9:30 AM (330 minutes)
                    # Market: 9:30 AM - 4:00 PM (390 minutes)
                    # Postmarket: 4:00 PM - 8:00 PM (240 minutes)
                    
                    session_volumes = []  # List of session totals for last 20 days
                    
                    # Group bars by date and session
                    bars_by_date = {}
                    for bar in bars:
                        bar_et = bar.timestamp.astimezone(et_tz) if bar.timestamp.tzinfo else et_tz.localize(bar.timestamp)
                        date_key = bar_et.date()
                        if date_key not in bars_by_date:
                            bars_by_date[date_key] = []
                        bars_by_date[date_key].append((bar_et, bar))
                    
                    # Process last 20 trading days (most recent first)
                    sorted_dates = sorted(bars_by_date.keys(), reverse=True)[:20]
                    
                    for date_key in sorted_dates:
                        date_bars = bars_by_date[date_key]
                        
                        # Calculate volume for each session on this date
                        premarket_vol = 0
                        market_vol = 0
                        postmarket_vol = 0
                        
                        for bar_et, bar in date_bars:
                            hour = bar_et.hour
                            minute = bar_et.minute
                            
                            # Premarket: 4:00 - 9:30
                            if 4 <= hour < 9 or (hour == 9 and minute < 30):
                                premarket_vol += int(bar.volume) if bar.volume else 0
                            # Market: 9:30 - 16:00
                            elif (hour == 9 and minute >= 30) or (10 <= hour < 16):
                                market_vol += int(bar.volume) if bar.volume else 0
                            # Postmarket: 16:00 - 20:00
                            elif 16 <= hour < 20:
                                postmarket_vol += int(bar.volume) if bar.volume else 0
                        
                        # Store session volume for the session we're currently in
                        if session_name == "premarket" and premarket_vol > 0:
                            session_volumes.append(premarket_vol)
                        elif session_name == "market_hours" and market_vol > 0:
                            session_volumes.append(market_vol)
                        elif session_name == "postmarket" and postmarket_vol > 0:
                            session_volumes.append(postmarket_vol)
                    
                    # Calculate average session volume (last 20 days)
                    if session_volumes:
                        avg_session_volume = int(sum(session_volumes) / len(session_volumes))
                    
                    # Calculate current session volume so far (for real-time context)
                    # Sum up minute bars from session start until event_time
                    current_date = event_et.date()
                    if current_date in bars_by_date:
                        for bar_et, bar in bars_by_date[current_date]:
                            # Only count bars up to event_time
                            if bar_et <= event_et:
                                hour = bar_et.hour
                                minute = bar_et.minute
                                
                                # Check if bar is in current session
                                in_session = False
                                if session_name == "premarket" and (4 <= hour < 9 or (hour == 9 and minute < 30)):
                                    in_session = True
                                elif session_name == "market_hours" and ((hour == 9 and minute >= 30) or (10 <= hour < 16)):
                                    in_session = True
                                elif session_name == "postmarket" and (16 <= hour < 20):
                                    in_session = True
                                
                                if in_session:
                                    current_session_volume = (current_session_volume or 0) + (int(bar.volume) if bar.volume else 0)
                    
                    # For market hours, also calculate 20-day daily average (all sessions combined)
                    if session_name == "market_hours":
                        daily_volumes = []
                        for date_key in sorted_dates:
                            date_bars = bars_by_date[date_key]
                            daily_total = sum(int(bar.volume) if bar.volume else 0 for _, bar in date_bars)
                            if daily_total > 0:
                                daily_volumes.append(daily_total)
                        if daily_volumes:
                            avg_daily_volume_20d = int(sum(daily_volumes) / len(daily_volumes))
                    
            except Exception as avg_error:
                logger.debug(
                    "Failed to fetch session-specific average volume",
                    symbol=symbol,
                    session=session_name,
                    error=str(avg_error)
                )
        
        if current_vol is None and not had_prior:
            surge_type = "NO_DATA"
            surge_score = None
        elif current_vol is None and had_prior:
            # Have prior data but no current - use prior avg as indicator
            surge_type = "PRIOR_ONLY"
            surge_score = round(prior_avg, 0) if prior_avg else None
        elif not had_prior or prior_avg == 0 or prior_avg is None:
            # No prior activity - this is NEW_ACTIVITY pattern (like WYFI)
            surge_type = "NEW_ACTIVITY"
            surge_score = float(current_vol) if current_vol else None
        else:
            # Had prior activity - calculate multiplier
            surge_type = "MULTIPLIER"
            surge_score = round(current_vol / prior_avg, 2) if prior_avg > 0 else None
        
        # Calculate publication → reception window metrics (CRITICAL for news-driven activity detection)
        pub_to_recv_seconds = None
        pub_to_recv_volume = None
        pub_to_recv_normalized_minute_volume = None
        pub_to_recv_vol_per_second = None
        pub_to_recv_trade_count = None
        
        if received_at and stats_now:
            # Calculate time window
            # received_at should already be a datetime, but handle string case
            if isinstance(received_at, str):
                received_at = datetime.fromisoformat(received_at.replace('Z', '+00:00'))
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
            
            pub_to_recv_seconds = (received_at - event_time).total_seconds()
            
            # Extract window metrics from stats_at_event (if it was a real-time window)
            if stats_now.window_seconds is not None:
                pub_to_recv_volume = stats_now.volume
                pub_to_recv_normalized_minute_volume = stats_now.normalized_minute_volume
                pub_to_recv_vol_per_second = stats_now.vol_per_second
                pub_to_recv_trade_count = stats_now.trade_count
        
        # Calculate microstructure changes (spread tightening, bid/ask size changes)
        spread_tightening_pct = None
        bid_size_change_pct = None
        ask_size_change_pct = None
        liquidity_ratio = None
        
        if stats_3min and stats_now:
            # Spread tightening: % change in spread from 3min before to at_event
            spread_3min = stats_3min.spread
            spread_now = stats_now.spread
            if spread_3min and spread_now and spread_3min > 0:
                spread_tightening_pct = round(((spread_3min - spread_now) / spread_3min) * 100, 2)
            
            # Bid/Ask size changes
            bid_size_3min = stats_3min.bid_size
            ask_size_3min = stats_3min.ask_size
            bid_size_now = stats_now.bid_size
            ask_size_now = stats_now.ask_size
            
            if bid_size_3min and bid_size_now and bid_size_3min > 0:
                bid_size_change_pct = round(((bid_size_now - bid_size_3min) / bid_size_3min) * 100, 2)
            if ask_size_3min and ask_size_now and ask_size_3min > 0:
                ask_size_change_pct = round(((ask_size_now - ask_size_3min) / ask_size_3min) * 100, 2)
            
            # Liquidity ratio: (bid_size + ask_size) / spread (higher = more liquid)
            if stats_now.bid_size and stats_now.ask_size and stats_now.spread and stats_now.spread > 0:
                total_size = (stats_now.bid_size or 0) + (stats_now.ask_size or 0)
                liquidity_ratio = round(total_size / stats_now.spread, 2) if total_size > 0 else None
        
        # --- Momentum Metrics ---
        mom_3m_1m = None
        mom_1m_evt = None
        mom_3m_evt = None
        
        mid_3m = stats_3min.mid if stats_3min else None
        mid_1m = stats_1min.mid if stats_1min else None
        mid_now = stats_now.mid if stats_now else None
        
        if mid_3m and mid_1m and mid_3m > 0:
             mom_3m_1m = round(((mid_1m - mid_3m) / mid_3m), 6) # Use 4-6 decimal places for pct
             
        if mid_1m and mid_now and mid_1m > 0:
             mom_1m_evt = round(((mid_now - mid_1m) / mid_1m), 6)
             
        if mid_3m and mid_now and mid_3m > 0:
             mom_3m_evt = round(((mid_now - mid_3m) / mid_3m), 6)
             
        # --- Volume Acceleration ---
        # We need volume rates (shares per second or normalized minute volume)
        # stats_now has normalized_minute_volume.
        # stats_3min/1min are full minute bars, so their volume IS the normalized minute volume.
        
        vol_accel_3m_1m = None
        vol_accel_prior_evt = None
        
        vol_3m = stats_3min.volume if stats_3min else None
        vol_1m = stats_1min.volume if stats_1min else None
        vol_now_norm = stats_now.normalized_minute_volume if stats_now else None
        
        if vol_3m is not None and vol_1m is not None and vol_3m > 0:
            vol_accel_3m_1m = round(((vol_1m - vol_3m) / vol_3m) * 100, 2) # As percentage
            
        if prior_avg and prior_avg > 0 and vol_now_norm is not None:
            vol_accel_prior_evt = round(((vol_now_norm - prior_avg) / prior_avg) * 100, 2) # As percentage

        result = VolumeSurgeAnalysis(
            symbol=symbol,
            event_time=event_time.isoformat(),
            stats_3min_before=stats_3min.to_dict() if stats_3min else None,
            stats_2min_before=stats_2min.to_dict() if stats_2min else None,
            stats_1min_before=stats_1min.to_dict() if stats_1min else None,
            stats_30sec_before=stats_30sec.to_dict() if stats_30sec else None,
            stats_at_event=stats_now.to_dict() if stats_now else None,
            prior_avg_volume=round(prior_avg, 2) if prior_avg else None,
            current_volume=current_vol,
            surge_type=surge_type,
            surge_score=surge_score,
            had_prior_activity=had_prior,
            last_reportable_volume=last_reportable_volume,
            last_reportable_volume_timestamp=last_reportable_timestamp,
            avg_daily_volume_20d=avg_daily_volume_20d,
            avg_session_volume=avg_session_volume,
            current_session_volume=current_session_volume,
            # Publication → Reception window metrics
            pub_to_recv_seconds=round(pub_to_recv_seconds, 3) if pub_to_recv_seconds else None,
            pub_to_recv_volume=pub_to_recv_volume,
            pub_to_recv_normalized_minute_volume=round(pub_to_recv_normalized_minute_volume, 2) if pub_to_recv_normalized_minute_volume else None,
            pub_to_recv_vol_per_second=round(pub_to_recv_vol_per_second, 2) if pub_to_recv_vol_per_second else None,
            pub_to_recv_trade_count=pub_to_recv_trade_count,
            # Microstructure changes
            spread_tightening_pct=spread_tightening_pct,
            bid_size_change_pct=bid_size_change_pct,
            ask_size_change_pct=ask_size_change_pct,
            liquidity_ratio=liquidity_ratio,
            # Momentum
            momentum_3min_to_1min_pct=mom_3m_1m,
            momentum_1min_to_event_pct=mom_1m_evt,
            momentum_3min_to_event_pct=mom_3m_evt,
            # Volume Acceleration
            volume_accel_3min_to_1min_pct=vol_accel_3m_1m,
            volume_accel_prior_to_event_pct=vol_accel_prior_evt,
            # Order Flow (Placeholder for now)
            order_flow_buy_volume=None,
            order_flow_sell_volume=None,
            order_flow_imbalance_ratio=None,
        )
        
        logger.info(
            "Volume analysis complete",
            symbol=symbol,
            surge_type=surge_type,
            surge_score=surge_score,
            current_volume=current_vol,
            prior_avg=prior_avg,
            had_prior=had_prior
        )
        
        return result
        
    except Exception as e:
        logger.error(
            "Error analyzing volume",
            symbol=symbol,
            error=str(e),
            exc_info=True
        )
        return VolumeSurgeAnalysis(
            symbol=symbol,
            event_time=event_time.isoformat(),
            surge_type="ERROR",
            error=str(e)
        )


def format_volume_stats_for_notification(analysis: VolumeSurgeAnalysis) -> List[str]:
    """
    Format volume analysis for inclusion in Telegram notification.
    
    NO FILTERING - just formats the collected data for display.
    
    Args:
        analysis: VolumeSurgeAnalysis result
        
    Returns:
        List of formatted message lines
    """
    if not analysis:
        return []
    
    lines = ["", "📊 VOLUME ANALYSIS:"]
    
    # Surge summary
    if analysis.surge_type == "NEW_ACTIVITY":
        vol_str = f"{analysis.surge_score:,.0f}" if analysis.surge_score else "N/A"
        lines.append(f"   🚀 NEW ACTIVITY: {vol_str} shares (no prior trades)")
    elif analysis.surge_type == "MULTIPLIER":
        score_str = f"{analysis.surge_score:.1f}x" if analysis.surge_score else "N/A"
        lines.append(f"   📈 SURGE: {score_str} prior avg volume")
    elif analysis.surge_type == "PRIOR_ONLY":
        avg_str = f"{analysis.surge_score:,.0f}" if analysis.surge_score else "N/A"
        lines.append(f"   📊 Prior Avg Vol: {avg_str} (current minute incomplete)")
    elif analysis.surge_type == "NO_DATA":
        lines.append("   ⚠️ No volume data available")
    elif analysis.surge_type == "ERROR":
        lines.append(f"   ⚠️ Error: {analysis.error or 'Unknown'}")
    
    # Current volume
    if analysis.current_volume is not None:
        lines.append(f"   📊 Current Vol: {analysis.current_volume:,}")
    
    if analysis.prior_avg_volume is not None:
        lines.append(f"   📉 Prior Avg: {analysis.prior_avg_volume:,.0f}")
    
    # NBBO at event time
    now_stats = analysis.stats_at_event
    if now_stats:
        if now_stats.get("spread") is not None:
            lines.append(f"   💱 Spread: ${now_stats['spread']:.3f} ({now_stats.get('spread_pct', 0):.2f}%)")
        if now_stats.get("bid") is not None and now_stats.get("ask") is not None:
            lines.append(f"   📊 Bid: ${now_stats['bid']:.2f} | Ask: ${now_stats['ask']:.2f}")
    
    return lines
