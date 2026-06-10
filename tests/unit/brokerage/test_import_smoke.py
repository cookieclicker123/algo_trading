"""
Import smoke tests for the brokerage package.

The cheapest, highest-value guard against a whole class of silent bugs: a module
that fails to import (a NameError at module scope, a bad/circular import, a typo in
a constant) would otherwise only surface at runtime — in production, mid-trade.
Importing every brokerage module here makes that failure loud and immediate in CI.
"""
import importlib
import pytest

BROKERAGE_MODULES = [
    "newsflash.services.brokerage.postfilter_engine",
    "newsflash.services.brokerage.auto_trade",
    "newsflash.infra.brokerage.utils",
    "newsflash.infra.brokerage.trade_executor_market_hours",
    "newsflash.infra.brokerage.trade_executor_extended_hours",
]


@pytest.mark.parametrize("modname", BROKERAGE_MODULES)
def test_module_imports(modname):
    importlib.import_module(modname)


def test_postfilter_engine_public_api_is_stable():
    """The engine's public surface is a contract — auto_trade and the backtest both
    depend on these exact names/shapes. If one changes, this fails loudly."""
    import newsflash.services.brokerage.postfilter_engine as eng
    for name in ("PostfilterInputs", "PostfilterConfig", "PostfilterDecision",
                 "evaluate_microstructure_postfilters", "strong_signal_runup_bypass",
                 "STRONG_SIGNAL_RUNUP_CEILING_PCT"):
        assert hasattr(eng, name), f"postfilter_engine.{name} missing — public API changed"

    # the helper auto_trade imports must exist with the right names
    from newsflash.services.brokerage.auto_trade import (  # noqa: F401
        PostfilterInputs as _PI,
        evaluate_microstructure_postfilters as _ev,
    )
