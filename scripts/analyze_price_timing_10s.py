#!/usr/bin/env python3
"""
Analyze price movement timing for today's biotech winners.
Check when the peak price occurred relative to reception (within 10 seconds or later).
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


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


def analyze_price_timing(record: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze when peak price occurred relative to reception."""
    
    initial_nbbo = record.get('initial_nbbo', {})
    initial_ask = initial_nbbo.get('ask')
    
    received_at_str = record.get('received_at')
    published_at_str = record.get('published_at')
    
    highest_price_data = record.get('highest_price_during_hold', {})
    
    if not received_at_str or not initial_ask or not highest_price_data:
        return {
            'error': 'Missing required data',
            'initial_ask': initial_ask,
            'received_at': received_at_str,
            'highest_price': None
        }
    
    try:
        received_at = parse_datetime(received_at_str)
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        
        published_at = parse_datetime(published_at_str) if published_at_str else None
        if published_at and published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        
        peak_price = highest_price_data.get('price')
        peak_timestamp_str = highest_price_data.get('timestamp')
        peak_timestamp = parse_datetime(peak_timestamp_str) if peak_timestamp_str else None
        if peak_timestamp and peak_timestamp.tzinfo is None:
            peak_timestamp = peak_timestamp.replace(tzinfo=timezone.utc)
        
        # Calculate timing
        pub_to_recv = (received_at - published_at).total_seconds() if published_at else None
        recv_to_peak = (peak_timestamp - received_at).total_seconds() if peak_timestamp else None
        
        # Calculate price movement
        max_excursion_pct = ((peak_price - initial_ask) / initial_ask) * 100 if peak_price and initial_ask else None
        
        return {
            'ticker': record.get('tickers', [None])[0],
            'initial_ask': initial_ask,
            'peak_price': peak_price,
            'max_excursion_pct': max_excursion_pct,
            'published_at': published_at.isoformat() if published_at else None,
            'received_at': received_at.isoformat(),
            'peak_timestamp': peak_timestamp.isoformat() if peak_timestamp else None,
            'pub_to_recv_seconds': pub_to_recv,
            'recv_to_peak_seconds': recv_to_peak,
            'peak_within_10s': recv_to_peak <= 10.0 if recv_to_peak is not None else None,
            'peak_within_30s': recv_to_peak <= 30.0 if recv_to_peak is not None else None,
            'peak_within_1min': recv_to_peak <= 60.0 if recv_to_peak is not None else None,
            'peak_minute': highest_price_data.get('minute'),
            'peak_second': highest_price_data.get('second')
        }
    except Exception as e:
        return {
            'error': str(e),
            'initial_ask': initial_ask,
            'received_at': received_at_str,
            'highest_price': highest_price_data
        }


def main():
    """Analyze price timing for today's biotech winners."""
    
    winners = load_todays_biotech_winners()
    
    print(f"\n{'='*80}")
    print(f"ANALYZING PRICE MOVEMENT TIMING FOR TODAY'S BIOTECH WINNERS")
    print(f"{'='*80}\n")
    
    results = []
    
    for i, winner in enumerate(winners, 1):
        article_id = winner.get('article_id')
        title = winner.get('title', '')
        max_excursion = winner.get('max_excursion_pct', 0)
        source_file = winner.get('source_file')
        
        print(f"\n[{i}/{len(winners)}] {title[:80]}...")
        print(f"  Article ID: {article_id}")
        print(f"  Final Max Excursion: {max_excursion:.2f}%")
        
        # Load full recall record
        record = load_recall_record(article_id, source_file)
        
        if not record:
            print(f"  ❌ Could not load recall record")
            continue
        
        # Analyze timing
        timing = analyze_price_timing(record)
        
        if timing.get('error'):
            print(f"  ❌ Error: {timing['error']}")
            continue
        
        ticker = timing.get('ticker')
        initial_ask = timing.get('initial_ask')
        peak_price = timing.get('peak_price')
        recv_to_peak = timing.get('recv_to_peak_seconds')
        max_excursion_pct = timing.get('max_excursion_pct')
        pub_to_recv = timing.get('pub_to_recv_seconds')
        
        print(f"\n  📊 Timing Analysis:")
        print(f"    Ticker: {ticker}")
        print(f"    Initial Ask: ${initial_ask:.4f}")
        print(f"    Peak Price: ${peak_price:.4f}")
        print(f"    Max Excursion: {max_excursion_pct:.2f}%")
        print(f"    Published → Received: {pub_to_recv:.2f}s" if pub_to_recv else "    Published → Received: N/A")
        print(f"    Received → Peak: {recv_to_peak:.2f}s" if recv_to_peak is not None else "    Received → Peak: N/A")
        
        if recv_to_peak is not None:
            if recv_to_peak <= 10.0:
                print(f"    ✅ Peak occurred WITHIN 10 seconds of reception ({recv_to_peak:.2f}s)")
            elif recv_to_peak <= 30.0:
                print(f"    ⚠️  Peak occurred within 30 seconds ({recv_to_peak:.2f}s)")
            elif recv_to_peak <= 60.0:
                print(f"    ⚠️  Peak occurred within 1 minute ({recv_to_peak:.2f}s)")
            else:
                minutes = recv_to_peak / 60.0
                print(f"    ❌ Peak occurred {minutes:.1f} minutes after reception ({recv_to_peak:.2f}s)")
        
        # Compare to final max excursion
        if max_excursion_pct is not None and max_excursion > 0:
            print(f"\n  📈 Comparison:")
            print(f"    Max Excursion at Peak: {max_excursion_pct:.2f}%")
            print(f"    Final Max Excursion: {max_excursion:.2f}%")
            if abs(max_excursion_pct - max_excursion) < 0.1:
                print(f"    ✅ Peak matches final max excursion")
            else:
                diff = max_excursion - max_excursion_pct
                print(f"    ⚠️  Peak is {diff:.2f}% lower than final (price continued rising)")
        
        results.append({
            'article_id': article_id,
            'title': title,
            'final_max_excursion_pct': max_excursion,
            'timing': timing
        })
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}\n")
    
    peaks_within_10s = sum(1 for r in results if r['timing'].get('peak_within_10s'))
    peaks_within_30s = sum(1 for r in results if r['timing'].get('peak_within_30s'))
    peaks_within_1min = sum(1 for r in results if r['timing'].get('peak_within_1min'))
    
    print(f"Total Winners: {len(results)}")
    print(f"Peaks within 10 seconds of reception: {peaks_within_10s}/{len(results)}")
    print(f"Peaks within 30 seconds of reception: {peaks_within_30s}/{len(results)}")
    print(f"Peaks within 1 minute of reception: {peaks_within_1min}/{len(results)}")
    
    print(f"\n📊 Detailed Timing:")
    for result in results:
        timing = result['timing']
        ticker = timing.get('ticker')
        recv_to_peak = timing.get('recv_to_peak_seconds')
        max_excursion_pct = timing.get('max_excursion_pct')
        peak_within_10s = timing.get('peak_within_10s')
        
        status = "✅" if peak_within_10s else "⚠️"
        print(f"  {status} {ticker}: Peak at {recv_to_peak:.1f}s after reception ({max_excursion_pct:.2f}% excursion)")
    
    # Save results
    output_file = PROJECT_ROOT / "biotech_price_timing_analysis.json"
    with open(output_file, 'w') as f:
        json.dump({
            'analysis_date': datetime.now(timezone.utc).isoformat(),
            'summary': {
                'total_winners': len(results),
                'peaks_within_10s': peaks_within_10s,
                'peaks_within_30s': peaks_within_30s,
                'peaks_within_1min': peaks_within_1min
            },
            'results': results
        }, f, indent=2)
    
    print(f"\n✅ Results saved to: {output_file}")


if __name__ == "__main__":
    main()
