#!/usr/bin/env python3
"""
Analyze all big moves (>5% gain) across all industries.
Check how many would have been tradeable with spread <= 2% filter.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


def calculate_max_excursion(record: Dict[str, Any]) -> float:
    """Calculate MAX price excursion (peak price reached) from initial NBBO."""
    initial_nbbo = record.get('initial_nbbo', {})
    
    if not initial_nbbo:
        return None
    
    initial_ask = initial_nbbo.get('ask')
    if not initial_ask or initial_ask <= 0:
        return None
    
    # Use highest_price_during_hold if available (most accurate - captures peak)
    highest_price_data = record.get('highest_price_during_hold', {})
    if highest_price_data and isinstance(highest_price_data, dict):
        peak_price = highest_price_data.get('price')
        if peak_price:
            change_pct = ((peak_price - initial_ask) / initial_ask) * 100
            return change_pct
    
    # Fallback to price_check_10min
    price_check_10min = record.get('price_check_10min', {})
    if price_check_10min:
        if 'percent_change' in price_check_10min:
            return price_check_10min['percent_change']
        
        final_ask = price_check_10min.get('ask')
        if final_ask:
            change_pct = ((final_ask - initial_ask) / initial_ask) * 100
            return change_pct
    
    return None


def calculate_spread_percentage(initial_nbbo: Dict[str, Any]) -> float:
    """Calculate spread as percentage of mid price."""
    bid = initial_nbbo.get('bid')
    ask = initial_nbbo.get('ask')
    mid = initial_nbbo.get('mid')
    
    if not bid or not ask or not mid or mid <= 0:
        return None
    
    spread = ask - bid
    spread_pct = (spread / mid) * 100
    
    return spread_pct


def get_industry(record: Dict[str, Any]) -> str:
    """Get the primary industry for this record."""
    ticker_metadata = record.get('ticker_metadata', {})
    
    if not ticker_metadata:
        return "Unknown"
    
    industries = []
    for ticker, metadata in ticker_metadata.items():
        if isinstance(metadata, dict):
            industry = metadata.get('industry')
            if industry:
                industries.append(industry)
    
    return industries[0] if industries else "Unknown"


def get_sector(record: Dict[str, Any]) -> str:
    """Get the primary sector for this record."""
    ticker_metadata = record.get('ticker_metadata', {})
    
    if not ticker_metadata:
        return "Unknown"
    
    sectors = []
    for ticker, metadata in ticker_metadata.items():
        if isinstance(metadata, dict):
            sector = metadata.get('sector')
            if sector:
                sectors.append(sector)
    
    return sectors[0] if sectors else "Unknown"


def main():
    """Analyze all big moves and spread filter."""
    
    recall_dir = PROJECT_ROOT / "tmp" / "statistics" / "recall" / "2026" / "01"
    
    if not recall_dir.exists():
        print(f"❌ Recall directory not found: {recall_dir}")
        return
    
    # Find all JSON files
    json_files = []
    for week_dir in sorted(recall_dir.glob("week_*")):
        for day_dir in sorted(week_dir.glob("*")):
            for session_dir in sorted(day_dir.glob("*")):
                for json_file in sorted(session_dir.glob("*.json")):
                    json_files.append(json_file)
    
    print(f"📂 Found {len(json_files)} recall JSON files")
    
    # Process all files
    all_winners = []
    total_records = 0
    
    for json_file in json_files:
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            # Handle both list and dict structures
            if isinstance(data, dict):
                if 'records' in data:
                    records = data['records']
                else:
                    records = [v for v in data.values() if isinstance(v, list)]
                    records = records[0] if records else []
            elif isinstance(data, list):
                records = data
            else:
                records = []
                
            records = [r for r in records if isinstance(r, dict) and 'article_id' in r]
            
            for record in records:
                total_records += 1
                
                try:
                    max_excursion = calculate_max_excursion(record)
                    
                    # Only process winners (>5% gain)
                    if max_excursion is None or max_excursion <= 5.0:
                        continue
                    
                    initial_nbbo = record.get('initial_nbbo', {})
                    if not initial_nbbo:
                        continue
                    
                    spread_pct = calculate_spread_percentage(initial_nbbo)
                    
                    industry = get_industry(record)
                    sector = get_sector(record)
                    tickers = record.get('tickers', [])
                    ticker = tickers[0] if tickers else None
                    
                    all_winners.append({
                        'article_id': record.get('article_id'),
                        'title': record.get('title', ''),
                        'ticker': ticker,
                        'industry': industry,
                        'sector': sector,
                        'max_excursion_pct': max_excursion,
                        'initial_ask': initial_nbbo.get('ask'),
                        'initial_bid': initial_nbbo.get('bid'),
                        'initial_mid': initial_nbbo.get('mid'),
                        'spread': initial_nbbo.get('spread'),
                        'spread_pct': spread_pct,
                        'session': record.get('session', ''),
                        'source_file': str(json_file.relative_to(PROJECT_ROOT))
                    })
                except Exception:
                    continue
        
        except Exception as e:
            continue
    
    print(f"\n📊 ANALYSIS RESULTS:")
    print(f"   Total records processed: {total_records}")
    print(f"   Winners (>5% gain): {len(all_winners)}")
    
    # Filter by spread - test both 2% and 5%
    SPREAD_THRESHOLD_2 = 2.0  # 2% spread filter
    SPREAD_THRESHOLD_5 = 5.0  # 5% spread filter
    
    tradeable_winners_2 = [w for w in all_winners if w.get('spread_pct') is not None and w['spread_pct'] <= SPREAD_THRESHOLD_2]
    filtered_out_2 = [w for w in all_winners if w.get('spread_pct') is None or w['spread_pct'] > SPREAD_THRESHOLD_2]
    
    tradeable_winners_5 = [w for w in all_winners if w.get('spread_pct') is not None and w['spread_pct'] <= SPREAD_THRESHOLD_5]
    filtered_out_5 = [w for w in all_winners if w.get('spread_pct') is None or w['spread_pct'] > SPREAD_THRESHOLD_5]
    
    print(f"\n{'='*80}")
    print(f"SPREAD FILTER ANALYSIS - COMPARISON")
    print(f"{'='*80}\n")
    
    print(f"Total Winners: {len(all_winners)}\n")
    
    print(f"📊 WITH 2% SPREAD FILTER:")
    print(f"  ✅ Tradeable (spread <= 2.0%): {len(tradeable_winners_2)} ({len(tradeable_winners_2)/len(all_winners)*100:.1f}%)")
    print(f"  ❌ Filtered Out (spread > 2.0%): {len(filtered_out_2)} ({len(filtered_out_2)/len(all_winners)*100:.1f}%)")
    
    print(f"\n📊 WITH 5% SPREAD FILTER:")
    print(f"  ✅ Tradeable (spread <= 5.0%): {len(tradeable_winners_5)} ({len(tradeable_winners_5)/len(all_winners)*100:.1f}%)")
    print(f"  ❌ Filtered Out (spread > 5.0%): {len(filtered_out_5)} ({len(filtered_out_5)/len(all_winners)*100:.1f}%)")
    
    additional_tradeable = len(tradeable_winners_5) - len(tradeable_winners_2)
    print(f"\n📈 IMPROVEMENT:")
    print(f"  Additional tradeable trades with 5% filter: +{additional_tradeable} ({additional_tradeable/len(all_winners)*100:.1f}%)")
    print(f"  Improvement: {len(tradeable_winners_5)/len(tradeable_winners_2)*100:.1f}% more tradeable than 2% filter")
    
    # Breakdown by industry (using 5% threshold)
    print(f"\n📊 BREAKDOWN BY INDUSTRY (5% Spread Filter):")
    industry_stats = defaultdict(lambda: {'total': 0, 'tradeable_2pct': 0, 'tradeable_5pct': 0, 'filtered': 0})
    
    for winner in all_winners:
        industry = winner.get('industry', 'Unknown')
        industry_stats[industry]['total'] += 1
        if winner in tradeable_winners_2:
            industry_stats[industry]['tradeable_2pct'] += 1
        if winner in tradeable_winners_5:
            industry_stats[industry]['tradeable_5pct'] += 1
        else:
            industry_stats[industry]['filtered'] += 1
    
    for industry in sorted(industry_stats.keys(), key=lambda x: industry_stats[x]['total'], reverse=True):
        stats = industry_stats[industry]
        tradeable_2_pct = (stats['tradeable_2pct'] / stats['total'] * 100) if stats['total'] > 0 else 0
        tradeable_5_pct = (stats['tradeable_5pct'] / stats['total'] * 100) if stats['total'] > 0 else 0
        print(f"  {industry}:")
        print(f"    Total Winners: {stats['total']}")
        print(f"    Tradeable (2%): {stats['tradeable_2pct']} ({tradeable_2_pct:.1f}%)")
        print(f"    Tradeable (5%): {stats['tradeable_5pct']} ({tradeable_5_pct:.1f}%)")
        print(f"    Filtered Out (>5%): {stats['filtered']}")
    
    # Breakdown by sector (using 5% threshold)
    print(f"\n📊 BREAKDOWN BY SECTOR (5% Spread Filter):")
    sector_stats = defaultdict(lambda: {'total': 0, 'tradeable_2pct': 0, 'tradeable_5pct': 0, 'filtered': 0})
    
    for winner in all_winners:
        sector = winner.get('sector', 'Unknown')
        sector_stats[sector]['total'] += 1
        if winner in tradeable_winners_2:
            sector_stats[sector]['tradeable_2pct'] += 1
        if winner in tradeable_winners_5:
            sector_stats[sector]['tradeable_5pct'] += 1
        else:
            sector_stats[sector]['filtered'] += 1
    
    for sector in sorted(sector_stats.keys(), key=lambda x: sector_stats[x]['total'], reverse=True):
        stats = sector_stats[sector]
        tradeable_2_pct = (stats['tradeable_2pct'] / stats['total'] * 100) if stats['total'] > 0 else 0
        tradeable_5_pct = (stats['tradeable_5pct'] / stats['total'] * 100) if stats['total'] > 0 else 0
        print(f"  {sector}:")
        print(f"    Total Winners: {stats['total']}")
        print(f"    Tradeable (2%): {stats['tradeable_2pct']} ({tradeable_2_pct:.1f}%)")
        print(f"    Tradeable (5%): {stats['tradeable_5pct']} ({tradeable_5_pct:.1f}%)")
        print(f"    Filtered Out (>5%): {stats['filtered']}")
    
    # Spread distribution
    print(f"\n📊 SPREAD DISTRIBUTION:")
    spread_ranges = {
        '0-0.5%': 0,
        '0.5-1%': 0,
        '1-2%': 0,
        '2-5%': 0,
        '5-10%': 0,
        '10%+': 0,
        'Missing': 0
    }
    
    for winner in all_winners:
        spread_pct = winner.get('spread_pct')
        if spread_pct is None:
            spread_ranges['Missing'] += 1
        elif spread_pct <= 0.5:
            spread_ranges['0-0.5%'] += 1
        elif spread_pct <= 1.0:
            spread_ranges['0.5-1%'] += 1
        elif spread_pct <= 2.0:
            spread_ranges['1-2%'] += 1
        elif spread_pct <= 5.0:
            spread_ranges['2-5%'] += 1
        elif spread_pct <= 10.0:
            spread_ranges['5-10%'] += 1
        else:
            spread_ranges['10%+'] += 1
    
    for range_name, count in spread_ranges.items():
        pct = (count / len(all_winners) * 100) if all_winners else 0
        print(f"  {range_name}: {count} ({pct:.1f}%)")
    
    # Examples of filtered out trades (with 5% filter)
    print(f"\n📊 EXAMPLES OF FILTERED OUT TRADES (spread > 5.0%):")
    filtered_sorted = sorted(filtered_out_5, key=lambda x: x.get('spread_pct', 999), reverse=True)
    for winner in filtered_sorted[:10]:
        spread_pct = winner.get('spread_pct', 'N/A')
        print(f"  {winner.get('ticker')}: {spread_pct:.2f}% spread, {winner.get('max_excursion_pct', 0):.2f}% gain - {winner.get('title', '')[:60]}...")
    
    # Examples of tradeable trades (with 5% filter)
    print(f"\n📊 EXAMPLES OF TRADEABLE TRADES (spread <= 5.0%):")
    tradeable_sorted = sorted(tradeable_winners_5, key=lambda x: x.get('max_excursion_pct', 0), reverse=True)
    
    # Show which ones are new with 5% filter
    new_with_5pct = [w for w in tradeable_winners_5 if w not in tradeable_winners_2]
    print(f"\n📊 NEWLY TRADEABLE WITH 5% FILTER (spread 2-5%):")
    new_sorted = sorted(new_with_5pct, key=lambda x: x.get('max_excursion_pct', 0), reverse=True)
    for winner in new_sorted[:10]:
        spread_pct = winner.get('spread_pct', 'N/A')
        print(f"  {winner.get('ticker')}: {spread_pct:.2f}% spread, {winner.get('max_excursion_pct', 0):.2f}% gain - {winner.get('title', '')[:60]}...")
    for winner in tradeable_sorted[:10]:
        spread_pct = winner.get('spread_pct', 'N/A')
        print(f"  {winner.get('ticker')}: {spread_pct:.2f}% spread, {winner.get('max_excursion_pct', 0):.2f}% gain - {winner.get('title', '')[:60]}...")
    
    # Save results
    output_file = PROJECT_ROOT / "spread_filter_analysis_all_winners.json"
    with open(output_file, 'w') as f:
        json.dump({
            'analysis_date': datetime.now(timezone.utc).isoformat(),
            'spread_threshold_2pct': SPREAD_THRESHOLD_2,
            'spread_threshold_5pct': SPREAD_THRESHOLD_5,
            'summary': {
                'total_winners': len(all_winners),
                'tradeable_winners_2pct': len(tradeable_winners_2),
                'tradeable_winners_5pct': len(tradeable_winners_5),
                'filtered_out_2pct': len(filtered_out_2),
                'filtered_out_5pct': len(filtered_out_5),
                'tradeable_percentage_2pct': len(tradeable_winners_2) / len(all_winners) * 100 if all_winners else 0,
                'tradeable_percentage_5pct': len(tradeable_winners_5) / len(all_winners) * 100 if all_winners else 0,
                'additional_tradeable_with_5pct': len(tradeable_winners_5) - len(tradeable_winners_2)
            },
            'industry_breakdown': dict(industry_stats),
            'sector_breakdown': dict(sector_stats),
            'spread_distribution': spread_ranges,
            'tradeable_winners_2pct': tradeable_winners_2,
            'tradeable_winners_5pct': tradeable_winners_5,
            'filtered_out_2pct': filtered_out_2,
            'filtered_out_5pct': filtered_out_5,
            'newly_tradeable_with_5pct': [w for w in tradeable_winners_5 if w not in tradeable_winners_2]
        }, f, indent=2)
    
    print(f"\n✅ Results saved to: {output_file}")


if __name__ == "__main__":
    main()
