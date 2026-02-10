#!/usr/bin/env python3
"""
Analyze headlines with large price excursions within the 10-minute hold window.
Extracts patterns in headlines, market cap, industry, etc.

Usage:
    python scripts/analyze_high_excursion_headlines.py [--min-excursion 5] [--output csv]
"""

import json
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import csv

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def find_recall_files(base_path: str, since_year: int = 2026, since_month: int = 1) -> list[Path]:
    """Find all recall JSON files since the given date."""
    files = []
    base = Path(base_path)

    for year_dir in sorted(base.iterdir()):
        if not year_dir.is_dir():
            continue
        try:
            year = int(year_dir.name)
        except ValueError:
            continue

        if year < since_year:
            continue

        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            try:
                month = int(month_dir.name)
            except ValueError:
                continue

            if year == since_year and month < since_month:
                continue

            # Find all JSON files in this month
            for json_file in month_dir.rglob("*.json"):
                files.append(json_file)

    return files


def extract_high_excursion_records(files: list[Path], min_excursion_pct: float = 5.0) -> list[dict]:
    """Extract records with high price excursions."""
    high_excursion_records = []

    for file_path in files:
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            continue

        records = data.get('records', [])

        for record in records:
            highest = record.get('highest_price_during_hold')
            if not highest:
                continue

            excursion_pct = highest.get('percent_gain_from_entry', 0)
            if excursion_pct < min_excursion_pct:
                continue

            # Extract ticker metadata
            ticker_metadata = record.get('ticker_metadata', {})
            tickers = record.get('tickers', [])
            primary_ticker = highest.get('ticker') or (tickers[0] if tickers else None)

            meta = ticker_metadata.get(primary_ticker, {}) if primary_ticker else {}

            # Extract volume stats for the primary ticker
            volume_stats = record.get('volume_stats', {})
            ticker_volume = volume_stats.get(primary_ticker, {}) if primary_ticker else {}

            # Extract confluence data if available
            confluence_data = {
                'confluence_score': record.get('confluence_score'),
                'confluence_volume': record.get('confluence_volume'),
                'confluence_imbalance_ratio': record.get('confluence_imbalance_ratio'),
                'confluence_buying_pressure_pct': record.get('confluence_buying_pressure_pct'),
            }

            high_excursion_records.append({
                'date': record.get('published_at', '')[:10],
                'time': record.get('published_at', '')[11:19],
                'session': record.get('session', ''),
                'title': record.get('title', ''),
                'ticker': primary_ticker,
                'excursion_pct': round(excursion_pct, 2),
                'peak_price': highest.get('price'),
                'peak_minute': highest.get('minute'),
                'peak_second': highest.get('second'),
                'industry': meta.get('industry', ''),
                'sector': meta.get('sector', ''),
                'market_cap_millions': round(meta.get('market_cap_millions', 0), 1) if meta.get('market_cap_millions') else None,
                'price': meta.get('price'),
                'exchange': meta.get('exchange', ''),
                # Volume stats from 4-second window
                'move_type': ticker_volume.get('move_type', ''),
                'surge_multiplier': ticker_volume.get('surge_multiplier'),
                'buying_pressure': ticker_volume.get('buying_pressure'),
                'imbalance_ratio': ticker_volume.get('imbalance_ratio'),
                # Confluence data (2-second window)
                **confluence_data,
                # AI classification
                'ai_classification': record.get('ai_classification', ''),
                'filter_reason': record.get('filter_reason', ''),
                'is_traded': record.get('is_traded', False),
            })

    return high_excursion_records


def analyze_patterns(records: list[dict]) -> dict:
    """Analyze patterns in high-excursion records."""

    stats = {
        'total_records': len(records),
        'by_industry': defaultdict(lambda: {'count': 0, 'avg_excursion': 0, 'total_excursion': 0}),
        'by_sector': defaultdict(lambda: {'count': 0, 'avg_excursion': 0, 'total_excursion': 0}),
        'by_market_cap_bucket': defaultdict(lambda: {'count': 0, 'avg_excursion': 0, 'total_excursion': 0}),
        'by_session': defaultdict(lambda: {'count': 0, 'avg_excursion': 0, 'total_excursion': 0}),
        'by_move_type': defaultdict(lambda: {'count': 0, 'avg_excursion': 0, 'total_excursion': 0}),
        'by_classification': defaultdict(lambda: {'count': 0, 'avg_excursion': 0, 'total_excursion': 0}),
        'traded_vs_missed': {'traded': {'count': 0, 'avg_excursion': 0}, 'missed': {'count': 0, 'avg_excursion': 0}},
        'headline_keywords': defaultdict(int),
    }

    # Market cap buckets
    def get_cap_bucket(cap):
        if cap is None:
            return 'Unknown'
        if cap < 10:
            return '<$10M (nano)'
        elif cap < 50:
            return '$10-50M (micro)'
        elif cap < 300:
            return '$50-300M (small)'
        elif cap < 2000:
            return '$300M-2B (mid)'
        else:
            return '>$2B (large)'

    # Headline keywords to track
    bullish_keywords = [
        'fda', 'approval', 'cleared', 'grant', 'awarded', 'contract', 'partnership',
        'acquisition', 'acquire', 'merger', 'deal', 'agreement', 'license',
        'breakthrough', 'patent', 'exclusive', 'strategic', 'milestone',
        'expands', 'expansion', 'launch', 'launches', 'new', 'first',
        'positive', 'successful', 'beats', 'exceeds', 'record', 'strong',
        'ai', 'artificial intelligence', 'data center', 'bitcoin', 'crypto',
        'defense', 'government', 'military', 'dod', 'nasa',
    ]

    for record in records:
        excursion = record['excursion_pct']

        # By industry
        industry = record['industry'] or 'Unknown'
        stats['by_industry'][industry]['count'] += 1
        stats['by_industry'][industry]['total_excursion'] += excursion

        # By sector
        sector = record['sector'] or 'Unknown'
        stats['by_sector'][sector]['count'] += 1
        stats['by_sector'][sector]['total_excursion'] += excursion

        # By market cap
        cap_bucket = get_cap_bucket(record['market_cap_millions'])
        stats['by_market_cap_bucket'][cap_bucket]['count'] += 1
        stats['by_market_cap_bucket'][cap_bucket]['total_excursion'] += excursion

        # By session
        session = record['session'] or 'Unknown'
        stats['by_session'][session]['count'] += 1
        stats['by_session'][session]['total_excursion'] += excursion

        # By move type
        move_type = record['move_type'] or 'Unknown'
        stats['by_move_type'][move_type]['count'] += 1
        stats['by_move_type'][move_type]['total_excursion'] += excursion

        # By classification
        classification = record['ai_classification'] or 'Not classified'
        stats['by_classification'][classification]['count'] += 1
        stats['by_classification'][classification]['total_excursion'] += excursion

        # Traded vs missed
        if record['is_traded']:
            stats['traded_vs_missed']['traded']['count'] += 1
            stats['traded_vs_missed']['traded']['avg_excursion'] += excursion
        else:
            stats['traded_vs_missed']['missed']['count'] += 1
            stats['traded_vs_missed']['missed']['avg_excursion'] += excursion

        # Headline keyword analysis
        title_lower = record['title'].lower()
        for keyword in bullish_keywords:
            if keyword in title_lower:
                stats['headline_keywords'][keyword] += 1

    # Calculate averages
    for category in ['by_industry', 'by_sector', 'by_market_cap_bucket', 'by_session', 'by_move_type', 'by_classification']:
        for key, data in stats[category].items():
            if data['count'] > 0:
                data['avg_excursion'] = round(data['total_excursion'] / data['count'], 2)

    for key in ['traded', 'missed']:
        if stats['traded_vs_missed'][key]['count'] > 0:
            stats['traded_vs_missed'][key]['avg_excursion'] = round(
                stats['traded_vs_missed'][key]['avg_excursion'] / stats['traded_vs_missed'][key]['count'], 2
            )

    return stats


def print_report(records: list[dict], stats: dict, top_n: int = 20):
    """Print analysis report."""

    print("=" * 80)
    print(f"HIGH EXCURSION HEADLINE ANALYSIS (>={args.min_excursion}% move)")
    print(f"Total records: {stats['total_records']}")
    print("=" * 80)

    # Top headlines by excursion
    print(f"\n{'='*80}")
    print(f"TOP {top_n} HEADLINES BY EXCURSION")
    print("=" * 80)
    sorted_records = sorted(records, key=lambda x: x['excursion_pct'], reverse=True)[:top_n]
    for i, r in enumerate(sorted_records, 1):
        print(f"\n{i}. +{r['excursion_pct']}% | {r['ticker']} | {r['industry']}")
        print(f"   MktCap: ${r['market_cap_millions']}M | Peak: {r['peak_minute']}m{r['peak_second']}s")
        print(f"   {r['title'][:100]}")
        if r['ai_classification']:
            print(f"   AI: {r['ai_classification']} | Traded: {r['is_traded']}")

    # By industry
    print(f"\n{'='*80}")
    print("BY INDUSTRY (sorted by count)")
    print("=" * 80)
    sorted_industries = sorted(stats['by_industry'].items(), key=lambda x: x[1]['count'], reverse=True)[:15]
    print(f"{'Industry':<40} {'Count':>8} {'Avg Move':>10}")
    print("-" * 60)
    for industry, data in sorted_industries:
        print(f"{industry[:40]:<40} {data['count']:>8} {data['avg_excursion']:>9.1f}%")

    # By market cap
    print(f"\n{'='*80}")
    print("BY MARKET CAP")
    print("=" * 80)
    cap_order = ['<$10M (nano)', '$10-50M (micro)', '$50-300M (small)', '$300M-2B (mid)', '>$2B (large)', 'Unknown']
    print(f"{'Market Cap':<20} {'Count':>8} {'Avg Move':>10}")
    print("-" * 40)
    for cap_bucket in cap_order:
        if cap_bucket in stats['by_market_cap_bucket']:
            data = stats['by_market_cap_bucket'][cap_bucket]
            print(f"{cap_bucket:<20} {data['count']:>8} {data['avg_excursion']:>9.1f}%")

    # By session
    print(f"\n{'='*80}")
    print("BY SESSION")
    print("=" * 80)
    print(f"{'Session':<15} {'Count':>8} {'Avg Move':>10}")
    print("-" * 35)
    for session, data in sorted(stats['by_session'].items(), key=lambda x: x[1]['count'], reverse=True):
        print(f"{session:<15} {data['count']:>8} {data['avg_excursion']:>9.1f}%")

    # By move type
    print(f"\n{'='*80}")
    print("BY MOVE TYPE (4-second window)")
    print("=" * 80)
    print(f"{'Move Type':<15} {'Count':>8} {'Avg Move':>10}")
    print("-" * 35)
    for move_type, data in sorted(stats['by_move_type'].items(), key=lambda x: x[1]['count'], reverse=True):
        print(f"{move_type:<15} {data['count']:>8} {data['avg_excursion']:>9.1f}%")

    # By AI classification
    print(f"\n{'='*80}")
    print("BY AI CLASSIFICATION")
    print("=" * 80)
    print(f"{'Classification':<20} {'Count':>8} {'Avg Move':>10}")
    print("-" * 40)
    for classification, data in sorted(stats['by_classification'].items(), key=lambda x: x[1]['count'], reverse=True):
        print(f"{classification:<20} {data['count']:>8} {data['avg_excursion']:>9.1f}%")

    # Traded vs missed
    print(f"\n{'='*80}")
    print("TRADED vs MISSED OPPORTUNITIES")
    print("=" * 80)
    traded = stats['traded_vs_missed']['traded']
    missed = stats['traded_vs_missed']['missed']
    print(f"Traded: {traded['count']} trades, avg +{traded['avg_excursion']}%")
    print(f"Missed: {missed['count']} opportunities, avg +{missed['avg_excursion']}%")

    # Headline keywords
    print(f"\n{'='*80}")
    print("BULLISH KEYWORDS IN HEADLINES")
    print("=" * 80)
    sorted_keywords = sorted(stats['headline_keywords'].items(), key=lambda x: x[1], reverse=True)[:20]
    for keyword, count in sorted_keywords:
        pct = round(count / stats['total_records'] * 100, 1)
        print(f"  {keyword:<25} {count:>5} ({pct}%)")


def export_csv(records: list[dict], output_path: str):
    """Export records to CSV."""
    if not records:
        print("No records to export", file=sys.stderr)
        return

    fieldnames = list(records[0].keys())

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"\nExported {len(records)} records to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze high-excursion headlines")
    parser.add_argument('--min-excursion', type=float, default=5.0, help='Minimum excursion %% (default: 5)')
    parser.add_argument('--output', choices=['report', 'csv', 'both'], default='report', help='Output format')
    parser.add_argument('--csv-path', type=str, default='high_excursion_headlines.csv', help='CSV output path')
    parser.add_argument('--since-year', type=int, default=2026, help='Start year (default: 2026)')
    parser.add_argument('--since-month', type=int, default=1, help='Start month (default: 1)')

    args = parser.parse_args()

    # Find recall files
    base_path = Path(__file__).parent.parent / "tmp" / "statistics" / "recall"
    print(f"Scanning {base_path} for recall files since {args.since_year}-{args.since_month:02d}...")

    files = find_recall_files(str(base_path), args.since_year, args.since_month)
    print(f"Found {len(files)} recall files")

    # Extract high excursion records
    print(f"Extracting records with >={args.min_excursion}% excursion...")
    records = extract_high_excursion_records(files, args.min_excursion)
    print(f"Found {len(records)} high-excursion records")

    if not records:
        print("No records found matching criteria")
        sys.exit(0)

    # Analyze patterns
    stats = analyze_patterns(records)

    # Output
    if args.output in ['report', 'both']:
        print_report(records, stats)

    if args.output in ['csv', 'both']:
        export_csv(records, args.csv_path)
