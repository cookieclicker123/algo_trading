import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from newsflash.models.base_models import StandardizedArticle, NewsSource, TradeInstrument
from newsflash.models.classification_models import ClassificationResult, NewsClassification
from newsflash.services.auto_trade_service import AutoTradeService
from newsflash.services.ibkr_trading_service import TradeResult
from newsflash.services.position_tracker import PositionTracker


@pytest.mark.asyncio
async def test_auto_trade_market_hours_uses_option(tmp_path):
    trading_service = MagicMock()
    trading_service.get_market_session.return_value = ("market_hours", False)

    trade_result = TradeResult(
        success=True,
        shares=1,
        fill_price=2.5,
        total_cost=250.0,
        session="market_hours",
        order_type="MARKET",
        instrument=TradeInstrument.OPTION.value,
        instrument_details={
            "expiry": "20251219",
            "strike": 100.0,
            "right": "C",
            "exchange": "SMART",
            "multiplier": "100",
        },
    )
    trading_service.process_trade_request = AsyncMock(return_value=trade_result)

    position_tracker = PositionTracker(str(tmp_path / "positions.json"))
    auto_trader = AutoTradeService(
        trading_service=trading_service,
        position_tracker=position_tracker,
        telegram_service=None,
        audit_trail=None,
        price_tracking_service=None,
    )

    article = StandardizedArticle(
        source=NewsSource.BENZINGA_WEBSOCKET,
        source_id="article-1",
        title="Company announces major government contract",
        content="",
        summary="",
        author=None,
        published=datetime.now(timezone.utc),
        updated=None,
        url=None,
        tickers=["AAPL"],
        tags=[],
        categories=[],
        images=[],
        raw_data={},
    )
    classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Major contract expected to move the stock.",
    )

    auto_trader._schedule_exit = AsyncMock()

    await auto_trader.process_imminent_article(article, classification)

    trade_request = trading_service.process_trade_request.call_args[0][0]
    assert trade_request.instrument == TradeInstrument.OPTION
    assert trade_request.shares == 1

    stored_position = position_tracker.get_position("AAPL", "article-1")
    assert stored_position is not None
    assert stored_position.instrument == TradeInstrument.OPTION.value
    assert stored_position.instrument_details["strike"] == 100.0


@pytest.mark.asyncio
async def test_auto_trade_extended_hours_uses_leveraged_shares(tmp_path):
    trading_service = MagicMock()
    trading_service.get_market_session.return_value = ("premarket", True)

    trade_result = TradeResult(
        success=True,
        shares=25,
        fill_price=40.0,
        total_cost=1000.0,
        session="premarket",
        order_type="LIMIT",
        instrument=TradeInstrument.STOCK.value,
        instrument_details={"leverage": 2.0, "target_notional": 1000.0},
    )
    trading_service.process_trade_request = AsyncMock(return_value=trade_result)

    position_tracker = PositionTracker(str(tmp_path / "positions.json"))
    auto_trader = AutoTradeService(
        trading_service=trading_service,
        position_tracker=position_tracker,
        telegram_service=None,
        audit_trail=None,
        price_tracking_service=None,
    )

    article = StandardizedArticle(
        source=NewsSource.BENZINGA_WEBSOCKET,
        source_id="article-2",
        title="After-hours acquisition rumor",
        content="",
        summary="",
        author=None,
        published=datetime.now(timezone.utc),
        updated=None,
        url=None,
        tickers=["MSFT"],
        tags=[],
        categories=[],
        images=[],
        raw_data={},
    )
    classification = ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Large acquisition likely to impact price.",
    )

    auto_trader._schedule_exit = AsyncMock()

    await auto_trader.process_imminent_article(article, classification)

    trade_request = trading_service.process_trade_request.call_args[0][0]
    assert trade_request.instrument == TradeInstrument.STOCK
    assert trade_request.shares is None
    assert trade_request.amount_usd == 1000.0
    assert trade_request.leverage == 2.0

    stored_position = position_tracker.get_position("MSFT", "article-2")
    assert stored_position is not None
    assert stored_position.instrument == TradeInstrument.STOCK.value
    assert stored_position.shares == 25


@pytest.mark.asyncio
async def test_premarket_integration_flow_executes_entry_and_exit(monkeypatch, tmp_path):
    # Accelerate the scheduled exit to a few milliseconds for test purposes
    monkeypatch.setattr(
        "newsflash.services.auto_trade_service.AUTO_TRADE_EXIT_DELAY_MINUTES", 0.0002
    )

    trading_service = MagicMock()
    trading_service.get_market_session.return_value = ("premarket", True)

    entry_result = TradeResult(
        success=True,
        shares=24,
        fill_price=120.50,
        total_cost=2892.0,
        session="premarket",
        order_type="LIMIT",
        instrument=TradeInstrument.STOCK.value,
        instrument_details={
            "leverage": 2.0,
            "target_notional": 1000.0,
            "fill_venue": "ARCA",
        },
    )
    exit_result = TradeResult(
        success=True,
        shares=24,
        fill_price=121.10,
        total_cost=2906.4,
        session="premarket",
        order_type="LIMIT",
        instrument=TradeInstrument.STOCK.value,
        instrument_details={"fill_venue": "ARCA"},
    )
    trading_service.process_trade_request = AsyncMock(
        side_effect=[entry_result, exit_result]
    )

    position_tracker = PositionTracker(str(tmp_path / "positions.json"))
    auto_trader = AutoTradeService(
        trading_service=trading_service,
        position_tracker=position_tracker,
        telegram_service=None,
        audit_trail=None,
        price_tracking_service=None,
    )
    auto_trader.trade_timeout_seconds = 0.5

    article = StandardizedArticle(
        source=NewsSource.BENZINGA_WEBSOCKET,
        source_id="article-amd",
        title="AMD lands massive AI accelerator deal",
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
        reasoning="Major AI deal expected to impact price.",
    )

    await auto_trader.process_imminent_article(article, classification)

    # Allow the accelerated exit schedule to run
    await asyncio.sleep(0.1)

    # Entry request assertions
    assert trading_service.process_trade_request.await_count >= 2
    entry_request = trading_service.process_trade_request.await_args_list[0][0][0]
    assert entry_request.ticker == "AMD"
    assert entry_request.instrument == TradeInstrument.STOCK
    assert entry_request.amount_usd == 1000.0
    assert entry_request.leverage == 2.0

    # Exit request assertions
    exit_request = trading_service.process_trade_request.await_args_list[1][0][0]
    assert exit_request.ticker == "AMD"
    assert exit_request.shares == entry_result.shares

    # Position should be cleared after exit completes
    assert not position_tracker.has_open_position("AMD")

