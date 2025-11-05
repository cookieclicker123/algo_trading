"""
Background price tracking service for audit trail.
Tracks price for 20 minutes after trade placement to analyze optimal exit timing.
"""
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from ..utils.logging_config import get_logger
from ..services.ibkr_trading_service import IBKRTradingService
from ib_insync import Stock

logger = get_logger(__name__)


class PriceTrackingService:
    """
    Background service to track price for 20 minutes after trade placement.
    Updates classification audit trail with minute-by-minute price data.
    """
    
    def __init__(self, ibkr_service: IBKRTradingService, audit_trail):
        """
        Initialize price tracking service.
        
        Args:
            ibkr_service: IBKRTradingService instance for price data
            audit_trail: ClassificationAuditTrail instance for updating entries
        """
        self.ibkr_service = ibkr_service
        self.audit_trail = audit_trail
        self.active_trackers: Dict[str, asyncio.Task] = {}
        logger.info("PriceTrackingService initialized")
    
    async def start_tracking(
        self,
        article_id: str,
        ticker: str,
        trade_placed_at: datetime
    ) -> None:
        """
        Start tracking price for 20 minutes after trade placement.
        
        Args:
            article_id: Article ID to track
            ticker: Stock ticker symbol
            trade_placed_at: When the trade was placed
        """
        # Create unique tracker ID
        tracker_id = f"{article_id}_{ticker}_{trade_placed_at.isoformat()}"
        
        # Check if already tracking
        if tracker_id in self.active_trackers:
            logger.warning("Price tracking already active", tracker_id=tracker_id)
            return
        
        # Start background tracking task
        task = asyncio.create_task(
            self._track_price_for_20_minutes(article_id, ticker, trade_placed_at, tracker_id)
        )
        self.active_trackers[tracker_id] = task
        
        logger.info(
            "Started price tracking",
            article_id=article_id,
            ticker=ticker,
            tracker_id=tracker_id
        )
    
    async def _track_price_for_20_minutes(
        self,
        article_id: str,
        ticker: str,
        trade_placed_at: datetime,
        tracker_id: str
    ) -> None:
        """
        Track price every minute for 20 minutes.
        
        Args:
            article_id: Article ID
            ticker: Stock ticker symbol
            trade_placed_at: When trade was placed
            tracker_id: Unique tracker ID
        """
        try:
            price_history = {}
            
            # Track for 20 minutes (minute 1 to minute 20)
            for minute in range(1, 21):
                # Calculate target time for this minute
                target_time = trade_placed_at + timedelta(minutes=minute)
                wait_seconds = (target_time - datetime.now()).total_seconds()
                
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                elif wait_seconds < -60:
                    # More than a minute late - skip this minute
                    logger.warning(
                        "Skipping price tracking minute (too late)",
                        minute=minute,
                        delay_seconds=abs(wait_seconds)
                    )
                    continue
                
                # Fetch current price
                try:
                    price = await self._get_current_price(ticker)
                    if price:
                        price_history[f"minute_{minute}"] = {
                            "price": price,
                            "timestamp": datetime.now().isoformat(),
                            "minutes_since_trade": minute
                        }
                        
                        # Update audit trail with latest price data (non-blocking)
                        try:
                            self.audit_trail.update_price_history(article_id, ticker, price_history)
                        except Exception as e:
                            logger.error("Failed to update price history in audit trail", error=str(e))
                        
                        logger.debug(
                            "Price tracked",
                            article_id=article_id,
                            ticker=ticker,
                            minute=minute,
                            price=price
                        )
                    else:
                        logger.warning(
                            "Failed to fetch price",
                            article_id=article_id,
                            ticker=ticker,
                            minute=minute
                        )
                        # Still record with None to show we tried
                        price_history[f"minute_{minute}"] = {
                            "price": None,
                            "timestamp": datetime.now().isoformat(),
                            "minutes_since_trade": minute,
                            "error": "Price fetch failed"
                        }
                except Exception as e:
                    logger.error(
                        "Error fetching price",
                        article_id=article_id,
                        ticker=ticker,
                        minute=minute,
                        error=str(e)
                    )
                    price_history[f"minute_{minute}"] = {
                        "price": None,
                        "timestamp": datetime.now().isoformat(),
                        "minutes_since_trade": minute,
                        "error": str(e)
                    }
            
            logger.info(
                "Price tracking completed",
                article_id=article_id,
                ticker=ticker,
                minutes_tracked=len(price_history)
            )
            
        except asyncio.CancelledError:
            logger.info("Price tracking cancelled", tracker_id=tracker_id)
        except Exception as e:
            logger.error(
                "Error in price tracking",
                article_id=article_id,
                ticker=ticker,
                error=str(e),
                exc_info=True
            )
        finally:
            # Remove from active trackers
            if tracker_id in self.active_trackers:
                del self.active_trackers[tracker_id]
    
    async def _get_current_price(self, ticker: str) -> Optional[float]:
        """
        Get current price from IBKR.
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            Current price or None if unavailable
        """
        try:
            ib = await self.ibkr_service._ensure_connected()
            contract = Stock(ticker, 'SMART', 'USD')
            price = await self.ibkr_service.get_ibkr_realtime_price(ib, contract)
            return price
        except Exception as e:
            logger.error("Failed to get price from IBKR", ticker=ticker, error=str(e))
            return None

