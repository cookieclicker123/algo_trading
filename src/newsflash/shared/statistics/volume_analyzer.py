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
from alpaca.data.requests import StockBarsRequest, StockQuotesRequest
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
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


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
        
        # Fetch quotes in a small window around target time
        start = target_time - timedelta(seconds=10)
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
        }
        
    except Exception as e:
        logger.debug(f"Error fetching quote for {symbol} at {target_time}: {e}")
        return None


def _get_stats_at_time(
    client: StockHistoricalDataClient,
    symbol: str,
    target_time: datetime,
) -> Optional[VolumeStats]:
    """
    Get combined volume and quote stats at a specific time.
    
    Args:
        client: Alpaca market data client
        symbol: Ticker symbol
        target_time: Target timestamp
        
    Returns:
        VolumeStats or None if no data
    """
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
    )
    
    return stats


async def analyze_volume_around_event(
    client: StockHistoricalDataClient,
    symbol: str,
    event_time: datetime,
) -> VolumeSurgeAnalysis:
    """
    Analyze volume and quotes at key intervals around an event.
    
    STATELESS FUNCTION - all dependencies passed as parameters.
    NO FILTERING - just data collection for future threshold derivation.
    
    Args:
        client: Alpaca StockHistoricalDataClient (injected)
        symbol: Ticker symbol to analyze
        event_time: When the event occurred (e.g., article received)
        
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
        
        # Fetch stats for each interval
        stats_3min = _get_stats_at_time(client, symbol, intervals["3min_before"])
        stats_2min = _get_stats_at_time(client, symbol, intervals["2min_before"])
        stats_1min = _get_stats_at_time(client, symbol, intervals["1min_before"])
        stats_30sec = _get_stats_at_time(client, symbol, intervals["30sec_before"])
        stats_now = _get_stats_at_time(client, symbol, intervals["at_event"])
        
        # Calculate prior average volume (from 3/2/1 min before)
        prior_volumes = []
        for stats in [stats_3min, stats_2min, stats_1min]:
            if stats and stats.volume is not None:
                prior_volumes.append(stats.volume)
        
        prior_avg = sum(prior_volumes) / len(prior_volumes) if prior_volumes else None
        current_vol = stats_now.volume if stats_now else None
        
        # Determine surge type and score
        had_prior = len(prior_volumes) > 0 and any(v > 0 for v in prior_volumes)
        
        if current_vol is None:
            surge_type = "NO_DATA"
            surge_score = None
        elif not had_prior or prior_avg == 0 or prior_avg is None:
            # No prior activity - this is NEW_ACTIVITY pattern (like WYFI)
            surge_type = "NEW_ACTIVITY"
            surge_score = float(current_vol) if current_vol else None
        else:
            # Had prior activity - calculate multiplier
            surge_type = "MULTIPLIER"
            surge_score = round(current_vol / prior_avg, 2) if prior_avg > 0 else None
        
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
