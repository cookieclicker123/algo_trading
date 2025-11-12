import asyncio
import os
from datetime import datetime, timezone

import pytest

from newsflash.models.base_models import StandardizedArticle, NewsSource
from newsflash.models.classification_models import ClassificationResult, NewsClassification
from newsflash.services.auto_trade_service import AutoTradeService
from newsflash.services.ibkr_trading_service import IBKRTradingService
from newsflash.services.position_tracker import PositionTracker
from newsflash.services.telegram_service import get_telegram_notifier


@pytest.mark.asyncio
async def test_paper_account_premarket_flow(monkeypatch, tmp_path):
    """
    Full IBKR integration flow that places a paper-trading order using the auto-trade
    service and verifies the scheduled exit completes. This requires an active IB
    Gateway, the paper account, and RUN_IBKR_INTEGRATION=1.
    """

    if os.getenv("RUN_IBKR_INTEGRATION") != "1":
        pytest.skip("Set RUN_IBKR_INTEGRATION=1 to run the live IBKR integration test.")

    trading_service = IBKRTradingService(paper_trading=True)
    await trading_service.start()

    session, _ = trading_service.get_market_session()
    if session == "closed":
        await telegram_service.stop()
        await trading_service.stop()
        pytest.skip("Market is fully closed; run the integration test during pre/regular hours.")

    # Force the auto-trader down the extended-hours branch to exercise leveraged equity flow
    monkeypatch.setattr(
        trading_service, "get_market_session", lambda: ("premarket", True)
    )
    monkeypatch.setattr(
        "newsflash.services.auto_trade_service.AUTO_TRADE_EXIT_DELAY_MINUTES", 0.001
    )

    position_tracker = PositionTracker(str(tmp_path / "integration_positions.json"))

    telegram_service = get_telegram_notifier()
    await telegram_service.start()

    auto_trader = AutoTradeService(
        trading_service=trading_service,
        position_tracker=position_tracker,
        telegram_service=telegram_service,
        audit_trail=None,
        price_tracking_service=None,
    )
    auto_trader.trade_timeout_seconds = 5.0

    article = StandardizedArticle(
        source=NewsSource.BENZINGA_WEBSOCKET,
        source_id="integration-amd",
        title="AMD secures record AI accelerator contract",
        content="",
        summary="",
        author=None,
        published=datetime.now(timezone.utc),
        updated=None,
        url=None,
        tickers=["AMD"],
        tags=[],
        categories=[],
        images=[],
        raw_data={},
    )
    classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Simulated integration headline.",
    )

    try:
        await auto_trader.process_imminent_article(article, classification)

        async def _wait_for_exit():
            for _ in range(50):
                if not position_tracker.has_open_position("AMD", "integration-amd"):
                    return True
                await asyncio.sleep(0.1)
            return False

        exited = await _wait_for_exit()
        assert exited, "Timed out waiting for the scheduled exit to complete"
    finally:
        await telegram_service.stop()
        await trading_service.stop()

