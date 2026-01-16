#!/usr/bin/env python3
"""
Check actual price movement at exactly 10 seconds after reception using Alpaca historical data.
This shows what the price was at 10 seconds, not when the peak occurred.
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
    from alpaca.data.requests import StockQuotesRequest
    from alpaca.data.enums import DataFeed
except ImportError:
    print("❌ Error: alpaca-py not installed")
    print("   Install with: pip install alpaca-py")
    sys.exit(1)

ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")

if not ALPACA_KEY or not ALPACA_SECRET:
    print("❌ Error: ALPACA_KEY and ALPACA_SECRET must be set in .env file")
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


def get_quote_at_time(ticker: str, target_time: datetime) -> Optional[Dict[str, Any]]:
    """Get the quote closest to target_time using Alpaca historical data."""
    
    if target_time.tzinfo is None:
        target_time = target_time.replace(tzinfo=timezone.utc)
    
    # Fetch quotes in a window around target time (30 seconds before, 5 seconds after)
    # This ensures we get quotes even for illiquid stocks
    start_time = target_time - timedelta(seconds=30)
    end_time = target_time + timedelta(seconds=5)
    
    try:
        quotes_request = StockQuotesRequest(
            symbol_or_symbols=[ticker],
            start=start_time,
            end=end_time,
            feed=DataFeed.SIP
        )
        
        try:
            quotes_response = client.get_stock_quotes(quotes_request)
        except Exception as e:
            return None
        
        # With raw_data=True, response is a dict: {ticker: [quotes...]}
        if not isinstance(quotes_response, dict) or ticker not in quotes_response:
            return None
        
        quotes_list = quotes_response[ticker]
        
        # Convert to list if it's an iterator/generator
        if hasattr(quotes_list, '__iter__') and not isinstance(quotes_list, (list, tuple)):
            quotes_list = list(quotes_list)
        
        if not quotes_list:
            return None
        
        if not quotes_list:
            return None
        
        # Find quote closest to target_time (at or before target_time preferred)
        closest_quote = None
        min_time_diff = None
        
        for quote in quotes_list:
            # With raw_data=True, quotes are dicts: {'t': timestamp, 'ap': ask_price, 'bp': bid_price, ...}
            if isinstance(quote, dict):
                quote_time_str = quote.get('t')
                if not quote_time_str:
                    continue
                quote_time = parse_datetime(quote_time_str)
            elif hasattr(quote, 'timestamp'):
                quote_time = quote.timestamp
            else:
                continue
                
            if quote_time.tzinfo is None:
                quote_time = quote_time.replace(tzinfo=timezone.utc)
            
            time_diff = abs((quote_time - target_time).total_seconds())
            
            # Prefer quotes at or before target_time, but accept after if close
            if quote_time <= target_time:
                if closest_quote is None or time_diff < min_time_diff:
                    closest_quote = quote
                    min_time_diff = time_diff
            elif closest_quote is None and time_diff < 5.0:  # Accept quotes up to 5s after
                closest_quote = quote
                min_time_diff = time_diff
        
        # If no quote before target_time, use closest one
        if closest_quote is None:
            for quote in quotes_list:
                if isinstance(quote, dict):
                    quote_time_str = quote.get('t')
                    if not quote_time_str:
                        continue
                    quote_time = parse_datetime(quote_time_str)
                elif hasattr(quote, 'timestamp'):
                    quote_time = quote.timestamp
                else:
                    continue
                    
                if quote_time.tzinfo is None:
                    quote_time = quote_time.replace(tzinfo=timezone.utc)
                time_diff = abs((quote_time - target_time).total_seconds())
                if closest_quote is None or time_diff < min_time_diff:
                    closest_quote = quote
                    min_time_diff = time_diff
        
        if not closest_quote:
            return None
        
        # Extract timestamp and prices from quote (dict format with raw_data=True)
        if isinstance(closest_quote, dict):
            quote_time_str = closest_quote.get('t')
            quote_time = parse_datetime(quote_time_str) if quote_time_str else None
            bid = float(closest_quote.get('bp')) if closest_quote.get('bp') else None
            ask = float(closest_quote.get('ap')) if closest_quote.get('ap') else None
        else:
            # Object format (if raw_data=False)
            quote_time = closest_quote.timestamp
            bid = float(closest_quote.bid_price) if closest_quote.bid_price else None
            ask = float(closest_quote.ask_price) if closest_quote.ask_price else None
        
        if quote_time.tzinfo is None:
            quote_time = quote_time.replace(tzinfo=timezone.utc)
        
        spread = ask - bid if bid and ask else None
        mid = (bid + ask) / 2.0 if bid and ask else None
        spread_pct = (spread / mid * 100) if spread and mid and mid > 0 else None
        
        return {
            'timestamp': quote_time,
            'bid': bid,
            'ask': ask,
            'mid': mid,
            'spread': spread,
            'spread_pct': spread_pct,
            'time_diff_seconds': abs((quote_time - target_time).total_seconds())
        }
    
    except Exception as e:
        print(f"⚠️  Error fetching quote for {ticker} at {target_time.isoformat()}: {e}")
        return None


def main():
    """Check price at 10 seconds after reception for today's biotech winners."""
    
    winners = load_todays_biotech_winners()
    
    print(f"\n{'='*80}")
    print(f"CHECKING PRICE MOVEMENT AT 10 SECONDS AFTER RECEPTION")
    print(f"Using Alpaca Historical Data API")
    print(f"{'='*80}\n")
    
    results = []
    
    for i, winner in enumerate(winners, 1):
        article_id = winner.get('article_id')
        title = winner.get('title', '')
        max_excursion = winner.get('max_excursion_pct', 0)
        source_file = winner.get('source_file')
        
        print(f"\n[{i}/{len(winners)}] {title[:70]}...")
        print(f"  Article ID: {article_id}")
        print(f"  Final Max Excursion: {max_excursion:.2f}%")
        
        # Load full recall record
        record = load_recall_record(article_id, source_file)
        
        if not record:
            print(f"  ❌ Could not load recall record")
            continue
        
        received_at_str = record.get('received_at')
        tickers = record.get('tickers', [])
        initial_nbbo = record.get('initial_nbbo', {})
        initial_ask = initial_nbbo.get('ask')
        
        if not received_at_str or not tickers or not initial_ask:
            print(f"  ❌ Missing required data")
            continue
        
        try:
            received_at = parse_datetime(received_at_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception as e:
            print(f"  ❌ Could not parse received_at: {e}")
            continue
        
        ticker = tickers[0]
        
        # Calculate time 10 seconds after reception
        target_time = received_at + timedelta(seconds=10.0)
        
        print(f"  Ticker: {ticker}")
        print(f"  Initial Ask (at reception): ${initial_ask:.4f}")
        print(f"  Reception time: {received_at.isoformat()}")
        print(f"  Target time (+10s): {target_time.isoformat()}")
        
        # Fetch quote at 10 seconds after reception
        print(f"  Fetching quote at 10 seconds after reception...")
        quote_at_10s = get_quote_at_time(ticker, target_time)
        
        if not quote_at_10s:
            print(f"  ❌ No quote data available at 10 seconds")
            results.append({
                'article_id': article_id,
                'ticker': ticker,
                'initial_ask': initial_ask,
                'price_at_10s': None,
                'price_change_pct': None,
                'error': 'No quote data'
            })
            continue
        
        ask_at_10s = quote_at_10s.get('ask')
        bid_at_10s = quote_at_10s.get('bid')
        mid_at_10s = quote_at_10s.get('mid')
        spread_at_10s = quote_at_10s.get('spread')
        quote_timestamp = quote_at_10s.get('timestamp')
        time_diff = quote_at_10s.get('time_diff_seconds', 0)
        
        # Calculate spread percentage
        spread_pct = None
        if spread_at_10s and mid_at_10s and mid_at_10s > 0:
            spread_pct = (spread_at_10s / mid_at_10s) * 100
        elif bid_at_10s and ask_at_10s:
            spread_at_10s = ask_at_10s - bid_at_10s
            mid_at_10s = (bid_at_10s + ask_at_10s) / 2.0
            if mid_at_10s > 0:
                spread_pct = (spread_at_10s / mid_at_10s) * 100
        
        print(f"\n  📊 Results:")
        print(f"    Quote timestamp: {quote_timestamp.isoformat()}")
        print(f"    Time difference from target: {time_diff:.2f}s")
        print(f"    Bid at ~10s: ${bid_at_10s:.4f}")
        print(f"    Ask at ~10s: ${ask_at_10s:.4f}")
        print(f"    Mid at ~10s: ${mid_at_10s:.4f}")
        print(f"    Spread: ${spread_at_10s:.4f}" if spread_at_10s else "    Spread: N/A")
        print(f"    Spread %: {spread_pct:.2f}%" if spread_pct is not None else "    Spread %: N/A")
        
        # Calculate price change
        if ask_at_10s and initial_ask and initial_ask > 0:
            price_change_pct = ((ask_at_10s - initial_ask) / initial_ask) * 100
            print(f"    Price Change (0-10s): {price_change_pct:.2f}%")
            
            if abs(price_change_pct) > 1.0:
                print(f"    ✅ Significant movement in first 10 seconds ({price_change_pct:.2f}%)")
            else:
                print(f"    ⚠️  Little movement in first 10 seconds ({price_change_pct:.2f}%)")
        
        # Compare to final max excursion
        print(f"\n  📈 Comparison:")
        print(f"    Price Change (0-10s): {price_change_pct:.2f}%" if ask_at_10s and initial_ask else "    Price Change (0-10s): N/A")
        print(f"    Final Max Excursion: {max_excursion:.2f}%")
        
        if ask_at_10s and initial_ask:
            remaining_move = max_excursion - price_change_pct
            if remaining_move > 2.0:
                print(f"    ⚠️  Most of the move happened AFTER 10 seconds ({remaining_move:.2f}% remaining)")
            elif abs(price_change_pct) > 1.0:
                print(f"    ✅ Significant move already happened in first 10 seconds")
        
        results.append({
            'article_id': article_id,
            'title': title,
            'ticker': ticker,
            'initial_ask': initial_ask,
            'received_at': received_at.isoformat(),
            'target_time': target_time.isoformat(),
            'quote_at_10s': {
                'timestamp': quote_timestamp.isoformat() if quote_timestamp else None,
                'bid': bid_at_10s,
                'ask': ask_at_10s,
                'mid': mid_at_10s,
                'spread': spread_at_10s,
                'spread_pct': spread_pct,
                'time_diff_seconds': time_diff
            },
            'price_change_pct': price_change_pct if ask_at_10s and initial_ask else None,
            'final_max_excursion_pct': max_excursion
        })
        
        # Small delay to avoid rate limiting
        import time
        time.sleep(0.5)
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}\n")
    
    valid_results = [r for r in results if r.get('price_change_pct') is not None]
    significant_moves = [r for r in valid_results if abs(r.get('price_change_pct', 0)) > 1.0]
    
    print(f"Total Winners: {len(results)}")
    print(f"Successfully fetched quotes: {len(valid_results)}/{len(results)}")
    print(f"Significant moves (>1%) in first 10s: {len(significant_moves)}/{len(valid_results)}")
    
    print(f"\n📊 Detailed Results:")
    for result in results:
        ticker = result.get('ticker')
        price_change = result.get('price_change_pct')
        final_max = result.get('final_max_excursion_pct', 0)
        
        if price_change is not None:
            status = "✅" if abs(price_change) > 1.0 else "⚠️"
            print(f"  {status} {ticker}: {price_change:.2f}% at 10s (Final: {final_max:.2f}%)")
        else:
            print(f"  ❌ {ticker}: No data available")
    
    # Save results
    output_file = PROJECT_ROOT / "biotech_price_at_10s_analysis.json"
    with open(output_file, 'w') as f:
        json.dump({
            'analysis_date': datetime.now(timezone.utc).isoformat(),
            'window_seconds': 10.0,
            'summary': {
                'total_winners': len(results),
                'successful_queries': len(valid_results),
                'significant_moves_in_10s': len(significant_moves)
            },
            'results': results
        }, f, indent=2)
    
    print(f"\n✅ Results saved to: {output_file}")


if __name__ == "__main__":
    main()
