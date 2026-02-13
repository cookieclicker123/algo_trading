"""
Statistics data models - shared between recall and signal engines.

STRUCTURE:
- Confluence Window (0-2s): Always captured for IMMINENT articles
  - Overall stats + 4 sub-slices (0-500ms, 500-1000ms, 1000-1500ms, 1500-2000ms)
  - Pressure consistency, timing, baseline ratios
- Surge Window (8s): Only captured if trade was surge-based
- Pre-news Baseline (5s before): For computing ratios
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from ...domain.brokerage.models import MarketSession


# ===== Sub-Slice Model for Micro-Trajectory Analysis =====

class ConfluenceSlice(BaseModel):
    """Stats for a single time slice within the 2-second confluence window."""
    slice_start_ms: int = Field(..., description="Start of slice in ms from publication")
    slice_end_ms: int = Field(..., description="End of slice in ms from publication")

    # Volume
    volume: int = Field(0, description="Total shares traded in this slice")
    trade_count: int = Field(0, description="Number of trades in this slice")
    buy_volume: int = Field(0, description="Volume from uptick trades")
    sell_volume: int = Field(0, description="Volume from downtick trades")

    # Price
    first_price: Optional[float] = Field(None, description="First trade price in slice")
    last_price: Optional[float] = Field(None, description="Last trade price in slice")
    high_price: Optional[float] = Field(None, description="Highest price in slice")
    low_price: Optional[float] = Field(None, description="Lowest price in slice")

    # Pressure
    imbalance_ratio: Optional[float] = Field(None, description="(buy-sell)/(buy+sell), -1 to +1")
    pressure_sign: int = Field(0, description="+1 if net buying, -1 if net selling, 0 if neutral")
    uptick_count: int = Field(0, description="Number of uptick trades")
    downtick_count: int = Field(0, description="Number of downtick trades")

    # Liquidity
    spread_at_end: Optional[float] = Field(None, description="Spread at end of slice")
    bid_depth_at_end: Optional[int] = Field(None, description="Bid depth at end of slice")
    ask_depth_at_end: Optional[int] = Field(None, description="Ask depth at end of slice")

    model_config = {"frozen": False}


class ConfluenceWindow(BaseModel):
    """
    Comprehensive 2-second confluence window stats with sub-slice breakdown.

    This is the core data structure for ML feature extraction.
    Captures micro-trajectory within the decision window.
    """
    # === OVERALL 2-SECOND STATS ===
    window_start_ms: int = Field(0, description="Start of window in ms from publication")
    window_end_ms: int = Field(2000, description="End of window in ms from publication")

    # Volume totals
    total_volume: int = Field(0, description="Total shares traded in 2s")
    total_trades: int = Field(0, description="Total trade count in 2s")
    total_buy_volume: int = Field(0, description="Total buy volume (upticks)")
    total_sell_volume: int = Field(0, description="Total sell volume (downticks)")
    dollar_volume: Optional[float] = Field(None, description="Total dollar volume")

    # Price trajectory
    first_price: Optional[float] = Field(None, description="First trade price")
    last_price: Optional[float] = Field(None, description="Last trade price")
    high_price: Optional[float] = Field(None, description="Highest price in window")
    low_price: Optional[float] = Field(None, description="Lowest price in window")
    vwap: Optional[float] = Field(None, description="Volume-weighted average price")
    price_excursion_pct: Optional[float] = Field(None, description="Max move from first price %")
    price_direction: int = Field(0, description="+1 up, -1 down, 0 flat")

    # Pressure analysis
    imbalance_ratio: Optional[float] = Field(None, description="(buy-sell)/(buy+sell)")
    buying_pressure_pct: Optional[float] = Field(None, description="buy/(buy+sell) * 100")
    uptick_count: int = Field(0, description="Total uptick trades")
    downtick_count: int = Field(0, description="Total downtick trades")
    uptick_ratio: Optional[float] = Field(None, description="upticks/(upticks+downticks)")

    # Pressure consistency (KEY FEATURE: does pressure sustain?)
    pressure_first_half: Optional[float] = Field(None, description="Imbalance ratio in first 1s")
    pressure_second_half: Optional[float] = Field(None, description="Imbalance ratio in second 1s")
    pressure_consistent: Optional[bool] = Field(None, description="Same sign pressure in both halves")
    pressure_strengthening: Optional[bool] = Field(None, description="Second half stronger than first")

    # Trade size analysis
    avg_trade_size: Optional[float] = Field(None, description="Average shares per trade")
    median_trade_size: Optional[float] = Field(None, description="Median shares per trade")
    max_single_trade: Optional[int] = Field(None, description="Largest single trade")
    large_trade_pct: Optional[float] = Field(None, description="% volume from trades >= 500 shares")

    # Timing (KEY FEATURES for reaction speed)
    first_trade_latency_ms: Optional[float] = Field(None, description="Ms to first trade after pub")
    first_uptick_latency_ms: Optional[float] = Field(None, description="Ms to first uptick")
    max_trade_gap_ms: Optional[float] = Field(None, description="Longest gap between trades")
    trades_in_first_500ms: int = Field(0, description="Trade count in first 500ms")
    volume_in_first_500ms: int = Field(0, description="Volume in first 500ms")

    # Spread/liquidity
    initial_spread: Optional[float] = Field(None, description="Spread at window start")
    final_spread: Optional[float] = Field(None, description="Spread at window end")
    spread_compression_pct: Optional[float] = Field(None, description="Spread change %")
    initial_bid_depth: Optional[int] = Field(None, description="Bid depth at start")
    initial_ask_depth: Optional[int] = Field(None, description="Ask depth at start")
    final_bid_depth: Optional[int] = Field(None, description="Bid depth at end")
    final_ask_depth: Optional[int] = Field(None, description="Ask depth at end")
    depth_ratio_change: Optional[float] = Field(None, description="Change in bid/ask depth ratio")
    quote_update_count: int = Field(0, description="Number of quote updates in window")

    # === RATIOS VS PRE-NEWS BASELINE (5 seconds before) ===
    baseline_volume_5s: Optional[int] = Field(None, description="Volume in 5s before news")
    baseline_trades_5s: Optional[int] = Field(None, description="Trade count in 5s before news")
    baseline_spread: Optional[float] = Field(None, description="Avg spread in 5s before news")
    baseline_avg_trade_size: Optional[float] = Field(None, description="Avg trade size before news")

    volume_ratio: Optional[float] = Field(None, description="2s_volume / 5s_baseline_volume")
    trade_count_ratio: Optional[float] = Field(None, description="2s_trades / 5s_baseline_trades")
    spread_ratio: Optional[float] = Field(None, description="final_spread / baseline_spread")
    trade_size_ratio: Optional[float] = Field(None, description="2s_avg_size / baseline_avg_size")

    # === CONFLUENCE SCORING ===
    has_volume_surge: bool = Field(False, description="Volume >= 2000 shares")
    has_price_excursion: bool = Field(False, description="Price move >= 1%")
    has_buying_pressure: bool = Field(False, description="Buying pressure >= 80%")
    confluence_score: int = Field(0, description="Sum of above (0-3)")
    confluence_met: bool = Field(False, description="Score >= 1 (trade triggered)")

    # === SUB-SLICES (8 x 250ms for granular micro-trajectory) ===
    slices: List[ConfluenceSlice] = Field(
        default_factory=list,
        description="8 sub-slices: 0-250ms, 250-500ms, 500-750ms, 750-1000ms, 1000-1250ms, 1250-1500ms, 1500-1750ms, 1750-2000ms"
    )

    model_config = {"frozen": False}


class SurgeWindow(BaseModel):
    """
    8-second surge window stats - only populated if trade was surge-based.

    Surge occurs when confluence failed but continued monitoring detected activity.
    """
    triggered: bool = Field(False, description="Whether surge monitoring was triggered")
    found: bool = Field(False, description="Whether surge criteria were met")
    detection_cycle: Optional[int] = Field(None, description="Which poll cycle detected surge (1-16)")
    seconds_elapsed: Optional[float] = Field(None, description="Seconds into window when found")

    # Volume at surge detection
    volume: Optional[int] = Field(None, description="Total volume at detection")
    trade_count: Optional[int] = Field(None, description="Trade count at detection")
    buy_volume: Optional[int] = Field(None, description="Buy volume at detection")
    sell_volume: Optional[int] = Field(None, description="Sell volume at detection")

    # Pressure at surge
    buying_pressure_pct: Optional[float] = Field(None, description="Buying pressure %")
    imbalance_ratio: Optional[float] = Field(None, description="Imbalance ratio")
    price_excursion_pct: Optional[float] = Field(None, description="Price excursion %")

    # Multipliers vs baseline
    volume_multiplier: Optional[float] = Field(None, description="Volume vs 10min avg")
    trade_count_multiplier: Optional[float] = Field(None, description="Trade count vs 10min avg")

    # NBBO at surge
    bid: Optional[float] = Field(None, description="Bid at surge detection")
    ask: Optional[float] = Field(None, description="Ask at surge detection")
    mid: Optional[float] = Field(None, description="Mid at surge detection")

    model_config = {"frozen": False}


# ===== Recall Engine Models =====

class RecallRecord(BaseModel):
    """Record of an article that could have been traded (missed opportunity)."""
    
    article_id: str = Field(..., description="Article identifier")
    title: str = Field(..., description="Article title")
    tickers: List[str] = Field(..., description="All tickers from article")
    session: MarketSession = Field(..., description="Market session when article was received")
    published_at: datetime = Field(..., description="When article was published")
    received_at: datetime = Field(..., description="When article was received")
    
    # Initial NBBO snapshot (when article received)
    initial_nbbo: Optional[Dict[str, Any]] = Field(
        None,
        description="Initial NBBO: bid, ask, spread, mid"
    )
    
    # 10-minute price check result (formerly 5-minute, then 15-minute)
    price_check_10min: Optional[Dict[str, Any]] = Field(
        None,
        description="10-minute price check: final_mid, percent_change, moved_1_percent"
    )
    
    # Ticker metadata (fetched via YahooFinanceCoordinator - shared across all services)
    ticker_metadata: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="ticker -> {industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Metadata fetch errors (why metadata couldn't be collected)
    metadata_errors: Dict[str, str] = Field(
        default_factory=dict,
        description="ticker -> error_reason: 'api_timeout', 'api_error', 'no_data_available', 'rate_limited', etc."
    )
    
    # Filter reason (why wasn't it traded?) - SINGULAR: one reason per article
    filter_reason: Optional[str] = Field(
        None,
        description="Single filter reason: 'ai_classified_ignore', 'prefilter_no_tickers', 'prefilter_low_market_cap', etc. Set immediately from events."
    )
    
    # AI classification result (what did AI say about this article?)
    ai_classification: Optional[str] = Field(
        None,
        description="AI classification: 'IMMINENT', 'SPECULATIVE', 'ROUTINE', 'IGNORE'. Always populated so we know why wasn't traded."
    )

    # Post-AI filter reason (why IMMINENT article wasn't traded)
    # Only set for articles that passed AI classification but failed post-AI checks
    postfilter_reason: Optional[str] = Field(
        None,
        description="Post-AI skip reason: 'postfilter_no_surge', 'postfilter_low_volume', 'postfilter_spread_too_wide', etc."
    )

    # Headline type classification (for statistical analysis)
    headline_type: Optional[str] = Field(
        None,
        description="Catalyst type: contract, fda, partnership, earnings, etc. Only classified for IMMINENT articles."
    )

    # Volume analysis at article receive time (for future filtering research)
    # NOTE: This is from the 4-second SURGE detection window, NOT the confluence window
    # Ticker -> Stats dictionary
    volume_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="ticker -> {move_type, surge_multiplier, trade_count_multiplier, max_excursion_pct, ...} (4-second window)"
    )

    # === CONFLUENCE WINDOW STATS (0-2 seconds after publication) ===
    # These match the EXACT metrics used for trade decisions in auto_trade.py
    # Aligned with SignalRecord for apples-to-apples comparison
    confluence_score: Optional[int] = Field(None, description="Confluence score 0-3 (volume + price + pressure)")
    confluence_volume: Optional[int] = Field(None, description="Total volume in 2s window")
    confluence_trade_count: Optional[int] = Field(None, description="Number of trades in 2s window")
    confluence_buy_volume: Optional[int] = Field(None, description="Buy volume (tick rule) in 2s window")
    confluence_sell_volume: Optional[int] = Field(None, description="Sell volume (tick rule) in 2s window")
    confluence_buying_pressure_pct: Optional[float] = Field(None, description="Buy volume / total volume * 100")
    confluence_imbalance_ratio: Optional[float] = Field(None, description="(buy - sell) / (buy + sell), range -1 to +1")
    confluence_price_excursion_pct: Optional[float] = Field(None, description="Max price move % in 2s window")
    confluence_first_price: Optional[float] = Field(None, description="First trade price in window")
    confluence_max_price: Optional[float] = Field(None, description="Max price in 2s window")
    confluence_min_price: Optional[float] = Field(None, description="Min price in 2s window")
    confluence_vwap: Optional[float] = Field(None, description="Volume-weighted avg price in 2s window")
    confluence_initial_spread: Optional[float] = Field(None, description="Spread at start of confluence")
    confluence_final_spread: Optional[float] = Field(None, description="Spread at end of confluence (2s)")
    confluence_spread_compression_pct: Optional[float] = Field(None, description="Spread compression % over 2s")
    confluence_first_trade_latency_ms: Optional[float] = Field(None, description="Ms from publication to first trade")
    confluence_avg_trade_size: Optional[float] = Field(None, description="Average trade size in window")
    confluence_max_trade_gap_ms: Optional[float] = Field(None, description="Longest gap between trades (ms)")
    confluence_has_volume_surge: Optional[bool] = Field(None, description="Volume >= 2000 shares")
    confluence_has_price_excursion: Optional[bool] = Field(None, description="Price move >= 1%")
    confluence_has_buying_pressure: Optional[bool] = Field(None, description="Buying pressure >= 80%")
    # Additional market physics (same as SignalRecord)
    confluence_last_price: Optional[float] = Field(None, description="Last trade price in 2s window")
    confluence_price_direction: Optional[int] = Field(None, description="+1 up, -1 down, 0 flat")
    confluence_dollar_volume: Optional[float] = Field(None, description="Total dollar volume in 2s window")
    confluence_max_single_trade: Optional[int] = Field(None, description="Largest single trade size")
    confluence_median_trade_size: Optional[float] = Field(None, description="Median trade size")
    confluence_large_trade_pct: Optional[float] = Field(None, description="% of volume from trades >= 500 shares")
    confluence_uptick_count: Optional[int] = Field(None, description="Number of uptick trades")
    confluence_downtick_count: Optional[int] = Field(None, description="Number of downtick trades")

    # === GAP/TRAP DETECTION: Price at publication vs reception ===
    # Critical for false negative analysis: did price run away before we could act?
    pub_time_ask: Optional[float] = Field(None, description="Ask price at PUBLICATION time (from historical API)")
    recv_time_ask: Optional[float] = Field(None, description="Ask price at RECEPTION time (when we first saw it)")
    fill_time_ask: Optional[float] = Field(None, description="Ask price at FILL/CHECK time (when we made decision)")
    pub_to_recv_pct: Optional[float] = Field(None, description="% ask change from publication to reception (front-running detection)")
    recv_to_fill_pct: Optional[float] = Field(None, description="% ask change from reception to fill (chase/volatility detection)")
    # Latency context
    pub_to_recv_latency_ms: Optional[float] = Field(None, description="Milliseconds from publication to reception")

    # === STRUCTURED CONFLUENCE WINDOW (comprehensive micro-trajectory) ===
    # This replaces the flat confluence_ fields above for ML analysis
    # Contains sub-slices, pressure consistency, timing, and baseline ratios
    confluence_window: Optional[ConfluenceWindow] = Field(
        None,
        description="Structured 2-second confluence window with sub-slices and ML features"
    )

    # === STRUCTURED SURGE WINDOW (only if surge-based trade) ===
    # Only populated if trade was made based on 8-second surge monitoring
    # If confluence_met=True, this should be None
    surge_window: Optional[SurgeWindow] = Field(
        None,
        description="8-second surge window stats - only if surge-based trade"
    )

    # Tracking metadata
    tracked_at: datetime = Field(default_factory=datetime.now, description="When tracking started")
    price_checked_at: Optional[datetime] = Field(None, description="When 10-minute price check completed")
    
    # Price tracking during 10-minute hold period
    highest_price_during_hold: Optional[Dict[str, Any]] = Field(
        None,
        description="Highest price reached during 10-minute hold: price, timestamp, percent_gain_from_entry, minute, second"
    )
    max_adverse_excursion: Optional[Dict[str, Any]] = Field(
        None,
        description="Lowest price during 10-minute hold (max adverse excursion): price, timestamp, percent_loss_from_entry, minute, second, stop_loss_percentage, stop_loss_dollar_per_share"
    )
    
    # Trade linkage (did we actually trade this?)
    is_traded: bool = Field(False, description="Whether this article resulted in a trade execution")
    trade_id: Optional[str] = Field(None, description="Trade ID if executed")
    
    # 2-minute monitoring for SURGE detection (for articles that didn't initially show SURGE)
    monitoring_status: Optional[str] = Field(
        None,
        description="Monitoring status: None (not monitored), 'initiated' (monitoring started), 'surge_detected' (surge found), 'completed_no_surge' (monitoring finished, no surge)"
    )
    monitoring_initiated_at: Optional[datetime] = Field(
        None,
        description="When 2-minute monitoring was initiated"
    )
    monitoring_cycles_completed: int = Field(
        0,
        description="Number of 4-second cycles completed during monitoring (max 30)"
    )
    surge_detected_at: Optional[datetime] = Field(
        None,
        description="When SURGE was detected during monitoring (if any)"
    )
    surge_detection_cycle: Optional[int] = Field(
        None,
        description="Which 4-second cycle detected the surge (0-29)"
    )
    surge_detection_window_stats: Optional[Dict[str, Any]] = Field(
        None,
        description="Volume stats from the window where surge was detected"
    )
    monitoring_completed_at: Optional[datetime] = Field(
        None,
        description="When monitoring completed (after 2 minutes or surge detected)"
    )

    # === FILTER CHECKPOINT VALUES (for hit rate analysis) ===
    # These capture the actual values at each filter checkpoint for FN/TN analysis.
    # Enables comparison with TP/FP to identify what distinguishes good from bad trades.
    filter_values: Optional[Dict[str, Any]] = Field(
        None,
        description="All filter checkpoint values: spread_pct, pub_to_recv_pct, recv_to_fill_pct, ask_vs_first_trade_pct, confluence_runup_pct, entry_delay_s, market_cap_m, etc."
    )
    # Which filters would have passed if we had proceeded (for FN analysis)
    filters_checked: Optional[Dict[str, bool]] = Field(
        None,
        description="Filter name -> would_pass (True/False). Shows which filters the trade would have passed."
    )

    model_config = {"frozen": False}  # Allow updates for price_check_10min and monitoring fields


class RecallSessionFile(BaseModel):
    """JSON file structure for a recall session."""
    
    session: MarketSession = Field(..., description="Market session")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    session_start: datetime = Field(..., description="Session start time")
    session_end: datetime = Field(..., description="Session end time")
    file_created_at: datetime = Field(default_factory=datetime.now, description="When file was created")
    last_updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Real-time summary (updated on each append)
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_articles_tracked": 0,
            "articles_with_1_percent_move": 0,
            "articles_traded": 0,
            "missed_opportunities": 0,
            "filter_breakdown": {},
            "ticker_breakdown": {},
            # Large trade pattern analysis (pump-and-dump detection)
            "large_trade_stats": {
                "total_with_data": 0,
                "avg_large_trade_pct": 0.0,
                "avg_max_single_trade": 0.0,
                "avg_trade_count": 0.0,
                # Breakdown by outcome (moved_1_percent True/False)
                "movers": {"count": 0, "avg_large_trade_pct": 0.0, "avg_max_single_trade": 0.0},
                "non_movers": {"count": 0, "avg_large_trade_pct": 0.0, "avg_max_single_trade": 0.0}
            }
        },
        description="Summary statistics updated in real-time"
    )
    
    # List of records (appended in real-time)
    records: List[RecallRecord] = Field(default_factory=list, description="List of recall records")
    
    model_config = {"frozen": False}  # Allow updates


# ===== Signal Engine Models =====

class SignalRecord(BaseModel):
    """Record of an actual trade execution."""
    
    trade_id: str = Field(..., description="Trade/order identifier")
    article_id: Optional[str] = Field(None, description="Associated article ID if triggered by news")
    ticker: str = Field(..., description="Ticker symbol traded")
    session: MarketSession = Field(..., description="Market session when trade executed")
    executed_at: datetime = Field(..., description="When trade was executed")
    
    # Entry details (from TradeResult)
    entry_price: float = Field(..., description="Entry fill price")
    entry_shares: int = Field(..., description="Number of shares")
    entry_amount_usd: float = Field(..., description="Total entry amount in USD")
    entry_nbbo: Optional[Dict[str, Any]] = Field(
        None,
        description="NBBO at entry: bid, ask, spread, mid"
    )
    
    # Ticker metadata (fetched via YahooFinanceCoordinator - shared across all services)
    ticker_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="{industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Trade outcome (if available)
    exit_price: Optional[float] = Field(None, description="Exit fill price")
    exit_shares: Optional[int] = Field(None, description="Number of shares exited")
    exit_amount_usd: Optional[float] = Field(None, description="Total exit amount in USD")
    exit_reason: Optional[str] = Field(None, description="Exit reason: stop_loss, early_exit_10pct, tier_15pct, manual_exit, time_exit, etc.")
    exited_at: Optional[datetime] = Field(None, description="When exit was executed")
    hold_duration_seconds: Optional[float] = Field(None, description="Seconds held from entry to exit")
    profit_loss_usd: Optional[float] = Field(None, description="Profit/loss in USD")
    profit_loss_percent: Optional[float] = Field(None, description="Profit/loss percentage")

    # Price tracking during hold period (like recall engine)
    highest_price_during_hold: Optional[Dict[str, Any]] = Field(
        None,
        description="Highest price reached during hold: price, timestamp, percent_gain_from_entry"
    )
    max_adverse_excursion: Optional[Dict[str, Any]] = Field(
        None,
        description="Lowest price during hold (max drawdown): price, timestamp, percent_loss_from_entry"
    )

    # Volume analysis at article publish time (for future filtering research)
    volume_stats: Optional[Dict[str, Any]] = Field(
        None,
        description="Volume microstructures: move_type, surge_multiplier, trade_count_multiplier, max_excursion_pct, etc."
    )

    # === CONFLUENCE WINDOW STATS (0-2 seconds after publication) ===
    # These are the EXACT metrics from the trade decision point
    confluence_score: Optional[int] = Field(None, description="Confluence score 0-3 (volume + price + pressure)")
    confluence_volume: Optional[int] = Field(None, description="Total volume in 2s window")
    confluence_trade_count: Optional[int] = Field(None, description="Number of trades in 2s window")
    confluence_buy_volume: Optional[int] = Field(None, description="Buy volume (tick rule) in 2s window")
    confluence_sell_volume: Optional[int] = Field(None, description="Sell volume (tick rule) in 2s window")
    confluence_buying_pressure_pct: Optional[float] = Field(None, description="Buy volume / total volume * 100")
    confluence_imbalance_ratio: Optional[float] = Field(None, description="(buy - sell) / (buy + sell), range -1 to +1")
    confluence_price_excursion_pct: Optional[float] = Field(None, description="Max price move % in 2s window")
    confluence_first_price: Optional[float] = Field(None, description="First trade price in window")
    confluence_max_price: Optional[float] = Field(None, description="Max price in 2s window")
    confluence_min_price: Optional[float] = Field(None, description="Min price in 2s window")
    confluence_vwap: Optional[float] = Field(None, description="Volume-weighted avg price in 2s window")
    confluence_initial_spread: Optional[float] = Field(None, description="Spread at start of confluence")
    confluence_final_spread: Optional[float] = Field(None, description="Spread at end of confluence (2s)")
    confluence_spread_compression_pct: Optional[float] = Field(None, description="Spread compression % over 2s")
    confluence_first_trade_latency_ms: Optional[float] = Field(None, description="Ms from publication to first trade")
    confluence_avg_trade_size: Optional[float] = Field(None, description="Average trade size in window")
    confluence_max_trade_gap_ms: Optional[float] = Field(None, description="Longest gap between trades (ms)")
    confluence_has_volume_surge: Optional[bool] = Field(None, description="Volume >= 2000 shares")
    confluence_has_price_excursion: Optional[bool] = Field(None, description="Price move >= 1%")
    confluence_has_buying_pressure: Optional[bool] = Field(None, description="Buying pressure >= 80%")
    # Additional market physics for long-term analysis
    confluence_last_price: Optional[float] = Field(None, description="Last trade price in 2s window")
    confluence_price_direction: Optional[int] = Field(None, description="+1 up, -1 down, 0 flat")
    confluence_dollar_volume: Optional[float] = Field(None, description="Total dollar volume in 2s window")
    confluence_max_single_trade: Optional[int] = Field(None, description="Largest single trade size (institutional indicator)")
    confluence_median_trade_size: Optional[float] = Field(None, description="Median trade size (retail vs institutional)")
    confluence_large_trade_pct: Optional[float] = Field(None, description="% of volume from trades >= 500 shares")
    confluence_uptick_count: Optional[int] = Field(None, description="Number of trades at higher price than previous")
    confluence_downtick_count: Optional[int] = Field(None, description="Number of trades at lower price than previous")

    # === SURGE WINDOW STATS (8-second last chance, only if confluence failed) ===
    # Stricter criteria: volume >= 3x avg, trade count >= 3x, price >= 5%, pressure >= 80%
    surge_triggered: Optional[bool] = Field(None, description="Whether surge window was triggered")
    surge_found: Optional[bool] = Field(None, description="Whether surge criteria were met")
    surge_detection_cycle: Optional[int] = Field(None, description="Which poll cycle detected surge (1-16)")
    surge_seconds_elapsed: Optional[float] = Field(None, description="Seconds into surge window when found")
    surge_volume: Optional[int] = Field(None, description="Volume at surge detection")
    surge_trade_count: Optional[int] = Field(None, description="Trade count at surge detection")
    surge_buy_volume: Optional[int] = Field(None, description="Buy volume at surge detection")
    surge_sell_volume: Optional[int] = Field(None, description="Sell volume at surge detection")
    surge_buying_pressure_pct: Optional[float] = Field(None, description="Buying pressure % at surge")
    surge_imbalance_ratio: Optional[float] = Field(None, description="Imbalance ratio at surge")
    surge_price_excursion_pct: Optional[float] = Field(None, description="Price excursion % at surge")
    surge_volume_multiplier: Optional[float] = Field(None, description="Volume vs 10min avg multiplier")
    surge_trade_count_multiplier: Optional[float] = Field(None, description="Trade count vs 10min avg multiplier")
    surge_ask: Optional[float] = Field(None, description="Ask price at surge detection")
    surge_bid: Optional[float] = Field(None, description="Bid price at surge detection")
    surge_mid: Optional[float] = Field(None, description="Mid price at surge detection")

    # === GAP/TRAP DETECTION: Price at publication vs reception ===
    # Critical for analyzing whether we entered at the right time
    pub_time_ask: Optional[float] = Field(None, description="Ask price at PUBLICATION time (from historical API)")
    recv_time_ask: Optional[float] = Field(None, description="Ask price at RECEPTION time (when we first saw it)")
    fill_time_ask: Optional[float] = Field(None, description="Ask price at FILL time (actual entry price context)")
    pub_to_recv_pct: Optional[float] = Field(None, description="% ask change from publication to reception (front-running detection)")
    recv_to_fill_pct: Optional[float] = Field(None, description="% ask change from reception to fill (chase/volatility detection)")
    pub_to_recv_latency_ms: Optional[float] = Field(None, description="Milliseconds from publication to reception")

    # === STRUCTURED CONFLUENCE WINDOW (comprehensive micro-trajectory) ===
    # Contains sub-slices, pressure consistency, timing, and baseline ratios
    # This is the core data for ML feature extraction
    confluence_window: Optional[ConfluenceWindow] = Field(
        None,
        description="Structured 2-second confluence window with sub-slices and ML features"
    )

    # === STRUCTURED SURGE WINDOW (only if surge-based trade) ===
    # Only populated if trade was made based on 8-second surge monitoring
    surge_window: Optional[SurgeWindow] = Field(
        None,
        description="8-second surge window stats - only if surge-based trade"
    )

    # === ENHANCED STATS (collected async post-trade, never delays execution) ===

    # Spread tracking at multiple time points
    spread_at_receive: Optional[float] = Field(None, description="Spread % when article received")
    spread_at_confluence: Optional[float] = Field(None, description="Spread % after 2s confluence")
    spread_at_fill: Optional[float] = Field(None, description="Spread % at actual fill time")
    spread_at_5s: Optional[float] = Field(None, description="Spread % at T+5 seconds")
    spread_at_10s: Optional[float] = Field(None, description="Spread % at T+10 seconds")
    spread_at_30s: Optional[float] = Field(None, description="Spread % at T+30 seconds")
    spread_compression_2s: Optional[float] = Field(None, description="Spread compression % in first 2s")
    spread_compression_30s: Optional[float] = Field(None, description="Spread compression % in first 30s")

    # Fill quality metrics
    slippage_from_decision: Optional[float] = Field(None, description="TRUE SLIPPAGE %: (fill_price - decision_ask) / decision_ask * 100. The most important metric - how much more you paid vs when you decided to trade.")
    slippage_vs_mid: Optional[float] = Field(None, description="Slippage vs fill-time mid %: (fill - mid) / mid * 100")
    slippage_vs_ask: Optional[float] = Field(None, description="Slippage vs fill-time ask %: (fill - ask) / ask * 100")
    fill_speed_ms: Optional[float] = Field(None, description="Milliseconds from order to fill")
    chase_attempts: Optional[int] = Field(None, description="Number of chase attempts before fill")

    # Order book depth at decision time (for liquidity analysis)
    decision_bid_size: Optional[int] = Field(None, description="Bid size (depth) at top of book when trade decision made")
    decision_ask_size: Optional[int] = Field(None, description="Ask size (depth) at top of book when trade decision made")
    order_vs_depth_ratio: Optional[float] = Field(None, description="Your order size / ask_size - >1 means you'll move the market")

    # Volume windows (collected async)
    volume_1min: Optional[int] = Field(None, description="Volume in first 1 minute after news")
    volume_5min: Optional[int] = Field(None, description="Volume in first 5 minutes after news")
    volume_10min: Optional[int] = Field(None, description="Volume in first 10 minutes after news")
    trade_count_1min: Optional[int] = Field(None, description="Trade count in first 1 minute")

    # Market context
    float_shares: Optional[int] = Field(None, description="Float shares")
    avg_daily_volume: Optional[int] = Field(None, description="Average daily volume")
    volume_vs_adv_ratio: Optional[float] = Field(None, description="First minute volume / ADV ratio")

    # Headline
    headline: Optional[str] = Field(None, description="Article headline/title")
    headline_type: Optional[str] = Field(None, description="Catalyst type: contract, fda, partnership, earnings, etc.")

    # Timing
    time_of_day: Optional[str] = Field(None, description="HH:MM format")
    minutes_after_open: Optional[float] = Field(None, description="Minutes after market/premarket open")
    day_of_week: Optional[str] = Field(None, description="Monday, Tuesday, etc.")

    # Post-trade price tracking (async collected over 30s-10min)
    price_at_5s: Optional[float] = Field(None, description="Price at T+5 seconds")
    price_at_10s: Optional[float] = Field(None, description="Price at T+10 seconds")
    price_at_30s: Optional[float] = Field(None, description="Price at T+30 seconds")
    price_at_1min: Optional[float] = Field(None, description="Price at T+1 minute")
    price_at_5min: Optional[float] = Field(None, description="Price at T+5 minutes")

    # Enrichment tracking
    enrichment_completed: bool = Field(False, description="Whether async enrichment finished")
    enrichment_completed_at: Optional[datetime] = Field(None, description="When enrichment finished")
    
    # === FILTER CHECKPOINT VALUES (for hit rate analysis) ===
    # These capture the actual values at each filter checkpoint, regardless of pass/fail.
    # Enables comparison of distributions between TP/FP to identify discriminating filters.
    filter_values: Optional[Dict[str, Any]] = Field(
        None,
        description="All filter checkpoint values: spread_pct, pub_to_recv_pct, recv_to_fill_pct, ask_vs_first_trade_pct, confluence_runup_pct, entry_delay_s, market_cap_m, etc."
    )
    # Which filters were checked and their pass/fail status
    filters_checked: Optional[Dict[str, bool]] = Field(
        None,
        description="Filter name -> passed (True/False). All filters that were evaluated."
    )

    # Tracking metadata
    recorded_at: datetime = Field(default_factory=datetime.now, description="When record was created")

    model_config = {"frozen": False}  # Allow updates for exit data


# ===== Failed Trades Engine Models =====

class FailedTradeRecord(BaseModel):
    """Record of a failed trade attempt."""
    
    trade_id: str = Field(..., description="Trade/order identifier")
    article_id: Optional[str] = Field(None, description="Associated article ID if triggered by news")
    ticker: str = Field(..., description="Ticker symbol that failed to trade")
    session: MarketSession = Field(..., description="Market session when trade failed")
    failed_at: datetime = Field(..., description="When trade failed")
    
    # Failure details
    failure_reason: str = Field(..., description="Error message/reason for failure")
    ladder_attempts: Optional[int] = Field(None, description="Number of ladder attempts made (for extended hours)")
    ladder_attempts_detail: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Detailed ladder attempts with prices and timestamps"
    )
    
    # NBBO at failure time (with bid/ask sizes)
    failure_nbbo: Optional[Dict[str, Any]] = Field(
        None,
        description="NBBO at failure: bid, ask, spread, mid, bid_size, ask_size"
    )
    
    # Time of day metrics
    hour: int = Field(..., description="Hour of day (0-23) when trade failed")
    minute: int = Field(..., description="Minute of hour (0-59) when trade failed")
    time_of_day: str = Field(..., description="Time of day string (HH:MM format)")
    
    # Ticker metadata (fetched via YahooFinanceCoordinator - shared across all services)
    ticker_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="{industry, sector, market_cap_millions, price, exchange}"
    )
    
    # Trade request details
    requested_shares: Optional[int] = Field(None, description="Number of shares requested")
    requested_price: Optional[float] = Field(None, description="Requested price (if limit order)")
    order_type: Optional[str] = Field(None, description="Order type (market, limit, etc.)")
    
    # Volume analysis at failure time (for future filtering research)
    volume_stats: Optional[Dict[str, Any]] = Field(
        None,
        description="Volume microstructures: move_type, surge_multiplier, trade_count_multiplier, max_excursion_pct, etc."
    )
    
    # Tracking metadata
    recorded_at: datetime = Field(default_factory=datetime.now, description="When record was created")
    
    model_config = {"frozen": False}  # Allow updates


class FailedTradeSessionFile(BaseModel):
    """JSON file structure for a failed trades session."""
    
    session: MarketSession = Field(..., description="Market session")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    session_start: datetime = Field(..., description="Session start time")
    session_end: datetime = Field(..., description="Session end time")
    file_created_at: datetime = Field(default_factory=datetime.now, description="When file was created")
    last_updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Real-time summary (updated on each append)
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_failed_trades": 0,
            "failure_reasons_breakdown": {},
            "ticker_breakdown": {},
            "time_of_day_breakdown": {},  # Hour -> count
            "session_breakdown": {},  # Session -> count
            "avg_spread_at_failure": 0.0,
            "avg_bid_size_at_failure": 0.0,
            "avg_ask_size_at_failure": 0.0,
        },
        description="Summary statistics updated in real-time"
    )
    
    # List of records (appended in real-time)
    records: List[FailedTradeRecord] = Field(default_factory=list, description="List of failed trade records")
    
    model_config = {"frozen": False}  # Allow updates


class SignalSessionFile(BaseModel):
    """JSON file structure for a signal session."""
    
    session: MarketSession = Field(..., description="Market session")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    session_start: datetime = Field(..., description="Session start time")
    session_end: datetime = Field(..., description="Session end time")
    file_created_at: datetime = Field(default_factory=datetime.now, description="When file was created")
    last_updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Real-time summary (updated on each append)
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_trades": 0,
            "profitable_trades": 0,
            "losing_trades": 0,
            "total_profit_loss_usd": 0.0,
            "average_spread_at_entry": 0.0,
            "ticker_breakdown": {},
            "industry_breakdown": {},
            "sector_breakdown": {}
        },
        description="Summary statistics updated in real-time"
    )
    
    # List of records (appended in real-time)
    records: List[SignalRecord] = Field(default_factory=list, description="List of signal records")
    
    model_config = {"frozen": False}  # Allow updates
