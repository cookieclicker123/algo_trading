#!/usr/bin/env python3
"""
Analyze feature thresholds to find optimal settings for 80%+ predictive power.

Tests different threshold combinations to find signals where at least one feature
is 80%+ predictive (appears in 80%+ of winners but not in losers).
"""
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).parent.parent


def load_results():
    """Load backtest results."""
    results_file = PROJECT_ROOT / "biotech_backtest_results.json"
    with open(results_file) as f:
        return json.load(f)


def check_feature(record: Dict, feature_name: str, time_key: str, threshold: float) -> bool:
    """Check if a feature meets threshold."""
    feature_data = record.get(feature_name, {})
    value = feature_data.get(time_key)
    
    if value is None:
        return False
    
    if feature_name == 'spread_compression':
        # Spread: compression >threshold OR widening <-threshold
        return value > threshold or value < -abs(threshold)
    elif feature_name == 'max_excursion_in_window':
        # Max excursion: positive movement
        return value > threshold
    else:
        # Tick density, delta ratio, quote frequency: simple > threshold
        return value > threshold


def analyze_thresholds(results: Dict, time_key: str = '3.0s'):
    """Analyze different threshold combinations."""
    winners = [w for w in results['detailed_results']['winners'] if w.get('data_available')]
    losers = [l for l in results['detailed_results']['losers'] if l.get('data_available')]
    
    print(f"📊 THRESHOLD OPTIMIZATION ANALYSIS (at {time_key})\n")
    print(f"Winners with data: {len(winners)}")
    print(f"Losers with data: {len(losers)}\n")
    
    # Test different thresholds for each feature
    features_to_test = {
        'tick_density': [2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        'delta_ratio': [1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
        'quote_frequency': [5.0, 7.0, 10.0, 15.0, 20.0],
        'max_excursion_in_window': [0.3, 0.5, 0.7, 1.0, 1.5],
        'spread_compression': [10.0, 15.0, 20.0, 25.0]  # Compression or widening threshold
    }
    
    best_combinations = []
    
    for feature_name, thresholds in features_to_test.items():
        print(f"\n🔍 Testing {feature_name}:")
        print("-" * 60)
        
        for threshold in thresholds:
            winner_count = sum(1 for w in winners if check_feature(w, feature_name, time_key, threshold))
            loser_count = sum(1 for l in losers if check_feature(l, feature_name, time_key, threshold))
            
            winner_pct = (winner_count / len(winners)) * 100 if winners else 0
            loser_pct = (loser_count / len(losers)) * 100 if losers else 0
            discrimination = winner_pct - loser_pct
            
            print(f"  Threshold {threshold}: Winners={winner_pct:.1f}% ({winner_count}/{len(winners)}), "
                  f"Losers={loser_pct:.1f}% ({loser_count}/{len(losers)}), "
                  f"Discrimination=+{discrimination:.1f}%")
            
            # Track if this meets 80%+ predictive power
            if winner_pct >= 80.0 and loser_pct <= 20.0:
                best_combinations.append({
                    'feature': feature_name,
                    'threshold': threshold,
                    'time': time_key,
                    'winner_pct': winner_pct,
                    'loser_pct': loser_pct,
                    'discrimination': discrimination
                })
    
    # Test combinations
    print(f"\n\n🎯 COMBINATION ANALYSIS (ANY feature met):")
    print("=" * 60)
    
    # Test different combinations
    test_combos = [
        {'tick_density': 3.0, 'delta_ratio': 2.0, 'quote_frequency': 7.0},
        {'tick_density': 4.0, 'delta_ratio': 2.5, 'quote_frequency': 10.0},
        {'tick_density': 5.0, 'delta_ratio': 3.0, 'quote_frequency': 10.0},
        {'tick_density': 3.0, 'delta_ratio': 2.0, 'quote_frequency': 7.0, 'max_excursion_in_window': 0.5},
        {'tick_density': 4.0, 'delta_ratio': 2.5, 'quote_frequency': 7.0, 'max_excursion_in_window': 0.5},
    ]
    
    for combo in test_combos:
        winner_any = sum(1 for w in winners if any(
            check_feature(w, feat, time_key, thresh) 
            for feat, thresh in combo.items()
        ))
        loser_any = sum(1 for l in losers if any(
            check_feature(l, feat, time_key, thresh) 
            for feat, thresh in combo.items()
        ))
        
        winner_pct = (winner_any / len(winners)) * 100 if winners else 0
        loser_pct = (loser_any / len(losers)) * 100 if losers else 0
        
        print(f"\nCombo: {combo}")
        print(f"  Winners (ANY): {winner_pct:.1f}% ({winner_any}/{len(winners)})")
        print(f"  Losers (ANY): {loser_pct:.1f}% ({loser_any}/{len(losers)})")
        print(f"  Discrimination: +{winner_pct - loser_pct:.1f}%")
        
        if winner_pct >= 80.0:
            print(f"  ✅ MEETS 80%+ TARGET!")
    
    return best_combinations


def analyze_spread_patterns(results: Dict):
    """Analyze spread size patterns over time."""
    print("\n\n📊 SPREAD SIZE PATTERN ANALYSIS:")
    print("=" * 60)
    
    winners = [w for w in results['detailed_results']['winners'] if w.get('data_available')]
    losers = [l for l in results['detailed_results']['losers'] if l.get('data_available')]
    
    # Load recall records to get initial spreads
    def load_recall_record(article_id):
        recall_dir = PROJECT_ROOT / "tmp/statistics/recall/2026/01"
        if not recall_dir.exists():
            return None
        
        for week_dir in recall_dir.glob("week_*"):
            for day_dir in week_dir.glob("*"):
                for session_file in [day_dir / "premarket.json", day_dir / "postmarket.json"]:
                    if not session_file.exists():
                        continue
                    try:
                        with open(session_file) as f:
                            data = json.load(f)
                        records = data if isinstance(data, list) else data.get('records', [])
                        for record in records:
                            if isinstance(record, dict) and record.get('article_id') == article_id:
                                return record
                    except:
                        continue
        return None
    
    winner_spreads = {t: [] for t in ['1.0s', '2.0s', '3.0s', '5.0s']}
    loser_spreads = {t: [] for t in ['1.0s', '2.0s', '3.0s', '5.0s']}
    
    for winner in winners:
        recall_record = load_recall_record(winner.get('article_id'))
        if not recall_record:
            continue
        
        initial_nbbo = recall_record.get('initial_nbbo', {})
        if not initial_nbbo:
            continue
        
        initial_spread = initial_nbbo.get('spread')
        initial_bid = initial_nbbo.get('bid')
        
        if not initial_spread or not initial_bid or initial_bid <= 0:
            continue
        
        for time_key in ['1.0s', '2.0s', '3.0s', '5.0s']:
            spread_comp = winner.get('spread_compression', {}).get(time_key)
            if spread_comp is not None:
                current_spread = initial_spread * (1 - spread_comp/100)
                spread_pct = (current_spread / initial_bid) * 100
                winner_spreads[time_key].append(spread_pct)
    
    for loser in losers:
        recall_record = load_recall_record(loser.get('article_id'))
        if not recall_record:
            continue
        
        initial_nbbo = recall_record.get('initial_nbbo', {})
        if not initial_nbbo:
            continue
        
        initial_spread = initial_nbbo.get('spread')
        initial_bid = initial_nbbo.get('bid')
        
        if not initial_spread or not initial_bid or initial_bid <= 0:
            continue
        
        for time_key in ['1.0s', '2.0s', '3.0s', '5.0s']:
            spread_comp = loser.get('spread_compression', {}).get(time_key)
            if spread_comp is not None:
                current_spread = initial_spread * (1 - spread_comp/100)
                spread_pct = (current_spread / initial_bid) * 100
                loser_spreads[time_key].append(spread_pct)
    
    print("\nWinners - Spread Size (% of bid):")
    for time_key in ['1.0s', '2.0s', '3.0s', '5.0s']:
        if winner_spreads[time_key]:
            spreads = winner_spreads[time_key]
            print(f"  {time_key}: avg={sum(spreads)/len(spreads):.2f}%, "
                  f"min={min(spreads):.2f}%, max={max(spreads):.2f}%, "
                  f"median={sorted(spreads)[len(spreads)//2]:.2f}%")
    
    print("\nLosers - Spread Size (% of bid):")
    for time_key in ['1.0s', '2.0s', '3.0s', '5.0s']:
        if loser_spreads[time_key]:
            spreads = loser_spreads[time_key]
            print(f"  {time_key}: avg={sum(spreads)/len(spreads):.2f}%, "
                  f"min={min(spreads):.2f}%, max={max(spreads):.2f}%, "
                  f"median={sorted(spreads)[len(spreads)//2]:.2f}%")
    
    # Check if spread size can filter losers
    print("\n🔍 SPREAD SIZE FILTER ANALYSIS:")
    for max_spread_pct in [1.0, 1.5, 2.0, 2.5, 3.0]:
        winner_pass = sum(1 for spreads in winner_spreads['3.0s'] if spreads <= max_spread_pct)
        loser_pass = sum(1 for spreads in loser_spreads['3.0s'] if spreads <= max_spread_pct)
        
        winner_total = len(winner_spreads['3.0s'])
        loser_total = len(loser_spreads['3.0s'])
        
        if winner_total > 0 and loser_total > 0:
            winner_pct = (winner_pass / winner_total) * 100
            loser_pct = (loser_pass / loser_total) * 100
            print(f"  Spread <{max_spread_pct}%: Winners={winner_pct:.1f}% ({winner_pass}/{winner_total}), "
                  f"Losers={loser_pct:.1f}% ({loser_pass}/{loser_total})")


def main():
    """Main analysis."""
    print("🔍 BIOTECH FEATURE THRESHOLD OPTIMIZATION")
    print("=" * 60)
    
    results = load_results()
    
    # Analyze thresholds
    best = analyze_thresholds(results, time_key='3.0s')
    
    # Analyze spread patterns
    analyze_spread_patterns(results)
    
    # Summary
    print("\n\n✅ SUMMARY:")
    print("=" * 60)
    if best:
        print(f"Found {len(best)} features meeting 80%+ predictive power:")
        for b in best:
            print(f"  - {b['feature']} (threshold={b['threshold']}): "
                  f"{b['winner_pct']:.1f}% winners, {b['loser_pct']:.1f}% losers")
    else:
        print("No single feature meets 80%+ predictive power alone.")
        print("Need to use combinations of features.")


if __name__ == "__main__":
    main()
