import asyncio
import os

import pytest

from newsflash.services.ibkr_trading_service import get_ibkr_trading_service


pytestmark = pytest.mark.skipif(
    os.environ.get("IBKR_MARKET_DATA_PROBE") != "1",
    reason="Set IBKR_MARKET_DATA_PROBE=1 to run live market data probe tests.",
)


def test_probe_market_data_structure():
    async def _run():
        service = get_ibkr_trading_service()
        await service.start()
        try:
            diagnostics = await service.probe_market_data(["AAPL"], timeout_seconds=2.0)
        finally:
            await service.stop()
        return diagnostics

    diagnostics = asyncio.run(_run())

    assert "AAPL" in diagnostics
    aapl_diag = diagnostics["AAPL"]
    assert "qualified" in aapl_diag
    assert "had_price" in aapl_diag
