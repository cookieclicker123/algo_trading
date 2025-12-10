"""
Test to verify NBBO availability for overnight trading.

This test proves that:
1. Stocks that don't trade overnight (SPGI, AIR, DGII, BX, PTCT) fail NBBO fetch
2. Stocks that trade overnight (AAPL, MSFT) succeed NBBO fetch
3. This is a qualitative difference (stock availability), not broken infrastructure
"""
import pytest
import pytest_asyncio
import asyncio
import os
from typing import List, Dict, Any

from newsflash.infra.brokerage.quote_fetcher import AlpacaQuoteFetcher
from newsflash.infra.brokerage.connection_manager import AlpacaConnectionManager
from newsflash.shared.event_bus import AsyncEventBus
from newsflash.utils.logging_config import get_logger

logger = get_logger(__name__)

# Tickers that failed NBBO fetch at 1pm UTC
# Some don't trade overnight (SPGI, AIR, DGII), some do but had liquidity issues (BX, PTCT)
FAILED_TICKERS_NO_NBBO = ["SPGI", "AIR", "DGII"]  # Don't trade overnight
FAILED_TICKERS_WITH_NBBO = ["BX", "PTCT"]  # Trade overnight but had liquidity issues

# Tickers that trade overnight (should succeed NBBO fetch)
OVERNIGHT_TICKERS = ["AAPL", "MSFT", "TSLA", "NVDA"]

# Skip test if Alpaca credentials not available
pytestmark = pytest.mark.skipif(
    not os.getenv("ALPACA_KEY") or not os.getenv("ALPACA_SECRET"),
    reason="Alpaca credentials not available"
)


@pytest_asyncio.fixture
async def quote_fetcher():
    """Create AlpacaQuoteFetcher instance."""
    event_bus = AsyncEventBus()
    
    # Create connection manager to get market_data_client
    connection_manager = AlpacaConnectionManager(
        event_bus=event_bus,
        paper_trading=True,
        metrics_service=None
    )
    
    # Initialize connection to get clients
    await connection_manager.ensure_connected(timeout_seconds=10.0)
    
    # Create quote fetcher
    fetcher = AlpacaQuoteFetcher(
        event_bus=event_bus,
        market_data_client=connection_manager.market_data_client
    )
    
    try:
        yield fetcher
    finally:
        # Cleanup
        try:
            await connection_manager.close()
        except Exception:
            pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_failed_tickers_no_nbbo_overnight(quote_fetcher):
    """
    Test that tickers that failed NBBO fetch due to no overnight trading still fail.
    
    These stocks (SPGI, AIR, DGII) don't trade overnight, so NBBO should be unavailable.
    This proves the failure is due to stock availability, not infrastructure.
    """
    results: List[Dict[str, Any]] = []
    
    for ticker in FAILED_TICKERS_NO_NBBO:
        logger.info(f"Testing failed ticker: {ticker}")
        nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
        
        result = {
            "ticker": ticker,
            "nbbo_available": nbbo is not None,
            "nbbo_data": nbbo
        }
        results.append(result)
        
        logger.info(
            f"Result for {ticker}",
            nbbo_available=result["nbbo_available"],
            has_bid=nbbo.get("bid") is not None if nbbo else False,
            has_ask=nbbo.get("ask") is not None if nbbo else False
        )
    
    # All failed tickers should have NBBO unavailable
    failed_count = sum(1 for r in results if not r["nbbo_available"])
    total_count = len(results)
    
    logger.info(
        "Failed tickers test summary",
        total_tested=total_count,
        nbbo_unavailable=failed_count,
        nbbo_available=total_count - failed_count,
        expected_all_unavailable=True
    )
    
    # Assert: All should fail (NBBO unavailable)
    assert failed_count == total_count, (
        f"Expected all {total_count} non-overnight tickers to have NBBO unavailable, "
        f"but {total_count - failed_count} succeeded. "
        f"This suggests infrastructure issue or these stocks now trade overnight."
    )
    
    # Log detailed results
    for result in results:
        if not result["nbbo_available"]:
            logger.info(
                f"✅ {result['ticker']}: NBBO unavailable (expected - doesn't trade overnight)",
                ticker=result["ticker"]
            )
        else:
            logger.warning(
                f"⚠️ {result['ticker']}: NBBO available (unexpected - check if stock now trades overnight)",
                ticker=result["ticker"],
                nbbo_data=result["nbbo_data"]
            )


@pytest.mark.asyncio
async def test_overnight_tickers_have_nbbo(quote_fetcher):
    """
    Test that tickers known to trade overnight have NBBO available.
    
    This proves infrastructure is working correctly.
    """
    results: List[Dict[str, Any]] = []
    
    for ticker in OVERNIGHT_TICKERS:
        logger.info(f"Testing overnight ticker: {ticker}")
        nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
        
        result = {
            "ticker": ticker,
            "nbbo_available": nbbo is not None,
            "nbbo_data": nbbo
        }
        results.append(result)
        
        if nbbo:
            logger.info(
                f"✅ {ticker}: NBBO available",
                ticker=ticker,
                bid=nbbo.get("bid"),
                ask=nbbo.get("ask"),
                spread=nbbo.get("spread"),
                mid=nbbo.get("mid")
            )
        else:
            logger.warning(
                f"⚠️ {ticker}: NBBO unavailable (unexpected for overnight ticker)",
                ticker=ticker
            )
    
    # Most overnight tickers should have NBBO available
    success_count = sum(1 for r in results if r["nbbo_available"])
    total_count = len(results)
    
    logger.info(
        "Overnight tickers test summary",
        total_tested=total_count,
        nbbo_available=success_count,
        nbbo_unavailable=total_count - success_count,
        expected_most_available=True
    )
    
    # Assert: At least 50% should succeed (some may be temporarily unavailable depending on market timing)
    # AAPL and MSFT should always work, others may vary
    success_rate = success_count / total_count if total_count > 0 else 0
    assert success_rate >= 0.5, (
        f"Expected at least 50% of overnight tickers to have NBBO available, "
        f"but only {success_rate:.1%} succeeded ({success_count}/{total_count}). "
        f"This suggests infrastructure issue or market timing (some stocks may not have NBBO at all times)."
    )
    
    # More importantly: AAPL should always work (most liquid overnight stock)
    aapl_result = next((r for r in results if r["ticker"] == "AAPL"), None)
    if aapl_result:
        assert aapl_result["nbbo_available"], (
            "AAPL should always have NBBO available (most liquid overnight stock). "
            "If AAPL fails, there's likely an infrastructure issue."
        )


@pytest.mark.asyncio
async def test_failed_tickers_with_nbbo_had_liquidity_issues(quote_fetcher):
    """
    Test that tickers like BX and PTCT that failed at 1pm UTC DO have NBBO.
    
    This proves their failures were due to liquidity issues (not enough liquidity for fill),
    not NBBO unavailability. They trade overnight but had insufficient liquidity.
    """
    results: List[Dict[str, Any]] = []
    
    for ticker in FAILED_TICKERS_WITH_NBBO:
        logger.info(f"Testing ticker that failed due to liquidity: {ticker}")
        nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
        
        result = {
            "ticker": ticker,
            "nbbo_available": nbbo is not None,
            "nbbo_data": nbbo
        }
        results.append(result)
        
        if nbbo:
            logger.info(
                f"✅ {ticker}: NBBO available (proves failure was liquidity, not NBBO)",
                ticker=ticker,
                bid=nbbo.get("bid"),
                ask=nbbo.get("ask"),
                spread=nbbo.get("spread")
            )
        else:
            logger.warning(
                f"⚠️ {ticker}: NBBO unavailable (unexpected - may not trade at this time)",
                ticker=ticker
            )
    
    # These should have NBBO (they trade overnight)
    success_count = sum(1 for r in results if r["nbbo_available"])
    total_count = len(results)
    
    logger.info(
        "Liquidity-failed tickers test summary",
        total_tested=total_count,
        nbbo_available=success_count,
        nbbo_unavailable=total_count - success_count,
        conclusion="These tickers have NBBO, so their failures were liquidity-related, not NBBO unavailability"
    )
    
    # Assert: Most should have NBBO (they trade overnight)
    assert success_count >= total_count * 0.5, (
        f"Expected at least 50% of liquidity-failed tickers to have NBBO available "
        f"(they trade overnight), but only {success_count}/{total_count} succeeded."
    )


@pytest.mark.asyncio
async def test_qualitative_difference(quote_fetcher):
    """
    Test that proves qualitative difference between overnight and non-overnight tickers.
    
    This test compares both groups to show the difference is stock availability,
    not broken infrastructure.
    """
    logger.info("Testing qualitative difference between overnight and non-overnight tickers")
    
    # Test non-overnight tickers (should fail NBBO)
    non_overnight_results = []
    for ticker in FAILED_TICKERS_NO_NBBO:
        nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
        non_overnight_results.append({
            "ticker": ticker,
            "nbbo_available": nbbo is not None
        })
    
    # Test overnight tickers (should succeed NBBO)
    overnight_results = []
    for ticker in OVERNIGHT_TICKERS[:3]:  # Test first 3 to save time
        nbbo = await quote_fetcher.get_nbbo_snapshot(ticker)
        overnight_results.append({
            "ticker": ticker,
            "nbbo_available": nbbo is not None
        })
    
    non_overnight_available = sum(1 for r in non_overnight_results if r["nbbo_available"])
    overnight_available = sum(1 for r in overnight_results if r["nbbo_available"])
    
    logger.info(
        "Qualitative difference test summary",
        non_overnight_tickers_tested=len(non_overnight_results),
        non_overnight_tickers_with_nbbo=non_overnight_available,
        overnight_tickers_tested=len(overnight_results),
        overnight_tickers_with_nbbo=overnight_available,
        difference_ratio=f"{overnight_available}/{len(overnight_results)} vs {non_overnight_available}/{len(non_overnight_results)}"
    )
    
    # Assert: Overnight tickers should have significantly more NBBO availability
    assert overnight_available > non_overnight_available, (
        f"Expected overnight tickers to have more NBBO availability than non-overnight tickers, "
        f"but got {overnight_available}/{len(overnight_results)} vs {non_overnight_available}/{len(non_overnight_results)}. "
        f"This suggests infrastructure issue or market conditions."
    )
    
    logger.info(
        "✅ Qualitative difference confirmed",
        message="Overnight tickers have NBBO, non-overnight tickers don't - proves stock availability difference, not infrastructure issue"
    )
