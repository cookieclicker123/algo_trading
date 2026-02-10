"""
Backtest IMMINENT trades for Feb 9, 2026 premarket.

Simulates our exact exit strategy (tiered exits + stop loss + floors)
using tick-by-tick trade data to calculate precise P&L.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from pathlib import Path

# Alpaca imports
from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest
from alpaca.data.enums import DataFeed
import os
from pathlib import Path
from dotenv import load_dotenv

# Load from project root
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

# Exit strategy constants (from position_manager.py)
STOP_LOSS_PCT = 0.05  # 5% below entry
ENTRY_GRACE_PERIOD_SECONDS = 5.0  # First 5 seconds: use confirmation
STOP_LOSS_CONFIRMATION_SECONDS = 0.5  # 0.5s to confirm stop

BREAKEVEN_TRIGGER_PCT = 0.05  # Move to breakeven after +5%
BREAKEVEN_CONFIRMATION_SECONDS = 0.5

TIERED_EXIT_THRESHOLDS = [
    (0.10, 0.50),  # +10%: Exit 50%
    (0.15, 0.50),  # +15%: Exit 50% of remaining
    (0.20, 1.00),  # +20%: Exit remaining
]

TIER_FLOOR_PCT = {
    0.10: 0.025,  # After +10%, floor at +2.5%
    0.15: 0.05,   # After +15%, floor at +5%
    0.20: None,   # Fully exited
}

POSITION_SIZE_USD = 10000  # $10k per trade for analysis


@dataclass
class SimulatedPosition:
    """Track position state during simulation."""
    ticker: str
    entry_price: float
    entry_time: datetime
    shares: float
    shares_remaining: float = field(init=False)

    # Stop tracking
    stop_breach_time: Optional[datetime] = None
    breakeven_trigger_time: Optional[datetime] = None
    breakeven_stop_active: bool = False

    # Tier tracking
    next_tier_index: int = 0
    last_exit_threshold: Optional[float] = None

    # Results
    exits: List[Dict] = field(default_factory=list)
    final_pnl_pct: float = 0.0
    exit_reason: str = "hold_expired"

    def __post_init__(self):
        self.shares_remaining = self.shares

    @property
    def stop_price(self) -> float:
        if self.breakeven_stop_active:
            return self.entry_price
        return self.entry_price * (1 - STOP_LOSS_PCT)

    @property
    def floor_price(self) -> Optional[float]:
        if self.last_exit_threshold:
            floor_pct = TIER_FLOOR_PCT.get(self.last_exit_threshold)
            if floor_pct:
                return self.entry_price * (1 + floor_pct)
        return None


def simulate_trade(
    ticker: str,
    entry_price: float,
    entry_time: datetime,
    trades: List[Any],
    hold_minutes: int = 10
) -> Dict:
    """
    Simulate a trade using actual tick data.

    Returns dict with P&L breakdown.
    """
    shares = int(POSITION_SIZE_USD / entry_price)
    position = SimulatedPosition(
        ticker=ticker,
        entry_price=entry_price,
        entry_time=entry_time,
        shares=shares
    )

    hold_end = entry_time + timedelta(minutes=hold_minutes)
    highest_price = entry_price
    lowest_price = entry_price

    for trade in trades:
        trade_time = trade.timestamp
        if trade_time.tzinfo is None:
            trade_time = trade_time.replace(tzinfo=timezone.utc)

        # Skip trades before entry
        if trade_time < entry_time:
            continue

        # Stop after hold period
        if trade_time > hold_end:
            break

        trade_price = float(trade.price)
        if not trade_price or trade_price <= 0:
            continue

        # Track extremes
        highest_price = max(highest_price, trade_price)
        lowest_price = min(lowest_price, trade_price)

        # Check if position fully exited
        if position.shares_remaining <= 0:
            break

        profit_pct = (trade_price - entry_price) / entry_price
        seconds_since_entry = (trade_time - entry_time).total_seconds()
        in_grace_period = seconds_since_entry <= ENTRY_GRACE_PERIOD_SECONDS

        # --- BREAKEVEN ACTIVATION ---
        if not position.breakeven_stop_active and profit_pct >= BREAKEVEN_TRIGGER_PCT:
            if position.breakeven_trigger_time is None:
                position.breakeven_trigger_time = trade_time
            else:
                trigger_duration = (trade_time - position.breakeven_trigger_time).total_seconds()
                if trigger_duration >= BREAKEVEN_CONFIRMATION_SECONDS:
                    position.breakeven_stop_active = True
        elif not position.breakeven_stop_active and profit_pct < BREAKEVEN_TRIGGER_PCT:
            position.breakeven_trigger_time = None

        # --- STOP LOSS CHECK ---
        if trade_price <= position.stop_price:
            if in_grace_period or position.breakeven_stop_active:
                # Need confirmation
                if position.stop_breach_time is None:
                    position.stop_breach_time = trade_time
                else:
                    breach_duration = (trade_time - position.stop_breach_time).total_seconds()
                    if breach_duration >= STOP_LOSS_CONFIRMATION_SECONDS:
                        # STOPPED OUT
                        exit_pct = profit_pct
                        position.exits.append({
                            "reason": "stop_loss" if not position.breakeven_stop_active else "breakeven_stop",
                            "price": trade_price,
                            "shares": position.shares_remaining,
                            "pct": exit_pct,
                            "time": trade_time.isoformat(),
                            "seconds_in": seconds_since_entry
                        })
                        position.shares_remaining = 0
                        position.exit_reason = "stop_loss"
                        break
            else:
                # After grace period - immediate stop
                exit_pct = profit_pct
                position.exits.append({
                    "reason": "stop_loss",
                    "price": trade_price,
                    "shares": position.shares_remaining,
                    "pct": exit_pct,
                    "time": trade_time.isoformat(),
                    "seconds_in": seconds_since_entry
                })
                position.shares_remaining = 0
                position.exit_reason = "stop_loss"
                break
        else:
            # Price above stop - reset breach
            position.stop_breach_time = None

        # --- FLOOR CHECK ---
        if position.floor_price and trade_price <= position.floor_price:
            exit_pct = profit_pct
            position.exits.append({
                "reason": "floor_exit",
                "price": trade_price,
                "shares": position.shares_remaining,
                "pct": exit_pct,
                "time": trade_time.isoformat(),
                "seconds_in": seconds_since_entry,
                "floor_after_tier": position.last_exit_threshold
            })
            position.shares_remaining = 0
            position.exit_reason = "floor_exit"
            break

        # --- TIERED EXIT CHECK ---
        if position.next_tier_index < len(TIERED_EXIT_THRESHOLDS):
            threshold_pct, exit_fraction = TIERED_EXIT_THRESHOLDS[position.next_tier_index]

            if profit_pct >= threshold_pct:
                shares_to_exit = int(position.shares_remaining * exit_fraction)
                if shares_to_exit < 1 and position.shares_remaining >= 1:
                    shares_to_exit = int(position.shares_remaining)

                if shares_to_exit > 0:
                    position.exits.append({
                        "reason": f"tier_{int(threshold_pct*100)}pct",
                        "price": trade_price,
                        "shares": shares_to_exit,
                        "pct": profit_pct,
                        "time": trade_time.isoformat(),
                        "seconds_in": seconds_since_entry
                    })
                    position.shares_remaining -= shares_to_exit
                    position.last_exit_threshold = threshold_pct
                    position.next_tier_index += 1

    # Calculate final P&L
    total_pnl = 0.0
    total_shares_exited = 0

    for exit in position.exits:
        shares_exited = exit["shares"]
        exit_pct = exit["pct"]
        pnl_from_exit = shares_exited * entry_price * exit_pct
        total_pnl += pnl_from_exit
        total_shares_exited += shares_exited

    # If any shares remaining at end of hold period, exit at last price
    if position.shares_remaining > 0 and trades:
        # Find last trade price within hold window
        last_trade = None
        for trade in reversed(trades):
            trade_time = trade.timestamp
            if trade_time.tzinfo is None:
                trade_time = trade_time.replace(tzinfo=timezone.utc)
            if trade_time <= hold_end:
                last_trade = trade
                break

        if last_trade:
            final_price = float(last_trade.price)
            final_pct = (final_price - entry_price) / entry_price
            pnl_from_hold = position.shares_remaining * entry_price * final_pct
            total_pnl += pnl_from_hold
            position.exits.append({
                "reason": "hold_expired",
                "price": final_price,
                "shares": position.shares_remaining,
                "pct": final_pct,
                "time": hold_end.isoformat(),
                "seconds_in": hold_minutes * 60
            })

    final_pnl_pct = (total_pnl / POSITION_SIZE_USD) * 100

    return {
        "ticker": ticker,
        "entry_price": entry_price,
        "entry_time": entry_time.isoformat(),
        "shares": shares,
        "position_size": POSITION_SIZE_USD,
        "exits": position.exits,
        "total_pnl_usd": round(total_pnl, 2),
        "total_pnl_pct": round(final_pnl_pct, 2),
        "exit_reason": position.exit_reason if position.shares_remaining == 0 else "hold_expired",
        "highest_price": highest_price,
        "lowest_price": lowest_price,
        "highest_pct": round((highest_price - entry_price) / entry_price * 100, 2),
        "lowest_pct": round((lowest_price - entry_price) / entry_price * 100, 2),
        "breakeven_activated": position.breakeven_stop_active
    }


async def main():
    # Load recall records
    recall_path = Path("tmp/statistics/recall/2026/02/week_7/09/premarket/premarket.json")

    with open(recall_path) as f:
        recall_data = json.load(f)

    # Filter to IMMINENT with <10% spread
    imminent_candidates = []

    for record in recall_data.get("records", []):
        ai_class = record.get("ai_classification", "")
        if ai_class and ai_class.upper() == "IMMINENT":
            initial_nbbo = record.get("initial_nbbo", {})
            ask = initial_nbbo.get("ask", 0)
            spread = initial_nbbo.get("spread", 0)

            if ask > 0 and spread > 0:
                spread_pct = (spread / ask) * 100
                if spread_pct < 10:
                    ticker = record.get("tickers", [None])[0]
                    if ticker:
                        imminent_candidates.append({
                            "ticker": ticker,
                            "headline": record.get("title", ""),
                            "entry_price": ask,
                            "published_at": record.get("published_at"),
                            "spread_pct": round(spread_pct, 2),
                            "market_cap": record.get("ticker_metadata", {}).get(ticker, {}).get("market_cap_millions"),
                            "sector": record.get("ticker_metadata", {}).get(ticker, {}).get("sector"),
                            "industry": record.get("ticker_metadata", {}).get(ticker, {}).get("industry"),
                            "confluence_score": record.get("confluence_score"),
                            "buying_pressure": record.get("confluence_buying_pressure_pct"),
                        })

    print(f"\n{'='*80}")
    print(f"IMMINENT BACKTEST - Feb 9, 2026 Premarket")
    print(f"{'='*80}")
    print(f"Found {len(imminent_candidates)} IMMINENT articles with <10% spread\n")

    # Initialize Alpaca client
    api_key = os.getenv("ALPACA_PAPER_API_KEY") or os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY")
    secret_key = os.getenv("ALPACA_PAPER_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_SECRET")

    if not api_key or not secret_key:
        print("ERROR: Missing Alpaca credentials")
        print(f"  Tried: ALPACA_PAPER_API_KEY, ALPACA_API_KEY, ALPACA_KEY")
        print(f"  Available env vars with 'ALPACA': {[k for k in os.environ if 'ALPACA' in k.upper()]}")
        return

    client = StockHistoricalDataClient(api_key, secret_key)

    results = []
    total_pnl = 0.0
    winners = 0
    losers = 0

    for candidate in imminent_candidates:
        ticker = candidate["ticker"]
        entry_price = candidate["entry_price"]
        pub_time = datetime.fromisoformat(candidate["published_at"].replace("Z", "+00:00"))

        print(f"\n--- {ticker} ---")
        print(f"Headline: {candidate['headline'][:80]}...")
        print(f"Entry: ${entry_price:.4f} | Spread: {candidate['spread_pct']:.1f}%")
        print(f"Market Cap: ${candidate['market_cap']:.1f}M | {candidate['sector']} / {candidate['industry']}")

        # Fetch trade data
        try:
            start_time = pub_time
            end_time = pub_time + timedelta(minutes=12)

            trades_request = StockTradesRequest(
                symbol_or_symbols=[ticker],
                start=start_time,
                end=end_time,
                feed=DataFeed.SIP
            )

            trades_response = client.get_stock_trades(trades_request)

            if not trades_response or not trades_response.data or ticker not in trades_response.data:
                print(f"  ⚠️  No trade data available")
                continue

            trades = trades_response.data[ticker]
            print(f"  Fetched {len(trades)} trades")

            # Simulate the trade
            result = simulate_trade(
                ticker=ticker,
                entry_price=entry_price,
                entry_time=pub_time,
                trades=trades,
                hold_minutes=10
            )

            result["headline"] = candidate["headline"]
            result["spread_pct"] = candidate["spread_pct"]
            result["market_cap"] = candidate["market_cap"]
            result["sector"] = candidate["sector"]
            result["industry"] = candidate["industry"]
            result["confluence_score"] = candidate["confluence_score"]
            result["buying_pressure"] = candidate["buying_pressure"]

            results.append(result)
            total_pnl += result["total_pnl_usd"]

            if result["total_pnl_usd"] > 0:
                winners += 1
                emoji = "✅"
            else:
                losers += 1
                emoji = "❌"

            print(f"  {emoji} P&L: ${result['total_pnl_usd']:+.2f} ({result['total_pnl_pct']:+.2f}%)")
            print(f"  Peak: {result['highest_pct']:+.2f}% | MAE: {result['lowest_pct']:.2f}%")
            print(f"  Exit reason: {result['exit_reason']}")

            for exit in result["exits"]:
                print(f"    → {exit['reason']}: {exit['shares']} shares @ ${exit['price']:.4f} ({exit['pct']*100:+.2f}%) at {exit['seconds_in']:.1f}s")

        except Exception as e:
            print(f"  ❌ Error: {e}")
            continue

    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    print(f"Total trades: {len(results)}")
    print(f"Winners: {winners} | Losers: {losers}")
    print(f"Win rate: {winners/len(results)*100:.1f}%" if results else "N/A")
    print(f"\nTotal P&L: ${total_pnl:+.2f}")
    print(f"Return on capital: {total_pnl/(POSITION_SIZE_USD*len(results))*100:+.2f}%" if results else "N/A")
    print(f"Capital deployed: ${POSITION_SIZE_USD * len(results):,.0f}")

    # Save detailed results
    output_path = Path("tmp/backtest_feb9_imminent.json")
    with open(output_path, "w") as f:
        json.dump({
            "date": "2026-02-09",
            "session": "premarket",
            "position_size": POSITION_SIZE_USD,
            "total_trades": len(results),
            "winners": winners,
            "losers": losers,
            "win_rate_pct": round(winners/len(results)*100, 1) if results else 0,
            "total_pnl_usd": round(total_pnl, 2),
            "return_pct": round(total_pnl/(POSITION_SIZE_USD*len(results))*100, 2) if results else 0,
            "results": results
        }, f, indent=2)

    print(f"\nDetailed results saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
