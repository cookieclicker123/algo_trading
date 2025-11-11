"""Manual diagnostic script to probe IBKR market data latency/coverage.

Usage:
    uv run python scripts/probe_ibkr_market_data.py AAPL MSFT ORCL

The script connects via the shared IBKR trading service, ensures the
gateway is running, and then attempts to pull live quotes for the given
tickers (default list provided if none are supplied). The output shows
whether qualification succeeded, how long it took to receive the first
price, and which price fields were populated.
"""

import argparse
import asyncio
import json
from typing import List

from newsflash.services.ibkr_trading_service import IBKRTradingService


async def probe(tickers: List[str], timeout_seconds: float, paper: bool) -> None:
    service = IBKRTradingService(paper_trading=paper)
    try:
        await service.start()
        diagnostics = await service.probe_market_data(tickers, timeout_seconds)
        print(json.dumps(diagnostics, indent=2, default=str))
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe IBKR market data availability")
    parser.add_argument(
        "tickers",
        nargs="*",
        default=["AAPL", "MSFT", "TSLA", "ORCL"],
        help="Tickers to probe",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for a quote before declaring failure",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Use the paper-trading gateway (default is live port)",
    )
    args = parser.parse_args()

    asyncio.run(probe(args.tickers, args.timeout, args.paper))


if __name__ == "__main__":
    main()
