#!/usr/bin/env python3
"""
Biotech Backtester: Validate Immediate Entry Features on Historical Winners/Losers

Tests the 3 recommended immediate-entry features:
1. Spread Compression (spread tightens >20% within 1-3 seconds)
2. Tick Density (>5 trades/sec in first 1-3 seconds)
3. Delta Ratio (>3:1 buy/sell ratio in first 1-3 seconds)

Uses Alpaca historical data to simulate real-time WebSocket data collection.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    PROJECT_ROOT = Path(__file__).parent.parent
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()  # Try default .env location
except ImportError:
    print("⚠️  Warning: python-dotenv not installed. Install with: pip install python-dotenv")
    print("   Continuing without .env file loading...")

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockQuotesRequest, StockTradesRequest
    from alpaca.data.enums import DataFeed
except ImportError:
    print("❌ Error: alpaca-py not installed. Install with: pip install alpaca-py")
    sys.exit(1)


def load_biotech_records() -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Load biotech winners, losers, and non-movers from healthcare analysis."""
    report_file = PROJECT_ROOT / "healthcare_pattern_analysis.json"
    
    if not report_file.exists():
        print(f"❌ Error: {report_file} not found. Run analyze_healthcare_patterns.py first.")
        sys.exit(1)
    
    with open(report_file) as f:
        report = json.load(f)
    
    # Get winners and non-movers from report
    winners = report['detailed_data']['winners']
    non_movers = report['detailed_data']['non_movers_sample']
    
    # Filter to biotech only
    biotech_winners = [w for w in winners if w.get('industry') == 'Biotechnology']
    biotech_non_movers = [n for n in non_movers if n.get('industry') == 'Biotechnology']
    
    # Load losers from recall files (records with max_excursion < -1%)
    print(f"📊 Loaded {len(biotech_winners)} biotech winners")
    print(f"📊 Loaded {len(biotech_non_movers)} biotech non-movers (sampled)")
    print("📂 Loading biotech losers from recall files...")
    
    biotech_losers = []
    recall_dir = PROJECT_ROOT / "tmp" / "statistics" / "recall" / "2026" / "01"
    
    if recall_dir.exists():
        json_files = []
        for week_dir in sorted(recall_dir.glob("week_*")):
            for day_dir in sorted(week_dir.glob("*")):
                for session_dir in sorted(day_dir.glob("*")):
                    for json_file in sorted(session_dir.glob("*.json")):
                        json_files.append(json_file)
        
        for json_file in json_files:
            try:
                with open(json_file) as f:
                    data = json.load(f)
                
                records = data if isinstance(data, list) else data.get('records', [])
                
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    
                    # Check if biotech
                    metadata = record.get('ticker_metadata', {})
                    is_biotech = False
                    for ticker_data in metadata.values():
                        if isinstance(ticker_data, dict):
                            industry = ticker_data.get('industry', '')
                            if 'Biotechnology' in industry or 'Biotech' in industry:
                                is_biotech = True
                                break
                    
                    if not is_biotech:
                        continue
                    
                    # Check if loser (max_excursion < -1%)
                    highest_price_data = record.get('highest_price_during_hold', {})
                    initial_nbbo = record.get('initial_nbbo', {})
                    
                    if highest_price_data and isinstance(highest_price_data, dict) and initial_nbbo:
                        peak_price = highest_price_data.get('price')
                        initial_ask = initial_nbbo.get('ask')
                        
                        if peak_price and initial_ask and initial_ask > 0:
                            max_excursion = ((peak_price - initial_ask) / initial_ask) * 100
                            
                            if max_excursion < -1.0:  # Loser threshold
                                # Extract headline data similar to winners format
                                loser_data = {
                                    'article_id': record.get('article_id'),
                                    'title': record.get('title', ''),
                                    'received_at': record.get('received_at'),
                                    'published_at': record.get('published_at'),
                                    'tickers': record.get('tickers', []),
                                    'industry': 'Biotechnology',
                                    'max_excursion_pct': max_excursion,
                                    'initial_ask': initial_ask,
                                    'peak_price': peak_price,
                                    'source_file': str(json_file.relative_to(PROJECT_ROOT))  # Store source file path
                                }
                                biotech_losers.append(loser_data)
            except Exception as e:
                continue
    
    print(f"📊 Loaded {len(biotech_losers)} biotech losers")
    
    return biotech_winners, biotech_losers, biotech_non_movers


def load_recall_record(article_id: str, received_at: datetime, source_file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load full recall record from JSON files to get initial_nbbo and other data."""
    
    # If source_file is provided, use it directly
    if source_file:
        file_path = PROJECT_ROOT / source_file
        if file_path.exists():
            try:
                with open(file_path) as f:
                    data = json.load(f)
                
                # Handle both list and dict formats
                records = data if isinstance(data, list) else data.get('records', [])
                
                # Find matching record
                for record in records:
                    if isinstance(record, dict) and record.get('article_id') == article_id:
                        return record
            except Exception as e:
                pass
    
    # Fallback: Search through recall files
    recall_dir = PROJECT_ROOT / "tmp" / "statistics" / "recall"
    
    if not recall_dir.exists():
        return None
    
    # Parse received_at to find the right file
    year = received_at.year
    month = received_at.month
    
    # Find week directory - search all weeks in the month
    month_dir = recall_dir / str(year) / f"{month:02d}"
    if not month_dir.exists():
        return None
    
    week_dirs = sorted(month_dir.glob("week_*"))
    if not week_dirs:
        return None
    
    # Check each week directory
    for week_dir in week_dirs:
        day_dirs = sorted(week_dir.glob("*"))
        for day_dir in day_dirs:
            # Check premarket and postmarket files
            for session_file in [day_dir / "premarket.json", day_dir / "postmarket.json"]:
                if not session_file.exists():
                    continue
                
                try:
                    with open(session_file) as f:
                        data = json.load(f)
                    
                    # Handle both list and dict formats
                    records = data if isinstance(data, list) else data.get('records', [])
                    
                    # Find matching record
                    for record in records:
                        if isinstance(record, dict):
                            if record.get('article_id') == article_id:
                                return record
                except Exception as e:
                    continue
    
    return None


def calculate_spread_compression(
    initial_spread: float,
    current_spread: float
) -> float:
    """Calculate spread compression percentage."""
    if not initial_spread or initial_spread <= 0:
        return None
    
    compression_pct = ((initial_spread - current_spread) / initial_spread) * 100
    return compression_pct


def calculate_tick_density(
    trades: List[Dict],
    window_start: datetime,
    window_end: datetime
) -> float:
    """Calculate trades per second in the window."""
    if not trades:
        return 0.0
    
    window_seconds = (window_end - window_start).total_seconds()
    if window_seconds <= 0:
        return 0.0
    
    # Filter trades in window
    trades_in_window = [
        t for t in trades
        if isinstance(t, dict) and 'timestamp' in t
        and window_start <= t['timestamp'] <= window_end
    ]
    
    return len(trades_in_window) / window_seconds if window_seconds > 0 else 0.0


def calculate_delta_ratio(
    trades: List[Dict],
    mid_price: float,
    window_start: datetime,
    window_end: datetime
) -> Optional[float]:
    """Calculate buy/sell volume ratio."""
    if not trades or not mid_price or mid_price <= 0:
        return None
    
    # Filter trades in window
    trades_in_window = [
        t for t in trades
        if isinstance(t, dict) and 'timestamp' in t
        and window_start <= t['timestamp'] <= window_end
    ]
    
    if not trades_in_window:
        return None
    
    buy_vol = sum(t.get('size', 0) for t in trades_in_window if t.get('price', 0) >= mid_price)
    sell_vol = sum(t.get('size', 0) for t in trades_in_window if t.get('price', 0) < mid_price)
    
    if sell_vol == 0:
        return float('inf') if buy_vol > 0 else None
    
    return buy_vol / sell_vol


def fetch_immediate_features(
    client: StockHistoricalDataClient,
    symbol: str,
    received_at: datetime,
    initial_nbbo: Dict[str, Any],
    lookback_seconds: float = 10.0  # Extended to 10s to check at 5s and 10s
) -> Dict[str, Any]:
    """
    Fetch and calculate immediate-entry features using Alpaca historical data.
    
    Simulates what we would see from WebSocket stream_manager in real-time.
    """
    # Ensure UTC
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    
    window_start = received_at
    window_end = received_at + timedelta(seconds=lookback_seconds)
    
    initial_spread = initial_nbbo.get('spread')
    initial_mid = initial_nbbo.get('mid')
    
    features = {
        'symbol': symbol,
        'received_at': received_at.isoformat(),
        'window_seconds': lookback_seconds,
        'spread_compression': {},
        'tick_density': {},
        'delta_ratio': {},
        'quote_frequency': {},  # NEW: Quotes per second
        'max_excursion_in_window': {},  # NEW: Price movement in window
        'data_available': False,
        'timing_analysis': {}
    }
    
    try:
        # Fetch quotes in window (simulating WebSocket quote updates)
        # Extend window to 10 seconds to capture data at 5s and 10s checkpoints
        quote_request = StockQuotesRequest(
            symbol_or_symbols=[symbol],
            start=window_start - timedelta(seconds=1),  # Small buffer
            end=received_at + timedelta(seconds=10),  # Extended to 10s
            feed=DataFeed.SIP
        )
        
        quotes_data = client.get_stock_quotes(quote_request)
        quotes = list(quotes_data[symbol]) if symbol in quotes_data.data else []
        
        # Fetch trades in window (simulating WebSocket trade updates)
        trade_request = StockTradesRequest(
            symbol_or_symbols=[symbol],
            start=window_start - timedelta(seconds=1),  # Small buffer
            end=received_at + timedelta(seconds=10),  # Extended to 10s
            feed=DataFeed.SIP
        )
        
        trades_data = client.get_stock_trades(trade_request)
        trades = list(trades_data[symbol]) if symbol in trades_data.data else []
        
        # Convert to dict format (simulating WebSocket cache format)
        quote_dicts = []
        for quote in quotes:
            quote_time = quote.timestamp
            if quote_time.tzinfo is None:
                quote_time = quote_time.replace(tzinfo=timezone.utc)
            
            bid = float(quote.bid_price) if quote.bid_price else None
            ask = float(quote.ask_price) if quote.ask_price else None
            
            if bid and ask:
                quote_dicts.append({
                    'timestamp': quote_time,
                    'bid': bid,
                    'ask': ask,
                    'spread': ask - bid,
                    'mid': (bid + ask) / 2.0
                })
        
        trade_dicts = []
        for trade in trades:
            trade_time = trade.timestamp
            if trade_time.tzinfo is None:
                trade_time = trade_time.replace(tzinfo=timezone.utc)
            
            trade_dicts.append({
                'timestamp': trade_time,
                'price': float(trade.price) if trade.price else None,
                'size': int(trade.size) if trade.size else 0
            })
        
        if not quote_dicts and not trade_dicts:
            return features
        
        features['data_available'] = True
        features['quotes_count'] = len(quote_dicts)
        features['trades_count'] = len(trade_dicts)
        
        # Calculate features at different time intervals (simulating real-time polling)
        # Check at 1s, 2s, 3s, 4s, 5s, 10s
        for check_seconds in [1.0, 2.0, 3.0, 4.0, 5.0, 10.0]:
            check_time = received_at + timedelta(seconds=check_seconds)
            check_window_end = received_at + timedelta(seconds=check_seconds)
            
            # Find latest quote at check_time
            latest_quote = None
            for q in quote_dicts:
                if q['timestamp'] <= check_time:
                    latest_quote = q
            
            # Spread compression/widening
            if latest_quote and initial_spread:
                current_spread = latest_quote['spread']
                compression = calculate_spread_compression(initial_spread, current_spread)
                features['spread_compression'][f'{check_seconds}s'] = compression
            
            # Tick density (trades per second)
            tick_density = calculate_tick_density(
                trade_dicts,
                received_at,
                check_window_end
            )
            features['tick_density'][f'{check_seconds}s'] = tick_density
            
            # Quote frequency (quotes per second) - NEW FEATURE
            quotes_in_window = sum(1 for q in quote_dicts if q['timestamp'] <= check_time)
            quote_frequency = quotes_in_window / check_seconds if check_seconds > 0 else 0
            features['quote_frequency'][f'{check_seconds}s'] = quote_frequency
            
            # Delta ratio
            if latest_quote:
                mid = latest_quote['mid']
                delta_ratio = calculate_delta_ratio(
                    trade_dicts,
                    mid,
                    received_at,
                    check_window_end
                )
                features['delta_ratio'][f'{check_seconds}s'] = delta_ratio
            
            # Max excursion (price movement) - NEW FEATURE
            if latest_quote and initial_nbbo.get('ask'):
                initial_ask = initial_nbbo.get('ask')
                current_ask = latest_quote['ask']
                if initial_ask > 0:
                    max_excursion_pct = ((current_ask - initial_ask) / initial_ask) * 100
                    features['max_excursion_in_window'][f'{check_seconds}s'] = max_excursion_pct
        
        # Timing analysis: How quickly could we have gotten this data?
        if quote_dicts:
            first_quote_time = min(q['timestamp'] for q in quote_dicts)
            latency_to_first_quote = (first_quote_time - received_at).total_seconds()
            features['timing_analysis']['latency_to_first_quote_ms'] = latency_to_first_quote * 1000
        
        if trade_dicts:
            first_trade_time = min(t['timestamp'] for t in trade_dicts)
            latency_to_first_trade = (first_trade_time - received_at).total_seconds()
            features['timing_analysis']['latency_to_first_trade_ms'] = latency_to_first_trade * 1000
        
        # Data completeness at each second
        for check_seconds in [1.0, 2.0, 3.0, 4.0, 5.0, 10.0]:
            check_time = received_at + timedelta(seconds=check_seconds)
            quotes_at_time = sum(1 for q in quote_dicts if q['timestamp'] <= check_time)
            trades_at_time = sum(1 for t in trade_dicts if t['timestamp'] <= check_time)
            features['timing_analysis'][f'quotes_at_{check_seconds}s'] = quotes_at_time
            features['timing_analysis'][f'trades_at_{check_seconds}s'] = trades_at_time
    
    except Exception as e:
        features['error'] = str(e)
        print(f"⚠️  Error fetching data for {symbol}: {e}")
    
    return features


def analyze_features(
    winners: List[Dict],
    losers: List[Dict],
    non_movers: List[Dict],
    client: StockHistoricalDataClient
) -> Dict[str, Any]:
    """Analyze immediate-entry features for winners vs losers."""
    
    print("\n🔍 Analyzing immediate-entry features...")
    print("=" * 80)
    
    winner_features = []
    loser_features = []
    
    # Process winners
    print(f"\n📈 Processing {len(winners)} winners...")
    for i, winner in enumerate(winners, 1):
        article_id = winner.get('article_id')
        received_at_str = winner.get('received_at')
        tickers = winner.get('tickers', [])
        source_file = winner.get('source_file')
        
        if not tickers or not received_at_str:
            continue
        
        ticker = tickers[0]
        
        try:
            # Handle different datetime formats
            if 'Z' in received_at_str:
                received_at = datetime.fromisoformat(received_at_str.replace('Z', '+00:00'))
            elif '+' in received_at_str or received_at_str.endswith('+00:00'):
                received_at = datetime.fromisoformat(received_at_str)
            else:
                # Try parsing without timezone
                received_at = datetime.fromisoformat(received_at_str)
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception as e:
            print(f"⚠️  Error parsing received_at for {article_id}: {e}")
            continue
        
        # Load full recall record to get initial_nbbo (use source_file if available)
        recall_record = load_recall_record(article_id, received_at, source_file=source_file)
        if not recall_record:
            print(f"⚠️  Could not find recall record for {article_id}")
            continue
        
        initial_nbbo = recall_record.get('initial_nbbo', {})
        if not initial_nbbo:
            print(f"⚠️  No initial_nbbo for {article_id}")
            continue
        
        print(f"  [{i}/{len(winners)}] {ticker} - {article_id[:20]}...")
        
        features = fetch_immediate_features(
            client,
            ticker,
            received_at,
            initial_nbbo,
            lookback_seconds=3.0
        )
        
        features['article_id'] = article_id
        features['max_excursion_pct'] = winner.get('max_excursion_pct')
        features['title'] = winner.get('title', '')[:100]
        
        winner_features.append(features)
    
    # Process non-movers (sample up to 25 for comparison)
    print(f"\n📊 Processing {min(len(non_movers), 25)} non-movers (sampled)...")
    sampled_non_movers = non_movers[:25]  # Sample first 25
    
    non_mover_features = []
    
    for i, non_mover in enumerate(sampled_non_movers, 1):
        article_id = non_mover.get('article_id')
        received_at_str = non_mover.get('received_at')
        tickers = non_mover.get('tickers', [])
        source_file = non_mover.get('source_file')
        
        if not tickers or not received_at_str:
            continue
        
        ticker = tickers[0]
        
        try:
            # Handle different datetime formats
            if 'Z' in received_at_str:
                received_at = datetime.fromisoformat(received_at_str.replace('Z', '+00:00'))
            elif '+' in received_at_str or received_at_str.endswith('+00:00'):
                received_at = datetime.fromisoformat(received_at_str)
            else:
                # Try parsing without timezone
                received_at = datetime.fromisoformat(received_at_str)
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception as e:
            continue
        
        recall_record = load_recall_record(article_id, received_at, source_file=source_file)
        if not recall_record:
            continue
        
        initial_nbbo = recall_record.get('initial_nbbo', {})
        if not initial_nbbo:
            continue
        
        print(f"  [{i}/{len(sampled_non_movers)}] {ticker} - {article_id[:20]}...")
        
        features = fetch_immediate_features(
            client,
            ticker,
            received_at,
            initial_nbbo,
            lookback_seconds=10.0  # Extended to 10s
        )
        
        features['article_id'] = article_id
        features['max_excursion_pct'] = non_mover.get('max_excursion_pct')
        features['title'] = non_mover.get('title', '')[:100]
        
        non_mover_features.append(features)
    
    # Process losers (sample up to 50 for comparison)
    print(f"\n📉 Processing {min(len(losers), 50)} losers (sampled)...")
    sampled_losers = losers[:50]  # Sample first 50
    
    for i, loser in enumerate(sampled_losers, 1):
        article_id = loser.get('article_id')
        received_at_str = loser.get('received_at')
        tickers = loser.get('tickers', [])
        source_file = loser.get('source_file')
        
        if not tickers or not received_at_str:
            continue
        
        ticker = tickers[0]
        
        try:
            # Handle different datetime formats
            if 'Z' in received_at_str:
                received_at = datetime.fromisoformat(received_at_str.replace('Z', '+00:00'))
            elif '+' in received_at_str or received_at_str.endswith('+00:00'):
                received_at = datetime.fromisoformat(received_at_str)
            else:
                # Try parsing without timezone
                received_at = datetime.fromisoformat(received_at_str)
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception as e:
            continue
        
        recall_record = load_recall_record(article_id, received_at, source_file=source_file)
        if not recall_record:
            continue
        
        initial_nbbo = recall_record.get('initial_nbbo', {})
        if not initial_nbbo:
            continue
        
        print(f"  [{i}/{len(sampled_losers)}] {ticker} - {article_id[:20]}...")
        
        features = fetch_immediate_features(
            client,
            ticker,
            received_at,
            initial_nbbo,
            lookback_seconds=10.0  # Extended to 10s
        )
        
        features['article_id'] = article_id
        features['max_excursion_pct'] = loser.get('max_excursion_pct')
        features['title'] = loser.get('title', '')[:100]
        
        loser_features.append(features)
    
    # Analyze results
    print("\n" + "=" * 80)
    print("📊 FEATURE ANALYSIS RESULTS")
    print("=" * 80)
    
    # Thresholds from IMMEDIATE_ENTRY_FEATURES.md
    SPREAD_COMPRESSION_THRESHOLD = 20.0  # >20% compression (or <-10% widening)
    TICK_DENSITY_THRESHOLD = 5.0  # >5 trades/sec
    DELTA_RATIO_THRESHOLD = 3.0  # >3:1 buy/sell
    QUOTE_FREQUENCY_THRESHOLD = 10.0  # >10 quotes/sec (NEW)
    MAX_EXCURSION_THRESHOLD = 0.5  # >0.5% price movement (NEW - early reactive)
    
    def check_criteria(features: Dict, check_time: str = '3.0s') -> Dict[str, bool]:
        """Check if features meet thresholds."""
        # Handle both '3s' and '3.0s' formats
        check_time_key = check_time if check_time in features.get('spread_compression', {}) else check_time.replace('s', '.0s')
        
        spread_comp = features.get('spread_compression', {}).get(check_time_key)
        tick_density = features.get('tick_density', {}).get(check_time_key, 0)
        delta_ratio = features.get('delta_ratio', {}).get(check_time_key)
        quote_frequency = features.get('quote_frequency', {}).get(check_time_key, 0)
        max_excursion = features.get('max_excursion_in_window', {}).get(check_time_key)
        
        # Spread: compression >20% OR widening <-10% (negative = widening)
        spread_signal = (
            (spread_comp is not None and spread_comp > SPREAD_COMPRESSION_THRESHOLD) or
            (spread_comp is not None and spread_comp < -10.0)  # Widening >10%
        )
        
        return {
            'spread_compression': spread_signal,
            'tick_density': tick_density > TICK_DENSITY_THRESHOLD,
            'delta_ratio': delta_ratio is not None and delta_ratio > DELTA_RATIO_THRESHOLD,
            'quote_frequency': quote_frequency > QUOTE_FREQUENCY_THRESHOLD,
            'max_excursion': max_excursion is not None and max_excursion > MAX_EXCURSION_THRESHOLD,
            'any_criteria': (
                spread_signal or
                tick_density > TICK_DENSITY_THRESHOLD or
                (delta_ratio is not None and delta_ratio > DELTA_RATIO_THRESHOLD) or
                quote_frequency > QUOTE_FREQUENCY_THRESHOLD or
                (max_excursion is not None and max_excursion > MAX_EXCURSION_THRESHOLD)
            ),
            'all_criteria': (
                spread_signal and
                tick_density > TICK_DENSITY_THRESHOLD and
                (delta_ratio is not None and delta_ratio > DELTA_RATIO_THRESHOLD) and
                quote_frequency > QUOTE_FREQUENCY_THRESHOLD and
                (max_excursion is not None and max_excursion > MAX_EXCURSION_THRESHOLD)
            )
        }
    
    # Analyze winners at different time intervals
    time_intervals = ['1.0s', '2.0s', '3.0s', '5.0s', '10.0s']
    
    # For summary, use 3.0s as primary
    winner_criteria = [check_criteria(f, check_time='3.0s') for f in winner_features if f.get('data_available')]
    winner_with_data = len(winner_criteria)
    
    winner_spread_met = sum(1 for c in winner_criteria if c['spread_compression'])
    winner_tick_met = sum(1 for c in winner_criteria if c['tick_density'])
    winner_delta_met = sum(1 for c in winner_criteria if c['delta_ratio'])
    winner_quote_freq_met = sum(1 for c in winner_criteria if c['quote_frequency'])
    winner_max_exc_met = sum(1 for c in winner_criteria if c['max_excursion'])
    winner_any_met = sum(1 for c in winner_criteria if c['any_criteria'])
    winner_all_met = sum(1 for c in winner_criteria if c['all_criteria'])
    
    # Analyze non-movers (check at 3.0s mark)
    non_mover_criteria = [check_criteria(f, check_time='3.0s') for f in non_mover_features if f.get('data_available')]
    
    # Analyze losers (check at 3.0s mark)
    loser_criteria = [check_criteria(f, check_time='3.0s') for f in loser_features if f.get('data_available')]
    non_mover_with_data = len(non_mover_criteria)
    loser_with_data = len(loser_criteria)
    
    non_mover_spread_met = sum(1 for c in non_mover_criteria if c['spread_compression'])
    non_mover_tick_met = sum(1 for c in non_mover_criteria if c['tick_density'])
    non_mover_delta_met = sum(1 for c in non_mover_criteria if c['delta_ratio'])
    non_mover_quote_freq_met = sum(1 for c in non_mover_criteria if c['quote_frequency'])
    non_mover_max_exc_met = sum(1 for c in non_mover_criteria if c['max_excursion'])
    non_mover_any_met = sum(1 for c in non_mover_criteria if c['any_criteria'])
    
    loser_spread_met = sum(1 for c in loser_criteria if c['spread_compression'])
    loser_tick_met = sum(1 for c in loser_criteria if c['tick_density'])
    loser_delta_met = sum(1 for c in loser_criteria if c['delta_ratio'])
    loser_quote_freq_met = sum(1 for c in loser_criteria if c['quote_frequency'])
    loser_max_exc_met = sum(1 for c in loser_criteria if c['max_excursion'])
    loser_any_met = sum(1 for c in loser_criteria if c['any_criteria'])
    loser_all_met = sum(1 for c in loser_criteria if c['all_criteria'])
    
    # Print results
    print(f"\n✅ WINNERS (n={winner_with_data} with data):")
    print(f"   Spread Compression (>20%): {winner_spread_met}/{winner_with_data} ({winner_spread_met/winner_with_data*100:.1f}%)" if winner_with_data > 0 else "   No data")
    print(f"   Tick Density (>5 trades/sec): {winner_tick_met}/{winner_with_data} ({winner_tick_met/winner_with_data*100:.1f}%)" if winner_with_data > 0 else "   No data")
    print(f"   Delta Ratio (>3:1): {winner_delta_met}/{winner_with_data} ({winner_delta_met/winner_with_data*100:.1f}%)" if winner_with_data > 0 else "   No data")
    print(f"   ANY criteria met: {winner_any_met}/{winner_with_data} ({winner_any_met/winner_with_data*100:.1f}%)" if winner_with_data > 0 else "   No data")
    print(f"   ALL criteria met: {winner_all_met}/{winner_with_data} ({winner_all_met/winner_with_data*100:.1f}%)" if winner_with_data > 0 else "   No data")
    
    print(f"\n❌ LOSERS (n={loser_with_data} with data):")
    print(f"   Spread Compression (>20%): {loser_spread_met}/{loser_with_data} ({loser_spread_met/loser_with_data*100:.1f}%)" if loser_with_data > 0 else "   No data")
    print(f"   Tick Density (>5 trades/sec): {loser_tick_met}/{loser_with_data} ({loser_tick_met/loser_with_data*100:.1f}%)" if loser_with_data > 0 else "   No data")
    print(f"   Delta Ratio (>3:1): {loser_delta_met}/{loser_with_data} ({loser_delta_met/loser_with_data*100:.1f}%)" if loser_with_data > 0 else "   No data")
    print(f"   ANY criteria met: {loser_any_met}/{loser_with_data} ({loser_any_met/loser_with_data*100:.1f}%)" if loser_with_data > 0 else "   No data")
    print(f"   ALL criteria met: {loser_all_met}/{loser_with_data} ({loser_all_met/loser_with_data*100:.1f}%)" if loser_with_data > 0 else "   No data")
    
    # Calculate discrimination power
    if winner_with_data > 0:
        print(f"\n🎯 DISCRIMINATION POWER (at 3.0s):")
        
        if loser_with_data > 0:
            spread_diff = (winner_spread_met/winner_with_data) - (loser_spread_met/loser_with_data)
            tick_diff = (winner_tick_met/winner_with_data) - (loser_tick_met/loser_with_data)
            delta_diff = (winner_delta_met/winner_with_data) - (loser_delta_met/loser_with_data)
            quote_freq_diff = (winner_quote_freq_met/winner_with_data) - (loser_quote_freq_met/loser_with_data)
            max_exc_diff = (winner_max_exc_met/winner_with_data) - (loser_max_exc_met/loser_with_data)
            any_diff = (winner_any_met/winner_with_data) - (loser_any_met/loser_with_data)
            
            print(f"   Spread Compression/Widening: +{spread_diff*100:.1f}% (winners vs losers)")
            print(f"   Tick Density: +{tick_diff*100:.1f}% (winners vs losers)")
            print(f"   Delta Ratio: +{delta_diff*100:.1f}% (winners vs losers)")
            print(f"   Quote Frequency: +{quote_freq_diff*100:.1f}% (winners vs losers)")
            print(f"   Max Excursion (>0.5%): +{max_exc_diff*100:.1f}% (winners vs losers)")
            print(f"   ANY criteria: +{any_diff*100:.1f}% (winners vs losers)")
        
        if non_mover_with_data > 0:
            spread_diff_nm = (winner_spread_met/winner_with_data) - (non_mover_spread_met/non_mover_with_data)
            tick_diff_nm = (winner_tick_met/winner_with_data) - (non_mover_tick_met/non_mover_with_data)
            delta_diff_nm = (winner_delta_met/winner_with_data) - (non_mover_delta_met/non_mover_with_data)
            quote_freq_diff_nm = (winner_quote_freq_met/winner_with_data) - (non_mover_quote_freq_met/non_mover_with_data)
            max_exc_diff_nm = (winner_max_exc_met/winner_with_data) - (non_mover_max_exc_met/non_mover_with_data)
            any_diff_nm = (winner_any_met/winner_with_data) - (non_mover_any_met/non_mover_with_data)
            
            print(f"\n   vs NON-MOVERS:")
            print(f"   Spread Compression/Widening: +{spread_diff_nm*100:.1f}%")
            print(f"   Tick Density: +{tick_diff_nm*100:.1f}%")
            print(f"   Delta Ratio: +{delta_diff_nm*100:.1f}%")
            print(f"   Quote Frequency: +{quote_freq_diff_nm*100:.1f}%")
            print(f"   Max Excursion (>0.5%): +{max_exc_diff_nm*100:.1f}%")
            print(f"   ANY criteria: +{any_diff_nm*100:.1f}%")
    
    # Analyze feature evolution over time
    print(f"\n📈 FEATURE EVOLUTION OVER TIME:")
    for time_interval in ['1.0s', '2.0s', '3.0s', '4.0s', '5.0s', '10.0s']:
        winner_criteria_at_time = [check_criteria(f, check_time=time_interval) for f in winner_features if f.get('data_available')]
        non_mover_criteria_at_time = [check_criteria(f, check_time=time_interval) for f in non_mover_features if f.get('data_available')]
        loser_criteria_at_time = [check_criteria(f, check_time=time_interval) for f in loser_features if f.get('data_available')]
        
        if winner_criteria_at_time:
            winner_any_at_time = sum(1 for c in winner_criteria_at_time if c['any_criteria'])
            print(f"   At {time_interval}:")
            print(f"     Winners (ANY criteria): {winner_any_at_time}/{len(winner_criteria_at_time)} ({winner_any_at_time/len(winner_criteria_at_time)*100:.1f}%)")
            if non_mover_criteria_at_time:
                non_mover_any_at_time = sum(1 for c in non_mover_criteria_at_time if c['any_criteria'])
                print(f"     Non-movers (ANY criteria): {non_mover_any_at_time}/{len(non_mover_criteria_at_time)} ({non_mover_any_at_time/len(non_mover_criteria_at_time)*100:.1f}%)")
            if loser_criteria_at_time:
                loser_any_at_time = sum(1 for c in loser_criteria_at_time if c['any_criteria'])
                print(f"     Losers (ANY criteria): {loser_any_at_time}/{len(loser_criteria_at_time)} ({loser_any_at_time/len(loser_criteria_at_time)*100:.1f}%)")
    
    # Timing analysis
    print(f"\n⏱️  TIMING ANALYSIS:")
    winner_timings = [f['timing_analysis'] for f in winner_features if f.get('data_available') and 'timing_analysis' in f]
    if winner_timings:
        avg_latency_quote = sum(t.get('latency_to_first_quote_ms', 0) for t in winner_timings) / len(winner_timings)
        avg_latency_trade = sum(t.get('latency_to_first_trade_ms', 0) for t in winner_timings) / len(winner_timings)
        print(f"   Average latency to first quote: {avg_latency_quote:.1f}ms")
        print(f"   Average latency to first trade: {avg_latency_trade:.1f}ms")
    
    # Save detailed results
    results = {
        'analysis_date': datetime.now(timezone.utc).isoformat(),
        'winners_analyzed': len(winner_features),
        'losers_analyzed': len(loser_features),
        'winners_with_data': winner_with_data,
        'losers_with_data': loser_with_data,
        'thresholds': {
            'spread_compression': SPREAD_COMPRESSION_THRESHOLD,
            'spread_widening': -10.0,  # <-10% widening
            'tick_density': TICK_DENSITY_THRESHOLD,
            'delta_ratio': DELTA_RATIO_THRESHOLD,
            'quote_frequency': QUOTE_FREQUENCY_THRESHOLD,
            'max_excursion': MAX_EXCURSION_THRESHOLD
        },
        'winner_stats': {
            'spread_compression_met': winner_spread_met,
            'tick_density_met': winner_tick_met,
            'delta_ratio_met': winner_delta_met,
            'quote_frequency_met': winner_quote_freq_met,
            'max_excursion_met': winner_max_exc_met,
            'any_criteria_met': winner_any_met,
            'all_criteria_met': winner_all_met
        },
        'loser_stats': {
            'spread_compression_met': loser_spread_met,
            'tick_density_met': loser_tick_met,
            'delta_ratio_met': loser_delta_met,
            'quote_frequency_met': loser_quote_freq_met,
            'max_excursion_met': loser_max_exc_met,
            'any_criteria_met': loser_any_met,
            'all_criteria_met': loser_all_met
        },
        'detailed_results': {
            'winners': winner_features,
            'losers': loser_features,
            'non_movers': non_mover_features
        }
    }
    
    output_file = PROJECT_ROOT / "biotech_backtest_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\n💾 Detailed results saved to: {output_file}")
    
    return results


def main():
    """Main entry point."""
    print("🧬 BIOTECH BACKTESTER: Immediate Entry Features Validation")
    print("=" * 80)
    
    # Check API credentials
    api_key = os.getenv("ALPACA_KEY")
    api_secret = os.getenv("ALPACA_SECRET")
    
    if not api_key or not api_secret:
        print("❌ Error: ALPACA_KEY and ALPACA_SECRET must be set in environment")
        sys.exit(1)
    
    # Initialize Alpaca client
    client = StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)
    
    # Load biotech records
    winners, losers, non_movers = load_biotech_records()
    
    if not winners:
        print("❌ Error: No biotech winners found")
        sys.exit(1)
    
    # Analyze features
    results = analyze_features(winners, losers, non_movers, client)
    
    print("\n✅ Backtest complete!")
    print("\n📋 Summary:")
    print(f"   - Analyzed {results['winners_analyzed']} winners")
    print(f"   - Analyzed {results['losers_analyzed']} losers")
    print(f"   - {results['winners_with_data']} winners had tradeable data")
    print(f"   - {results['losers_with_data']} losers had tradeable data")


if __name__ == "__main__":
    main()
