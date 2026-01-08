#!/usr/bin/env python3
"""
Deep dive into headline patterns for winners vs losers.
"""
import json
import glob
import re
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

def extract_keywords(title: str) -> List[str]:
    """Extract important keywords from title."""
    title = title.lower()
    
    # Common patterns
    patterns = [
        r'phase\s+[i1]{1,3}|phase\s+[123]',  # Phase trials
        r'fda\s+(approval|approve|approved)',  # FDA approvals
        r'(department\s+of\s+defense|dod|defense\s+contract)',  # Defense contracts
        r'contract\s+(award|awarded|worth)',  # Contract awards
        r'(earnings|revenue|guidance|quarterly)',  # Earnings
        r'(merger|acquisition|acquire|buyout)',  # M&A
        r'(partnership|collaborate|joint\s+venture)',  # Partnerships
        r'(clinical\s+trial|trial\s+results)',  # Clinical trials
        r'(approval|approved|approve)',  # General approvals
        r'(breakthrough|discovery|novel)',  # Discoveries
        r'(positive\s+results|positive\s+data)',  # Positive results
        r'(conference|presentation|conference\s+call)',  # Conferences
        r'(lawsuit|fraud|investigation)',  # Negative keywords
        r'(rebrand|reorganization|restructuring)',  # Rebrands
    ]
    
    keywords = []
    for pattern in patterns:
        matches = re.findall(pattern, title)
        keywords.extend(matches)
    
    return keywords

def analyze_headline_patterns():
    """Analyze headline patterns for winners vs losers."""
    recall_files = glob.glob('tmp/statistics/recall/2026/01/**/*.json', recursive=True)
    all_records = []
    
    for file_path in recall_files:
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    all_records.extend(data)
                elif isinstance(data, dict) and 'records' in data:
                    all_records.extend(data['records'])
        except:
            continue
    
    # Calculate profit and categorize
    winner_titles = []
    loser_titles = []
    winner_keywords = Counter()
    loser_keywords = Counter()
    
    for record in all_records:
        initial_nbbo = record.get('initial_nbbo')
        price_check = record.get('price_check_10min')
        title = record.get('title', '')
        
        if not initial_nbbo or not price_check:
            continue
        
        initial_mid = initial_nbbo.get('mid')
        final_mid = price_check.get('mid')
        percent_change = price_check.get('percent_change')
        
        if initial_mid and final_mid and initial_mid > 0:
            profit_pct = ((final_mid - initial_mid) / initial_mid) * 100
        elif percent_change is not None:
            profit_pct = percent_change
        else:
            continue
        
        keywords = extract_keywords(title)
        
        if profit_pct > 0:
            winner_titles.append((title, profit_pct))
            winner_keywords.update(keywords)
        else:
            loser_titles.append((title, profit_pct))
            loser_keywords.update(keywords)
    
    print("="*80)
    print("HEADLINE PATTERNS ANALYSIS")
    print("="*80)
    
    print("\n🏆 TOP KEYWORDS IN WINNERS:")
    print("-"*80)
    for keyword, count in winner_keywords.most_common(20):
        if count >= 2:  # Only show keywords that appear 2+ times
            print(f"  {keyword}: {count} times")
    
    print("\n❌ TOP KEYWORDS IN LOSERS:")
    print("-"*80)
    for keyword, count in loser_keywords.most_common(20):
        if count >= 2:
            print(f"  {keyword}: {count} times")
    
    # Unique to winners
    winner_only = set(winner_keywords.keys()) - set(loser_keywords.keys())
    loser_only = set(loser_keywords.keys()) - set(winner_keywords.keys())
    
    print("\n✅ KEYWORDS ONLY IN WINNERS:")
    print("-"*80)
    for keyword in sorted(winner_only):
        if winner_keywords[keyword] >= 2:
            print(f"  {keyword}: {winner_keywords[keyword]} times")
    
    print("\n❌ KEYWORDS ONLY IN LOSERS:")
    print("-"*80)
    for keyword in sorted(loser_only):
        if loser_keywords[keyword] >= 2:
            print(f"  {keyword}: {loser_keywords[keyword]} times")
    
    # Top winners by profit
    winner_titles.sort(key=lambda x: x[1], reverse=True)
    print("\n💰 TOP 10 WINNERS (by profit %):")
    print("-"*80)
    for i, (title, profit) in enumerate(winner_titles[:10], 1):
        keywords = extract_keywords(title)
        print(f"\n{i}. +{profit:.2f}%")
        print(f"   {title[:100]}")
        if keywords:
            print(f"   Keywords: {', '.join(keywords[:5])}")
    
    # Biggest losers
    loser_titles.sort(key=lambda x: x[1])
    print("\n📉 TOP 10 LOSERS (by loss %):")
    print("-"*80)
    for i, (title, loss) in enumerate(loser_titles[:10], 1):
        keywords = extract_keywords(title)
        print(f"\n{i}. {loss:.2f}%")
        print(f"   {title[:100]}")
        if keywords:
            print(f"   Keywords: {', '.join(keywords[:5])}")

if __name__ == '__main__':
    analyze_headline_patterns()
