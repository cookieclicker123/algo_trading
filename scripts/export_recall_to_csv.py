#!/usr/bin/env python3
"""
Export recall statistics to CSV for backtesting analysis.

Outputs:
- recall_control_group.csv: Losers and neutrals (your control group with exact Benzinga headlines)
- recall_winners.csv: Winners from your own data
- recall_all.csv: Complete dataset

Usage:
    python scripts/export_recall_to_csv.py
"""

import csv
import json
import os
from datetime import datetime
from pathlib import Path


def extract_record(record: dict) -> dict | None:
    """Extract relevant fields from a recall record."""

    # Get primary ticker
    tickers = record.get('tickers', [])
    if not tickers:
        return None
    primary_ticker = tickers[0]

    # Get ticker metadata
    meta = record.get('ticker_metadata', {}).get(primary_ticker, {})

    # Get volume stats
    vol_stats = record.get('volume_stats', {}).get(primary_ticker, {})

    # Get price check results
    price_check = record.get('price_check_10min', {}) or {}

    # Get initial NBBO
    initial_nbbo = record.get('initial_nbbo', {}) or {}

    # Get max excursion - this is the key metric for winner classification
    # max_excursion_pct from volume_stats is during initial monitoring window
    max_excursion = vol_stats.get('max_excursion_pct', 0) or 0

    # Check highest_price_during_hold for full 10 min monitoring window
    # This field exists for both traded and non-traded stocks
    highest_price_data = record.get('highest_price_during_hold')
    if highest_price_data:
        if isinstance(highest_price_data, dict):
            # New format: dict with percent_gain_from_entry
            full_hold_excursion = highest_price_data.get('percent_gain_from_entry', 0) or 0
            max_excursion = max(max_excursion, full_hold_excursion)
        elif isinstance(highest_price_data, (int, float)):
            # Old format: just the price
            initial_ask = initial_nbbo.get('ask', 0)
            if initial_ask and initial_ask > 0:
                full_hold_excursion = (highest_price_data - initial_ask) / initial_ask * 100
                max_excursion = max(max_excursion, full_hold_excursion)

    # Calculate outcome category based on MAX EXCURSION during hold
    # 4-class system for NER training:
    #   0 = LOSER (negative or breakeven after spread costs)
    #   1 = NON_MOVER (0% to <5% - not enough to overcome costs)
    #   2 = WINNER (5% to <10% - solid profitable move)
    #   3 = BIG_WINNER (≥10% - major catalyst)
    if max_excursion >= 10:
        label = 3
        outcome = 'BIG_WINNER'
    elif max_excursion >= 5:
        label = 2
        outcome = 'WINNER'
    elif max_excursion < 0:
        label = 0
        outcome = 'LOSER'
    else:
        label = 1
        outcome = 'NON_MOVER'

    # Also keep price change at 10 min for reference (follow-through indicator)
    pct_change_10min = price_check.get('percent_change', 0) or 0

    return {
        # Identifiers
        'article_id': record.get('article_id', ''),
        'ticker': primary_ticker,

        # Headline (the key data for pattern mining)
        'headline': record.get('title', '').replace('\n', ' ').replace('\r', ''),

        # Classification
        'sector': meta.get('sector', ''),
        'industry': meta.get('industry', ''),

        # Stock characteristics at time of headline
        'market_cap_millions': meta.get('market_cap_millions', ''),
        'price': meta.get('price', ''),
        'exchange': meta.get('exchange', ''),
        'float_shares': vol_stats.get('float_shares', ''),

        # Timing
        'published_at': record.get('published_at', ''),
        'received_at': record.get('received_at', ''),
        'session': record.get('session', ''),
        'pub_to_recv_seconds': vol_stats.get('pub_to_recv_seconds', ''),

        # Initial quote
        'initial_bid': initial_nbbo.get('bid', ''),
        'initial_ask': initial_nbbo.get('ask', ''),
        'initial_spread': initial_nbbo.get('spread', ''),
        'initial_mid': initial_nbbo.get('mid', ''),

        # Outcome - based on MAX EXCURSION during 10 min hold
        'label': label,  # 0=LOSER, 1=NON_MOVER, 2=WINNER, 3=BIG_WINNER
        'max_excursion_pct': max_excursion,
        'outcome': outcome,
        'pct_change_10min': pct_change_10min,  # Follow-through at 10 min mark
        'moved_1_percent': price_check.get('moved_1_percent', False),

        # Microstructure features (for correlation analysis)
        'move_type': vol_stats.get('move_type', ''),
        'surge_multiplier': vol_stats.get('surge_multiplier', ''),
        'trade_count': vol_stats.get('trade_count', ''),
        'imbalance_ratio': vol_stats.get('imbalance_ratio', ''),
        'latency_to_first_trade': vol_stats.get('latency_to_first_trade', ''),
        'buy_volume': vol_stats.get('buy_volume', ''),
        'sell_volume': vol_stats.get('sell_volume', ''),
        'block_trade_pct': vol_stats.get('block_trade_pct', ''),
        'tape_quality_score': vol_stats.get('tape_quality_score', ''),

        # Filter info (why it wasn't traded)
        'filter_reason': record.get('filter_reason', ''),
        'is_traded': record.get('is_traded', False),

        # AI classification if available
        'ai_classification': record.get('ai_classification', ''),
    }


def main():
    recall_dir = Path('tmp/statistics/recall')
    output_dir = Path('tmp/backtest_data')
    output_dir.mkdir(parents=True, exist_ok=True)

    all_records = []

    # Collect all records
    for json_file in recall_dir.rglob('*.json'):
        try:
            with open(json_file) as f:
                data = json.load(f)
                for record in data.get('records', []):
                    extracted = extract_record(record)
                    if extracted:
                        all_records.append(extracted)
        except Exception as e:
            print(f"Error processing {json_file}: {e}")

    print(f"Total records extracted: {len(all_records)}")

    # Sort by max excursion (the key outcome metric)
    all_records.sort(key=lambda x: x['max_excursion_pct'], reverse=True)

    # Define CSV columns
    columns = [
        'article_id', 'ticker', 'headline',
        'sector', 'industry', 'market_cap_millions', 'price', 'exchange', 'float_shares',
        'published_at', 'received_at', 'session', 'pub_to_recv_seconds',
        'initial_bid', 'initial_ask', 'initial_spread', 'initial_mid',
        'label', 'max_excursion_pct', 'outcome', 'pct_change_10min', 'moved_1_percent',
        'move_type', 'surge_multiplier', 'trade_count', 'imbalance_ratio',
        'latency_to_first_trade', 'buy_volume', 'sell_volume',
        'block_trade_pct', 'tape_quality_score',
        'filter_reason', 'is_traded', 'ai_classification'
    ]

    # Write all records
    all_csv = output_dir / 'recall_all.csv'
    with open(all_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(all_records)
    print(f"Wrote {len(all_records)} records to {all_csv}")

    # Split by label
    # Label 0=LOSER, 1=NON_MOVER, 2=WINNER, 3=BIG_WINNER
    big_winners = [r for r in all_records if r['label'] == 3]
    winners = [r for r in all_records if r['label'] in (2, 3)]
    non_movers = [r for r in all_records if r['label'] == 1]
    losers = [r for r in all_records if r['label'] == 0]
    control = [r for r in all_records if r['label'] in (0, 1)]

    # Write winners
    winners_csv = output_dir / 'recall_winners.csv'
    with open(winners_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(winners)
    print(f"Wrote {len(winners)} winners to {winners_csv}")

    # Write control group (losers + neutrals)
    control_csv = output_dir / 'recall_control_group.csv'
    with open(control_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(control)
    print(f"Wrote {len(control)} control group records to {control_csv}")

    # Label distribution summary
    print("\n--- LABEL DISTRIBUTION ---")
    print(f"  Label 0 (LOSER):      {len(losers):5}")
    print(f"  Label 1 (NON_MOVER):  {len(non_movers):5}")
    print(f"  Label 2 (WINNER):     {len([r for r in all_records if r['label'] == 2]):5}")
    print(f"  Label 3 (BIG_WINNER): {len(big_winners):5}")

    # Summary by industry
    print("\n--- SUMMARY BY INDUSTRY (sorted by winner count) ---")
    industry_stats = {}
    for r in all_records:
        ind = r['industry'] or 'Unknown'
        if ind not in industry_stats:
            industry_stats[ind] = {'total': 0, 'l0': 0, 'l1': 0, 'l2': 0, 'l3': 0}
        industry_stats[ind]['total'] += 1
        industry_stats[ind][f"l{r['label']}"] += 1

    for ind, stats in sorted(industry_stats.items(), key=lambda x: x[1]['l2'] + x[1]['l3'], reverse=True)[:20]:
        print(f"{ind:40} total={stats['total']:4} L0={stats['l0']:3} L1={stats['l1']:4} L2={stats['l2']:3} L3={stats['l3']:3}")

    # Summary by sector
    print("\n--- SUMMARY BY SECTOR ---")
    sector_stats = {}
    for r in all_records:
        sec = r['sector'] or 'Unknown'
        if sec not in sector_stats:
            sector_stats[sec] = {'total': 0, 'l0': 0, 'l1': 0, 'l2': 0, 'l3': 0}
        sector_stats[sec]['total'] += 1
        sector_stats[sec][f"l{r['label']}"] += 1

    for sec, stats in sorted(sector_stats.items(), key=lambda x: x[1]['total'], reverse=True):
        winners = stats['l2'] + stats['l3']
        win_rate = winners / stats['total'] * 100 if stats['total'] > 0 else 0
        print(f"{sec:25} total={stats['total']:4} L0={stats['l0']:4} L1={stats['l1']:4} L2={stats['l2']:3} L3={stats['l3']:3} win_rate={win_rate:.1f}%")

    print(f"\n--- OUTPUT FILES ---")
    print(f"All data:      {all_csv}")
    print(f"Winners:       {winners_csv}")
    print(f"Control group: {control_csv}")


if __name__ == '__main__':
    main()
