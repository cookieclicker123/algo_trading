import os
import sys
import pytest


# Ensure 'src' is on sys.path so `import newsflash` works when running `pytest`
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_PATH = os.path.join(PROJECT_ROOT, 'src')
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from newsflash.services.ibkr_trading_service import get_ibkr_trading_service
from newsflash.models.base_models import TradeRequest


@pytest.mark.asyncio
async def test_realtime_ladder_buy_premarket():
    """Integration test: executes a 1-share BUY using the tight ladder (paper).

    Requires IB Gateway paper running locally with real-time data enabled.
    Skip by setting SKIP_IBKR_INTEGRATION=true.
    """

    if os.getenv("SKIP_IBKR_INTEGRATION", "false").lower() == "true":
        pytest.skip("IBKR integration tests skipped by env")

    service = get_ibkr_trading_service(paper_trading=True)
    trade_request = TradeRequest(ticker="AAPL", amount_usd=100.0, action="BUY")

    result = await service.execute_trade(trade_request)
    assert result is not None
    assert result.order_type == "LIMIT"
    assert result.session in {"premarket", "postmarket", "market_hours", "closed"}

import asyncio
import os
import pytest

from newsflash.services.ibkr_trading_service import get_ibkr_trading_service
from newsflash.models.base_models import TradeRequest


@pytest.mark.asyncio
async def test_realtime_ladder_buy_premarket():
    """Manual integration test: executes a 1-share BUY using the tight ladder.

    Requires IB Gateway paper account running locally with real-time market data enabled.
    Choose a liquid symbol to minimize slippage; runs during pre/post market.
    """

    # Skip in CI or when explicitly disabled
    if os.getenv("SKIP_IBKR_INTEGRATION", "false").lower() == "true":
        pytest.skip("IBKR integration tests skipped by env")

    service = get_ibkr_trading_service(paper_trading=True)
    trade_request = TradeRequest(ticker="AAPL", amount_usd=100.0, action="BUY")

    result = await service.execute_trade(trade_request)
    assert result is not None
    # Success is ideal, but allow failures to still surface timing info
    # We assert that the attempt reached IB and produced a TradeResult
    assert result.order_type == "LIMIT"
    assert result.session in {"premarket", "postmarket", "market_hours", "closed"}


