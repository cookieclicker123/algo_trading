#!/usr/bin/env python3
"""
Analyze today's winners (>5% gain) vs losses/non-movers in same industry/sector.
Identify distinctive linguistic patterns in winners that are NOT in bad headlines.
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


def extract_keywords_phrases(title: str) -> Dict[str, Set[str]]:
    """Extract keywords, phrases, and entities from headline."""
    if not title:
        return {'keywords': set(), 'phrases': set(), 'entities': set()}
    
    title_lower = title.lower()
    
    # Comprehensive keyword extraction
    keywords = set()
    
    # Common trading-relevant keywords
    common_keywords = [
        # M&A
        'acquisition', 'acquire', 'acquires', 'acquired', 'merger', 'merge', 'merges', 'merged',
        'deal', 'deals', 'transaction', 'transactions', 'takeover', 'buyout', 'buy', 'buys',
        
        # Partnerships & Collaborations
        'partnership', 'partnerships', 'partner', 'partners', 'collaboration', 'collaborate',
        'collaborates', 'alliance', 'alliances', 'joint venture', 'agreement', 'agreements',
        'contract', 'contracts', 'award', 'awards', 'awarded',
        
        # Product/Service Launches
        'launch', 'launches', 'launched', 'release', 'releases', 'released', 'unveil', 'unveils',
        'unveiled', 'introduce', 'introduces', 'introduced', 'introduction', 'new', 'preview',
        'beta', 'alpha', 'product', 'products', 'service', 'services', 'platform', 'platforms',
        
        # Announcements
        'announce', 'announces', 'announcement', 'announcements', 'announced', 'declare',
        'declares', 'report', 'reports', 'reporting',
        
        # Financial Performance
        'earnings', 'revenue', 'revenues', 'profit', 'profits', 'profitability', 'income',
        'guidance', 'forecast', 'forecasts', 'outlook', 'expectations', 'beat', 'beats',
        'exceed', 'exceeds', 'surpass', 'surpasses', 'miss', 'misses',
        'quarter', 'quarters', 'q1', 'q2', 'q3', 'q4', 'fiscal', 'annual',
        
        # Growth & Expansion
        'growth', 'grow', 'grows', 'growing', 'expanding', 'expansion', 'expand', 'expands',
        'scale', 'scaling', 'scalable', 'market', 'markets', 'global', 'international',
        'increase', 'increases', 'increased', 'capacity',
        
        # Positive Signals
        'positive', 'strong', 'stronger', 'strength', 'improve', 'improves', 'improvement',
        'rise', 'rises', 'rising', 'up', 'success', 'successful', 'win', 'wins', 'winning',
        'gain', 'gains', 'gained', 'record', 'records', 'high', 'higher', 'highest',
        'best', 'better', 'breakthrough', 'innovative', 'innovation', 'breakthrough',
        
        # Technology/AI
        'ai', 'artificial intelligence', 'machine learning', 'ml', 'technology', 'tech',
        'software', 'hardware', 'chip', 'chips', 'gpu', 'cpu', 'cloud', 'data',
        
        # Healthcare/Biotech
        'fda', 'approval', 'approve', 'approves', 'approved', 'trial', 'trials', 'clinical',
        'drug', 'drugs', 'treatment', 'treatments', 'therapy', 'therapies', 'patient', 'patients',
        
        # Sales & Operations
        'sale', 'sales', 'sell', 'sells', 'sold', 'order', 'orders', 'booking', 'bookings',
        'delivery', 'deliveries', 'deliver', 'delivers', 'production', 'produce', 'produces',
        'operation', 'operations', 'operate', 'operates', 'facility', 'facilities',
        
        # Corporate Actions
        'stock', 'share', 'shares', 'dividend', 'dividends', 'split', 'splits',
        'ipo', 'public offering', 'offering', 'offerings', 'listing', 'listings',
        
        # Negative signals (for comparison)
        'decline', 'declines', 'declining', 'decrease', 'decreases', 'decreased',
        'loss', 'losses', 'lose', 'loses', 'down', 'lower', 'lowest', 'worst',
        'concern', 'concerns', 'risk', 'risks', 'uncertainty', 'challenge', 'challenges',
        'delay', 'delays', 'delayed', 'shutdown', 'shutdowns', 'closure', 'closures'
    ]
    
    # Extract matching keywords
    for keyword in common_keywords:
        if keyword in title_lower:
            keywords.add(keyword)
    
    # Extract 2-3 word phrases
    phrases = set()
    words = title_lower.split()
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        phrases.add(bigram)
        if i < len(words) - 2:
            trigram = f"{words[i]} {words[i+1]} {words[i+2]}"
            phrases.add(trigram)
    
    # Extract capitalized entities (company names, proper nouns)
    entities = set(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', title))
    
    return {
        'keywords': keywords,
        'phrases': phrases,
        'entities': entities
    }


def analyze_patterns(winners: List[Dict], losers: List[Dict], non_movers: List[Dict]) -> Dict[str, Any]:
    """Analyze patterns comparing winners vs losers and non-movers."""
    
    # Extract all patterns from each category
    winner_keywords = Counter()
    winner_phrases = Counter()
    winner_entities = Counter()
    
    loser_keywords = Counter()
    loser_phrases = Counter()
    loser_entities = Counter()
    
    non_mover_keywords = Counter()
    non_mover_phrases = Counter()
    non_mover_entities = Counter()
    
    for winner in winners:
        title = winner.get('title', '')
        patterns = extract_keywords_phrases(title)
        winner_keywords.update(patterns['keywords'])
        winner_phrases.update(patterns['phrases'])
        winner_entities.update(patterns['entities'])
    
    for loser in losers:
        title = loser.get('title', '')
        patterns = extract_keywords_phrases(title)
        loser_keywords.update(patterns['keywords'])
        loser_phrases.update(patterns['phrases'])
        loser_entities.update(patterns['entities'])
    
    for non_mover in non_movers:
        title = non_mover.get('title', '')
        patterns = extract_keywords_phrases(title)
        non_mover_keywords.update(patterns['keywords'])
        non_mover_phrases.update(patterns['phrases'])
        non_mover_entities.update(patterns['entities'])
    
    # Find distinctive patterns (in winners, NOT in losers/non-movers)
    all_winner_keywords = set(winner_keywords.keys())
    all_loser_keywords = set(loser_keywords.keys())
    all_non_mover_keywords = set(non_mover_keywords.keys())
    
    distinctive_keywords = all_winner_keywords - all_loser_keywords - all_non_mover_keywords
    
    # Also find keywords that appear much more frequently in winners
    keyword_scores = {}
    for keyword in all_winner_keywords | all_loser_keywords | all_non_mover_keywords:
        winner_count = winner_keywords.get(keyword, 0)
        loser_count = loser_keywords.get(keyword, 0)
        non_mover_count = non_mover_keywords.get(keyword, 0)
        
        total_others = loser_count + non_mover_count
        winner_pct = (winner_count / len(winners)) * 100 if winners else 0
        others_pct = (total_others / (len(losers) + len(non_movers))) * 100 if (losers or non_movers) else 0
        
        keyword_scores[keyword] = {
            'winner_count': winner_count,
            'winner_pct': round(winner_pct, 2),
            'loser_count': loser_count,
            'non_mover_count': non_mover_count,
            'others_total': total_others,
            'others_pct': round(others_pct, 2),
            'difference_pct': round(winner_pct - others_pct, 2),
            'is_distinctive': keyword in distinctive_keywords
        }
    
    # Find distinctive phrases (appear in winners, NOT in losers/non-movers)
    all_winner_phrases = set(winner_phrases.keys())
    all_loser_phrases = set(loser_phrases.keys())
    all_non_mover_phrases = set(non_mover_phrases.keys())
    
    distinctive_phrases = all_winner_phrases - all_loser_phrases - all_non_mover_phrases
    
    # Sort by difference (most distinctive for winners)
    top_keywords = sorted(
        keyword_scores.items(),
        key=lambda x: (x[1]['is_distinctive'], x[1]['difference_pct']),
        reverse=True
    )
    
    return {
        'distinctive_keywords': list(distinctive_keywords),
        'distinctive_phrases': list(distinctive_phrases)[:30],  # Top 30
        'keyword_analysis': dict(top_keywords[:50]),  # Top 50
        'winner_keyword_frequency': dict(winner_keywords.most_common(30)),
        'loser_keyword_frequency': dict(loser_keywords.most_common(30)),
        'non_mover_keyword_frequency': dict(non_mover_keywords.most_common(30)),
        'total_winners': len(winners),
        'total_losers': len(losers),
        'total_non_movers': len(non_movers)
    }


def main():
    """Main analysis function."""
    recall_dir = PROJECT_ROOT / "tmp" / "statistics" / "recall" / "2026" / "01"
    
    if not recall_dir.exists():
        print(f"❌ Recall directory not found: {recall_dir}")
        return
    
    # Load TODAY's data (week_3/15)
    today_dir = recall_dir / "week_3" / "15"
    if not today_dir.exists():
        print(f"❌ Today's directory not found: {today_dir}")
        return
    
    today_files = []
    for session_dir in sorted(today_dir.glob("*")):
        for json_file in sorted(session_dir.glob("*.json")):
            today_files.append(json_file)
    
    print(f"📂 Found {len(today_files)} recall files for today (week_3/15)")
    
    # Load ALL week data (for losers/non-movers comparison)
    week_files = []
    for week_dir in sorted(recall_dir.glob("week_*")):
        for day_dir in sorted(week_dir.glob("*")):
            for session_dir in sorted(day_dir.glob("*")):
                for json_file in sorted(session_dir.glob("*.json")):
                    week_files.append(json_file)
    
    print(f"📂 Found {len(week_files)} recall files for the week")
    
    # Process TODAY's files (winners)
    today_records = []
    for json_file in today_files:
        try:
            with open(json_file) as f:
                data = json.load(f)
            
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
                try:
                    max_excursion = calculate_max_excursion(record)
                    if max_excursion is not None and max_excursion > 5.0:
                        today_records.append({
                            'article_id': record.get('article_id'),
                            'title': record.get('title') or record.get('article_title', ''),
                            'industry': get_industry(record),
                            'sector': get_sector(record),
                            'max_excursion_pct': max_excursion,
                            'initial_ask': record.get('initial_nbbo', {}).get('ask'),
                            'highest_price': record.get('highest_price_during_hold', {}).get('price') if isinstance(record.get('highest_price_during_hold'), dict) else None,
                            'session': record.get('session', ''),
                            'source_file': str(json_file.relative_to(PROJECT_ROOT))
                        })
                except Exception:
                    continue
        except Exception as e:
            print(f"⚠️  Error processing {json_file}: {e}")
            continue
    
    print(f"\n📊 TODAY'S WINNERS (>5% gain): {len(today_records)}")
    
    if not today_records:
        print("❌ No winners found today!")
        return
    
    # Group today's winners by industry and sector
    winners_by_industry = defaultdict(list)
    winners_by_sector = defaultdict(list)
    
    for record in today_records:
        industry = record.get('industry', 'Unknown')
        sector = record.get('sector', 'Unknown')
        winners_by_industry[industry].append(record)
        winners_by_sector[sector].append(record)
    
    # Process ALL week files (losers and non-movers)
    week_records = []
    for json_file in week_files:
        try:
            with open(json_file) as f:
                data = json.load(f)
            
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
                try:
                    max_excursion = calculate_max_excursion(record)
                    if max_excursion is not None:
                        industry = get_industry(record)
                        sector = get_sector(record)
                        week_records.append({
                            'article_id': record.get('article_id'),
                            'title': record.get('title') or record.get('article_title', ''),
                            'industry': industry,
                            'sector': sector,
                            'max_excursion_pct': max_excursion,
                            'session': record.get('session', ''),
                            'source_file': str(json_file.relative_to(PROJECT_ROOT))
                        })
                except Exception:
                    continue
        except Exception as e:
            continue
    
    # Analyze by industry
    industry_analysis = {}
    
    for industry, industry_winners in winners_by_industry.items():
        if not industry_winners:
            continue
        
        # Find matching losers and non-movers from same industry
        industry_losers = [
            r for r in week_records
            if r.get('industry') == industry and -10.0 <= r.get('max_excursion_pct', 0) < -1.0
        ]
        industry_non_movers = [
            r for r in week_records
            if r.get('industry') == industry and -1.0 <= r.get('max_excursion_pct', 0) <= 1.0
        ]
        
        # Analyze patterns
        patterns = analyze_patterns(industry_winners, industry_losers, industry_non_movers)
        
        industry_analysis[industry] = {
            'winners_count': len(industry_winners),
            'losers_count': len(industry_losers),
            'non_movers_count': len(industry_non_movers),
            'winners': industry_winners,
            'patterns': patterns
        }
    
    # Analyze by sector
    sector_analysis = {}
    
    for sector, sector_winners in winners_by_sector.items():
        if not sector_winners:
            continue
        
        # Find matching losers and non-movers from same sector
        sector_losers = [
            r for r in week_records
            if r.get('sector') == sector and -10.0 <= r.get('max_excursion_pct', 0) < -1.0
        ]
        sector_non_movers = [
            r for r in week_records
            if r.get('sector') == sector and -1.0 <= r.get('max_excursion_pct', 0) <= 1.0
        ]
        
        # Analyze patterns
        patterns = analyze_patterns(sector_winners, sector_losers, sector_non_movers)
        
        sector_analysis[sector] = {
            'winners_count': len(sector_winners),
            'losers_count': len(sector_losers),
            'non_movers_count': len(sector_non_movers),
            'winners': sector_winners,
            'patterns': patterns
        }
    
    # Create report
    report = {
        'analysis_date': datetime.now(timezone.utc).isoformat(),
        'today_date': '2026-01-15',
        'summary': {
            'total_winners_today': len(today_records),
            'industries_with_winners': len(industry_analysis),
            'sectors_with_winners': len(sector_analysis)
        },
        'winners_today': today_records,
        'industry_analysis': {
            industry: {
                'winners_count': data['winners_count'],
                'losers_count': data['losers_count'],
                'non_movers_count': data['non_movers_count'],
                'patterns': data['patterns']
            }
            for industry, data in industry_analysis.items()
        },
        'sector_analysis': {
            sector: {
                'winners_count': data['winners_count'],
                'losers_count': data['losers_count'],
                'non_movers_count': data['non_movers_count'],
                'patterns': data['patterns']
            }
            for sector, data in sector_analysis.items()
        },
        'detailed_industry_data': industry_analysis,
        'detailed_sector_data': sector_analysis
    }
    
    # Save report
    output_file = PROJECT_ROOT / "todays_winners_linguistic_analysis.json"
    with open(output_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n✅ Saved report to: {output_file}")
    
    # Print key findings
    print(f"\n📈 KEY LINGUISTIC PATTERNS (WINNERS vs LOSSES/NON-MOVERS):")
    
    for industry, data in sorted(industry_analysis.items(), key=lambda x: x[1]['winners_count'], reverse=True):
        patterns = data['patterns']
        distinctive = patterns.get('distinctive_keywords', [])
        
        print(f"\n{'='*80}")
        print(f"INDUSTRY: {industry}")
        print(f"  Winners: {data['winners_count']} | Losers: {data['losers_count']} | Non-movers: {data['non_movers_count']}")
        
        if distinctive:
            print(f"\n  🔥 DISTINCTIVE KEYWORDS (in winners, NOT in losers/non-movers):")
            for kw in distinctive[:15]:
                print(f"     • {kw}")
        else:
            print(f"\n  ⚠️  No distinctive keywords found (all keywords also appear in losers/non-movers)")
        
        print(f"\n  📊 TOP KEYWORDS BY DIFFERENCE (winners vs others):")
        for kw, kw_data in list(patterns.get('keyword_analysis', {}).items())[:10]:
            if kw_data.get('is_distinctive'):
                marker = "🌟"
            elif kw_data.get('difference_pct', 0) > 20:
                marker = "✓"
            else:
                marker = "  "
            print(f"     {marker} {kw}: +{kw_data['difference_pct']:.1f}% (winners: {kw_data['winner_pct']:.1f}%, others: {kw_data['others_pct']:.1f}%)")
        
        print(f"\n  📰 WINNER HEADLINES:")
        for winner in data['winners'][:5]:
            print(f"     • {winner['title']} ({winner['max_excursion_pct']:.2f}%)")
    
    # Sector-level analysis
    print(f"\n\n{'='*80}")
    print(f"SECTOR-LEVEL ANALYSIS:")
    for sector, data in sorted(sector_analysis.items(), key=lambda x: x[1]['winners_count'], reverse=True):
        patterns = data['patterns']
        distinctive = patterns.get('distinctive_keywords', [])
        
        print(f"\n  SECTOR: {sector}")
        print(f"    Winners: {data['winners_count']} | Losers: {data['losers_count']} | Non-movers: {data['non_movers_count']}")
        if distinctive:
            print(f"    Distinctive keywords: {', '.join(distinctive[:10])}")
    
    return report


if __name__ == "__main__":
    report = main()
    print(f"\n✅ Analysis complete!")
