"""
Quote fetcher for IBKR market data.
Pure infrastructure - fetches quotes and publishes events.
"""
import asyncio
import time
from typing import Optional, Dict, Any

from ib_insync import IB, Stock

from datetime import datetime

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from .events import QuoteReceivedEvent
from .infrastructure_models import InfrastructureQuoteData
from ...utils.brokerage.nbbo_formatters import build_nbbo_info

logger = get_logger(__name__)


class IBKRQuoteFetcher:
    """
    Fetches market quotes/NBBO from IBKR.
    
    Responsibilities:
    - Fetch real-time quotes for stocks
    - Manage quote snapshots
    - Publish QuoteReceivedEvent
    
    Does NOT:
    - Execute trades
    - Know about business logic
    """
    
    def __init__(self, event_bus: AsyncEventBus):
        """
        Initialize quote fetcher.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
        """
        self.event_bus = event_bus
        self._quote_snapshots: Dict[str, Dict[str, Any]] = {}
        
        logger.info("IBKRQuoteFetcher initialized")
    
    def get_last_quote_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get the most recently recorded NBBO snapshot for a symbol.
        
        Args:
            symbol: Stock ticker symbol
            
        Returns:
            Quote snapshot dictionary or None
        """
        return self._quote_snapshots.get(symbol)
    
    def _record_quote_snapshot(self, symbol: str, snapshot: Dict[str, Any]) -> None:
        """
        Store the most recent NBBO snapshot for a symbol.
        
        Args:
            symbol: Stock ticker symbol
            snapshot: Quote snapshot dictionary
        """
        try:
            clean_snapshot = {
                key: float(value) if isinstance(value, (int, float)) and value is not None else value
                for key, value in snapshot.items()
            }
        except Exception:
            clean_snapshot = snapshot
        
        self._quote_snapshots[symbol] = clean_snapshot
    
    async def get_realtime_price(
        self,
        ib: IB,
        contract: Stock,
        timeout_deadline: Optional[float] = None,
    ) -> Optional[float]:
        """
        Get real-time price for a stock contract.
        
        Args:
            ib: IBKR connection instance
            contract: Stock contract
            timeout_deadline: Optional timeout deadline
            
        Returns:
            Real-time price or None if unavailable
        """
        try:
            def time_left() -> Optional[float]:
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()
            
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Trade timed out before qualifying contract")
            
            logger.info(f"📊 Requesting IBKR real-time quote for {contract.symbol}...")
            
            # Request real-time market data type
            try:
                ib.reqMarketDataType(1)
            except Exception as exc:
                logger.warning("⚠️ Unable to request real-time market data type", error=str(exc))
            
            # Qualify contract
            qualify_coro = ib.qualifyContractsAsync(contract)
            if remaining is None:
                qualified_list = await qualify_coro
            else:
                qualified_list = await asyncio.wait_for(qualify_coro, timeout=max(remaining, 0))
            
            if not qualified_list:
                logger.error("❌ IBKR returned empty qualification list")
                return None
            
            [qualified] = qualified_list
            logger.debug("Qualified contract", contract=qualified)
            
            # Request market data
            ticker = ib.reqMktData(qualified, "", True, False)
            last_snapshot: Dict[str, Any] = {}
            
            # Wait for quote data
            for iteration in range(10):
                remaining = time_left()
                if remaining is not None and remaining <= 0:
                    break
                
                sleep_interval = 0.05 if remaining is None else min(0.05, max(remaining, 0))
                if sleep_interval > 0:
                    await asyncio.sleep(sleep_interval)
                
                # Extract quote data
                last_price = getattr(ticker, "last", None)
                bid = getattr(ticker, "bid", None)
                ask = getattr(ticker, "ask", None)
                close = getattr(ticker, "close", None)
                
                snapshot = {
                    "last": float(last_price) if last_price else None,
                    "bid": float(bid) if bid else None,
                    "ask": float(ask) if ask else None,
                    "close": float(close) if close else None,
                    "iteration": iteration,
                }
                
                # Build NBBO
                if snapshot.get("bid") is not None and snapshot.get("ask") is not None:
                    snapshot["mid"] = round((snapshot["bid"] + snapshot["ask"]) / 2.0, 4)
                    snapshot["spread"] = round(snapshot["ask"] - snapshot["bid"], 4)
                
                last_snapshot = snapshot
                
                # Try to get price from various sources
                if last_price and last_price > 0:
                    snapshot["price_used"] = float(last_price)
                    snapshot["price_source"] = "last"
                    self._record_quote_snapshot(contract.symbol, snapshot)
                    await self._publish_quote(contract.symbol, snapshot)
                    ib.cancelMktData(qualified)
                    return float(last_price)
                
                if bid and ask and bid > 0 and ask > 0:
                    mid_price = (bid + ask) / 2.0
                    snapshot["price_used"] = float(mid_price)
                    snapshot["price_source"] = "mid"
                    self._record_quote_snapshot(contract.symbol, snapshot)
                    await self._publish_quote(contract.symbol, snapshot)
                    ib.cancelMktData(qualified)
                    return float(mid_price)
                
                if close and close > 0:
                    snapshot["price_used"] = float(close)
                    snapshot["price_source"] = "close"
                    self._record_quote_snapshot(contract.symbol, snapshot)
                    await self._publish_quote(contract.symbol, snapshot)
                    ib.cancelMktData(qualified)
                    return float(close)
            
            # Cleanup
            ib.cancelMktData(qualified)
            
            # Timeout or no data
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                last_snapshot.setdefault("price_used", None)
                last_snapshot.setdefault("price_source", "timeout")
                self._record_quote_snapshot(contract.symbol, last_snapshot)
                logger.error(
                    "⏱️ Timeout waiting for IBKR quote",
                    ticker=contract.symbol,
                    snapshot=last_snapshot,
                )
                raise TimeoutError(
                    "Timeout waiting for IBKR quote (no last/bid/ask received)"
                )
            
            last_snapshot.setdefault("price_used", None)
            last_snapshot.setdefault("price_source", "unavailable")
            self._record_quote_snapshot(contract.symbol, last_snapshot)
            logger.error(
                "❌ IBKR quote unavailable (no last/bbo/close)",
                ticker=contract.symbol,
                snapshot=last_snapshot,
            )
            return None
        
        except TimeoutError:
            raise
        except Exception as exc:
            snapshot = {"price_used": None, "price_source": "error", "error": str(exc)}
            self._record_quote_snapshot(contract.symbol, snapshot)
            logger.error(f"❌ Error fetching IBKR quote for {contract.symbol}: {exc}")
            return None
    
    async def get_nbbo_snapshot(
        self,
        ib: IB,
        contract: Stock,
        timeout_deadline: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get NBBO snapshot for a stock contract (bid/ask only, no price).
        
        Args:
            ib: IBKR connection instance
            contract: Stock contract
            timeout_deadline: Optional timeout deadline
            
        Returns:
            NBBO dictionary with bid/ask/spread or None
        """
        try:
            def time_left() -> Optional[float]:
                if timeout_deadline is None:
                    return None
                return timeout_deadline - time.monotonic()
            
            remaining = time_left()
            if remaining is not None and remaining <= 0:
                return None
            
            # Qualify contract
            qualify_coro = ib.qualifyContractsAsync(contract)
            if remaining is None:
                qualified_list = await qualify_coro
            else:
                qualified_list = await asyncio.wait_for(qualify_coro, timeout=max(remaining, 0))
            
            if not qualified_list:
                return None
            
            [qualified] = qualified_list
            
            # Request market data
            ticker = ib.reqMktData(qualified, "", True, False)
            
            # Wait briefly for NBBO
            sleep_interval = 0.03 if remaining is None else min(0.03, max(remaining, 0))
            if sleep_interval > 0:
                await asyncio.sleep(sleep_interval)
            
            # Extract bid/ask
            bid = getattr(ticker, "bid", None)
            ask = getattr(ticker, "ask", None)
            
            # Build NBBO
            nbbo = build_nbbo_info(
                float(bid) if bid and bid > 0 else None,
                float(ask) if ask and ask > 0 else None,
                source="ladder_snapshot",
                fallback=self.get_last_quote_snapshot(contract.symbol),
            )
            
            ib.cancelMktData(qualified)
            
            if nbbo:
                await self._publish_quote(contract.symbol, nbbo)
            
            return nbbo
        
        except Exception as exc:
            logger.error(f"Error fetching NBBO snapshot for {contract.symbol}", error=str(exc))
            return None
    
    async def _publish_quote(self, symbol: str, nbbo: Dict[str, Any]) -> None:
        """Publish QuoteReceivedEvent with typed infrastructure model."""
        # Convert dict to typed InfrastructureQuoteData
        quote_data = InfrastructureQuoteData(
            bid=nbbo.get("bid", 0.0),
            ask=nbbo.get("ask", 0.0),
            last=nbbo.get("last"),
            volume=nbbo.get("volume"),
            spread=nbbo.get("spread")
        )
        
        event = QuoteReceivedEvent(
            symbol=symbol,
            nbbo=quote_data,  # ✅ Typed infrastructure model
            received_at=datetime.now()
        )
        await self.event_bus.publish("QuoteReceived", event.model_dump())
        logger.debug("Published QuoteReceived event", symbol=symbol)

