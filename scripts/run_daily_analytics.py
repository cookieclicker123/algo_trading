#!/usr/bin/env python3
"""
Run daily analytics manually for a specific date.

Usage:
    python scripts/run_daily_analytics.py                  # Yesterday
    python scripts/run_daily_analytics.py --date 2026-02-05  # Specific date
    python scripts/run_daily_analytics.py --days 7         # Last 7 days
"""
import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.jobs.daily_analytics import DailyAnalyticsJob


async def main():
    parser = argparse.ArgumentParser(description="Run daily analytics for trade analysis")
    parser.add_argument("--date", type=str, help="Specific date to analyze (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Analyze last N days")
    parser.add_argument("--output-path", type=str, default="tmp/analytics/daily",
                        help="Output directory for analytics JSON files")

    args = parser.parse_args()

    job = DailyAnalyticsJob(output_path=Path(args.output_path))

    if args.days:
        # Analyze last N days
        print(f"Analyzing last {args.days} days...")
        for i in range(args.days):
            target = date.today() - timedelta(days=i + 1)
            print(f"\n{'='*60}")
            print(f"Analyzing {target}...")
            print('='*60)
            report = await job.run(target)
            if report:
                print(f"✅ {target}: {report.total_trades} trades, "
                      f"{report.win_rate_pct}% win rate, "
                      f"${report.total_pnl_usd:+.2f} P&L")
                print(f"   Peak: {report.avg_peak_profit_pct:+.1f}%, "
                      f"Exit: {report.avg_exit_profit_pct:+.1f}%, "
                      f"Left: {report.avg_money_left_on_table_pct:.1f}%")
                print(f"   Market: {report.market_regime.regime} "
                      f"(NDX {report.market_regime.nasdaq_change_pct:+.1f}%, "
                      f"SPX {report.market_regime.sp500_change_pct:+.1f}%)")
            else:
                print(f"⚪ {target}: No trades")

    elif args.date:
        # Specific date
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
        print(f"Analyzing {target}...")
        report = await job.run(target)
        if report:
            print(f"\n{'='*60}")
            print(f"DAILY ANALYTICS: {target}")
            print('='*60)
            print(f"\nSUMMARY:")
            print(f"  Trades: {report.total_trades} ({report.profitable_trades}W / {report.losing_trades}L)")
            print(f"  Win Rate: {report.win_rate_pct}%")
            print(f"  Total P&L: ${report.total_pnl_usd:+.2f}")
            print(f"  Avg P&L: ${report.avg_pnl_per_trade_usd:+.2f}/trade")
            print(f"\nEXIT QUALITY:")
            print(f"  Avg Peak: {report.avg_peak_profit_pct:+.1f}%")
            print(f"  Avg Exit: {report.avg_exit_profit_pct:+.1f}%")
            print(f"  Left on Table: {report.avg_money_left_on_table_pct:.1f}%")
            print(f"\nMARKET REGIME:")
            mr = report.market_regime
            print(f"  Regime: {mr.regime.upper()}")
            print(f"  NASDAQ 100: {mr.nasdaq_open} → {mr.nasdaq_close} ({mr.nasdaq_change_pct:+.1f}%)")
            print(f"  S&P 500: {mr.sp500_open} → {mr.sp500_close} ({mr.sp500_change_pct:+.1f}%)")
            if mr.consecutive_bearish_days > 0:
                print(f"  ⚠️ Consecutive Bearish Days: {mr.consecutive_bearish_days}")
            if mr.consecutive_bullish_days > 0:
                print(f"  ✅ Consecutive Bullish Days: {mr.consecutive_bullish_days}")
            print(f"\nBY INDUSTRY:")
            for industry, stats in sorted(report.by_industry.items(), key=lambda x: -x[1].get('count', 0)):
                if stats.get('count', 0) > 0:
                    print(f"  {industry}: {stats['count']} trades, "
                          f"{stats.get('win_rate_pct', 0)}% win, "
                          f"peak {stats.get('avg_peak_pct', 0):+.1f}%")
            print(f"\nBY MARKET CAP:")
            for bucket, stats in report.by_market_cap_bucket.items():
                if stats.get('count', 0) > 0:
                    print(f"  {bucket}: {stats['count']} trades, "
                          f"{stats.get('win_rate_pct', 0)}% win")
            print(f"\n✅ Saved to: {job.output_path / f'{target}.json'}")
        else:
            print(f"No trades found for {target}")

    else:
        # Default: yesterday
        target = date.today() - timedelta(days=1)
        print(f"Analyzing yesterday ({target})...")
        report = await job.run(target)
        if report:
            print(f"\n✅ {report.total_trades} trades analyzed")
            print(f"   Win Rate: {report.win_rate_pct}%")
            print(f"   P&L: ${report.total_pnl_usd:+.2f}")
            print(f"   Saved to: {job.output_path / f'{target}.json'}")
        else:
            print(f"No trades found for {target}")


if __name__ == "__main__":
    asyncio.run(main())
