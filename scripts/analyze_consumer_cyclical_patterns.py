#!/usr/bin/env python3
"""
Comprehensive Consumer Cyclical Sector Pattern Analysis.
Compare winners (>5% gain) vs non-movers (<1% movement) to identify linguistic patterns.
"""
import json
import sys
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Set
from collections import Counter, defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


def is_consumer_cyclical(record: Dict[str, Any]) -> bool:
    """Check if record is for a Consumer Cyclical sector stock."""
    ticker_metadata = record.get('ticker_metadata', {})
    
    for ticker, metadata in ticker_metadata.items():
        if isinstance(metadata, dict):
            sector = metadata.get('sector') or ''
            sector_lower = str(sector).lower()
            
            if 'consumer cyclical' in sector_lower or 'consumer' in sector_lower:
                return True
    
    return False


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


def calculate_max_excursion(record: Dict[str, Any]) -> float:
    """Calculate MAX price excursion (peak price reached) from initial NBBO.
    
    Uses highest_price_during_hold if available (most accurate), 
    otherwise falls back to price_check_10min.
    
    This captures quick moves that may retrace by 10min.
    """
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
    
    # Fallback to price_check_10min (less accurate - may miss quick moves)
    price_check_10min = record.get('price_check_10min', {})
    if price_check_10min:
        # Use percent_change if available
        if 'percent_change' in price_check_10min:
            return price_check_10min['percent_change']
        
        final_ask = price_check_10min.get('ask')
        if final_ask:
            change_pct = ((final_ask - initial_ask) / initial_ask) * 100
            return change_pct
    
    return None


def extract_headline_data(record: Dict[str, Any]) -> Dict[str, Any]:
    """Extract headline and metadata from record."""
    article_id = record.get('article_id', '')
    title = record.get('title') or record.get('article_title', '')
    published_at = record.get('published_at', '')
    received_at = record.get('received_at', '')
    
    # Get tickers
    tickers = list(record.get('ticker_metadata', {}).keys())
    
    # Get industry/sector
    ticker_metadata = record.get('ticker_metadata', {})
    industries = set()
    sectors = set()
    for ticker, metadata in ticker_metadata.items():
        if isinstance(metadata, dict):
            industry = metadata.get('industry')
            sector = metadata.get('sector')
            if industry:
                industries.add(industry)
            if sector:
                sectors.add(sector)
    
    industry = get_industry(record)
    max_excursion = calculate_max_excursion(record)
    
    # Get peak price info
    highest_price_data = record.get('highest_price_during_hold', {})
    peak_price = highest_price_data.get('price') if isinstance(highest_price_data, dict) else None
    peak_price_timestamp = highest_price_data.get('timestamp') if isinstance(highest_price_data, dict) else None
    
    return {
        'article_id': article_id,
        'title': title,
        'published_at': published_at,
        'received_at': received_at,
        'tickers': tickers,
        'industry': industry,
        'industries': list(industries),
        'sectors': list(sectors),
        'initial_ask': record.get('initial_nbbo', {}).get('ask'),
        'peak_price': peak_price,
        'peak_price_timestamp': peak_price_timestamp,
        'final_ask_10min': record.get('price_check_10min', {}).get('ask'),
        'max_excursion_pct': max_excursion,  # Peak price excursion
        'session': record.get('session', ''),
        'filter_reason': record.get('filter_reason')
    }


def extract_keywords_phrases(title: str) -> Dict[str, List[str]]:
    """Extract keywords, phrases, and patterns from headline."""
    if not title:
        return {'keywords': [], 'phrases': [], 'entities': []}
    
    title_lower = title.lower()
    
    # Consumer Cyclical keywords
    keywords = []
    consumer_cyclical_keywords = [
        # Retail & E-commerce
        'retail', 'retailer', 'retailers', 'store', 'stores', 'shop', 'shops', 'shopping',
        'e-commerce', 'ecommerce', 'online', 'digital', 'marketplace', 'marketplaces',
        'brand', 'brands', 'product', 'products', 'merchandise',
        
        # Automotive
        'car', 'cars', 'automotive', 'vehicle', 'vehicles', 'truck', 'trucks',
        'electric vehicle', 'ev', 'evs', 'hybrid', 'autonomous', 'self-driving',
        'dealership', 'dealerships', 'auto', 'automaker', 'automakers',
        
        # Apparel & Fashion
        'apparel', 'clothing', 'fashion', 'wear', 'wears', 'footwear', 'shoes',
        'designer', 'designers', 'collection', 'collections', 'line', 'lines',
        
        # Restaurants & Food
        'restaurant', 'restaurants', 'food', 'dining', 'cuisine', 'menu', 'menus',
        'franchise', 'franchises', 'chain', 'chains', 'location', 'locations',
        
        # Entertainment & Media
        'entertainment', 'media', 'streaming', 'stream', 'content', 'creator', 'creators',
        'game', 'games', 'gaming', 'gamer', 'gamers', 'esports',
        'movie', 'movies', 'film', 'films', 'tv', 'television', 'show', 'shows',
        
        # Travel & Leisure
        'travel', 'tourism', 'tourist', 'vacation', 'vacations', 'hotel', 'hotels',
        'resort', 'resorts', 'cruise', 'cruises', 'airline', 'airlines',
        'booking', 'bookings', 'trip', 'trips',
        
        # Home & Garden
        'home', 'homes', 'house', 'houses', 'furniture', 'furnishings',
        'appliance', 'appliances', 'garden', 'gardening', 'outdoor',
        
        # M&A & Partnerships
        'acquisition', 'acquire', 'acquires', 'merger', 'merge', 'merges',
        'deal', 'deals', 'transaction', 'transactions', 'agreement', 'agreements',
        'partnership', 'partnerships', 'collaboration', 'collaborate', 'collaborates',
        'alliance', 'alliances', 'joint venture', 'takeover', 'buyout',
        
        # Product Launches
        'launch', 'launches', 'launched', 'release', 'releases', 'released',
        'introduce', 'introduces', 'introduction', 'unveil', 'unveils', 'unveiled',
        'announce', 'announces', 'announcement', 'announcements',
        'new', 'preview', 'beta', 'alpha', 'debut',
        
        # Sales & Performance
        'sale', 'sales', 'sell', 'sells', 'sold', 'revenue', 'revenues',
        'earnings', 'profit', 'profits', 'profitability', 'income',
        'guidance', 'forecast', 'forecasts', 'outlook', 'expectations',
        'quarter', 'quarters', 'q1', 'q2', 'q3', 'q4', 'fiscal', 'annual',
        'beat', 'beats', 'miss', 'misses', 'exceed', 'exceeds', 'surpass',
        
        # Growth & Expansion
        'growth', 'grow', 'grows', 'expanding', 'expansion', 'expand', 'expands',
        'scale', 'scaling', 'scalable', 'market', 'markets', 'global',
        'capacity', 'capabilities', 'increase', 'increases', 'increased',
        'open', 'opens', 'opening', 'openings', 'new location', 'new locations',
        
        # Positive Signals
        'positive', 'strong', 'stronger', 'strength', 'improve', 'improves', 'improvement',
        'rise', 'rises', 'rising', 'up', 'success', 'successful', 'win', 'wins',
        'gain', 'gains', 'gained', 'record', 'records', 'high', 'higher', 'highest',
        'best', 'better', 'breakthrough', 'innovative', 'innovation', 'popular', 'popularity',
        
        # Customer & Demand
        'customer', 'customers', 'consumer', 'consumers', 'demand', 'demands',
        'order', 'orders', 'booking', 'bookings', 'reservation', 'reservations',
        
        # Negative Signals (for losers analysis)
        'decline', 'declines', 'declining', 'decrease', 'decreases', 'decreased',
        'loss', 'losses', 'lose', 'loses', 'down', 'lower', 'lowest', 'worst',
        'concern', 'concerns', 'risk', 'risks', 'uncertainty', 'challenge', 'challenges',
        'delay', 'delays', 'delayed', 'recall', 'recalls', 'recalled'
    ]
    
    for keyword in consumer_cyclical_keywords:
        if keyword in title_lower:
            keywords.append(keyword)
    
    # Extract phrases (2-3 word combinations)
    phrases = []
    words = title_lower.split()
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        if any(kw in bigram for kw in consumer_cyclical_keywords):
            phrases.append(bigram)
        if i < len(words) - 2:
            trigram = f"{words[i]} {words[i+1]} {words[i+2]}"
            if any(kw in trigram for kw in consumer_cyclical_keywords):
                phrases.append(trigram)
    
    # Extract entities (capitalized phrases)
    entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', title)
    
    return {
        'keywords': keywords,
        'phrases': phrases[:10],  # Limit to top 10
        'entities': entities[:10]  # Limit to top 10
    }


def analyze_patterns(winners: List[Dict], non_movers: List[Dict]) -> Dict[str, Any]:
    """Analyze patterns comparing winners vs non-movers."""
    
    # Extract keywords/phrases from winners
    winner_keywords = Counter()
    winner_phrases = Counter()
    winner_entities = Counter()
    
    for winner in winners:
        title = winner.get('title', '')
        patterns = extract_keywords_phrases(title)
        winner_keywords.update(patterns['keywords'])
        winner_phrases.update(patterns['phrases'])
        winner_entities.update(patterns['entities'])
    
    # Extract keywords/phrases from non-movers
    non_mover_keywords = Counter()
    non_mover_phrases = Counter()
    non_mover_entities = Counter()
    
    for non_mover in non_movers:
        title = non_mover.get('title', '')
        patterns = extract_keywords_phrases(title)
        non_mover_keywords.update(patterns['keywords'])
        non_mover_phrases.update(patterns['phrases'])
        non_mover_entities.update(patterns['entities'])
    
    # Calculate differences (winners - non_movers)
    all_keywords = set(winner_keywords.keys()) | set(non_mover_keywords.keys())
    keyword_scores = {}
    for keyword in all_keywords:
        winner_count = winner_keywords.get(keyword, 0)
        non_mover_count = non_mover_keywords.get(keyword, 0)
        
        winner_pct = (winner_count / len(winners)) * 100 if winners else 0
        non_mover_pct = (non_mover_count / len(non_movers)) * 100 if non_movers else 0
        
        keyword_scores[keyword] = {
            'winner_count': winner_count,
            'winner_pct': round(winner_pct, 2),
            'non_mover_count': non_mover_count,
            'non_mover_pct': round(non_mover_pct, 2),
            'difference_pct': round(winner_pct - non_mover_pct, 2)
        }
    
    # Sort by difference (most distinctive for winners)
    top_keywords = sorted(
        keyword_scores.items(),
        key=lambda x: x[1]['difference_pct'],
        reverse=True
    )[:20]
    
    return {
        'keyword_analysis': dict(top_keywords),
        'total_keywords_analyzed': len(all_keywords),
        'winner_keyword_frequency': dict(winner_keywords.most_common(20)),
        'non_mover_keyword_frequency': dict(non_mover_keywords.most_common(20))
    }


def main():
    """Main analysis function."""
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
    consumer_cyclical_records = []
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
                
                # Skip records without required fields
                if not isinstance(record, dict) or 'article_id' not in record:
                    continue
                
                try:
                    if is_consumer_cyclical(record):
                        headline_data = extract_headline_data(record)
                        headline_data['source_file'] = str(json_file.relative_to(PROJECT_ROOT))
                        consumer_cyclical_records.append(headline_data)
                except Exception as e:
                    # Skip records that fail to extract
                    continue
        
        except Exception as e:
            print(f"⚠️  Error processing {json_file}: {e}")
            continue
    
    print(f"\n📊 ANALYSIS RESULTS:")
    print(f"   Total records processed: {total_records}")
    print(f"   Consumer Cyclical records found: {len(consumer_cyclical_records)}")
    
    # Categorize: winners (>5% max excursion), non-movers (-1% to 1%), losers (<-1%)
    winners = []
    non_movers = []
    losers = []
    
    for record in consumer_cyclical_records:
        max_excursion = record.get('max_excursion_pct')
        if max_excursion is None:
            continue
        
        if max_excursion > 5.0:
            winners.append(record)
        elif -1.0 <= max_excursion <= 1.0:
            non_movers.append(record)
        elif max_excursion < -1.0:
            losers.append(record)
    
    # Sample non-movers (limit to 50 for manageable analysis)
    non_movers_sample = non_movers[:50] if len(non_movers) > 50 else non_movers
    
    print(f"   Consumer Cyclical winners (>5% gain): {len(winners)}")
    print(f"   Consumer Cyclical non-movers (-1% to 1%): {len(non_movers)} (sampling {len(non_movers_sample)})")
    print(f"   Consumer Cyclical losers (<-1%): {len(losers)}")
    
    # Group by industry
    winners_by_industry = defaultdict(list)
    non_movers_by_industry = defaultdict(list)
    
    for winner in winners:
        industry = winner.get('industry', 'Unknown')
        winners_by_industry[industry].append(winner)
    
    for non_mover in non_movers_sample:
        industry = non_mover.get('industry', 'Unknown')
        non_movers_by_industry[industry].append(non_mover)
    
    # Analyze patterns per industry
    industry_analysis = {}
    
    for industry in set(list(winners_by_industry.keys()) + list(non_movers_by_industry.keys())):
        industry_winners = winners_by_industry.get(industry, [])
        industry_non_movers = non_movers_by_industry.get(industry, [])
        
        if industry_winners or industry_non_movers:
            patterns = analyze_patterns(industry_winners, industry_non_movers)
            
            industry_analysis[industry] = {
                'winners_count': len(industry_winners),
                'non_movers_count': len(industry_non_movers),
                'patterns': patterns,
                'winners': industry_winners,
                'non_movers_sample': industry_non_movers
            }
    
    # Overall pattern analysis
    overall_patterns = analyze_patterns(winners, non_movers_sample)
    
    # Create comprehensive report
    report = {
        'analysis_metadata': {
            'analysis_date': datetime.now(timezone.utc).isoformat(),
            'period': '2026-01-02 to 2026-01-13',
            'total_records_processed': total_records,
            'total_consumer_cyclical_records': len(consumer_cyclical_records)
        },
        'summary': {
            'total_winners': len(winners),
            'total_non_movers': len(non_movers),
            'total_non_movers_sampled': len(non_movers_sample),
            'total_losers': len(losers),
            'industries_analyzed': len(industry_analysis)
        },
        'overall_patterns': overall_patterns,
        'industry_breakdown': {
            industry: {
                'winners_count': data['winners_count'],
                'non_movers_count': data['non_movers_count'],
                'patterns': data['patterns']
            }
            for industry, data in industry_analysis.items()
        },
        'detailed_data': {
            'winners': winners,
            'non_movers_sample': non_movers_sample,
            'industry_analysis': {
                industry: {
                    'winners': data['winners'],
                    'non_movers_sample': data['non_movers_sample']
                }
                for industry, data in industry_analysis.items()
            }
        }
    }
    
    # Save report
    output_file = PROJECT_ROOT / "consumer_cyclical_pattern_analysis.json"
    with open(output_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n✅ Saved comprehensive report to: {output_file}")
    
    # Print summary
    print(f"\n📈 KEY FINDINGS:")
    print(f"\n   Overall Patterns (Winners vs Non-Movers):")
    print(f"   Top distinctive keywords for winners:")
    for keyword, data in list(overall_patterns['keyword_analysis'].items())[:10]:
        print(f"      {keyword}: +{data['difference_pct']:.1f}% (winners: {data['winner_pct']:.1f}%, non-movers: {data['non_mover_pct']:.1f}%)")
    
    print(f"\n   Industry Breakdown:")
    for industry, data in sorted(industry_analysis.items(), key=lambda x: x[1]['winners_count'], reverse=True):
        print(f"\n   {industry}:")
        print(f"      Winners: {data['winners_count']}")
        print(f"      Non-movers sampled: {data['non_movers_count']}")
        if data['patterns']['keyword_analysis']:
            print(f"      Top distinctive keywords:")
            for keyword, kw_data in list(data['patterns']['keyword_analysis'].items())[:5]:
                print(f"         {keyword}: +{kw_data['difference_pct']:.1f}%")
    
    return report


if __name__ == "__main__":
    report = main()
    print(f"\n✅ Analysis complete!")
