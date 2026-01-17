#!/usr/bin/env python3
"""
Collect historical price movers from Alpaca - ALL sectors.

FAST version - batches minute bar requests for speed.

Usage:
    arch -arm64 .venv/bin/python scripts/collect_alpaca_movers.py
"""

import csv
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

# Configuration
START_DATE = datetime(2024, 1, 1)
END_DATE = datetime.now()

MIN_MOVE_PCT = 5.0
WINDOW_MINUTES = 10
OUTPUT_DIR = Path("tmp/alpaca_movers")

# Batch settings - KEY FOR SPEED
SYMBOLS_PER_DAILY_REQUEST = 3000  # Max symbols per daily bar request
SYMBOLS_PER_MINUTE_REQUEST = 100  # Batch minute requests
PAUSE_BETWEEN_MONTHS_SECS = 1

# Extended hours
EXTENDED_HOURS_START = 4
EXTENDED_HOURS_END = 20

# US Market holidays
US_HOLIDAYS = {
    datetime(2024, 1, 1), datetime(2024, 1, 15), datetime(2024, 2, 19),
    datetime(2024, 3, 29), datetime(2024, 5, 27), datetime(2024, 6, 19),
    datetime(2024, 7, 4), datetime(2024, 9, 2), datetime(2024, 11, 28),
    datetime(2024, 12, 25),
    datetime(2025, 1, 1), datetime(2025, 1, 20), datetime(2025, 2, 17),
    datetime(2025, 4, 18), datetime(2025, 5, 26), datetime(2025, 6, 19),
    datetime(2025, 7, 4), datetime(2025, 9, 1), datetime(2025, 11, 27),
    datetime(2025, 12, 25),
    datetime(2026, 1, 1), datetime(2026, 1, 19),
}


def is_trading_day(date: datetime) -> bool:
    if date.weekday() >= 5:
        return False
    return date.replace(hour=0, minute=0, second=0, microsecond=0) not in US_HOLIDAYS


def get_trading_days(start: datetime, end: datetime) -> list[datetime]:
    days = []
    current = start
    while current < end:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def group_by_month(days: list[datetime]) -> dict[str, list[datetime]]:
    months = {}
    for day in days:
        key = day.strftime("%Y-%m")
        if key not in months:
            months[key] = []
        months[key].append(day)
    return months


class AlpacaMoverCollector:
    def __init__(self):
        self.data_client = StockHistoricalDataClient(
            api_key=os.getenv("ALPACA_KEY"),
            secret_key=os.getenv("ALPACA_SECRET")
        )
        self.trading_client = TradingClient(
            api_key=os.getenv("ALPACA_KEY"),
            secret_key=os.getenv("ALPACA_SECRET"),
            paper=True
        )
        self.tradeable_symbols = None
        self.winners_5_to_10 = []
        self.winners_10_plus = []
        self.stats = {"days": 0, "candidates": 0, "5_to_10": 0, "10_plus": 0}

    def get_tradeable_symbols(self) -> list[str]:
        if self.tradeable_symbols:
            return self.tradeable_symbols

        print("Fetching symbols...", end=" ", flush=True)
        request = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
        assets = self.trading_client.get_all_assets(request)

        symbols = [a.symbol for a in assets if a.tradable
                   and a.exchange in ("NASDAQ", "NYSE", "AMEX", "ARCA")
                   and not a.symbol.endswith("W") and "." not in a.symbol
                   and len(a.symbol) <= 5]

        self.tradeable_symbols = sorted(symbols)
        print(f"{len(symbols)} symbols")
        return self.tradeable_symbols

    def get_daily_candidates(self, date: datetime) -> list[dict]:
        """Get all tickers where high >= open + 5% for the day."""
        candidates = []
        symbols = self.get_tradeable_symbols()
        next_day = date + timedelta(days=1)

        # Batch request for all symbols
        for i in range(0, len(symbols), SYMBOLS_PER_DAILY_REQUEST):
            batch = symbols[i:i + SYMBOLS_PER_DAILY_REQUEST]
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Day,
                    start=date,
                    end=next_day,
                )
                response = self.data_client.get_stock_bars(request)

                if response.data:
                    for symbol, bars in response.data.items():
                        if bars:
                            bar = bars[0]
                            o, h, l, c, v = float(bar.open), float(bar.high), float(bar.low), float(bar.close), int(bar.volume)
                            if o > 0:
                                move = ((h - o) / o) * 100
                                if move >= MIN_MOVE_PCT:
                                    candidates.append({
                                        "ticker": symbol, "date": date,
                                        "open": o, "high": h, "low": l, "close": c,
                                        "volume": v, "daily_move": round(move, 2)
                                    })
            except Exception:
                pass

        return candidates

    def confirm_moves_batch(self, candidates: list[dict], date: datetime) -> list[dict]:
        """Batch confirm 10-min moves for multiple candidates."""
        confirmed = []
        tickers = [c["ticker"] for c in candidates]
        ticker_map = {c["ticker"]: c for c in candidates}

        ext_start = date.replace(hour=EXTENDED_HOURS_START, minute=0, second=0)
        ext_end = date.replace(hour=EXTENDED_HOURS_END, minute=0, second=0)

        # Batch fetch minute bars
        for i in range(0, len(tickers), SYMBOLS_PER_MINUTE_REQUEST):
            batch = tickers[i:i + SYMBOLS_PER_MINUTE_REQUEST]
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Minute,
                    start=ext_start,
                    end=ext_end,
                )
                response = self.data_client.get_stock_bars(request)

                if response.data:
                    for symbol, bars in response.data.items():
                        bars = list(bars)
                        if len(bars) < WINDOW_MINUTES:
                            continue

                        # Find best 10-min window
                        best = None
                        best_exc = 0

                        for j in range(len(bars) - WINDOW_MINUTES + 1):
                            window = bars[j:j + WINDOW_MINUTES]
                            start_price = float(window[0].open)
                            if start_price <= 0:
                                continue

                            max_high = start_price
                            peak_bar = window[0]
                            for bar in window:
                                if float(bar.high) > max_high:
                                    max_high = float(bar.high)
                                    peak_bar = bar

                            exc = ((max_high - start_price) / start_price) * 100
                            if exc >= MIN_MOVE_PCT and exc > best_exc:
                                best_exc = exc
                                best = {
                                    "start_time": window[0].timestamp.isoformat(),
                                    "peak_time": peak_bar.timestamp.isoformat(),
                                    "end_time": window[-1].timestamp.isoformat(),
                                    "start_price": round(start_price, 4),
                                    "peak_price": round(max_high, 4),
                                    "end_price": round(float(window[-1].close), 4),
                                    "excursion": round(exc, 2),
                                }

                        if best:
                            c = ticker_map[symbol]
                            confirmed.append({
                                "ticker": symbol,
                                "date": c["date"].strftime("%Y-%m-%d"),
                                "sector": "", "industry": "",
                                "daily_open": c["open"], "daily_high": c["high"],
                                "daily_low": c["low"], "daily_close": c["close"],
                                "daily_volume": c["volume"], "daily_move_pct": c["daily_move"],
                                "move_start_time": best["start_time"],
                                "move_peak_time": best["peak_time"],
                                "move_end_time": best["end_time"],
                                "move_start_price": best["start_price"],
                                "move_peak_price": best["peak_price"],
                                "move_end_price": best["end_price"],
                                "max_excursion_pct": best["excursion"],
                            })
            except Exception:
                pass

        return confirmed

    def process_day(self, date: datetime) -> tuple[int, int]:
        candidates = self.get_daily_candidates(date)
        self.stats["candidates"] += len(candidates)

        if not candidates:
            return 0, 0

        confirmed = self.confirm_moves_batch(candidates, date)

        c5, c10 = 0, 0
        for record in confirmed:
            if record["max_excursion_pct"] >= 10:
                self.winners_10_plus.append(record)
                self.stats["10_plus"] += 1
                c10 += 1
            else:
                self.winners_5_to_10.append(record)
                self.stats["5_to_10"] += 1
                c5 += 1

        self.stats["days"] += 1
        return c5, c10

    def save_csvs(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        columns = [
            "ticker", "date", "sector", "industry",
            "daily_open", "daily_high", "daily_low", "daily_close", "daily_volume", "daily_move_pct",
            "move_start_time", "move_peak_time", "move_end_time",
            "move_start_price", "move_peak_price", "move_end_price", "max_excursion_pct",
        ]

        self.winners_5_to_10.sort(key=lambda x: x["max_excursion_pct"], reverse=True)
        self.winners_10_plus.sort(key=lambda x: x["max_excursion_pct"], reverse=True)

        for name, data in [("5_to_10_pct_winners", self.winners_5_to_10),
                           ("10_plus_pct_winners", self.winners_10_plus)]:
            with open(OUTPUT_DIR / f"{name}.csv", "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerows(data)

    def collect_all(self):
        trading_days = get_trading_days(START_DATE, END_DATE)
        months = group_by_month(trading_days)

        print(f"{'='*60}")
        print(f"ALPACA MOVER COLLECTION (FAST)")
        print(f"{'='*60}")
        print(f"Range: {START_DATE.date()} to {END_DATE.date()}")
        print(f"Days: {len(trading_days)} | Months: {len(months)}")
        print(f"{'='*60}\n")

        pbar = tqdm(total=len(trading_days), desc="Total",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')

        for month_key in sorted(months.keys()):
            month_days = months[month_key]
            m5, m10 = 0, 0

            for day in month_days:
                d5, d10 = self.process_day(day)
                m5 += d5
                m10 += d10
                pbar.set_postfix({"5-10%": self.stats["5_to_10"], "10%+": self.stats["10_plus"]})
                pbar.update(1)

            tqdm.write(f"  {month_key}: 5-10%={m5} | 10%+={m10}")
            self.save_csvs()
            time.sleep(PAUSE_BETWEEN_MONTHS_SECS)

        pbar.close()

        print(f"\n{'='*60}")
        print(f"DONE | 5-10%: {self.stats['5_to_10']} | 10%+: {self.stats['10_plus']}")
        print(f"Files: {OUTPUT_DIR}/5_to_10_pct_winners.csv")
        print(f"       {OUTPUT_DIR}/10_plus_pct_winners.csv")
        print(f"{'='*60}")

        if self.winners_10_plus:
            print(f"\nTop 10%+ movers:")
            for m in self.winners_10_plus[:10]:
                print(f"  {m['ticker']:5} {m['date']} +{m['max_excursion_pct']:.1f}%")


def main():
    t0 = time.time()
    AlpacaMoverCollector().collect_all()
    print(f"\nRuntime: {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()
