#!/usr/bin/env python3
"""
Analyze biotech winners from Jan 2nd to today.
Find all biotech stocks that finished >5% higher from initial NBBO.
Extract headlines and identify patterns.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


def is_biotech(record: Dict[str, Any]) -> bool:
    """Check if record is for a biotech stock."""
    ticker_metadata = record.get('ticker_metadata', {})
    
    for ticker, metadata in ticker_metadata.items():
        if isinstance(metadata, dict):
            industry = metadata.get('industry') or ''
            sector = metadata.get('sector') or ''
            
            # Convert to lowercase for comparison
            industry_lower = str(industry).lower()
            sector_lower = str(sector).lower()
            
            # Biotech keywords
            if 'biotechnology' in industry_lower or 'biotech' in industry_lower:
                return True
            if 'biotechnology' in sector_lower or 'biotech' in sector_lower:
                return True
    
    return False


def calculate_price_change(record: Dict[str, Any]) -> float:
    """Calculate price change percentage from initial NBBO to 10min check."""
    initial_nbbo = record.get('initial_nbbo', {})
    price_check_10min = record.get('price_check_10min', {})
    
    if not initial_nbbo or not price_check_10min:
        return None
    
    initial_ask = initial_nbbo.get('ask')
    final_ask = price_check_10min.get('ask')
    
    # Use percent_change if available, otherwise calculate
    if 'percent_change' in price_check_10min:
        return price_check_10min['percent_change']
    
    if not initial_ask or not final_ask or initial_ask <= 0:
        return None
    
    change_pct = ((final_ask - initial_ask) / initial_ask) * 100
    return change_pct


def extract_headline_data(record: Dict[str, Any]) -> Dict[str, Any]:
    """Extract headline and metadata from record."""
    article_id = record.get('article_id', '')
    title = record.get('title') or record.get('article_title', '')  # Try both keys
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
    
    return {
        'article_id': article_id,
        'title': title,
        'published_at': published_at,
        'received_at': received_at,
        'tickers': tickers,
        'industries': list(industries),
        'sectors': list(sectors),
        'initial_ask': record.get('initial_nbbo', {}).get('ask'),
        'final_ask': record.get('price_check_10min', {}).get('ask'),
        'price_change_pct': calculate_price_change(record),
        'session': record.get('session', ''),
        'filter_reason': record.get('filter_reason', '')
    }


def main():
    """Main analysis function."""
    recall_dir = PROJECT_ROOT / "tmp" / "statistics" / "recall" / "2026" / "01"
    
    if not recall_dir.exists():
        print(f"❌ Recall directory not found: {recall_dir}")
        return
    
    # Find all JSON files from Jan 2nd onwards
    # Jan 2nd = week_1, day 02
    # Jan 13th = week_3, day 13
    json_files = []
    for week_dir in sorted(recall_dir.glob("week_*")):
        for day_dir in sorted(week_dir.glob("*")):
            for session_dir in sorted(day_dir.glob("*")):
                for json_file in sorted(session_dir.glob("*.json")):
                    json_files.append(json_file)
    
    json_files.sort()
    print(f"📂 Found {len(json_files)} recall JSON files")
    
    # Process all files
    biotech_winners = []
    total_biotech = 0
    total_records = 0
    
    for json_file in json_files:
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            # Handle both list and dict structures
            if isinstance(data, dict):
                # Check if it's the metadata format with records array
                if 'records' in data:
                    records = data['records']
                else:
                    # Look for array in the dict
                    records = [v for v in data.values() if isinstance(v, list)]
                    records = records[0] if records else []
            elif isinstance(data, list):
                records = data
            else:
                records = []
                
            # Filter to actual records (skip metadata objects)
            records = [r for r in records if isinstance(r, dict) and 'article_id' in r]
            
            for record in records:
                total_records += 1
                
                # Check if biotech
                if not is_biotech(record):
                    continue
                
                total_biotech += 1
                
                # Calculate price change
                price_change = calculate_price_change(record)
                
                if price_change is None:
                    continue
                
                # Filter for >5% gain
                if price_change > 5.0:
                    headline_data = extract_headline_data(record)
                    headline_data['source_file'] = str(json_file.relative_to(PROJECT_ROOT))
                    biotech_winners.append(headline_data)
        
        except Exception as e:
            print(f"⚠️  Error processing {json_file}: {e}")
            continue
    
    print(f"\n📊 ANALYSIS RESULTS:")
    print(f"   Total records processed: {total_records}")
    print(f"   Biotech records found: {total_biotech}")
    print(f"   Biotech winners (>5% gain): {len(biotech_winners)}")
    
    # Sort by price change (descending)
    biotech_winners.sort(key=lambda x: x.get('price_change_pct', 0), reverse=True)
    
    # Save to JSON
    output_file = PROJECT_ROOT / "biotech_winners_jan2_jan13.json"
    with open(output_file, 'w') as f:
        json.dump({
            'analysis_date': datetime.now(timezone.utc).isoformat(),
            'period': '2026-01-02 to 2026-01-13',
            'total_winners': len(biotech_winners),
            'winners': biotech_winners
        }, f, indent=2)
    
    print(f"\n✅ Saved {len(biotech_winners)} biotech winners to: {output_file}")
    
    # Print summary
    print(f"\n📈 TOP 10 WINNERS:")
    for i, winner in enumerate(biotech_winners[:10], 1):
        print(f"\n   {i}. {winner['tickers'][0] if winner['tickers'] else 'N/A'}: +{winner['price_change_pct']:.2f}%")
        print(f"      {winner['title'][:100]}...")
    
    # Pattern analysis
    print(f"\n🔍 PATTERN ANALYSIS:")
    print(f"   Analyzing {len(biotech_winners)} winning headlines...")
    
    # Extract common keywords/phrases
    keywords = {}
    for winner in biotech_winners:
        title = winner['title'].lower()
        # Look for common biotech keywords
        biotech_keywords = [
            'fda', 'approval', 'approved', 'phase', 'trial', 'clinical', 
            'drug', 'treatment', 'therapy', 'data', 'results', 'positive',
            'breakthrough', 'efficacy', 'safety', 'submission', 'nda', 'bla',
            'orphan', 'designation', 'pdufa', 'catalyst', 'update'
        ]
        
        for keyword in biotech_keywords:
            if keyword in title:
                keywords[keyword] = keywords.get(keyword, 0) + 1
    
    print(f"\n   📝 COMMON KEYWORDS:")
    for keyword, count in sorted(keywords.items(), key=lambda x: x[1], reverse=True)[:15]:
        print(f"      {keyword}: {count} occurrences")
    
    return biotech_winners


if __name__ == "__main__":
    winners = main()
    print(f"\n✅ Analysis complete!")
