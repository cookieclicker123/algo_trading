#!/usr/bin/env python3
"""
Quick test: Does Alpaca have ANY news for our top movers?
Try different lookback windows to see when news was published.
"""

import os
from datetime import datetime, timedelta
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET", "")

# Top movers from your results
TEST_TICKERS = [
    ("TNON", "2024-09-13", "2024-09-13T08:04:00+00:00", 2504),  # Biggest mover
    ("BKKT", "2024-04-29", "2024-04-29T08:00:00+00:00", 1182),
    ("BON", "2025-03-14", "2025-03-14T12:00:00+00:00", 1016),
    ("NVDA", "2024-02-22", None, 0),  # Known ticker - should have news
    ("TSLA", "2024-03-01", None, 0),  # Known ticker - should have news
]


def main():
    client = NewsClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY)

    print("=" * 70)
    print("NEWS AVAILABILITY TEST")
    print("=" * 70)

    for ticker, date_str, move_time, excursion in TEST_TICKERS:
        print(f"\n{ticker} ({date_str}) +{excursion}%")
        print("-" * 50)

        # Parse date
        date = datetime.strptime(date_str, "%Y-%m-%d")

        # Try different windows
        windows = [
            ("30 min before move", -30, 0),
            ("2 hours before move", -120, 0),
            ("12 hours before move", -720, 0),
            ("Full day", -1440, 1440),  # 24h before to 24h after
        ]

        if move_time:
            base_time = datetime.fromisoformat(move_time.replace("Z", "+00:00"))
        else:
            base_time = date.replace(hour=14, minute=0)  # Use 2 PM if no move time

        for label, min_before, min_after in windows:
            start = base_time + timedelta(minutes=min_before)
            end = base_time + timedelta(minutes=min_after) if min_after else base_time + timedelta(minutes=5)

            try:
                request = NewsRequest(
                    symbols=ticker,
                    start=start,
                    end=end,
                    limit=10,
                )
                response = client.get_news(request)
                count = len(response.news)

                if count > 0:
                    print(f"  {label}: {count} articles")
                    for art in response.news[:3]:  # Show first 3
                        source = getattr(art, "source", "?")
                        headline = art.headline[:60] if art.headline else "?"
                        time = art.created_at.strftime("%H:%M") if art.created_at else "?"
                        print(f"    [{source}] {time}: {headline}...")
                else:
                    print(f"  {label}: 0 articles")

            except Exception as e:
                print(f"  {label}: ERROR - {e}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
