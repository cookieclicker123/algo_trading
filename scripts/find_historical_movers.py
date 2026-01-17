#!/usr/bin/env python3
"""
Find historical 10%+ movers using Alpaca API and generate Perplexity batch queries.

This script:
1. Scans historical minute bars for stocks that moved 10%+ in 10 minutes
2. Filters to target sectors and market cap
3. Outputs batches ready for Perplexity queries

Requirements:
- APCA_API_KEY_ID and APCA_API_SECRET_KEY env vars
- Alpaca Algo Trader Plus subscription for historical data

Usage:
    python scripts/find_historical_movers.py --start 2024-01-01 --end 2024-12-31 --sector Healthcare
"""

import argparse
import csv
import os
from datetime import datetime, timedelta
from pathlib import Path

# Check for Alpaca SDK
try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    print("Please install alpaca-py: pip install alpaca-py")
    exit(1)


# Target sectors and their industries
TARGET_SECTORS = {
    'Healthcare': [
        'Biotechnology', 'Drug Manufacturers - Specialty & Generic',
        'Medical Devices', 'Medical Instruments & Supplies',
        'Diagnostics & Research', 'Medical Care Facilities',
        'Health Information Services', 'Pharmaceutical Retailers'
    ],
    'Technology': [
        'Software - Application', 'Software - Infrastructure',
        'Semiconductors', 'Communication Equipment',
        'Information Technology Services', 'Electronic Components',
        'Computer Hardware', 'Scientific & Technical Instruments'
    ],
    'Consumer Cyclical': [
        'Internet Retail', 'Specialty Retail', 'Auto Parts',
        'Restaurants', 'Apparel Retail', 'Home Improvement Retail',
        'Residential Construction', 'Leisure', 'Gambling'
    ],
    'Industrials': [
        'Aerospace & Defense', 'Farm & Heavy Construction Machinery',
        'Integrated Freight & Logistics', 'Engineering & Construction',
        'Industrial Distribution', 'Specialty Industrial Machinery',
        'Trucking', 'Railroads', 'Marine Shipping'
    ],
    'Financial Services': [
        'Banks - Regional', 'Capital Markets', 'Asset Management',
        'Insurance - Property & Casualty', 'Insurance - Life',
        'Financial Data & Stock Exchanges', 'Credit Services'
    ],
}


def get_stock_universe(sector: str | None = None) -> list[str]:
    """
    Get list of tickers to scan.

    In production, you'd want to:
    1. Get all tickers from your target exchanges (NYSE, NASDAQ, AMEX)
    2. Filter by sector/industry
    3. Filter by market cap < $1B

    For now, this returns a sample list. Replace with your actual universe.
    """
    # TODO: Implement actual universe fetching
    # Options:
    # 1. Use Alpaca assets API: client.get_all_assets()
    # 2. Use a screener like Finviz to export tickers
    # 3. Use yfinance to get sector/industry info

    print("WARNING: Using placeholder ticker list. Replace with actual universe.")
    print("To get real universe, use Alpaca's assets API or a stock screener.")

    # Sample tickers for testing - replace with your actual list
    return [
        'JFBR', 'CJMB', 'SPHL', 'BNKK', 'GP', 'VSTA', 'LCFY', 'AMOD',
        'ASNS', 'RPID', 'AGEN', 'PONY', 'BYND', 'AEVA', 'SUUN'
    ]


def find_movers_for_date(
    client: StockHistoricalDataClient,
    tickers: list[str],
    date: datetime,
    min_move_pct: float = 10.0,
    window_minutes: int = 10
) -> list[dict]:
    """Find stocks that moved >= min_move_pct within window_minutes on given date."""

    movers = []

    # Market hours (9:30 AM - 4:00 PM ET)
    market_open = date.replace(hour=9, minute=30, second=0)
    market_close = date.replace(hour=16, minute=0, second=0)

    for ticker in tickers:
        try:
            request = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=TimeFrame.Minute,
                start=market_open,
                end=market_close
            )
            bars = client.get_stock_bars(request)

            if ticker not in bars.data or len(bars.data[ticker]) < window_minutes:
                continue

            ticker_bars = bars.data[ticker]

            # Scan for moves
            for i in range(len(ticker_bars) - window_minutes):
                window = ticker_bars[i:i + window_minutes]
                start_price = window[0].open
                end_price = window[-1].close
                high_price = max(b.high for b in window)

                # Calculate move
                move_pct = (high_price - start_price) / start_price * 100

                if move_pct >= min_move_pct:
                    movers.append({
                        'ticker': ticker,
                        'date': date.strftime('%Y-%m-%d'),
                        'time_et': window[0].timestamp.strftime('%H:%M'),
                        'move_pct': round(move_pct, 1),
                        'start_price': round(start_price, 2),
                        'high_price': round(high_price, 2),
                        'volume': sum(b.volume for b in window),
                    })
                    # Only record first move of the day for this ticker
                    break

        except Exception as e:
            print(f"Error processing {ticker} on {date}: {e}")
            continue

    return movers


def generate_perplexity_batch(movers: list[dict], batch_num: int) -> str:
    """Generate a Perplexity query for a batch of movers."""

    header = """I need to find the ORIGINAL press releases that caused these stock moves. For each, provide:

1. The EXACT headline from the wire service (not rewritten by news sites)
2. Wire source: PR Newswire, Business Wire, Globe Newswire, Accesswire, or Newsfile
3. Timestamp of the press release (as precise as possible)
4. URL to the original release if available

IMPORTANT: I need the SOURCE press release from the wire services listed above, NOT news articles reporting on the move.

Stock moves to research:

"""

    items = []
    for i, m in enumerate(movers, 1):
        items.append(f"{i}. {m['ticker']} moved +{m['move_pct']}% on {m['date']} around {m['time_et']} ET")

    footer = """

For each, respond in this exact format:

[TICKER]:
- Headline: "[exact headline]"
- Wire: [source]
- Time: [timestamp]
- URL: [url or "not found"]
- Confidence: [HIGH/MEDIUM/LOW]

If no wire release found, respond: [TICKER]: NO WIRE RELEASE FOUND - [reason]
"""

    return header + "\n".join(items) + footer


def main():
    parser = argparse.ArgumentParser(description='Find historical movers and generate Perplexity batches')
    parser.add_argument('--start', type=str, default='2024-01-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default='2024-12-31', help='End date (YYYY-MM-DD)')
    parser.add_argument('--sector', type=str, help='Filter to specific sector')
    parser.add_argument('--min-move', type=float, default=10.0, help='Minimum move percentage')
    parser.add_argument('--batch-size', type=int, default=20, help='Movers per Perplexity batch')
    parser.add_argument('--output-dir', type=str, default='tmp/backtest_data/movers', help='Output directory')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without API calls')

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse dates
    start_date = datetime.strptime(args.start, '%Y-%m-%d')
    end_date = datetime.strptime(args.end, '%Y-%m-%d')

    print(f"Finding {args.min_move}%+ movers from {args.start} to {args.end}")

    if args.dry_run:
        print("\n[DRY RUN] Would scan these tickers:")
        tickers = get_stock_universe(args.sector)
        print(f"  {len(tickers)} tickers")
        print(f"\n[DRY RUN] Date range: {(end_date - start_date).days} trading days")
        print(f"\n[DRY RUN] Output would go to: {output_dir}")
        return

    # Initialize Alpaca client
    api_key = os.environ.get('APCA_API_KEY_ID')
    api_secret = os.environ.get('APCA_API_SECRET_KEY')

    if not api_key or not api_secret:
        print("ERROR: Set APCA_API_KEY_ID and APCA_API_SECRET_KEY environment variables")
        return

    client = StockHistoricalDataClient(api_key, api_secret)

    # Get ticker universe
    tickers = get_stock_universe(args.sector)
    print(f"Scanning {len(tickers)} tickers")

    # Collect all movers
    all_movers = []
    current_date = start_date

    while current_date <= end_date:
        # Skip weekends
        if current_date.weekday() < 5:
            print(f"Scanning {current_date.strftime('%Y-%m-%d')}...", end=' ')
            movers = find_movers_for_date(client, tickers, current_date, args.min_move)
            print(f"found {len(movers)} movers")
            all_movers.extend(movers)

        current_date += timedelta(days=1)

    print(f"\nTotal movers found: {len(all_movers)}")

    # Save raw movers to CSV
    movers_csv = output_dir / 'historical_movers.csv'
    with open(movers_csv, 'w', newline='') as f:
        if all_movers:
            writer = csv.DictWriter(f, fieldnames=all_movers[0].keys())
            writer.writeheader()
            writer.writerows(all_movers)
    print(f"Saved movers to {movers_csv}")

    # Generate Perplexity batches
    batches_dir = output_dir / 'perplexity_batches'
    batches_dir.mkdir(exist_ok=True)

    for i in range(0, len(all_movers), args.batch_size):
        batch = all_movers[i:i + args.batch_size]
        batch_num = i // args.batch_size + 1

        query = generate_perplexity_batch(batch, batch_num)

        batch_file = batches_dir / f'batch_{batch_num:03d}.txt'
        with open(batch_file, 'w') as f:
            f.write(query)

    num_batches = (len(all_movers) + args.batch_size - 1) // args.batch_size
    print(f"Generated {num_batches} Perplexity batch files in {batches_dir}")

    # Summary
    print(f"\n--- SUMMARY ---")
    print(f"Total movers: {len(all_movers)}")
    print(f"Perplexity batches: {num_batches}")
    print(f"Estimated time: {num_batches * 5} minutes ({num_batches * 5 / 60:.1f} hours)")
    print(f"\nNext steps:")
    print(f"1. Open each batch file in {batches_dir}")
    print(f"2. Copy contents to Perplexity Pro")
    print(f"3. Record results in {output_dir}/attributed_headlines.csv")


if __name__ == '__main__':
    main()
