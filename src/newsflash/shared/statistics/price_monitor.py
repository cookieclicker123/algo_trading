"""
Price monitor module - handles 10-minute price tracking.

Extracted from RecallStatsEngine to separate price tracking from surge detection.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Dict, Protocol

try:
    from alpaca.data import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockTradesRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
except ImportError:
    StockHistoricalDataClient = None
    StockBarsRequest = None
    StockTradesRequest = None
    TimeFrame = None
    DataFeed = None

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class QuoteFetcherProtocol(Protocol):
    """Protocol for quote fetching."""
    async def get_nbbo_snapshot(self, ticker: str) -> Optional[Dict[str, Any]]: ...


class RepositoryProtocol(Protocol):
    """Protocol for record updates."""
    async def update_recall_record(
        self,
        article_id: str,
        updates: Dict[str, Any],
        session: str,
        date: datetime
    ) -> bool: ...


class PriceMonitor:
    """
    Monitors ticker prices for 10 minutes and tracks price action.

    Responsibilities:
    - Wait 10 minutes after article publication
    - Track highest price reached (with timestamp)
    - Track lowest price (max adverse excursion)
    - Calculate final P&L
    - Update record with price tracking data

    Design:
    - Receives dependencies via protocols (testable)
    - Monitors primary ticker only (avoids ticker mismatch)
    """

    def __init__(
        self,
        market_data_client: Any,  # StockHistoricalDataClient
        quote_fetcher: QuoteFetcherProtocol,
        repository: RepositoryProtocol,
        monitoring_tasks: Dict[str, asyncio.Task],
        monitoring_lock: asyncio.Lock
    ):
        """
        Initialize price monitor.

        Args:
            market_data_client: Alpaca market data client for bar data
            quote_fetcher: Quote fetcher for NBBO snapshots
            repository: Statistics repository for record updates
            monitoring_tasks: Shared dict of monitoring tasks
            monitoring_lock: Lock protecting monitoring_tasks dict
        """
        self.market_data_client = market_data_client
        self.quote_fetcher = quote_fetcher
        self.repository = repository
        self._monitoring_tasks = monitoring_tasks
        self._monitoring_lock = monitoring_lock

    async def monitor_price(
        self,
        article_id: str,
        tickers: list[str],
        initial_nbbos: Dict[str, Dict[str, Any]],
        session: str,
        received_at: datetime,
        published_at: datetime
    ) -> None:
        """
        Monitor ticker price for 10 minutes and track price action.

        Args:
            article_id: Article ID
            tickers: List of ticker symbols
            initial_nbbos: Initial NBBO snapshots
            session: Market session
            received_at: When article was received
            published_at: When article was published
        """
        try:
            # Wait 10 minutes
            hold_duration_seconds = 600
            await asyncio.sleep(hold_duration_seconds)

            # Only monitor primary ticker (avoids ticker mismatch issues)
            target_ticker = tickers[0] if tickers else None
            if not target_ticker or not initial_nbbos.get(target_ticker):
                return

            initial_nbbo = initial_nbbos[target_ticker]
            entry_price = initial_nbbo.get("ask") or initial_nbbo.get("mid")
            if not entry_price or entry_price <= 0:
                return

            # Get monitoring start time
            monitoring_start = published_at
            if monitoring_start.tzinfo is None:
                monitoring_start = monitoring_start.replace(tzinfo=timezone.utc)

            # Fetch and analyze price data
            highest_price_data = None
            max_adverse_data = None

            if self.market_data_client and StockBarsRequest:
                bar_analysis = await self._analyze_price_bars(
                    target_ticker, monitoring_start, entry_price, article_id
                )
                if bar_analysis:
                    highest_price_data, max_adverse_data = bar_analysis

            # Get final NBBO at 10 minutes
            final_nbbo = await self.quote_fetcher.get_nbbo_snapshot(target_ticker)
            if not final_nbbo:
                return

            # Calculate P&L
            price_check = self._calculate_pnl(initial_nbbo, final_nbbo)
            if not price_check:
                return

            # Ensure highest price is at least 10-minute ask
            highest_price_data = self._reconcile_highest_price(
                highest_price_data, price_check, entry_price, target_ticker, article_id
            )

            # Build updates
            updates = {
                "price_check_10min": price_check,
                "price_checked_at": datetime.now()
            }

            if highest_price_data:
                updates["highest_price_during_hold"] = highest_price_data

            if max_adverse_data:
                updates["max_adverse_excursion"] = max_adverse_data

            # Update record
            updated = await self.repository.update_recall_record(
                article_id=article_id,
                updates=updates,
                session=session,
                date=received_at
            )

            if updated:
                logger.info(
                    "PriceMonitor: 10-minute price check completed",
                    article_id=article_id,
                    ticker=target_ticker,
                    actual_pnl=price_check.get("actual_pnl"),
                    highest_price=highest_price_data.get("price") if highest_price_data else None,
                    max_adverse=max_adverse_data.get("price") if max_adverse_data else None
                )
            else:
                logger.warning(
                    "PriceMonitor: Failed to update record",
                    article_id=article_id
                )

        except asyncio.CancelledError:
            logger.debug("PriceMonitor: Task cancelled", article_id=article_id)
        except Exception as e:
            logger.error(
                "PriceMonitor: Error monitoring price",
                article_id=article_id,
                error=str(e),
                exc_info=True
            )
        finally:
            async with self._monitoring_lock:
                self._monitoring_tasks.pop(article_id, None)

    async def _analyze_price_bars(
        self,
        ticker: str,
        monitoring_start: datetime,
        entry_price: float,
        article_id: str
    ) -> Optional[tuple[Optional[Dict], Optional[Dict]]]:
        """
        Fetch and analyze minute bars for price tracking.

        Returns:
            (highest_price_data, max_adverse_data) or None on error
        """
        try:
            bars_end = monitoring_start + timedelta(minutes=15)
            bars_request = StockBarsRequest(
                symbol_or_symbols=[ticker],
                timeframe=TimeFrame.Minute,
                start=monitoring_start,
                end=bars_end,
                feed=DataFeed.SIP
            )
            bars_response = self.market_data_client.get_stock_bars(bars_request)

            if not bars_response or not bars_response.data or ticker not in bars_response.data:
                return None

            # Find highest and lowest prices
            highest_price, minute_with_highest = self._find_extreme_price(
                bars_response.data[ticker], find_highest=True
            )
            lowest_price, minute_with_lowest = self._find_extreme_price(
                bars_response.data[ticker], find_highest=False
            )

            # Get exact timestamps from trade data
            highest_price_data = None
            if highest_price and minute_with_highest:
                exact_ts = await self._get_exact_timestamp(
                    ticker, minute_with_highest, find_highest=True
                )
                highest_price_data = self._build_price_data(
                    highest_price, exact_ts or minute_with_highest, entry_price, ticker, is_gain=True
                )

            max_adverse_data = None
            if lowest_price and minute_with_lowest:
                exact_ts = await self._get_exact_timestamp(
                    ticker, minute_with_lowest, find_highest=False
                )
                max_adverse_data = self._build_price_data(
                    lowest_price, exact_ts or minute_with_lowest, entry_price, ticker, is_gain=False
                )

            return (highest_price_data, max_adverse_data)

        except Exception as e:
            logger.debug(
                "PriceMonitor: Error fetching minute bars",
                article_id=article_id,
                ticker=ticker,
                error=str(e)
            )
            return None

    def _find_extreme_price(
        self,
        bars: list,
        find_highest: bool
    ) -> tuple[Optional[float], Optional[datetime]]:
        """Find highest or lowest price from bars."""
        extreme_price = None
        extreme_timestamp = None

        for bar in bars:
            bar_price = float(bar.high if find_highest else bar.low) if (bar.high if find_highest else bar.low) else None
            bar_timestamp = bar.timestamp
            if bar_timestamp.tzinfo is None:
                bar_timestamp = bar_timestamp.replace(tzinfo=timezone.utc)

            if bar_price:
                if extreme_price is None or (bar_price > extreme_price if find_highest else bar_price < extreme_price):
                    extreme_price = bar_price
                    extreme_timestamp = bar_timestamp

        return extreme_price, extreme_timestamp

    async def _get_exact_timestamp(
        self,
        ticker: str,
        minute_timestamp: datetime,
        find_highest: bool
    ) -> Optional[datetime]:
        """Get exact timestamp by fetching trades for the minute."""
        if not StockTradesRequest:
            return None

        try:
            minute_start = minute_timestamp.replace(second=0, microsecond=0)
            minute_end = minute_start + timedelta(minutes=1)

            trades_request = StockTradesRequest(
                symbol_or_symbols=[ticker],
                start=minute_start,
                end=minute_end,
                feed=DataFeed.SIP
            )
            trades_response = self.market_data_client.get_stock_trades(trades_request)

            if not trades_response or not trades_response.data or ticker not in trades_response.data:
                return None

            extreme_price = None
            exact_ts = None

            for trade in trades_response.data[ticker]:
                trade_price = float(trade.price) if trade.price else None
                if trade_price:
                    if extreme_price is None or (trade_price > extreme_price if find_highest else trade_price < extreme_price):
                        extreme_price = trade_price
                        trade_ts = trade.timestamp
                        if trade_ts.tzinfo is None:
                            trade_ts = trade_ts.replace(tzinfo=timezone.utc)
                        exact_ts = trade_ts

            return exact_ts
        except Exception:
            return None

    def _build_price_data(
        self,
        price: float,
        timestamp: datetime,
        entry_price: float,
        ticker: str,
        is_gain: bool
    ) -> Dict[str, Any]:
        """Build price tracking data structure."""
        percent_change = ((price - entry_price) / entry_price) * 100

        data = {
            "price": price,
            "timestamp": timestamp.isoformat(),
            "minute": timestamp.minute,
            "second": timestamp.second,
            "ticker": ticker
        }

        if is_gain:
            data["percent_gain_from_entry"] = round(percent_change, 2)
        else:
            data["percent_loss_from_entry"] = round(percent_change, 2)
            stop_loss_pct = abs(round(percent_change, 3))
            data["stop_loss_percentage"] = stop_loss_pct
            data["stop_loss_dollar_per_share"] = round(entry_price * (stop_loss_pct / 100), 4)

        return data

    def _calculate_pnl(
        self,
        initial_nbbo: Dict[str, Any],
        final_nbbo: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Calculate P&L from initial and final NBBO."""
        initial_ask = initial_nbbo.get("ask")
        initial_mid = initial_nbbo.get("mid")
        final_bid = final_nbbo.get("bid")
        final_mid = final_nbbo.get("mid")

        if not initial_ask or initial_ask <= 0:
            return None

        # Actual P&L: buy at ask, sell at bid
        actual_pnl = None
        if final_bid:
            actual_pnl = ((final_bid - initial_ask) / initial_ask) * 100

        # Mid price change for reference
        mid_price_change = None
        if initial_mid and final_mid and initial_mid > 0:
            mid_price_change = ((final_mid - initial_mid) / initial_mid) * 100

        percent_change = actual_pnl if actual_pnl is not None else mid_price_change
        if percent_change is None:
            return None

        return {
            **final_nbbo,
            "percent_change": percent_change,
            "mid_price_change": mid_price_change,
            "actual_pnl": actual_pnl,
            "moved_1_percent": percent_change >= 1.0
        }

    def _reconcile_highest_price(
        self,
        highest_price_data: Optional[Dict],
        price_check: Dict[str, Any],
        entry_price: float,
        ticker: str,
        article_id: str
    ) -> Optional[Dict[str, Any]]:
        """Ensure highest price is at least the 10-minute ask."""
        final_ask = price_check.get("ask")
        if not final_ask:
            return highest_price_data

        current_highest = highest_price_data.get("price") if highest_price_data else None

        if current_highest is None or final_ask > current_highest:
            # Update to use 10-minute ask
            highest_gain_pct = ((final_ask - entry_price) / entry_price) * 100
            price_check_time = datetime.now(timezone.utc)

            logger.debug(
                "PriceMonitor: Updated highest price to match 10-minute ask",
                article_id=article_id,
                ticker=ticker,
                previous=current_highest,
                new=final_ask
            )

            return {
                "price": final_ask,
                "timestamp": price_check_time.isoformat(),
                "percent_gain_from_entry": round(highest_gain_pct, 2),
                "minute": price_check_time.minute,
                "second": price_check_time.second,
                "ticker": ticker
            }

        return highest_price_data
