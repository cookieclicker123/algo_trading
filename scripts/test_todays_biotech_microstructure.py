#!/usr/bin/env python3
"""
Test Today's Biotech Winners: Check if microstructure signal would have worked in first 3 seconds.

For each biotech winner today (>5% gain), check if tick_density > 1.0 OR delta_ratio > 1.0
in the first 3 seconds after publication.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    PROJECT_ROOT = Path(__file__).parent.parent
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()
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
    from alpaca.data.requests import StockTradesRequest, StockQuotesRequest
    from alpaca.data.enums import DataFeed
except ImportError:
    print("❌ Error: alpaca-py not installed. Install with: pip install alpaca-py")
    sys.exit(1)

# Initialize Alpaca client
ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")

if not ALPACA_KEY or not ALPACA_SECRET:
    print("❌ Error: ALPACA_KEY and ALPACA_SECRET must be set in .env file")
    sys.exit(1)

client = StockHistoricalDataClient(api_key=ALPACA_KEY, secret_key=ALPACA_SECRET, raw_data=True)


def load_todays_biotech_winners() -> list[Dict[str, Any]]:
    """Load today's biotech winners from the linguistic analysis."""
    report_file = PROJECT_ROOT / "todays_winners_linguistic_analysis.json"
    
    if not report_file.exists():
        print(f"❌ Error: {report_file} not found. Run analyze_todays_winners_vs_losses.py first.")
        sys.exit(1)
    
    with open(report_file) as f:
        report = json.load(f)
    
    # Get biotech winners from detailed_industry_data
    industry_data = report.get('detailed_industry_data', {}).get('Biotechnology', {})
    winners = industry_data.get('winners', [])
    
    print(f"📊 Found {len(winners)} biotech winners today")
    return winners


def load_recall_record(article_id: str, source_file: str) -> Optional[Dict[str, Any]]:
    """Load full recall record to get published_at and tickers."""
    file_path = PROJECT_ROOT / source_file
    
    if not file_path.exists():
        print(f"⚠️  Warning: Source file not found: {source_file}")
        return None
    
    try:
        with open(file_path) as f:
            data = json.load(f)
        
        # Handle both list and dict formats
        records = data if isinstance(data, list) else data.get('records', [])
        
        # Find matching record
        for record in records:
            if isinstance(record, dict) and record.get('article_id') == article_id:
                return record
        
        print(f"⚠️  Warning: Record {article_id} not found in {source_file}")
        return None
    except Exception as e:
        print(f"⚠️  Error loading {source_file}: {e}")
        return None


def parse_datetime(dt_str: str) -> datetime:
    """Parse datetime string to datetime object."""
    if isinstance(dt_str, str):
        # Try ISO format first
        try:
            if dt_str.endswith('Z'):
                dt_str = dt_str[:-1] + '+00:00'
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except ValueError:
            # Try other formats
            for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S']:
                try:
                    dt = datetime.strptime(dt_str.split('+')[0].split('Z')[0], fmt)
                    if 'Z' in dt_str or '+' in dt_str:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    continue
            raise ValueError(f"Could not parse datetime: {dt_str}")
    return dt_str


def fetch_immediate_features(ticker: str, event_time: datetime, lookback_seconds: float = 10.0) -> Dict[str, Any]:
    """Fetch tick density and delta ratio in first 3 seconds after publication."""
    
    # Define window: from event_time to event_time + lookback_seconds
    window_start = event_time
    window_end = event_time + timedelta(seconds=lookback_seconds)
    
    # Ensure timezone-aware
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)
    
    features = {
        'tick_density': 0.0,
        'delta_ratio': 0.0,
        'trades_count': 0,
        'buy_volume': 0.0,
        'sell_volume': 0.0,
        'total_volume': 0.0,
        'window_start': window_start.isoformat(),
        'window_end': window_end.isoformat(),
        'error': None
    }
    
    try:
        # Fetch trades in the window
        trades_request = StockTradesRequest(
            symbol_or_symbols=[ticker],
            start=window_start,
            end=window_end,
            feed=DataFeed.SIP
        )
        
        trades_response = client.get_stock_trades(trades_request)
        
        # Handle response structure (check both .data and direct access)
        if not trades_response:
            features['error'] = 'No trades response'
            return features
        
        trades_data = trades_response.data if hasattr(trades_response, 'data') else trades_response
        
        if not trades_data or ticker not in trades_data:
            features['error'] = 'No trades data available'
            return features
        
        trades = list(trades_data[ticker]) if hasattr(trades_data[ticker], '__iter__') else trades_data[ticker]
        
        if not trades:
            features['error'] = 'No trades in window'
            return features
        
        # Calculate tick density (trades per second)
        window_duration_seconds = lookback_seconds
        features['trades_count'] = len(trades)
        features['tick_density'] = len(trades) / window_duration_seconds if window_duration_seconds > 0 else 0.0
        
        # Calculate buy/sell volume and delta ratio
        buy_volume = 0.0
        sell_volume = 0.0
        previous_price = None
        
        # Fetch quotes to determine buy/sell
        quotes_request = StockQuotesRequest(
            symbol_or_symbols=[ticker],
            start=window_start,
            end=window_end,
            feed=DataFeed.SIP
        )
        
        quotes_response = client.get_stock_quotes(quotes_request)
        quotes_dict = {}
        
        # Handle response structure (check both .data and direct access)
        if quotes_response:
            quotes_data = quotes_response.data if hasattr(quotes_response, 'data') else quotes_response
            
            if quotes_data and ticker in quotes_data:
                quotes_list = list(quotes_data[ticker]) if hasattr(quotes_data[ticker], '__iter__') else quotes_data[ticker]
                
                for quote in quotes_list:
                    quote_time = quote.timestamp
                    if quote_time.tzinfo is None:
                        quote_time = quote_time.replace(tzinfo=timezone.utc)
                    quotes_dict[quote_time] = quote
        
        # Classify trades as buy or sell based on quote data
        for trade in trades:
            trade_price = float(trade.price) if trade.price else None
            trade_size = float(trade.size) if trade.size else 0.0
            trade_time = trade.timestamp
            
            if trade_time.tzinfo is None:
                trade_time = trade_time.replace(tzinfo=timezone.utc)
            
            if trade_price is None or trade_size == 0:
                continue
            
            # Find nearest quote to classify trade
            nearest_quote = None
            min_time_diff = None
            
            for quote_time, quote in quotes_dict.items():
                time_diff = abs((trade_time - quote_time).total_seconds())
                if min_time_diff is None or time_diff < min_time_diff:
                    min_time_diff = time_diff
                    nearest_quote = quote
            
            if nearest_quote and min_time_diff and min_time_diff < 1.0:  # Quote within 1 second
                bid = float(nearest_quote.bid) if nearest_quote.bid else None
                ask = float(nearest_quote.ask) if nearest_quote.ask else None
                
                if bid and ask:
                    mid = (bid + ask) / 2
                    # Trade above mid = buy, below mid = sell
                    if trade_price >= mid:
                        buy_volume += trade_size
                    else:
                        sell_volume += trade_size
            else:
                # Fallback: compare to previous price
                if previous_price is not None:
                    if trade_price >= previous_price:
                        buy_volume += trade_size
                    else:
                        sell_volume += trade_size
                previous_price = trade_price
            
            features['total_volume'] += trade_size
        
        features['buy_volume'] = buy_volume
        features['sell_volume'] = sell_volume
        
        # Calculate delta ratio (buy/sell, avoid division by zero)
        if sell_volume > 0:
            features['delta_ratio'] = buy_volume / sell_volume
        elif buy_volume > 0:
            features['delta_ratio'] = 999.0  # All buys, very high ratio
        else:
            features['delta_ratio'] = 0.0
    
    except Exception as e:
        features['error'] = str(e)
        print(f"⚠️  Error fetching features for {ticker}: {e}")
    
    return features


def main():
    """Test today's biotech winners against microstructure signal."""
    
    # Load today's biotech winners
    winners = load_todays_biotech_winners()
    
    if not winners:
        print("❌ No biotech winners found today!")
        return
    
    print(f"\n{'='*80}")
    print(f"TESTING TODAY'S BIOTECH WINNERS AGAINST MICROSTRUCTURE SIGNAL")
    print(f"Signal: tick_density > 1.0 OR delta_ratio > 1.0 (in first 10 seconds)")
    print(f"{'='*80}\n")
    
    results = []
    
    for i, winner in enumerate(winners, 1):
        article_id = winner.get('article_id')
        title = winner.get('title', '')
        max_excursion = winner.get('max_excursion_pct', 0)
        source_file = winner.get('source_file')
        
        print(f"\n[{i}/{len(winners)}] Testing: {title[:80]}...")
        print(f"  Article ID: {article_id}")
        print(f"  Max Excursion: {max_excursion:.2f}%")
        
        # Load full recall record to get published_at and tickers
        record = load_recall_record(article_id, source_file)
        
        if not record:
            print(f"  ❌ Could not load recall record")
            results.append({
                'article_id': article_id,
                'title': title,
                'max_excursion_pct': max_excursion,
                'status': 'error',
                'error': 'Could not load recall record'
            })
            continue
        
        # Get received_at (when we got the article - 1s after published_at via websocket)
        # We use received_at because that's when we would execute the trade in real-time
        received_at_str = record.get('received_at') or record.get('published_at')
        tickers = record.get('tickers', [])
        
        if not received_at_str or not tickers:
            print(f"  ❌ Missing received_at or tickers")
            results.append({
                'article_id': article_id,
                'title': title,
                'max_excursion_pct': max_excursion,
                'status': 'error',
                'error': 'Missing received_at or tickers'
            })
            continue
        
        try:
            received_at = parse_datetime(received_at_str)
        except Exception as e:
            print(f"  ❌ Could not parse received_at: {e}")
            results.append({
                'article_id': article_id,
                'title': title,
                'max_excursion_pct': max_excursion,
                'status': 'error',
                'error': f'Could not parse received_at: {e}'
            })
            continue
        
        # Test primary ticker (first ticker)
        ticker = tickers[0] if tickers else None
        
        if not ticker:
            print(f"  ❌ No ticker found")
            results.append({
                'article_id': article_id,
                'title': title,
                'max_excursion_pct': max_excursion,
                'status': 'error',
                'error': 'No ticker found'
            })
            continue
        
        print(f"  Ticker: {ticker}")
        print(f"  Received at: {received_at.isoformat()}")
        
        # Fetch immediate features (first 10 seconds after we receive the article)
        # This simulates: websocket receives article -> classify -> fetch quotes/trades -> execute
        print(f"  Fetching microstructure features (0-10 seconds after receipt)...")
        features = fetch_immediate_features(ticker, received_at, lookback_seconds=10.0)
        
        tick_density = features.get('tick_density', 0.0)
        delta_ratio = features.get('delta_ratio', 0.0)
        trades_count = features.get('trades_count', 0)
        buy_volume = features.get('buy_volume', 0.0)
        sell_volume = features.get('sell_volume', 0.0)
        
        print(f"  Results (0-10 seconds):")
        print(f"    Trades: {trades_count}")
        print(f"    Tick Density: {tick_density:.2f} trades/sec")
        print(f"    Buy Volume: {buy_volume:.2f}")
        print(f"    Sell Volume: {sell_volume:.2f}")
        print(f"    Delta Ratio: {delta_ratio:.2f}")
        
        # Check if signal would have triggered
        signal_triggered = tick_density > 1.0 or delta_ratio > 1.0
        
        if features.get('error'):
            print(f"    ⚠️  Error: {features.get('error')}")
        
        if signal_triggered:
            print(f"    ✅ SIGNAL TRIGGERED (tick_density > 1.0 OR delta_ratio > 1.0)")
        else:
            print(f"    ❌ SIGNAL NOT TRIGGERED (tick_density <= 1.0 AND delta_ratio <= 1.0)")
        
        results.append({
            'article_id': article_id,
            'title': title,
            'ticker': ticker,
            'max_excursion_pct': max_excursion,
            'received_at': received_at.isoformat(),
            'features': features,
            'signal_triggered': signal_triggered,
            'status': 'success'
        })
        
        # Small delay to avoid rate limiting
        import time
        time.sleep(0.5)
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}\n")
    
    total_tested = len([r for r in results if r.get('status') == 'success'])
    signals_triggered = len([r for r in results if r.get('signal_triggered') == True])
    errors = len([r for r in results if r.get('status') == 'error'])
    
    print(f"Total Winners: {len(winners)}")
    print(f"Successfully Tested: {total_tested}")
    print(f"Errors: {errors}")
    print(f"Signals Triggered: {signals_triggered}/{total_tested} ({signals_triggered/total_tested*100:.1f}%)" if total_tested > 0 else "Signals Triggered: N/A")
    
    print(f"\n📊 Detailed Results:")
    for result in results:
        if result.get('status') == 'success':
            status_icon = "✅" if result.get('signal_triggered') else "❌"
            print(f"  {status_icon} {result.get('ticker')}: {result.get('max_excursion_pct'):.2f}% - "
                  f"tick_density={result['features'].get('tick_density', 0):.2f}, "
                  f"delta_ratio={result['features'].get('delta_ratio', 0):.2f}")
    
    # Save results
    output_file = PROJECT_ROOT / "todays_biotech_microstructure_test.json"
    with open(output_file, 'w') as f:
        json.dump({
            'test_date': datetime.now(timezone.utc).isoformat(),
            'signal_definition': 'tick_density > 1.0 OR delta_ratio > 1.0 (first 10 seconds)',
            'summary': {
                'total_winners': len(winners),
                'successfully_tested': total_tested,
                'errors': errors,
                'signals_triggered': signals_triggered,
                'signal_rate': signals_triggered / total_tested * 100 if total_tested > 0 else 0
            },
            'results': results
        }, f, indent=2)
    
    print(f"\n✅ Results saved to: {output_file}")


if __name__ == "__main__":
    main()
