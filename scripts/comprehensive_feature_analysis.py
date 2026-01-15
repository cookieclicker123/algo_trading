#!/usr/bin/env python3
"""
Comprehensive Feature Analysis: Find 80%+ Predictive Signals

Addresses:
1. Are thresholds too strict?
2. Why do records fail?
3. Load non-movers for comparison
4. Spread size analysis at 1,2,3,4,5 seconds
5. Find signals with 80%+ predictive power
"""
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).parent.parent


def load_all_biotech_records():
    """Load winners, losers, and non-movers from healthcare analysis."""
    report_file = PROJECT_ROOT / "healthcare_pattern_analysis.json"
    
    if not report_file.exists():
        print(f"❌ Error: {report_file} not found")
        sys.exit(1)
    
    with open(report_file) as f:
        report = json.load(f)
    
    # Get all healthcare records
    winners = report['detailed_data']['winners']
    non_movers = report['detailed_data']['non_movers_sample']
    
    # Filter to biotech
    biotech_winners = [w for w in winners if w.get('industry') == 'Biotechnology']
    biotech_non_movers = [n for n in non_movers if n.get('industry') == 'Biotechnology']
    
    # Load losers from recall files
    biotech_losers = []
    recall_dir = PROJECT_ROOT / "tmp" / "statistics" / "recall" / "2026" / "01"
    
    if recall_dir.exists():
        for week_dir in sorted(recall_dir.glob("week_*")):
            for day_dir in sorted(week_dir.glob("*")):
                for session_dir in sorted(day_dir.glob("*")):
                    for json_file in sorted(session_dir.glob("*.json")):
                        try:
                            with open(json_file) as f:
                                data = json.load(f)
                            records = data if isinstance(data, list) else data.get('records', [])
                            
                            for record in records:
                                if not isinstance(record, dict):
                                    continue
                                
                                # Check if biotech
                                metadata = record.get('ticker_metadata', {})
                                is_biotech = False
                                for ticker_data in metadata.values():
                                    if isinstance(ticker_data, dict):
                                        industry = ticker_data.get('industry', '')
                                        if 'Biotechnology' in industry or 'Biotech' in industry:
                                            is_biotech = True
                                            break
                                
                                if not is_biotech:
                                    continue
                                
                                # Check if loser
                                highest_price_data = record.get('highest_price_during_hold', {})
                                initial_nbbo = record.get('initial_nbbo', {})
                                
                                if highest_price_data and isinstance(highest_price_data, dict) and initial_nbbo:
                                    peak_price = highest_price_data.get('price')
                                    initial_ask = initial_nbbo.get('ask')
                                    
                                    if peak_price and initial_ask and initial_ask > 0:
                                        max_excursion = ((peak_price - initial_ask) / initial_ask) * 100
                                        
                                        if max_excursion < -1.0:
                                            loser_data = {
                                                'article_id': record.get('article_id'),
                                                'title': record.get('title', ''),
                                                'received_at': record.get('received_at'),
                                                'published_at': record.get('published_at'),
                                                'tickers': record.get('tickers', []),
                                                'industry': 'Biotechnology',
                                                'max_excursion_pct': max_excursion,
                                                'initial_ask': initial_ask,
                                                'peak_price': peak_price,
                                                'source_file': str(json_file.relative_to(PROJECT_ROOT))
                                            }
                                            biotech_losers.append(loser_data)
                        except:
                            continue
    
    return biotech_winners, biotech_losers, biotech_non_movers


def load_backtest_results():
    """Load backtest feature results."""
    results_file = PROJECT_ROOT / "biotech_backtest_results.json"
    if not results_file.exists():
        return None
    
    with open(results_file) as f:
        return json.load(f)


def analyze_spread_sizes(results: Dict):
    """Analyze spread sizes at 1,2,3,4,5 seconds."""
    print("\n" + "=" * 80)
    print("📊 SPREAD SIZE ANALYSIS (1-5 seconds)")
    print("=" * 80)
    
    def load_recall(article_id):
        recall_dir = PROJECT_ROOT / "tmp/statistics/recall/2026/01"
        if not recall_dir.exists():
            return None
        for week_dir in recall_dir.glob("week_*"):
            for day_dir in week_dir.glob("*"):
                for f in [day_dir / "premarket.json", day_dir / "postmarket.json"]:
                    if not f.exists():
                        continue
                    try:
                        with open(f) as file:
                            data = json.load(file)
                        records = data if isinstance(data, list) else data.get('records', [])
                        for r in records:
                            if isinstance(r, dict) and r.get('article_id') == article_id:
                                return r
                    except:
                        continue
        return None
    
    winners = [w for w in results['detailed_results']['winners'] if w.get('data_available')]
    losers = [l for l in results['detailed_results']['losers'] if l.get('data_available')]
    
    winner_spreads = {t: [] for t in ['1.0s', '2.0s', '3.0s', '5.0s']}
    loser_spreads = {t: [] for t in ['1.0s', '2.0s', '3.0s', '5.0s']}
    
    print("\nProcessing winners...")
    for winner in winners:
        rec = load_recall(winner.get('article_id'))
        if not rec:
            continue
        nbbo = rec.get('initial_nbbo', {})
        if not nbbo:
            continue
        init_spread = nbbo.get('spread')
        init_bid = nbbo.get('bid')
        if not init_spread or not init_bid or init_bid <= 0:
            continue
        
        for t in ['1.0s', '2.0s', '3.0s', '5.0s']:
            comp = winner.get('spread_compression', {}).get(t)
            if comp is not None:
                curr_spread = init_spread * (1 - comp/100)
                spread_pct = (curr_spread / init_bid) * 100
                winner_spreads[t].append(spread_pct)
    
    print("Processing losers...")
    for loser in losers:
        rec = load_recall(loser.get('article_id'))
        if not rec:
            continue
        nbbo = rec.get('initial_nbbo', {})
        if not nbbo:
            continue
        init_spread = nbbo.get('spread')
        init_bid = nbbo.get('bid')
        if not init_spread or not init_bid or init_bid <= 0:
            continue
        
        for t in ['1.0s', '2.0s', '3.0s', '5.0s']:
            comp = loser.get('spread_compression', {}).get(t)
            if comp is not None:
                curr_spread = init_spread * (1 - comp/100)
                spread_pct = (curr_spread / init_bid) * 100
                loser_spreads[t].append(spread_pct)
    
    print("\n✅ WINNERS - Spread Size (% of bid):")
    for t in ['1.0s', '2.0s', '3.0s', '5.0s']:
        if winner_spreads[t]:
            s = winner_spreads[t]
            print(f"  {t}: avg={sum(s)/len(s):.2f}%, min={min(s):.2f}%, max={max(s):.2f}%, "
                  f"median={sorted(s)[len(s)//2]:.2f}%, count={len(s)}")
    
    print("\n❌ LOSERS - Spread Size (% of bid):")
    for t in ['1.0s', '2.0s', '3.0s', '5.0s']:
        if loser_spreads[t]:
            s = loser_spreads[t]
            print(f"  {t}: avg={sum(s)/len(s):.2f}%, min={min(s):.2f}%, max={max(s):.2f}%, "
                  f"median={sorted(s)[len(s)//2]:.2f}%, count={len(s)}")
    
    # Test spread filters
    print("\n🔍 SPREAD FILTER EFFECTIVENESS:")
    for max_spread in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
        w_pass = sum(1 for s in winner_spreads['3.0s'] if s <= max_spread)
        l_pass = sum(1 for s in loser_spreads['3.0s'] if s <= max_spread)
        w_total = len(winner_spreads['3.0s'])
        l_total = len(loser_spreads['3.0s'])
        if w_total > 0:
            print(f"  Spread <{max_spread}%: Winners={w_pass}/{w_total} ({w_pass/w_total*100:.1f}%), "
                  f"Losers={l_pass}/{l_total} ({l_pass/l_total*100:.1f}% if {l_total}>0)")


def find_80_percent_signals(results: Dict):
    """Find feature combinations with 80%+ predictive power."""
    print("\n" + "=" * 80)
    print("🎯 FINDING 80%+ PREDICTIVE SIGNALS")
    print("=" * 80)
    
    winners = [w for w in results['detailed_results']['winners'] if w.get('data_available')]
    losers = [l for l in results['detailed_results']['losers'] if l.get('data_available')]
    
    def check_feature(record, feature_name, time_key, threshold):
        value = record.get(feature_name, {}).get(time_key)
        if value is None:
            return False
        if feature_name == 'spread_compression':
            return value > threshold or value < -abs(threshold)
        elif feature_name == 'max_excursion_in_window':
            return value > threshold
        else:
            return value > threshold
    
    # Test individual features with various thresholds
    print("\n📈 INDIVIDUAL FEATURES:")
    print("-" * 80)
    
    features_config = [
        ('tick_density', [2.0, 3.0, 4.0, 5.0, 6.0]),
        ('delta_ratio', [1.5, 2.0, 2.5, 3.0, 4.0]),
        ('quote_frequency', [5.0, 7.0, 10.0, 15.0]),
        ('max_excursion_in_window', [0.3, 0.5, 0.7, 1.0]),
        ('spread_compression', [10.0, 15.0, 20.0])
    ]
    
    best_single = []
    
    for feature_name, thresholds in features_config:
        print(f"\n{feature_name.upper()}:")
        for threshold in thresholds:
            winner_count = sum(1 for w in winners if check_feature(w, feature_name, '3.0s', threshold))
            loser_count = sum(1 for l in losers if check_feature(l, feature_name, '3.0s', threshold))
            
            winner_pct = (winner_count / len(winners)) * 100 if winners else 0
            loser_pct = (loser_count / len(losers)) * 100 if losers else 0
            
            marker = "✅ 80%+!" if winner_pct >= 80.0 and loser_pct <= 20.0 else ""
            print(f"  {threshold}: {winner_pct:.1f}% winners ({winner_count}/{len(winners)}), "
                  f"{loser_pct:.1f}% losers ({loser_count}/{len(losers)}) {marker}")
            
            if winner_pct >= 80.0:
                best_single.append((feature_name, threshold, winner_pct, loser_pct))
    
    # Test combinations
    print("\n\n📊 COMBINATIONS (ANY feature met):")
    print("-" * 80)
    
    combos = [
        {'tick_density': 3.0, 'delta_ratio': 2.0},
        {'tick_density': 3.0, 'quote_frequency': 7.0},
        {'tick_density': 3.0, 'max_excursion_in_window': 0.5},
        {'delta_ratio': 2.0, 'quote_frequency': 7.0},
        {'tick_density': 3.0, 'delta_ratio': 2.0, 'quote_frequency': 7.0},
        {'tick_density': 3.0, 'delta_ratio': 2.0, 'max_excursion_in_window': 0.5},
        {'tick_density': 3.0, 'delta_ratio': 2.0, 'quote_frequency': 7.0, 'max_excursion_in_window': 0.5},
        {'tick_density': 2.0, 'delta_ratio': 1.5, 'quote_frequency': 5.0},  # More relaxed
        {'tick_density': 2.0, 'delta_ratio': 1.5, 'max_excursion_in_window': 0.3},  # More relaxed
    ]
    
    best_combos = []
    
    for combo in combos:
        winner_any = sum(1 for w in winners if any(
            check_feature(w, feat, '3.0s', thresh) for feat, thresh in combo.items()
        ))
        loser_any = sum(1 for l in losers if any(
            check_feature(l, feat, '3.0s', thresh) for feat, thresh in combo.items()
        ))
        
        winner_pct = (winner_any / len(winners)) * 100 if winners else 0
        loser_pct = (loser_any / len(losers)) * 100 if losers else 0
        
        marker = "✅ 80%+!" if winner_pct >= 80.0 else ""
        print(f"\n{combo}")
        print(f"  Winners (ANY): {winner_pct:.1f}% ({winner_any}/{len(winners)}) {marker}")
        print(f"  Losers (ANY): {loser_pct:.1f}% ({loser_any}/{len(losers)})")
        
        if winner_pct >= 80.0:
            best_combos.append((combo, winner_pct, loser_pct))
    
    return best_single, best_combos


def main():
    """Main analysis."""
    print("=" * 80)
    print("🔍 COMPREHENSIVE BIOTECH FEATURE ANALYSIS")
    print("=" * 80)
    
    # Load backtest results
    results = load_backtest_results()
    if not results:
        print("❌ Error: biotech_backtest_results.json not found. Run biotech_backtester.py first.")
        sys.exit(1)
    
    winners = results['detailed_results']['winners']
    losers = results['detailed_results']['losers']
    
    winners_with_data = [w for w in winners if w.get('data_available')]
    losers_with_data = [l for l in losers if l.get('data_available')]
    
    print(f"\n📊 DATA AVAILABILITY:")
    print(f"  Winners: {len(winners_with_data)}/{len(winners)} ({len(winners_with_data)/len(winners)*100:.1f}%)")
    print(f"  Losers: {len(losers_with_data)}/{len(losers)} ({len(losers_with_data)/len(losers)*100:.1f}%)")
    
    # Why records failed
    failed_winners = [w for w in winners if not w.get('data_available')]
    print(f"\n🔍 WHY {len(failed_winners)} WINNERS FAILED:")
    for w in failed_winners:
        error = w.get('error', 'No trades/quotes in 3s window (illiquid or no activity)')
        print(f"  {w.get('symbol')}: {w.get('article_id')} - {error}")
    
    # Load all biotech records to see total counts
    print("\n📂 Loading all biotech records...")
    biotech_winners, biotech_losers, biotech_non_movers = load_all_biotech_records()
    
    print(f"\n📊 TOTAL BIOTECH RECORDS:")
    print(f"  Winners (>5%): {len(biotech_winners)}")
    print(f"  Losers (<-1%): {len(biotech_losers)}")
    print(f"  Non-movers (-1% to 1%): {len(biotech_non_movers)}")
    print(f"  Total: {len(biotech_winners) + len(biotech_losers) + len(biotech_non_movers)}")
    
    # Spread analysis
    analyze_spread_sizes(results)
    
    # Find 80%+ signals
    best_single, best_combos = find_80_percent_signals(results)
    
    # Summary
    print("\n\n" + "=" * 80)
    print("✅ SUMMARY")
    print("=" * 80)
    
    if best_single:
        print(f"\n🎯 Single features meeting 80%+ target:")
        for feat, thresh, w_pct, l_pct in best_single:
            print(f"  {feat} (threshold={thresh}): {w_pct:.1f}% winners, {l_pct:.1f}% losers")
    else:
        print("\n⚠️  No single feature meets 80%+ target alone")
    
    if best_combos:
        print(f"\n🎯 Combinations meeting 80%+ target:")
        for combo, w_pct, l_pct in best_combos:
            print(f"  {combo}: {w_pct:.1f}% winners, {l_pct:.1f}% losers")
    else:
        print("\n⚠️  No combinations meet 80%+ target (need more data or relaxed thresholds)")


if __name__ == "__main__":
    main()
