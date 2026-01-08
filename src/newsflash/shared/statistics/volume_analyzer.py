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
from typing import Optional, Dict, Any, List

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

        # 2. Fetch all quotes in this window to build a 'Moving Ruler'
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
            
            prev_price = price
        
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
            "imbalance_ratio": imbalance,
            "range": range_p,
            "normalized_minute_range": round(normalized_minute_range, 4),
            "window_seconds": round(window_seconds, 2),
            "normalized_minute_volume": round(normalized_minute_volume, 0),
            "max_price": float(max_p),
            "total_dollar_volume": total_dollar_volume,
            "block_trade_pct": block_pct,
            "tape_acceleration_pct": tape_accel,
            "first_trade_ts": first_trade_ts
        }
    except Exception as e:
        logger.debug(f"Error fetching trades for {symbol} in window: {e}")
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


def _get_stats_at_time(
    client: StockHistoricalDataClient,
    symbol: str,
    target_time: datetime,
    use_realtime_window: bool = False,
    window_end: datetime = None,
    reference_nbbo: Optional[Dict[str, Any]] = None,
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
        
        trades_data = _fetch_trades_in_window(client, symbol, target_time, window_end, reference_nbbo=reference_nbbo)
        
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
            }
    else:
        # Get minute bar for this minute
        minute_start = target_time.replace(second=0, microsecond=0)
        bar_data = _fetch_minute_bar(client, symbol, minute_start)
    
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
        quotes = client.get_stock_quotes(request)
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
        first_trade_ts=bar_data.get("first_trade_ts") if bar_data else None
    )
    
    return stats


async def analyze_volume_around_event(
    client: StockHistoricalDataClient,
    symbol: str,
    event_time: datetime,
    received_at: datetime = None,
    reference_nbbo: Optional[Dict[str, Any]] = None,
    sector: Optional[str] = None
) -> VolumeSurgeAnalysis:
    """
    Analyze volume and quotes at key intervals around an event.
    
    Focuses on the "Third Confluence":
    1. News (AI)
    2. Volume Acceleration
    3. Order Flow (Buying Pressure)
    4. Price Action (Execution Cost)
    """
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    
    logger.debug("Analyzing volume around event", symbol=symbol, event_time=event_time.isoformat())
    
    try:
        # 🚀 THE SHOCK WINDOW PRINCIPLE:
        # We always analyze the FIRST 4 SECONDS of the move for classification and pillars.
        # This prevents dilution of intensity (Momentum/Pressure) caused by latency windows.
        shock_window_seconds = 4.0
        shock_end_time = event_time + timedelta(seconds=shock_window_seconds)
        
        # Determine real-world reception latency for metadata
        real_window_seconds = 0
        if received_at:
            received_at_utc = received_at.replace(tzinfo=timezone.utc)
            real_window_seconds = (received_at_utc - event_time).total_seconds()
        
        # If we are faster than the shock window, wait to get enough 'Tape'
        now = datetime.now(timezone.utc)
        if now < shock_end_time:
            wait_time = (shock_end_time - now).total_seconds()
            if wait_time > 0:
                logger.debug(f"Waiting {round(wait_time, 2)}s for Shock Window", symbol=symbol)
                await asyncio.sleep(wait_time)

        # Fetch 10-minute prior history baseline
        prior_history = _fetch_prior_history_stats(client, symbol, event_time, lookback_minutes=10)
        prior_avg_vol = prior_history.get("avg_volume", 0) if prior_history else 0
        prior_avg_range = prior_history.get("avg_range", 0) if prior_history else 0
        prior_avg_trades = prior_history.get("avg_trade_count", 0) if prior_history else 0
        
        # 🎯 FETCH SHOCK STATS (Strict 4s window)
        stats_now = _get_stats_at_time(
            client=client, 
            symbol=symbol, 
            target_time=event_time, 
            use_realtime_window=True, 
            window_end=shock_end_time, 
            reference_nbbo=reference_nbbo
        )
        
        current_vol = stats_now.volume if stats_now else None
        norm_vol = stats_now.normalized_minute_volume if stats_now else None
        
        # 1. PILLAR: SIZE (Volume Multiplier)
        surge_score = 0.0
        if prior_avg_vol > 0 and norm_vol is not None:
            surge_score = round(norm_vol / prior_avg_vol, 2)
        elif norm_vol is not None:
            surge_score = round(norm_vol, 0)

        # 2. PILLAR: FREQUENCY (Trade Count Multiplier)
        trade_count_multiplier = 0.0
        norm_factor = (60 / shock_window_seconds)
        norm_trades = ((stats_now.trade_count or 0) * norm_factor) if stats_now else 0
        if prior_avg_trades > 0:
            trade_count_multiplier = round(norm_trades / prior_avg_trades, 2)
        else:
            trade_count_multiplier = round(norm_trades, 1)

        # 3. PILLAR: MOMENTUM (Max Excursion)
        # We check the Peak price touched in the window vs Publication Ask
        pub_ask = stats_now.ask if stats_now else None 
        max_seen = stats_now.max_price if stats_now else None
        max_excursion = 0.0
        if pub_ask and max_seen and pub_ask > 0:
            max_excursion = round(((max_seen - pub_ask) / pub_ask) * 100, 3)

        # 4. PILLAR: CONVICTION (Buying Pressure)
        imbalance = stats_now.imbalance_ratio if stats_now else 0
        pressure_pct = (imbalance + 1) / 2 * 100 if imbalance is not None else 50.0
        buy_volume = stats_now.buy_volume if stats_now else None

        # 5. SPREAD COMPRESSION (Liquidity Tracking - SHADOW ONLY)
        prior_avg_spread = 0.0
        try:
            spread_start = event_time - timedelta(minutes=10)
            spread_req = StockQuotesRequest(symbol_or_symbols=[symbol], start=spread_start, end=event_time, feed=DataFeed.SIP)
            spread_quotes = client.get_stock_quotes(spread_req)
            if symbol in spread_quotes.data and spread_quotes[symbol]:
                all_spreads = [(float(q.ask_price) - float(q.bid_price)) for q in spread_quotes[symbol] if q.ask_price and q.bid_price]
                prior_avg_spread = sum(all_spreads) / len(all_spreads) if all_spreads else 0.0
        except: pass
        
        current_spread = stats_now.spread if stats_now else 0.0
        spread_compression = 0.0
        if prior_avg_spread > 0 and current_spread is not None:
            spread_compression = round((1 - (current_spread / prior_avg_spread)) * 100, 1)

        # --- PILLAR 6: ADVANCED SHADOW TRACKING (ALPHA RESEARCH) ---
        block_trade_pct = getattr(stats_now, 'block_trade_pct', 0.0) or 0.0
        tape_accel = getattr(stats_now, 'tape_acceleration_pct', 0.0) or 0.0
        
        # Latency to First Print
        latency_to_print = None
        first_trade_ts = getattr(stats_now, 'first_trade_ts', None)
        if stats_now and first_trade_ts:
            latency_to_print = round((first_trade_ts - event_time).total_seconds(), 3)
            
        # Price Impact bps / $100k
        price_impact_bps = 0.0
        total_usd_vol = getattr(stats_now, 'total_dollar_volume', 0.0) or 0.0
        if stats_now and total_usd_vol > 0 and stats_now.mid:
            # mid price at event reception vs mid price after surge
            ref_mid = reference_nbbo.get("mid") if reference_nbbo else stats_now.ask
            if ref_mid and ref_mid > 0:
                price_move_pct = abs(stats_now.mid - ref_mid) / ref_mid
                # bps per $100k
                price_impact_bps = round((price_move_pct * 10000) / (total_usd_vol / 100000), 2)

        # Support Velocity (Post-Surge Bid/Ask Ratio)
        post_trade_bid_ratio = 0.0
        bid_sz = getattr(stats_now, 'bid_size', 0.0) or 0.0
        ask_sz = getattr(stats_now, 'ask_size', 0.0) or 0.0
        if stats_now and bid_sz and ask_sz:
            post_trade_bid_ratio = round(bid_sz / ask_sz, 2)

        # --- MOVE TYPE CLASSIFICATION (Four-Pillar Surge Model) ---
        # SURGE classification has three paths:
        #
        # STANDARD PATH (Four Pillars):
        # 1. SIZE: Volume surge multiplier >= 3.0x
        # 2. FREQUENCY: Trade count multiplier >= 2.0x
        # 3. MOMENTUM: Max excursion >= 1.0%
        # 4. CONVICTION: Buying pressure >= 70% AND buy_volume >= 1000 (if pressure >= 70%)
        #    - Edge case: Low-volume trades can skew imbalance ratio, so require minimum buy_volume
        #    - This ensures buying pressure is meaningful, not just statistical noise
        # 5. MINIMUM VOLUME: Window volume >= 5000 (absolute minimum for reliability)
        #
        # PREFERRED SECTOR EXCEPTION (Healthcare, Technology, Financial Services):
        # These three sectors have proven reliability and don't need minimum volume requirements
        # - Healthcare: 31.3% win rate, +13.08% avg win (biotech/pharma very reliable)
        # - Technology: 31.6% win rate, +55.73% avg win (huge winners when they hit)
        # - Financial Services: 25% win rate, +2,024% avg win (extreme winners)
        # - EXCEPTION: No minimum window volume (5000) requirement
        # - EXCEPTION: No minimum buy_volume (1000) requirement (only need 70% buying pressure)
        # - Still requires: 3x volume, 2x trade count, 1% excursion, 70% buying pressure
        #
        # HIGH-VOLUME EDGE CASE (Buying Pressure Waived):
        # If window volume >= 50,000, buying pressure (conviction pillar) is NOT required
        # - These are high-volume "tug of war" moves - massive activity even if not one-sided
        # - Natural movers with real volume, sometimes more balanced (50-70% buying pressure)
        # - Requires: 50k volume, 3x multiplier, 2x trade count, 1% excursion
        # - Note: If volume >= 50k, we automatically have >= 5k minimum and >= 1k buy_volume
        # - This catches big winners that are high-volume but slightly "toxic" (balanced buying/selling)
        #
        # This combination has high predictive power with minimal noise
        if current_vol is None or current_vol == 0:
            move_type = "INACTIVE"
        elif prior_avg_vol == 0:
            move_type = "NEW_ACTIVITY"
        else:
            # FOUR-PILLAR SURGE CRITERIA
            VOLUME_SURGE_THRESHOLD = 3.0  # Volume must be 3x normal
            TRADE_COUNT_THRESHOLD = 2.0  # Trade count must be 2x normal
            MAX_EXCURSION_THRESHOLD = 1.0  # Price must move at least 1%
            BUYING_PRESSURE_THRESHOLD = 70.0  # Buying pressure must be at least 70%
            MIN_BUY_VOLUME_THRESHOLD = 1000  # If buying pressure >= 70%, buy_volume must be >= 1000
            MIN_WINDOW_VOLUME_THRESHOLD = 5000  # Absolute minimum window volume for reliability
            
            # Check each pillar
            is_size_ok = surge_score >= VOLUME_SURGE_THRESHOLD
            is_freq_ok = trade_count_multiplier >= TRADE_COUNT_THRESHOLD
            is_mom_ok = max_excursion >= MAX_EXCURSION_THRESHOLD
            
            # SECTOR EXCEPTIONS: Healthcare, Technology, Financial Services
            # These three sectors don't require minimum window volume (5000) or minimum buy volume (1000)
            # All other sectors require all criteria including minimum volumes
            PREFERRED_SECTORS = {"Healthcare", "Technology", "Financial Services"}
            is_preferred_sector = (sector and sector in PREFERRED_SECTORS)
            
            # CONVICTION: Buying pressure >= 70% AND if so, buy_volume >= 1000
            # EXCEPTION: Preferred sectors don't need minimum buy_volume (1000)
            has_sufficient_buying_pressure = pressure_pct >= BUYING_PRESSURE_THRESHOLD
            if is_preferred_sector:
                # Preferred sectors: Only need buying pressure >= 70%, no minimum buy_volume
                if has_sufficient_buying_pressure:
                    is_conv_ok = True  # No buy_volume check for preferred sectors
                else:
                    is_conv_ok = False
            else:
                # All other sectors: Require buying pressure >= 70% AND buy_volume >= 1000
                if has_sufficient_buying_pressure:
                    is_conv_ok = (buy_volume is not None and buy_volume >= MIN_BUY_VOLUME_THRESHOLD)
                else:
                    is_conv_ok = False
            
            # MINIMUM VOLUME: Window volume must be >= 5000 for reliability
            # Low-volume moves (< 5000) are highly unreliable even if other criteria are met
            # EXCEPTION: Preferred sectors (Healthcare, Technology, Financial Services) don't need this
            if is_preferred_sector:
                # Preferred sectors: No minimum volume requirement
                has_minimum_volume = True  # Always pass for preferred sectors
            else:
                # All other sectors: Require minimum volume
                has_minimum_volume = (current_vol is not None and current_vol >= MIN_WINDOW_VOLUME_THRESHOLD)
            
            # EDGE CASE: High-volume "tug of war" moves (>= 50k window volume)
            # Very high volume moves are reliable even if buying pressure < 70%
            # These are natural movers with massive activity (tug of war), not one-sided pumps
            # If window volume >= 50k, buying pressure (conviction pillar) is waived
            HIGH_VOLUME_THRESHOLD = 50000  # 50k absolute window volume
            is_high_volume_tug_of_war = (current_vol is not None and current_vol >= HIGH_VOLUME_THRESHOLD)
            
            # SURGE classification logic:
            # Option 1: Standard four pillars + minimum volume (buying pressure required)
            # Option 2: High-volume edge case (buying pressure waived, requires 50k volume)
            # Option 3: Preferred sector (Healthcare/Technology/Financial Services) - no minimum volume requirements
            if is_high_volume_tug_of_war:
                # High-volume edge case: Buying pressure doesn't matter
                # Requires: 50k volume, 3x multiplier, 2x trade count, 1% excursion
                # Note: If volume >= 50k, we automatically have >= 5k minimum and >= 1k buy_volume
                if is_size_ok and is_freq_ok and is_mom_ok:
                    move_type = "SURGE"
                else:
                    # High volume but other pillars failed
                    move_type = "STRENGTH"
            elif is_size_ok and is_freq_ok and is_mom_ok and is_conv_ok and has_minimum_volume:
                # Standard four pillars + minimum volume (all requirements met)
                move_type = "SURGE"
            elif is_size_ok and is_freq_ok and is_mom_ok and is_conv_ok and not has_minimum_volume:
                # All four pillars met but volume too low - classify as STRENGTH instead
                # This prevents unreliable low-volume trades from being classified as SURGE
                move_type = "STRENGTH"
            elif surge_score >= 1.5:
                # High volume but didn't meet all surge criteria
                move_type = "STRENGTH"
            elif surge_score >= 1.0:
                move_type = "NORMAL_ACTIVITY"
            else:
                move_type = "LOW_ACTIVITY"

        vol_accel = None
        if norm_vol is not None and prior_avg_vol > 0:
            vol_accel = round(((norm_vol - prior_avg_vol) / prior_avg_vol) * 100, 1)

        # 2. PRICE CLIP / SLIPPAGE (Use real received_at for speed monitoring)
        recv_ask = reference_nbbo.get("ask") if reference_nbbo else None
        ask_change = None
        if pub_ask is not None and recv_ask is not None and pub_ask > 0:
            ask_change = round(((recv_ask - pub_ask) / pub_ask) * 100, 3)

        # 3. VOLATILITY SURGE (Normalized to 1 minute)
        window_range = stats_now.range if stats_now and stats_now.range is not None else 0
        norm_range = stats_now.normalized_minute_range if stats_now and stats_now.normalized_minute_range is not None else 0
        vol_surge_ratio = None
        if norm_range is not None and prior_avg_range > 0:
            vol_surge_ratio = round(norm_range / prior_avg_range, 2)

        return VolumeSurgeAnalysis(
            symbol=symbol,
            event_time=event_time.isoformat(),
            move_type=move_type,
            window_volume=current_vol if current_vol is not None else 0,
            prior_avg_10min_volume=round(prior_avg_vol, 1),
            surge_multiplier=surge_score,
            window_volume_normalised=norm_vol,
            volume_accel_pct=vol_accel,
            trade_count=stats_now.trade_count if stats_now else 0,
            trade_count_normalised=round(norm_trades, 1),
            prior_avg_10min_trade_count=round(prior_avg_trades, 1),
            trade_count_multiplier=trade_count_multiplier,
            max_excursion_pct=max_excursion,
            spread_compression_pct=spread_compression,
            pub_price=pub_ask,
            recv_price=recv_ask,
            ask_change_pct=ask_change,
            buy_volume=stats_now.buy_volume if stats_now else None,
            sell_volume=stats_now.sell_volume if stats_now else None,
            imbalance_ratio=imbalance,
            block_trade_pct=block_trade_pct,
            price_impact_bps=price_impact_bps,
            tape_acceleration_pct=tape_accel,
            latency_to_first_trade=latency_to_print,
            post_trade_bid_ratio=post_trade_bid_ratio,
            pub_to_recv_seconds=round(real_window_seconds, 3),
            volatility_surge_ratio=vol_surge_ratio,
            prior_avg_minute_range=round(prior_avg_range, 4),
            prior_avg_10min_spread=round(prior_avg_spread, 4),
            pub_to_recv_range=window_range,
            pub_ask=pub_ask,
            recv_ask=recv_ask,
            error=None
        )
        
    except Exception as e:
        logger.error("Error analyzing volume", symbol=symbol, error=str(e), exc_info=True)
        return VolumeSurgeAnalysis(symbol=symbol, event_time=event_time.isoformat(), move_type="ERROR", error=str(e), prior_avg_10min_volume=0, window_volume=0, surge_multiplier=0)
    

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
