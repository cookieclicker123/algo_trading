#!/usr/bin/env python3
"""
Test trade simulation methodology on a single example.

This script walks through tick-by-tick simulation to verify:
1. Entry at reception + 3 seconds
2. First 5 seconds: 1.25s soft stop confirmation at -5%
3. After 5 seconds: hard stop at -5%
4. Take profits: +15% (50%), +30% (25%), +40% (25%) with trailing stop

Usage:
    python scripts/test_trade_simulation.py TICKER "2026-02-10T09:30:00-05:00"
    python scripts/test_trade_simulation.py MWYN "2026-02-10T10:15:23-05:00"
"""
import argparse
import os
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

# Simulation parameters
ENTRY_DELAY_SECONDS = 3.0  # Time from publication to entry
SOFT_STOP_WINDOW_SECONDS = 5.0  # First 5 seconds use soft stop
SOFT_STOP_CONFIRMATION_SECONDS = 1.25  # Must stay below stop for 1.25s
STOP_LOSS_PCT = -5.0  # -5% stop loss
SIMULATION_DURATION_SECONDS = 600  # 10 minutes

# Take profit tiers: (trigger_pct, sell_pct, new_stop_pct)
TAKE_PROFIT_TIERS = [
    (15.0, 50, 5.0),   # At +15%, sell 50%, stop moves to +5%
    (30.0, 25, 15.0),  # At +30%, sell 25%, stop moves to +15%
    (40.0, 25, None),  # At +40%, sell remaining 25%
]


def get_alpaca_client():
    """Get Alpaca historical data client."""
    from alpaca.data.historical import StockHistoricalDataClient

    api_key = (
        os.getenv("ALPACA_KEY_PAPER") or
        os.getenv("ALPACA_API_KEY") or
        os.getenv("ALPACA_KEY")
    )
    secret_key = (
        os.getenv("ALPACA_SECRET_PAPER") or
        os.getenv("ALPACA_SECRET_KEY") or
        os.getenv("ALPACA_SECRET")
    )

    if not api_key or not secret_key:
        raise ValueError("Alpaca API credentials not found in environment")

    return StockHistoricalDataClient(api_key, secret_key)


def fetch_tick_data(client, ticker: str, start_time: datetime, duration_seconds: int = 620, use_quotes: bool = True):
    """Fetch tick data from Alpaca for the simulation period.

    Args:
        use_quotes: If True, fetch NBBO quotes. If False, fetch trades.
                   Quotes are more comprehensive for illiquid stocks.
    """
    from alpaca.data.enums import DataFeed

    end_time = start_time + timedelta(seconds=duration_seconds)

    print(f"\n📡 Fetching {'quote' if use_quotes else 'trade'} data for {ticker}")
    print(f"   Start: {start_time}")
    print(f"   End:   {end_time}")

    if use_quotes:
        from alpaca.data.requests import StockQuotesRequest
        response = client.get_stock_quotes(StockQuotesRequest(
            symbol_or_symbols=ticker,
            start=start_time,
            end=end_time,
            feed=DataFeed.SIP
        ))
    else:
        from alpaca.data.requests import StockTradesRequest
        response = client.get_stock_trades(StockTradesRequest(
            symbol_or_symbols=ticker,
            start=start_time,
            end=end_time,
            feed=DataFeed.SIP
        ))

    if not response.data or ticker not in response.data:
        print(f"   ❌ No data found")
        return [], use_quotes

    data = response.data[ticker]
    print(f"   ✓ Found {len(data)} {'quotes' if use_quotes else 'trades'}")
    return data, use_quotes


def run_simulation(ticker: str, received_time: datetime, data: list, is_quotes: bool = False, verbose: bool = True):
    """
    Run tick-by-tick simulation with detailed output.

    Args:
        received_time: When article was RECEIVED (not published) - entry is received + 3s
        data: List of trades or quotes from Alpaca
        is_quotes: True if data is quotes (use midpoint), False if trades (use price)

    Returns dict with full simulation results.
    """
    if not data:
        return None

    # Target entry time is RECEIVED + 3 seconds (not publication + 3s)
    target_entry_time = received_time + timedelta(seconds=ENTRY_DELAY_SECONDS)

    def get_price(item):
        """Get price from trade or midpoint from quote."""
        if is_quotes:
            # Use ask price for entry (we're buying), midpoint for P&L calculation
            return (item.bid_price + item.ask_price) / 2
        else:
            return item.price

    def get_ask_price(item):
        """Get ask price (for entry)."""
        if is_quotes:
            return item.ask_price
        else:
            return item.price

    # Find entry point (first data point at or after target entry time)
    entry_item = None
    for item in data:
        if item.timestamp >= target_entry_time:
            entry_item = item
            break

    if not entry_item:
        print(f"\n❌ No data found after entry time")
        return None

    # Entry at ask price (we're buying), use actual timestamp
    entry_price = get_ask_price(entry_item)
    entry_time = entry_item.timestamp

    # Calculate end times from ACTUAL entry time (not target)
    sim_end_time = entry_time + timedelta(seconds=SIMULATION_DURATION_SECONDS)
    soft_stop_end_time = entry_time + timedelta(seconds=SOFT_STOP_WINDOW_SECONDS)

    print(f"\n{'='*80}")
    print(f"SIMULATION: {ticker} (using {'quotes' if is_quotes else 'trades'})")
    print(f"{'='*80}")
    print(f"Received time:      {received_time}")
    print(f"Target entry (+3s): {target_entry_time}")
    print(f"Actual entry:       {entry_time}")
    print(f"Soft stop ends:     {soft_stop_end_time} (+{SOFT_STOP_WINDOW_SECONDS}s from actual entry)")
    print(f"Simulation ends:    {sim_end_time} (+{SIMULATION_DURATION_SECONDS}s from actual entry)")

    print(f"\n📈 ENTRY")
    print(f"   Price: ${entry_price:.4f}")
    print(f"   Time:  {entry_time}")

    # Initialize simulation state
    position_pct = 100  # Start with 100% position
    current_stop_pct = STOP_LOSS_PCT  # -5%
    realized_pnl = 0.0

    breach_start = None
    stopped_out = False
    stop_triggered_at = None
    stop_price = None
    stop_type = None
    tp_events = []
    tp_tier_index = 0

    max_price = entry_price
    min_price = entry_price
    max_pnl_pct = 0.0
    min_pnl_pct = 0.0

    final_price = entry_price
    trade_count = 0

    # Key events for detailed logging
    key_events = []

    print(f"\n{'='*80}")
    print(f"TICK-BY-TICK SIMULATION")
    print(f"{'='*80}")
    print(f"{'Time':>10} | {'Price':>8} | {'P&L%':>7} | {'Pos%':>5} | {'Stop%':>6} | Event")
    print(f"{'-'*80}")

    for item in data:
        # Skip data before entry
        if item.timestamp < entry_time:
            continue

        # Stop at simulation end
        if item.timestamp > sim_end_time:
            break

        trade_count += 1
        # Use bid price for stop-loss checks (worst case for sell)
        # Use midpoint for general P&L tracking
        if is_quotes:
            price = (item.bid_price + item.ask_price) / 2
            bid_price = item.bid_price  # For stop-loss (we'd sell at bid)
        else:
            price = item.price
            bid_price = item.price

        final_price = price
        elapsed = (item.timestamp - entry_time).total_seconds()
        pnl_pct = ((price - entry_price) / entry_price) * 100
        # For stop-loss, use bid price (what we'd actually get if we sold)
        bid_pnl_pct = ((bid_price - entry_price) / entry_price) * 100

        # Track extremes
        if price > max_price:
            max_price = price
            max_pnl_pct = pnl_pct
        if price < min_price:
            min_price = price
            min_pnl_pct = pnl_pct

        # Skip if already stopped out or no position
        if stopped_out or position_pct <= 0:
            continue

        event = ""
        in_soft_window = item.timestamp <= soft_stop_end_time

        # Check stop-loss (use bid price - what we'd actually get when selling)
        if bid_pnl_pct <= current_stop_pct:
            if in_soft_window:
                # Soft stop - need 1.25s confirmation
                if breach_start is None:
                    breach_start = item.timestamp
                    breach_duration = 0.0
                    event = f"⚠️  BREACH START @ {bid_pnl_pct:+.1f}% (soft window, need {SOFT_STOP_CONFIRMATION_SECONDS}s confirm)"
                else:
                    breach_duration = (item.timestamp - breach_start).total_seconds()
                    if breach_duration >= SOFT_STOP_CONFIRMATION_SECONDS:
                        # Confirmed soft stop
                        stopped_out = True
                        stop_triggered_at = item.timestamp
                        stop_price = bid_price
                        stop_type = "soft"
                        realized_pnl += position_pct * (bid_pnl_pct / 100)
                        event = f"🛑 SOFT STOP TRIGGERED after {breach_duration:.2f}s breach @ {bid_pnl_pct:+.1f}%"
                        position_pct = 0
                    else:
                        event = f"⚠️  BREACH CONTINUING ({breach_duration:.2f}s / {SOFT_STOP_CONFIRMATION_SECONDS}s) @ {bid_pnl_pct:+.1f}%"
            else:
                # Hard stop - immediate
                stopped_out = True
                stop_triggered_at = item.timestamp
                stop_price = bid_price
                stop_type = "hard"
                realized_pnl += position_pct * (bid_pnl_pct / 100)
                event = f"🛑 HARD STOP TRIGGERED @ {bid_pnl_pct:+.1f}%"
                position_pct = 0
        else:
            # Price recovered - reset breach timer
            if breach_start is not None:
                breach_duration = (item.timestamp - breach_start).total_seconds()
                event = f"✓ Breach recovered after {breach_duration:.2f}s (now @ {bid_pnl_pct:+.1f}%)"
                breach_start = None

        # Check take profits (only if we have position)
        # Use bid price for TP (that's what we'd sell at)
        if position_pct > 0 and tp_tier_index < len(TAKE_PROFIT_TIERS):
            trigger_pct, sell_pct, new_stop_pct = TAKE_PROFIT_TIERS[tp_tier_index]

            if bid_pnl_pct >= trigger_pct:
                actual_sell = min(sell_pct, position_pct)
                tier_realized = actual_sell * (bid_pnl_pct / 100)
                realized_pnl += tier_realized
                position_pct -= actual_sell

                old_stop = current_stop_pct
                if new_stop_pct is not None:
                    current_stop_pct = new_stop_pct

                tp_events.append({
                    "tier": tp_tier_index + 1,
                    "trigger_pct": trigger_pct,
                    "price": bid_price,
                    "pnl_pct": bid_pnl_pct,
                    "sold_pct": actual_sell,
                    "realized": tier_realized,
                    "new_stop_pct": new_stop_pct,
                    "timestamp": item.timestamp.isoformat(),
                    "elapsed_seconds": elapsed,
                })

                event = f"💰 TP{tp_tier_index + 1}: Sold {actual_sell}% @ +{bid_pnl_pct:.1f}%, stop {old_stop:+.0f}% → {current_stop_pct:+.0f}%"
                tp_tier_index += 1
                breach_start = None

        # Log important events or sample ticks
        if event or trade_count <= 10 or trade_count % 100 == 0:
            time_str = f"+{elapsed:.1f}s"
            print(f"{time_str:>10} | ${price:>7.4f} | {pnl_pct:>+6.2f}% | {position_pct:>4}% | {current_stop_pct:>+5.0f}% | {event}")

    # Calculate final P&L
    final_pnl_pct = ((final_price - entry_price) / entry_price) * 100
    unrealized_pnl = (position_pct / 100) * final_pnl_pct if position_pct > 0 else 0.0
    total_pnl = realized_pnl + unrealized_pnl

    # Determine outcome
    would_have_traded = not stopped_out or len(tp_events) > 0

    print(f"\n{'='*80}")
    print(f"SIMULATION RESULTS")
    print(f"{'='*80}")
    print(f"\n📊 Position Summary:")
    print(f"   Entry price:      ${entry_price:.4f}")
    print(f"   Final price:      ${final_price:.4f}")
    print(f"   Max price:        ${max_price:.4f} (+{max_pnl_pct:.2f}%)")
    print(f"   Min price:        ${min_price:.4f} ({min_pnl_pct:.2f}%)")
    print(f"   Ticks processed:  {trade_count}")

    print(f"\n💰 P&L Summary:")
    print(f"   Realized P&L:     {realized_pnl:+.2f}%")
    print(f"   Unrealized P&L:   {unrealized_pnl:+.2f}% ({position_pct}% position)")
    print(f"   Total P&L:        {total_pnl:+.2f}%")

    print(f"\n🎯 Take Profit Events: {len(tp_events)}")
    for tp in tp_events:
        print(f"   TP{tp['tier']}: +{tp['pnl_pct']:.1f}% @ {tp['elapsed_seconds']:.1f}s, sold {tp['sold_pct']}%, realized {tp['realized']:.2f}%")

    print(f"\n🛑 Stop Loss:")
    if stopped_out:
        stop_elapsed = (stop_triggered_at - entry_time).total_seconds()
        print(f"   Triggered: YES ({stop_type} stop)")
        print(f"   Price:     ${stop_price:.4f}")
        print(f"   Time:      +{stop_elapsed:.1f}s")
    else:
        print(f"   Triggered: NO")

    print(f"\n✅ Would Have Traded: {'YES' if would_have_traded else 'NO'}")
    if would_have_traded:
        print(f"   Reason: {'Hit take profit before stop' if tp_events else 'Never hit stop'}")
    else:
        print(f"   Reason: Stopped out before any take profit")

    # Calculate stop elapsed
    stop_elapsed_seconds = None
    if stop_triggered_at:
        stop_elapsed_seconds = (stop_triggered_at - entry_time).total_seconds()

    def format_elapsed(secs):
        if secs is None:
            return None
        mins = int(secs // 60)
        s = secs % 60
        if mins > 0:
            return f"{mins}m {s:.3f}s"
        return f"{s:.3f}s"

    return {
        "ticker": ticker,
        "received_time": received_time.isoformat(),
        "entry_time": entry_time.isoformat(),
        "entry_price": entry_price,
        "would_have_traded": would_have_traded,
        "total_pnl_pct": round(total_pnl, 2),
        "realized_pnl_pct": round(realized_pnl, 2),
        "unrealized_pnl_pct": round(unrealized_pnl, 2),
        "position_remaining_pct": position_pct,
        "final_price": final_price,
        "final_pnl_pct": round(final_pnl_pct, 2),
        "stopped_out": stopped_out,
        "stop_triggered_at": stop_triggered_at.isoformat() if stop_triggered_at else None,
        "stop_elapsed": format_elapsed(stop_elapsed_seconds),
        "stop_elapsed_seconds": round(stop_elapsed_seconds, 3) if stop_elapsed_seconds else None,
        "stop_price": stop_price,
        "stop_type": stop_type,
        "tp_events": tp_events,
        "max_price": max_price,
        "max_pnl_pct": round(max_pnl_pct, 2),
        "min_price": min_price,
        "min_pnl_pct": round(min_pnl_pct, 2),
        "trade_count": trade_count,
    }


def main():
    parser = argparse.ArgumentParser(description="Test trade simulation on a single example")
    parser.add_argument("ticker", help="Stock ticker")
    parser.add_argument("received_time", help="RECEIVED time (ISO format with timezone) - NOT publication time")
    parser.add_argument("--entry-price", type=float, help="Override entry price hint")
    parser.add_argument("--use-trades", action="store_true", help="Use trades instead of quotes")

    args = parser.parse_args()

    # Parse received time (entry is received + 3s)
    recv_time = datetime.fromisoformat(args.received_time)

    # Get Alpaca client
    client = get_alpaca_client()

    # Fetch tick data (quotes by default, trades if --use-trades)
    data, is_quotes = fetch_tick_data(client, args.ticker, recv_time, use_quotes=not args.use_trades)

    if not data:
        print("No data available")
        return

    # Run simulation
    result = run_simulation(args.ticker, recv_time, data, is_quotes=is_quotes)

    if result:
        print(f"\n{'='*80}")
        print("JSON OUTPUT")
        print(f"{'='*80}")
        import json
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
