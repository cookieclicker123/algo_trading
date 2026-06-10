"""
Integration test for process_imminent_article — exercises the real orchestration
(gate -> postfilter engine -> execute), not just the pure units.

This is the net the pure golden masters can't provide: it proves the WIRING inside
the god-function routes to the right outcome. We mock only the true dependency
boundary (classification gate, confluence monitoring, blacklist, market data,
trade execution) and assert on the two observable outcomes: which postfilter
reason (if any) was recorded, and whether a trade was published.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import newsflash.services.brokerage.auto_trade as at


def _cm(**over):
    """A clean confluence_metadata that passes the gate + every postfilter."""
    base = dict(
        confluence_score=4,
        confluence_price_excursion_pct=1.0,
        confluence_trade_count=10,
        initial_spread=0.01, initial_ask=1.0, initial_bid=0.99,
        confluence_imbalance_ratio=0.5,
        confluence_max_price=1.0, confluence_first_price=1.0,
        is_mega_trade=False,
    )
    base.update(over)
    return base


async def _run(cm, *, headline_type="cancer_catalyst",
               fill_nbbo=None, pub_quote_ask=None):
    """Drive process_imminent_article with the boundary mocked.
    Returns (skip_reason_or_None, traded_bool)."""
    skip = AsyncMock()
    publish = AsyncMock()

    qf = MagicMock()
    qf.get_nbbo_snapshot = AsyncMock(
        return_value=fill_nbbo or {"bid": 0.995, "ask": 1.0, "spread": 0.005, "mid": 0.9975}
    )
    qf.stream_manager = MagicMock()
    pub_quotes = (
        [{"timestamp": datetime.now(timezone.utc) - timedelta(seconds=1), "ask": pub_quote_ask}]
        if pub_quote_ask is not None else []
    )
    qf.stream_manager.get_recent_quotes = AsyncMock(return_value=pub_quotes)

    mc = MagicMock()
    mc.get = AsyncMock(return_value={
        "sector": "Healthcare", "industry": "Biotechnology",
        "market_cap_millions": 100.0, "float_shares": 1_000_000,
    })

    cr = MagicMock()
    cr.article_id = "test:1"
    cr.classification.value = "imminent"

    patches = {
        "should_process_classification": MagicMock(return_value=True),
        "has_active_position": MagicMock(return_value=False),
        "is_ticker_in_cooldown": MagicMock(return_value=False),
        "is_circuit_breaker_triggered": MagicMock(return_value=False),
        "check_confluence_signals": AsyncMock(return_value=(at.ConvictionLevel.STANDARD, cm)),
        "_record_postfilter_skip": skip,
        "publish_trade_request": publish,
        "build_trade_request_for_article": MagicMock(return_value=MagicMock()),
        "monitor_for_last_chance_surge": AsyncMock(return_value=None),
        "monitor_for_late_entry": AsyncMock(return_value=None),
    }
    ctxs = [patch.object(at, name, val) for name, val in patches.items()]
    ctxs.append(patch("newsflash.services.brokerage.ticker_blacklist.is_ticker_blacklisted",
                      AsyncMock(return_value=False)))
    for c in ctxs:
        c.start()
    try:
        await at.process_imminent_article(
            event_bus=AsyncMock(), storage_service=MagicMock(),
            classification_result=cr, enabled=True,
            market_data_client=MagicMock(), quote_fetcher=qf, metadata_cache=mc,
            event_tickers=["TEST"], event_title="t",
            event_published_at=datetime.now(timezone.utc) - timedelta(seconds=2),
            event_position_size="MODERATE", event_headline_type=headline_type,
        )
    finally:
        for c in ctxs:
            c.stop()

    skip_reason = skip.call_args[0][1] if skip.called else None
    return skip_reason, publish.called


@pytest.mark.asyncio
async def test_clean_signal_trades():
    reason, traded = await _run(_cm())
    assert reason is None, f"unexpected skip: {reason}"
    assert traded is True


@pytest.mark.asyncio
async def test_wide_entry_spread_blocks_and_does_not_trade():
    # initial_spread 0.10 on ask 1.0 -> ~10% > 5% entry cap
    reason, traded = await _run(_cm(initial_spread=0.10, initial_bid=0.90))
    assert reason and reason.startswith("postfilter_spread_too_wide")
    assert traded is False


@pytest.mark.asyncio
async def test_heavy_selling_blocks():
    reason, traded = await _run(_cm(confluence_imbalance_ratio=-0.5))
    assert reason and reason.startswith("postfilter_selling_pressure")
    assert traded is False


@pytest.mark.asyncio
async def test_feed_class_strong_signal_bypasses_pub_to_recv_and_trades():
    # recv ask 0.95, pub ask 0.87 -> pub_to_recv 9.2% (>7.5 cap) but conf4 + <=15% -> bypass
    reason, traded = await _run(
        _cm(initial_ask=0.95, initial_bid=0.945, initial_spread=0.005,
            confluence_max_price=0.95, confluence_first_price=0.95),  # keep momentum runup at 0
        fill_nbbo={"bid": 0.945, "ask": 0.95, "spread": 0.005, "mid": 0.9475},
        pub_quote_ask=0.87,
    )
    assert reason is None, f"FEED-class should trade, got skip {reason}"
    assert traded is True


@pytest.mark.asyncio
async def test_dgnx_class_excess_runup_blocks_pub_to_recv():
    # recv ask 1.27, pub ask 1.0 -> 27% > 15% ceiling -> blocked
    reason, traded = await _run(
        _cm(confluence_score=6, initial_ask=1.27, initial_bid=1.265, initial_spread=0.005),
        fill_nbbo={"bid": 1.265, "ask": 1.27, "spread": 0.005, "mid": 1.2675},
        pub_quote_ask=1.0,
    )
    assert reason and reason.startswith("postfilter_pub_to_recv")
    assert traded is False


@pytest.mark.asyncio
async def test_no_activity_fails_entry_gate():
    # score 0 -> no STRENGTH/HC; surge+late mocked to None -> no_strength_or_surge_or_late
    reason, traded = await _run(_cm(confluence_score=0, confluence_price_excursion_pct=0.0))
    assert reason and reason.startswith("postfilter_no_strength_or_surge_or_late")
    assert traded is False
