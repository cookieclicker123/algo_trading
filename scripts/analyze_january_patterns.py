#!/usr/bin/env python3
"""
Analyze January recall records for sector/industry patterns, headline patterns, and win rates.
"""
import json
import glob
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any, Optional
from pathlib import Path

def load_all_recall_records() -> List[Dict[str, Any]]:
    """Load all recall records from January files."""
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
        except Exception as e:
            print(f"Error loading {file_path}: {e}")
            continue
    
    print(f"Loaded {len(all_records)} total records from {len(recall_files)} files")
    return all_records

def calculate_profit_from_record(record: Dict[str, Any]) -> Optional[float]:
    """Calculate profit from initial_nbbo to price_check_10min."""
    initial_nbbo = record.get('initial_nbbo')
    price_check = record.get('price_check_10min')
    
    if not initial_nbbo or not price_check:
        return None
    
    initial_mid = initial_nbbo.get('mid')
    final_mid = price_check.get('mid')
    percent_change = price_check.get('percent_change')
    
    if initial_mid and final_mid and initial_mid > 0:
        # Calculate profit percentage
        profit_pct = ((final_mid - initial_mid) / initial_mid) * 100
        return profit_pct
    
    if percent_change is not None:
        return percent_change
    
    return None

def is_winner(profit_pct: Optional[float]) -> bool:
    """Determine if trade was a winner (profit > 0)."""
    return profit_pct is not None and profit_pct > 0

def extract_sector_industry(record: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Extract sector and industry from ticker_metadata."""
    ticker_metadata = record.get('ticker_metadata', {})
    
    # Get first ticker's metadata (or aggregate if multiple)
    sectors = []
    industries = []
    
    for ticker, metadata in ticker_metadata.items():
        if isinstance(metadata, dict):
            sector = metadata.get('sector')
            industry = metadata.get('industry')
            if sector:
                sectors.append(sector)
            if industry:
                industries.append(industry)
    
    return {
        'sector': sectors[0] if sectors else None,
        'industry': industries[0] if industries else None,
        'all_sectors': list(set(sectors)),
        'all_industries': list(set(industries))
    }

def analyze_patterns():
    """Main analysis function."""
    records = load_all_recall_records()
    
    # Statistics by sector
    sector_stats = defaultdict(lambda: {
        'trades': 0,
        'wins': 0,
        'losses': 0,
        'total_profit_pct': 0.0,
        'winning_profits': [],
        'losing_profits': [],
        'surge_detections': 0,
        'is_traded': 0,
        'titles': []
    })
    
    # Statistics by industry
    industry_stats = defaultdict(lambda: {
        'trades': 0,
        'wins': 0,
        'losses': 0,
        'total_profit_pct': 0.0,
        'winning_profits': [],
        'losing_profits': [],
        'surge_detections': 0,
        'is_traded': 0,
        'titles': []
    })
    
    # Headline patterns for winners
    winner_titles = []
    loser_titles = []
    
    # Process each record
    for record in records:
        # Get sector/industry
        sector_info = extract_sector_industry(record)
        sector = sector_info['sector']
        industry = sector_info['industry']
        
        # Calculate profit
        profit_pct = calculate_profit_from_record(record)
        title = record.get('title', '')
        is_traded_record = record.get('is_traded', False)
        
        # Check if surge was detected
        volume_stats = record.get('volume_stats', {})
        has_surge = False
        for ticker, stats in volume_stats.items():
            if isinstance(stats, dict) and stats.get('move_type') == 'SURGE':
                has_surge = True
                break
        
        # Track by sector
        if sector:
            sector_stats[sector]['trades'] += 1
            if profit_pct is not None:
                sector_stats[sector]['total_profit_pct'] += profit_pct
                if is_winner(profit_pct):
                    sector_stats[sector]['wins'] += 1
                    sector_stats[sector]['winning_profits'].append(profit_pct)
                    winner_titles.append((sector, title))
                else:
                    sector_stats[sector]['losses'] += 1
                    sector_stats[sector]['losing_profits'].append(profit_pct)
                    loser_titles.append((sector, title))
            if has_surge:
                sector_stats[sector]['surge_detections'] += 1
            if is_traded_record:
                sector_stats[sector]['is_traded'] += 1
            sector_stats[sector]['titles'].append(title)
        
        # Track by industry
        if industry:
            industry_stats[industry]['trades'] += 1
            if profit_pct is not None:
                industry_stats[industry]['total_profit_pct'] += profit_pct
                if is_winner(profit_pct):
                    industry_stats[industry]['wins'] += 1
                    industry_stats[industry]['winning_profits'].append(profit_pct)
                    winner_titles.append((industry, title))
                else:
                    industry_stats[industry]['losses'] += 1
                    industry_stats[industry]['losing_profits'].append(profit_pct)
                    loser_titles.append((industry, title))
            if has_surge:
                industry_stats[industry]['surge_detections'] += 1
            if is_traded_record:
                industry_stats[industry]['is_traded'] += 1
            industry_stats[industry]['titles'].append(title)
    
    # Calculate win rates and averages
    def calculate_stats(stats_dict):
        result = []
        for key, stats in stats_dict.items():
            total = stats['wins'] + stats['losses']
            if total > 0:
                win_rate = (stats['wins'] / total) * 100
                avg_profit = stats['total_profit_pct'] / total if total > 0 else 0
                avg_win = sum(stats['winning_profits']) / len(stats['winning_profits']) if stats['winning_profits'] else 0
                avg_loss = sum(stats['losing_profits']) / len(stats['losing_profits']) if stats['losing_profits'] else 0
                
                result.append({
                    'name': key,
                    'trades': total,
                    'wins': stats['wins'],
                    'losses': stats['losses'],
                    'win_rate': win_rate,
                    'avg_profit_pct': avg_profit,
                    'avg_win_pct': avg_win,
                    'avg_loss_pct': avg_loss,
                    'total_profit_pct': stats['total_profit_pct'],
                    'surge_detections': stats['surge_detections'],
                    'is_traded': stats['is_traded'],
                    'titles': stats['titles']
                })
        return result
    
    sector_results = calculate_stats(sector_stats)
    industry_results = calculate_stats(industry_stats)
    
    # Sort by win rate (then by total profit)
    sector_results.sort(key=lambda x: (x['win_rate'], x['total_profit_pct']), reverse=True)
    industry_results.sort(key=lambda x: (x['win_rate'], x['total_profit_pct']), reverse=True)
    
    # Print results
    print("\n" + "="*80)
    print("SECTOR ANALYSIS (Top Performers)")
    print("="*80)
    for i, sector in enumerate(sector_results[:10], 1):
        print(f"\n{i}. {sector['name']}")
        print(f"   Trades: {sector['trades']} | Wins: {sector['wins']} | Losses: {sector['losses']}")
        print(f"   Win Rate: {sector['win_rate']:.1f}%")
        print(f"   Avg Profit: {sector['avg_profit_pct']:.2f}% | Avg Win: {sector['avg_win_pct']:.2f}% | Avg Loss: {sector['avg_loss_pct']:.2f}%")
        print(f"   Total Profit: {sector['total_profit_pct']:.2f}%")
        print(f"   Surge Detections: {sector['surge_detections']} | Actually Traded: {sector['is_traded']}")
    
    print("\n" + "="*80)
    print("INDUSTRY ANALYSIS (Top Performers)")
    print("="*80)
    for i, industry in enumerate(industry_results[:15], 1):
        if industry['trades'] >= 2:  # Only show industries with 2+ trades
            print(f"\n{i}. {industry['name']}")
            print(f"   Trades: {industry['trades']} | Wins: {industry['wins']} | Losses: {industry['losses']}")
            print(f"   Win Rate: {industry['win_rate']:.1f}%")
            print(f"   Avg Profit: {industry['avg_profit_pct']:.2f}% | Avg Win: {industry['avg_win_pct']:.2f}% | Avg Loss: {industry['avg_loss_pct']:.2f}%")
            print(f"   Total Profit: {industry['total_profit_pct']:.2f}%")
            print(f"   Surge Detections: {industry['surge_detections']} | Actually Traded: {industry['is_traded']}")
    
    # Headline pattern analysis for winners
    print("\n" + "="*80)
    print("HEADLINE PATTERNS FOR WINNERS")
    print("="*80)
    print(f"\nTotal Winner Titles: {len(winner_titles)}")
    print("\nSample Winner Headlines:")
    for sector, title in winner_titles[:20]:
        print(f"  [{sector}] {title}")
    
    # Headline pattern analysis for losers
    print("\n" + "="*80)
    print("HEADLINE PATTERNS FOR LOSERS")
    print("="*80)
    print(f"\nTotal Loser Titles: {len(loser_titles)}")
    print("\nSample Loser Headlines:")
    for sector, title in loser_titles[:20]:
        print(f"  [{sector}] {title}")
    
    # Worst performers
    print("\n" + "="*80)
    print("WORST SECTORS (Low Win Rate)")
    print("="*80)
    worst_sectors = [s for s in sector_results if s['trades'] >= 2]
    worst_sectors.sort(key=lambda x: (x['win_rate'], -x['avg_profit_pct']))
    for i, sector in enumerate(worst_sectors[:5], 1):
        print(f"\n{i}. {sector['name']}")
        print(f"   Trades: {sector['trades']} | Win Rate: {sector['win_rate']:.1f}%")
        print(f"   Avg Profit: {sector['avg_profit_pct']:.2f}% | Avg Loss: {sector['avg_loss_pct']:.2f}%")
    
    print("\n" + "="*80)
    print("WORST INDUSTRIES (Low Win Rate)")
    print("="*80)
    worst_industries = [i for i in industry_results if i['trades'] >= 2]
    worst_industries.sort(key=lambda x: (x['win_rate'], -x['avg_profit_pct']))
    for i, industry in enumerate(worst_industries[:5], 1):
        print(f"\n{i}. {industry['name']}")
        print(f"   Trades: {industry['trades']} | Win Rate: {industry['win_rate']:.1f}%")
        print(f"   Avg Profit: {industry['avg_profit_pct']:.2f}% | Avg Loss: {industry['avg_loss_pct']:.2f}%")
    
    return {
        'sectors': sector_results,
        'industries': industry_results,
        'winner_titles': winner_titles,
        'loser_titles': loser_titles
    }

if __name__ == '__main__':
    results = analyze_patterns()
