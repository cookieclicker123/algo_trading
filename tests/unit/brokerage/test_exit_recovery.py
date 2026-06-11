"""
Guaranteed-exit recovery tests (TGL 2026-06-11 incident).

The old chase-the-bid SELL path ABORTED when the bid fell >5% below the initial
bid, publishing TradeFailed and abandoning the position — while a resting limit
order from an earlier chase attempt could still fill later with no TradeExecuted
event (no exit stats telegram, orphaned tracking).

These tests drive the REAL executor `execute()` SELL path with a scripted
trading client and assert the new invariant: a position-closing SELL terminates
with a TradeExecuted event (exit message always fires), never a silent abandon.
"""
import asyncio
import pytest

import newsflash.infra.brokerage.trade_executor_extended_hours as executor_module
from newsflash.infra.brokerage.trade_executor_extended_hours import (
    AlpacaExtendedHoursTradeExecutor,
)
from newsflash.models.base_models import TradeRequest


# ── Stubs ────────────────────────────────────────────────────────────────────
class _Order:
    def __init__(self, order_id, status="new", filled_qty=None, filled_avg_price=None):
        self.id = order_id
        self.status = status
        self.filled_qty = filled_qty
        self.filled_avg_price = filled_avg_price
        self.side = "sell"
        self.qty = filled_qty


class ScriptedTradingClient:
    """Each submit_order returns ord1, ord2, ... get_order_by_id pops scripted
    states per order (last state repeats). cancel marks canceled unless filled."""

    def __init__(self, order_scripts):
        self.order_scripts = order_scripts  # {order_n: [list of _Order states]}
        self.submitted = []                 # LimitOrderRequest objects in order
        self.cancelled = []
        self._n = 0

    def get_orders(self, filter=None):
        return []

    def submit_order(self, order_data):
        self._n += 1
        order_id = f"ord{self._n}"
        self.submitted.append(order_data)
        return _Order(order_id)

    def get_order_by_id(self, order_id):
        states = self.order_scripts.get(order_id, [_Order(order_id, "new")])
        if len(states) > 1:
            return states.pop(0)
        return states[0]

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)
        states = self.order_scripts.get(order_id)
        if states and states[-1].status not in ("filled",):
            states[-1].status = "canceled"


class ScriptedQuoteFetcher:
    """get_nbbo_snapshot returns scripted snapshots in order (last repeats)."""

    def __init__(self, snapshots, price=6.15):
        self.snapshots = list(snapshots)
        self.price = price
        self.stream_manager = None

    async def get_nbbo_snapshot(self, ticker):
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    async def get_realtime_price(self, ticker):
        return self.price


class RecordingEventBus:
    def __init__(self):
        self.events = []

    async def publish(self, name, payload):
        self.events.append((name, payload))


def _nbbo(bid, ask=None):
    ask = ask or round(bid * 1.02, 2)
    return {"bid": bid, "ask": ask, "spread": round(ask - bid, 4),
            "spread_pct": round((ask - bid) / ((ask + bid) / 2) * 100, 2),
            "mid": round((ask + bid) / 2, 4), "bid_size": 100, "ask_size": 100}


def _sell_request():
    return TradeRequest(ticker="TGL", action="SELL", shares=280.0, amount_usd=None)


async def _run(executor, request):
    return await executor.execute(
        request, session="premarket", timing_info={}, timeout_deadline=None,
        metadata={"exit_reason": "stop_loss"},
    )


def _events(bus, name):
    return [p for n, p in bus.events if n == name]


@pytest.fixture
def fast_recovery(monkeypatch):
    """Shrink recovery wait times so tests run in ~1s."""
    monkeypatch.setattr(executor_module, "EXIT_RECOVERY_RESTING_SECONDS", 0.05)
    monkeypatch.setattr(executor_module, "EXIT_RECOVERY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(executor_module, "EXIT_CAPITULATION_MAX_ATTEMPTS", 3)


# ── Tests ────────────────────────────────────────────────────────────────────
def test_floor_breach_takeover_captures_late_fill_of_resting_order(fast_recovery):
    """TGL replay: chase attempt 1 rests at bid 6.15, attempt 2 sees bid 5.72
    (below the 5.84 floor). The old code aborted here — the resting order then
    filled on a bounce with NO event. New code: recovery takes over the resting
    order, sees the fill, and publishes TradeExecuted."""
    client = ScriptedTradingClient({
        # ord1 = chase attempt 1: open during chase, FILLED by the time recovery takes over
        "ord1": [_Order("ord1", "new"),
                 _Order("ord1", "filled", filled_qty=280.0, filled_avg_price=6.15)],
    })
    fetcher = ScriptedQuoteFetcher([_nbbo(6.15), _nbbo(5.72)])
    bus = RecordingEventBus()
    executor = AlpacaExtendedHoursTradeExecutor(bus, fetcher, client)

    result = asyncio.run(_run(executor, _sell_request()))

    assert result["success"] is True
    assert result["fill_price"] == pytest.approx(6.15)
    assert result["shares"] == 280.0
    assert result["order_type"] == "CHASE_LIMIT_RECOVERY"
    executed = _events(bus, "TradeExecuted")
    assert len(executed) == 1 and executed[0]["success"] is True
    assert not _events(bus, "TradeFailed")


def test_floor_breach_resting_at_floor_fills_on_bounce(fast_recovery):
    """Takeover order is dead; Phase A rests a limit at the floor and it fills."""
    client = ScriptedTradingClient({
        "ord1": [_Order("ord1", "new")],  # chase order never fills, gets cancelled
        # ord2 = Phase A resting limit at the floor: fills on first poll
        "ord2": [_Order("ord2", "filled", filled_qty=280.0, filled_avg_price=5.84)],
    })
    fetcher = ScriptedQuoteFetcher([_nbbo(6.15), _nbbo(5.72)])
    bus = RecordingEventBus()
    executor = AlpacaExtendedHoursTradeExecutor(bus, fetcher, client)

    result = asyncio.run(_run(executor, _sell_request()))

    assert result["success"] is True
    assert result["fill_price"] == pytest.approx(5.84)
    # resting limit was placed at the floor (5% under initial bid 6.15)
    resting = client.submitted[1]
    assert float(resting.limit_price) == pytest.approx(round(6.15 * 0.95, 2))
    assert len(_events(bus, "TradeExecuted")) == 1
    assert not _events(bus, "TradeFailed")


def test_floor_breach_capitulation_sells_at_bid_with_no_floor(fast_recovery):
    """Resting phase times out unfilled; Phase B chases the bid below the old
    floor until flat. The position can no longer be orphaned."""
    client = ScriptedTradingClient({
        "ord1": [_Order("ord1", "new")],                    # chase order, cancelled
        "ord2": [_Order("ord2", "new")],                    # resting at floor, never fills
        "ord3": [_Order("ord3", "filled", filled_qty=280.0, filled_avg_price=5.60)],
    })
    fetcher = ScriptedQuoteFetcher([_nbbo(6.15), _nbbo(5.72), _nbbo(5.60)])
    bus = RecordingEventBus()
    executor = AlpacaExtendedHoursTradeExecutor(bus, fetcher, client)

    result = asyncio.run(_run(executor, _sell_request()))

    assert result["success"] is True
    assert result["fill_price"] == pytest.approx(5.60)
    # capitulation order placed AT the bid, below the old 5.84 floor
    assert float(client.submitted[2].limit_price) == pytest.approx(5.60)
    assert len(_events(bus, "TradeExecuted")) == 1
    assert not _events(bus, "TradeFailed")


def test_unrecoverable_failure_is_loud_about_open_position(fast_recovery):
    """If every order submission errors (API outage), the TradeFailed event must
    say POSITION STILL OPEN so the telegram is unambiguous."""
    class ExplodingClient(ScriptedTradingClient):
        def submit_order(self, order_data):
            raise RuntimeError("api down")

    client = ExplodingClient({})
    fetcher = ScriptedQuoteFetcher([_nbbo(6.15), _nbbo(5.72)])
    bus = RecordingEventBus()
    executor = AlpacaExtendedHoursTradeExecutor(bus, fetcher, client)

    result = asyncio.run(_run(executor, _sell_request()))

    assert result["success"] is False
    assert "POSITION STILL OPEN" in result["error"]
    failed = _events(bus, "TradeFailed")
    assert len(failed) == 1 and "POSITION STILL OPEN" in failed[0]["error"]
