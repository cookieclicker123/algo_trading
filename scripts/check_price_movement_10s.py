#!/usr/bin/env python3
"""
Check price movement in first 10 seconds after reception for today's biotech winners.
Compare initial_ask vs price at 10 seconds to see if moves happened despite low trade volume.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

# Load environment variables
try:
    from dotenv import load_dotenv
    PROJECT_ROOT = Path(__file__).parent.parent
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockQuotesRequest, StockBarsRequest
    from alpaca.data.enums import DataFeed, TimeFrame
except ImportError:
    print("❌ Error: alpaca-py not installed")
    sys.exit(1)

ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")

if not ALPACA_KEY or not ALPACA_SECRET:
    print("❌ Error: ALPACA_KEY and ALPACA_SECRET must be set")
    sys.exit(1)

client = StockHistoricalDataClient(api_key=ALPACA_KEY, secret_key=ALPACA_SECRET, raw_data=True)


def parse_datetime(dt_str: str) -> datetime:
    """Parse datetime string to datetime object."""
    if isinstance(dt_str, str):
        try:
            if dt_str.endswith('Z'):
                dt_str = dt_str[:-1] + '+00:00'
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except ValueError:
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


def load_todays_biotech_winners() -> list[Dict[str, Any]]:
    """Load today's biotech winners."""
    report_file = PROJECT_ROOT / "todays_winners_linguistic_analysis.json"
    
    if not report_file.exists():
        print(f"❌ Error: {report_file} not found")
        sys.exit(1)
    
    with open(report_file) as f:
        report = json.load(f)
    
    industry_data = report.get('detailed_industry_data', {}).get('Biotechnology', {})
    winners = industry_data.get('winners', [])
    
    return winners


def load_recall_record(article_id: str, source_file: str) -> Optional[Dict[str, Any]]:
    """Load full recall record."""
    file_path = PROJECT_ROOT / source_file
    
    if not file_path.exists():
        return None
    
    try:
        with open(file_path) as f:
            data = json.load(f)
        
        records = data if isinstance(data, list) else data.get('records', [])
        
        for record in records:
            if isinstance(record, dict) and record.get('article_id') == article_id:
                return record
        
        return None
    except Exception as e:
        print(f"⚠️  Error loading {source_file}: {e}")
        return None


def check_price_movement(ticker: str, received_at: datetime, initial_ask: float, window_seconds: float = 10.0) -> Dict[str, Any]:
    """Check price movement in first 10 seconds after reception."""
    
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    
    window_start = received_at
    window_end = received_at + timedelta(seconds=window_seconds)
    
    result = {
        'ticker': ticker,
        'received_at': received_at.isoformat(),
        'initial_ask': initial_ask,
        'window_seconds': window_seconds,
        'quotes_at_10s': None,
        'ask_at_10s': None,
        'bid_at_10s': None,
        'mid_at_10s': None,
        'price_change_pct': None,
        'highest_ask_in_window': None,
        'highest_mid_in_window': None,
        'max_excursion_pct': None,
        'quotes_count': 0,
        'error': None
    }
    
    try:
        # Fetch quotes in the 10-second window
        quotes_request = StockQuotesRequest(
            symbol_or_symbols=[ticker],
            start=window_start - timedelta(seconds=1),  # Small buffer
            end=window_end + timedelta(seconds=1),  # Small buffer
            feed=DataFeed.SIP
        )
        
        quotes_response = client.get_stock_quotes(quotes_request)
        
        # Handle response structure
        quotes_data = quotes_response.data if hasattr(quotes_response, 'data') else quotes_response
        
        if not quotes_data or ticker not in quotes_data:
            result['error'] = 'No quotes data available'
            return result
        
        quotes_list = list(quotes_data[ticker]) if hasattr(quotes_data[ticker], '__iter__') else quotes_data[ticker]
        
        if not quotes_list:
            result['error'] = 'Empty quotes list'
            return result
        
        result['quotes_count'] = len(quotes_list)
        
        # Find quote at 10 seconds (or closest to it)
        quotes_in_window = []
        for quote in quotes_list:
            quote_time = quote.timestamp
            if quote_time.tzinfo is None:
                quote_time = quote_time.replace(tzinfo=timezone.utc)
            
            if window_start <= quote_time <= window_end:
                quotes_in_window.append({
                    'timestamp': quote_time,
                    'bid': float(quote.bid_price) if quote.bid_price else None,
                    'ask': float(quote.ask_price) if quote.ask_price else None,
                    'mid': None
                })
                if quotes_in_window[-1]['bid'] and quotes_in_window[-1]['ask']:
                    quotes_in_window[-1]['mid'] = (quotes_in_window[-1]['bid'] + quotes_in_window[-1]['ask']) / 2.0
        
        if not quotes_in_window:
            result['error'] = 'No quotes in window'
            return result
        
        # Sort by timestamp
        quotes_in_window.sort(key=lambda x: x['timestamp'])
        
        # Get quote closest to 10 seconds
        target_time = window_end
        closest_quote = min(quotes_in_window, key=lambda x: abs((x['timestamp'] - target_time).total_seconds()))
        
        result['quotes_at_10s'] = closest_quote['timestamp'].isoformat()
        result['bid_at_10s'] = closest_quote['bid']
        result['ask_at_10s'] = closest_quote['ask']
        result['mid_at_10s'] = closest_quote['mid']
        
        # Calculate price change
        if initial_ask and initial_ask > 0 and result['ask_at_10s']:
            result['price_change_pct'] = ((result['ask_at_10s'] - initial_ask) / initial_ask) * 100
        
        # Find highest ask and mid in the window
        highest_ask_quote = max(quotes_in_window, key=lambda x: x['ask'] or 0)
        highest_mid_quote = max(quotes_in_window, key=lambda x: x['mid'] or 0)
        
        result['highest_ask_in_window'] = highest_ask_quote['ask']
        result['highest_mid_in_window'] = highest_mid_quote['mid']
        result['highest_ask_timestamp'] = highest_ask_quote['timestamp'].isoformat()
        result['highest_mid_timestamp'] = highest_mid_quote['timestamp'].isoformat()
        
        # Calculate max excursion
        if initial_ask and initial_ask > 0 and result['highest_ask_in_window']:
            result['max_excursion_pct'] = ((result['highest_ask_in_window'] - initial_ask) / initial_ask) * 100
        
        # Also try fetching minute bars as fallback
        try:
            bars_request = StockBarsRequest(
                symbol_or_symbols=[ticker],
                timeframe=TimeFrame.Minute,
                start=window_start,
                end=window_end,
                feed=DataFeed.SIP
            )
            bars_response = client.get_stock_bars(bars_request)
            bars_data = bars_response.data if hasattr(bars_response, 'data') else bars_response
            
            if bars_data and ticker in bars_data:
                bars = list(bars_data[ticker]) if hasattr(bars_data[ticker], '__iter__') else bars_data[ticker]
                if bars:
                    # Get the first bar (which should cover the window)
                    first_bar = bars[0]
                    result['bar_high'] = float(first_bar.high) if first_bar.high else None
                    result['bar_low'] = float(first_bar.low) if first_bar.low else None
                    result['bar_open'] = float(first_bar.open) if first_bar.open else None
                    result['bar_close'] = float(first_bar.close) if first_bar.close else None
                    result['bar_volume'] = int(first_bar.volume) if first_bar.volume else None
                    result['bar_timestamp'] = first_bar.timestamp.isoformat() if first_bar.timestamp else None
        except Exception as e:
            result['bar_error'] = str(e)
    
    except Exception as e:
        result['error'] = str(e)
        print(f"⚠️  Error checking price movement for {ticker}: {e}")
    
    return result


def main():
    """Check price movement for today's biotech winners."""
    
    winners = load_todays_biotech_winners()
    
    print(f"\n{'='*80}")
    print(f"CHECKING PRICE MOVEMENT IN FIRST 10 SECONDS AFTER RECEPTION")
    print(f"{'='*80}\n")
    
    results = []
    
    for i, winner in enumerate(winners, 1):
        article_id = winner.get('article_id')
        title = winner.get('title', '')
        max_excursion = winner.get('max_excursion_pct', 0)
        initial_ask = winner.get('initial_ask')
        source_file = winner.get('source_file')
        
        print(f"\n[{i}/{len(winners)}] {title[:80]}...")
        print(f"  Article ID: {article_id}")
        print(f"  Final Max Excursion: {max_excursion:.2f}%")
        
        # Load full recall record
        record = load_recall_record(article_id, source_file)
        
        if not record:
            print(f"  ❌ Could not load recall record")
            continue
        
        received_at_str = record.get('received_at') or record.get('published_at')
        tickers = record.get('tickers', [])
        
        if not received_at_str or not tickers:
            print(f"  ❌ Missing received_at or tickers")
            continue
        
        try:
            received_at = parse_datetime(received_at_str)
        except Exception as e:
            print(f"  ❌ Could not parse received_at: {e}")
            continue
        
        # Get initial_ask from record if not in winner data
        if not initial_ask:
            initial_nbbo = record.get('initial_nbbo', {})
            initial_ask = initial_nbbo.get('ask')
        
        if not initial_ask or initial_ask <= 0:
            print(f"  ❌ No valid initial_ask")
            continue
        
        ticker = tickers[0] if tickers else None
        
        if not ticker:
            print(f"  ❌ No ticker found")
            continue
        
        print(f"  Ticker: {ticker}")
        print(f"  Initial Ask: ${initial_ask:.4f}")
        print(f"  Received at: {received_at.isoformat()}")
        
        # Check price movement
        price_data = check_price_movement(ticker, received_at, initial_ask, window_seconds=10.0)
        
        print(f"\n  📊 Price Movement (0-10 seconds):")
        
        if price_data.get('error'):
            print(f"    ❌ Error: {price_data['error']}")
        else:
            print(f"    Quotes in window: {price_data['quotes_count']}")
            
            if price_data.get('ask_at_10s'):
                print(f"    Ask at 10s: ${price_data['ask_at_10s']:.4f}")
                if price_data.get('price_change_pct') is not None:
                    print(f"    Price Change (0-10s): {price_data['price_change_pct']:.2f}%")
            
            if price_data.get('highest_ask_in_window'):
                print(f"    Highest Ask in window: ${price_data['highest_ask_in_window']:.4f}")
                print(f"    At: {price_data.get('highest_ask_timestamp', 'N/A')}")
                if price_data.get('max_excursion_pct') is not None:
                    print(f"    Max Excursion (0-10s): {price_data['max_excursion_pct']:.2f}%")
            
            if price_data.get('bar_high'):
                print(f"    Minute Bar High: ${price_data['bar_high']:.4f}")
                if initial_ask:
                    bar_excursion = ((price_data['bar_high'] - initial_ask) / initial_ask) * 100
                    print(f"    Bar Excursion: {bar_excursion:.2f}%")
        
        # Compare to final max excursion
        if price_data.get('max_excursion_pct') is not None:
            print(f"\n  📈 Comparison:")
            print(f"    Max Excursion (0-10s): {price_data['max_excursion_pct']:.2f}%")
            print(f"    Final Max Excursion: {max_excursion:.2f}%")
            difference = max_excursion - price_data['max_excursion_pct']
            print(f"    Difference: {difference:.2f}% ({'moved later' if difference > 2 else 'moved early' if price_data['max_excursion_pct'] > max_excursion + 2 else 'similar'})")
        
        results.append({
            'article_id': article_id,
            'title': title,
            'ticker': ticker,
            'final_max_excursion_pct': max_excursion,
            'initial_ask': initial_ask,
            'received_at': received_at.isoformat(),
            'price_movement_10s': price_data
        })
        
        import time
        time.sleep(0.5)
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}\n")
    
    for result in results:
        price_data = result.get('price_movement_10s', {})
        ticker = result.get('ticker')
        max_10s = price_data.get('max_excursion_pct')
        final_max = result.get('final_max_excursion_pct', 0)
        
        print(f"  {ticker}:")
        print(f"    Max Excursion (0-10s): {max_10s:.2f}%" if max_10s is not None else "    Max Excursion (0-10s): N/A")
        print(f"    Final Max Excursion: {final_max:.2f}%")
        if max_10s is not None and final_max > 0:
            if max_10s > 1.0:
                print(f"    ✅ Moved significantly in first 10 seconds ({max_10s:.2f}%)")
            else:
                print(f"    ⚠️  Little movement in first 10 seconds ({max_10s:.2f}%), moved later ({final_max:.2f}%)")
    
    # Save results
    output_file = PROJECT_ROOT / "biotech_price_movement_10s_analysis.json"
    with open(output_file, 'w') as f:
        json.dump({
            'analysis_date': datetime.now(timezone.utc).isoformat(),
            'window_seconds': 10.0,
            'results': results
        }, f, indent=2)
    
    print(f"\n✅ Results saved to: {output_file}")


if __name__ == "__main__":
    main()
