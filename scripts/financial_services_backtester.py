#!/usr/bin/env python3
"""
Financial Services Backtester: Validate Immediate Entry Features on Historical Winners/Losers

Tests microstructure features (tick_density, delta_ratio) for Financial Services industries.
Similar to biotech_backtester.py but for Financial Services sector.

Usage:
    python scripts/financial_services_backtester.py [industry_name]
    
    If industry_name is not provided, defaults to "Capital Markets" (best performing industry).
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


def load_financial_services_records(industry: str = "Capital Markets") -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Load Financial Services winners, losers, and non-movers for specified industry."""
    report_file = PROJECT_ROOT / "financial_services_pattern_analysis.json"
    
    if not report_file.exists():
        print(f"❌ Error: {report_file} not found. Run analyze_financial_services_patterns.py first.")
        sys.exit(1)
    
    with open(report_file) as f:
        report = json.load(f)
    
    # Get winners and non-movers from report
    winners = report['detailed_data']['winners']
    non_movers = report['detailed_data']['non_movers_sample']
    
    # Filter to specified industry
    industry_winners = [w for w in winners if w.get('industry') == industry]
    industry_non_movers = [n for n in non_movers if n.get('industry') == industry]
    
    # Load losers from recall files (records with max_excursion < -1%)
    print(f"📊 Loaded {len(industry_winners)} {industry} winners")
    print(f"📊 Loaded {len(industry_non_movers)} {industry} non-movers (sampled)")
    print(f"📂 Loading {industry} losers from recall files...")
    
    industry_losers = []
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
                    
                    # Check if Financial Services and correct industry
                    metadata = record.get('ticker_metadata', {})
                    is_financial_services = False
                    is_correct_industry = False
                    
                    for ticker_data in metadata.values():
                        if isinstance(ticker_data, dict):
                            sector = ticker_data.get('sector', '')
                            record_industry = ticker_data.get('industry', '')
                            
                            if 'financial' in sector.lower() or 'finance' in sector.lower():
                                is_financial_services = True
                            
                            if record_industry == industry:
                                is_correct_industry = True
                                break
                    
                    if not (is_financial_services and is_correct_industry):
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
                                    'industry': industry,
                                    'max_excursion_pct': max_excursion,
                                    'initial_ask': initial_ask,
                                    'peak_price': peak_price,
                                    'source_file': str(json_file.relative_to(PROJECT_ROOT))
                                }
                                industry_losers.append(loser_data)
            except Exception as e:
                continue
    
    print(f"📊 Loaded {len(industry_losers)} {industry} losers")
    
    return industry_winners, industry_losers, industry_non_movers


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
                except Exception:
                    continue
    
    return None


def calculate_spread_compression(
    quotes: List[Dict],
    initial_nbbo: Dict[str, Any],
    received_at: datetime,
    check_window_end: datetime
) -> Optional[float]:
    """Calculate spread compression percentage."""
    if not quotes:
        return None
    
    initial_spread = initial_nbbo.get('spread')
    if not initial_spread or initial_spread <= 0:
        return None
    
    # Get latest quote in window
    window_quotes = [q for q in quotes if q['timestamp'] <= check_window_end]
    if not window_quotes:
        return None
    
    latest_quote = max(window_quotes, key=lambda x: x['timestamp'])
    current_spread = latest_quote.get('spread')
    
    if not current_spread or current_spread <= 0:
        return None
    
    # Calculate compression (positive = compression, negative = widening)
    compression_pct = ((initial_spread - current_spread) / initial_spread) * 100
    return compression_pct


def calculate_tick_density(
    trades: List[Dict],
    received_at: datetime,
    check_window_end: datetime
) -> float:
    """Calculate trades per second (tick density)."""
    window_trades = [t for t in trades if t['timestamp'] <= check_window_end]
    
    if not window_trades:
        return 0.0
    
    window_duration = (check_window_end - received_at).total_seconds()
    if window_duration <= 0:
        return 0.0
    
    return len(window_trades) / window_duration


def calculate_delta_ratio(
    trades: List[Dict],
    mid_price: float,
    received_at: datetime,
    check_window_end: datetime
) -> Optional[float]:
    """Calculate buy/sell volume ratio (delta ratio)."""
    window_trades = [t for t in trades if t['timestamp'] <= check_window_end]
    
    if not window_trades:
        return None
    
    buy_volume = 0.0
    sell_volume = 0.0
    
    for trade in window_trades:
        price = trade.get('price', 0)
        size = trade.get('size', 0)
        
        if price > mid_price:
            buy_volume += size
        elif price < mid_price:
            sell_volume += size
        else:
            # At mid, split 50/50
            buy_volume += size * 0.5
            sell_volume += size * 0.5
    
    if sell_volume == 0:
        return float('inf') if buy_volume > 0 else None
    
    return buy_volume / sell_volume


def fetch_immediate_features(
    client: StockHistoricalDataClient,
    symbol: str,
    received_at: datetime,
    initial_nbbo: Dict[str, Any],
    lookback_seconds: float = 10.0
) -> Dict[str, Any]:
    """Fetch immediate-entry microstructure features from Alpaca historical data."""
    
    features = {
        'symbol': symbol,
        'received_at': received_at.isoformat(),
        'window_seconds': lookback_seconds,
        'spread_compression': {},
        'tick_density': {},
        'delta_ratio': {},
        'quote_frequency': {},
        'max_excursion_in_window': {},
        'data_available': False,
        'timing_analysis': {}
    }
    
    try:
        # Calculate time window
        start_time = received_at - timedelta(seconds=1)  # Small buffer
        end_time = received_at + timedelta(seconds=lookback_seconds)
        
        # Fetch quotes
        quotes_request = StockQuotesRequest(
            symbol_or_symbols=[symbol],
            start=start_time,
            end=end_time,
            feed=DataFeed.IEX
        )
        
        quotes_response = client.get_stock_quotes(quotes_request)
        quote_dicts = []
        
        if symbol in quotes_response:
            for quote in quotes_response[symbol]:
                quote_dicts.append({
                    'timestamp': quote.timestamp,
                    'bid': quote.bid_price,
                    'ask': quote.ask_price,
                    'spread': quote.ask_price - quote.bid_price if quote.ask_price and quote.bid_price else None,
                    'mid': (quote.bid_price + quote.ask_price) / 2 if quote.bid_price and quote.ask_price else None
                })
        
        # Fetch trades
        trades_request = StockTradesRequest(
            symbol_or_symbols=[symbol],
            start=start_time,
            end=end_time,
            feed=DataFeed.IEX
        )
        
        trades_response = client.get_stock_trades(trades_request)
        trade_dicts = []
        
        if symbol in trades_response:
            for trade in trades_response[symbol]:
                trade_dicts.append({
                    'timestamp': trade.timestamp,
                    'price': trade.price,
                    'size': trade.size
                })
        
        if not quote_dicts and not trade_dicts:
            features['error'] = 'No trades/quotes in window'
            return features
        
        features['data_available'] = True
        features['quotes_count'] = len(quote_dicts)
        features['trades_count'] = len(trade_dicts)
        
        # Calculate features at different time intervals
        for check_seconds in [1.0, 2.0, 3.0, 4.0, 5.0, 10.0]:
            check_window_end = received_at + timedelta(seconds=check_seconds)
            
            # Spread compression
            spread_comp = calculate_spread_compression(
                quote_dicts,
                initial_nbbo,
                received_at,
                check_window_end
            )
            if spread_comp is not None:
                features['spread_compression'][f'{check_seconds}s'] = spread_comp
            
            # Tick density
            tick_density = calculate_tick_density(
                trade_dicts,
                received_at,
                check_window_end
            )
            features['tick_density'][f'{check_seconds}s'] = tick_density
            
            # Quote frequency
            window_quotes = [q for q in quote_dicts if q['timestamp'] <= check_window_end]
            quote_freq = len(window_quotes) / check_seconds if check_seconds > 0 else 0
            features['quote_frequency'][f'{check_seconds}s'] = quote_freq
            
            # Delta ratio
            latest_quote = max(window_quotes, key=lambda x: x['timestamp']) if window_quotes else None
            if latest_quote:
                mid = latest_quote['mid']
                delta_ratio = calculate_delta_ratio(
                    trade_dicts,
                    mid,
                    received_at,
                    check_window_end
                )
                features['delta_ratio'][f'{check_seconds}s'] = delta_ratio
            
            # Max excursion (price movement)
            if latest_quote and initial_nbbo.get('ask'):
                initial_ask = initial_nbbo.get('ask')
                current_ask = latest_quote['ask']
                if initial_ask > 0:
                    max_excursion_pct = ((current_ask - initial_ask) / initial_ask) * 100
                    features['max_excursion_in_window'][f'{check_seconds}s'] = max_excursion_pct
        
        # Timing analysis
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


def check_criteria(features: Dict, check_time: str = '3.0s') -> Dict[str, bool]:
    """Check if features meet trading criteria."""
    
    # Use correct key format
    check_time_key = check_time if check_time in features.get('tick_density', {}) else check_time.replace('s', '.0s')
    
    # Thresholds (relaxed based on biotech analysis)
    TICK_DENSITY_THRESHOLD = 1.0
    DELTA_RATIO_THRESHOLD = 1.0
    
    tick_density = features.get('tick_density', {}).get(check_time_key, 0)
    delta_ratio = features.get('delta_ratio', {}).get(check_time_key)
    
    tick_density_met = tick_density > TICK_DENSITY_THRESHOLD
    delta_ratio_met = delta_ratio is not None and delta_ratio > DELTA_RATIO_THRESHOLD
    
    any_criteria = tick_density_met or delta_ratio_met
    
    return {
        'tick_density': tick_density_met,
        'delta_ratio': delta_ratio_met,
        'any_criteria': any_criteria
    }


def analyze_features(
    winners: List[Dict],
    losers: List[Dict],
    non_movers: List[Dict],
    client: StockHistoricalDataClient,
    industry: str
) -> Dict[str, Any]:
    """Analyze immediate-entry features for winners vs losers."""
    
    print(f"\n🔍 Analyzing immediate-entry features for {industry}...")
    print("=" * 80)
    
    winner_features = []
    loser_features = []
    non_mover_features = []
    
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
            if 'Z' in received_at_str:
                received_at = datetime.fromisoformat(received_at_str.replace('Z', '+00:00'))
            elif '+' in received_at_str or received_at_str.endswith('+00:00'):
                received_at = datetime.fromisoformat(received_at_str)
            else:
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
        
        print(f"  [{i}/{len(winners)}] {ticker} - {article_id[:20]}...")
        
        features = fetch_immediate_features(
            client,
            ticker,
            received_at,
            initial_nbbo,
            lookback_seconds=10.0
        )
        
        features['article_id'] = article_id
        features['max_excursion_pct'] = winner.get('max_excursion_pct')
        features['title'] = winner.get('title', '')[:100]
        
        winner_features.append(features)
    
    # Process losers
    print(f"\n📉 Processing {len(losers)} losers...")
    for i, loser in enumerate(losers, 1):
        article_id = loser.get('article_id')
        received_at_str = loser.get('received_at')
        tickers = loser.get('tickers', [])
        source_file = loser.get('source_file')
        
        if not tickers or not received_at_str:
            continue
        
        ticker = tickers[0]
        
        try:
            if 'Z' in received_at_str:
                received_at = datetime.fromisoformat(received_at_str.replace('Z', '+00:00'))
            elif '+' in received_at_str:
                received_at = datetime.fromisoformat(received_at_str)
            else:
                received_at = datetime.fromisoformat(received_at_str)
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        
        recall_record = load_recall_record(article_id, received_at, source_file=source_file)
        if not recall_record:
            continue
        
        initial_nbbo = recall_record.get('initial_nbbo', {})
        if not initial_nbbo:
            continue
        
        print(f"  [{i}/{len(losers)}] {ticker} - {article_id[:20]}...")
        
        features = fetch_immediate_features(
            client,
            ticker,
            received_at,
            initial_nbbo,
            lookback_seconds=10.0
        )
        
        features['article_id'] = article_id
        features['max_excursion_pct'] = loser.get('max_excursion_pct')
        features['title'] = loser.get('title', '')[:100]
        
        loser_features.append(features)
    
    # Process non-movers
    print(f"\n📊 Processing {len(non_movers)} non-movers...")
    for i, non_mover in enumerate(non_movers, 1):
        article_id = non_mover.get('article_id')
        received_at_str = non_mover.get('received_at')
        tickers = non_mover.get('tickers', [])
        source_file = non_mover.get('source_file')
        
        if not tickers or not received_at_str:
            continue
        
        ticker = tickers[0]
        
        try:
            if 'Z' in received_at_str:
                received_at = datetime.fromisoformat(received_at_str.replace('Z', '+00:00'))
            elif '+' in received_at_str:
                received_at = datetime.fromisoformat(received_at_str)
            else:
                received_at = datetime.fromisoformat(received_at_str)
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        
        recall_record = load_recall_record(article_id, received_at, source_file=source_file)
        if not recall_record:
            continue
        
        initial_nbbo = recall_record.get('initial_nbbo', {})
        if not initial_nbbo:
            continue
        
        print(f"  [{i}/{len(non_movers)}] {ticker} - {article_id[:20]}...")
        
        features = fetch_immediate_features(
            client,
            ticker,
            received_at,
            initial_nbbo,
            lookback_seconds=10.0
        )
        
        features['article_id'] = article_id
        features['max_excursion_pct'] = non_mover.get('max_excursion_pct')
        features['title'] = non_mover.get('title', '')[:100]
        
        non_mover_features.append(features)
    
    # Analyze results
    print("\n" + "=" * 80)
    print("📊 ANALYSIS RESULTS")
    print("=" * 80)
    
    winners_with_data = [w for w in winner_features if w.get('data_available')]
    losers_with_data = [l for l in loser_features if l.get('data_available')]
    non_movers_with_data = [n for n in non_mover_features if n.get('data_available')]
    
    print(f"\nData Availability:")
    print(f"  Winners: {len(winners_with_data)}/{len(winner_features)}")
    print(f"  Losers: {len(losers_with_data)}/{len(loser_features)}")
    print(f"  Non-movers: {len(non_movers_with_data)}/{len(non_mover_features)}")
    
    # Check criteria at 3.0s
    winners_meeting_criteria = sum(1 for w in winners_with_data if check_criteria(w, '3.0s')['any_criteria'])
    losers_meeting_criteria = sum(1 for l in losers_with_data if check_criteria(l, '3.0s')['any_criteria'])
    non_movers_meeting_criteria = sum(1 for n in non_movers_with_data if check_criteria(n, '3.0s')['any_criteria'])
    
    total_signals = winners_meeting_criteria + losers_meeting_criteria + non_movers_meeting_criteria
    precision = (winners_meeting_criteria / total_signals * 100) if total_signals > 0 else 0
    recall = (winners_meeting_criteria / len(winners_with_data) * 100) if winners_with_data else 0
    
    print(f"\n🎯 Signal Performance (at 3.0s):")
    print(f"  Winners meeting criteria: {winners_meeting_criteria}/{len(winners_with_data)} ({recall:.1f}% recall)")
    print(f"  Losers meeting criteria: {losers_meeting_criteria}/{len(losers_with_data)}")
    print(f"  Non-movers meeting criteria: {non_movers_meeting_criteria}/{len(non_movers_with_data)}")
    print(f"  Precision: {precision:.1f}% ({winners_meeting_criteria}/{total_signals})")
    
    # Create results report
    results = {
        'analysis_date': datetime.now(timezone.utc).isoformat(),
        'industry': industry,
        'winners_analyzed': len(winner_features),
        'losers_analyzed': len(loser_features),
        'non_movers_analyzed': len(non_mover_features),
        'winners_with_data': len(winners_with_data),
        'losers_with_data': len(losers_with_data),
        'non_movers_with_data': len(non_movers_with_data),
        'thresholds': {
            'tick_density': 1.0,
            'delta_ratio': 1.0
        },
        'winner_stats': {
            'tick_density_met': sum(1 for w in winners_with_data if check_criteria(w, '3.0s')['tick_density']),
            'delta_ratio_met': sum(1 for w in winners_with_data if check_criteria(w, '3.0s')['delta_ratio']),
            'any_criteria_met': winners_meeting_criteria
        },
        'loser_stats': {
            'tick_density_met': sum(1 for l in losers_with_data if check_criteria(l, '3.0s')['tick_density']),
            'delta_ratio_met': sum(1 for l in losers_with_data if check_criteria(l, '3.0s')['delta_ratio']),
            'any_criteria_met': losers_meeting_criteria
        },
        'non_mover_stats': {
            'tick_density_met': sum(1 for n in non_movers_with_data if check_criteria(n, '3.0s')['tick_density']),
            'delta_ratio_met': sum(1 for n in non_movers_with_data if check_criteria(n, '3.0s')['delta_ratio']),
            'any_criteria_met': non_movers_meeting_criteria
        },
        'performance': {
            'precision': precision,
            'recall': recall,
            'false_positives': losers_meeting_criteria + non_movers_meeting_criteria
        },
        'detailed_results': {
            'winners': winner_features,
            'losers': loser_features,
            'non_movers': non_mover_features
        }
    }
    
    # Save results
    output_file = PROJECT_ROOT / f"{industry.lower().replace(' ', '_')}_backtest_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n✅ Saved results to: {output_file}")
    
    return results


def main():
    """Main function."""
    # Get industry from command line or default to Capital Markets
    industry = sys.argv[1] if len(sys.argv) > 1 else "Capital Markets"
    
    print(f"🎯 Financial Services Backtester")
    print(f"   Industry: {industry}")
    print("=" * 80)
    
    # Load records
    winners, losers, non_movers = load_financial_services_records(industry)
    
    if not winners:
        print(f"❌ No winners found for {industry}")
        return
    
    # Initialize Alpaca client
    api_key = os.getenv('ALPACA_KEY')
    api_secret = os.getenv('ALPACA_SECRET')
    
    if not api_key or not api_secret:
        print("❌ Error: ALPACA_KEY and ALPACA_SECRET must be set in .env file")
        sys.exit(1)
    
    client = StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)
    
    # Analyze features
    results = analyze_features(winners, losers, non_movers, client, industry)
    
    print(f"\n✅ Analysis complete!")


if __name__ == "__main__":
    main()
