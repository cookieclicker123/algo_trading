"""
Queue manager for trades that arrive when market is closed.
Pure infrastructure - queues trades and publishes events.
"""
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

from ...utils.logging_config import get_logger
from ...models.base_models import TradeRequest
from ...shared.event_bus import AsyncEventBus
from .events import TradeRequestQueuedEvent
from .event_builders import build_infrastructure_trade_request_data
from ...utils.brokerage.session_detector import get_next_premarket_time

logger = get_logger(__name__)


class TradeQueueManager:
    """
    Manages queue of trades for closed market periods.
    
    Responsibilities:
    - Queue trades when market is closed
    - Retrieve queued trades for premarket
    - Publish queue events
    
    Does NOT:
    - Execute trades (that's the executor's job)
    - Know about business logic
    """
    
    def __init__(self, event_bus: AsyncEventBus, queue_file_path: Optional[Path] = None):
        """
        Initialize queue manager.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            queue_file_path: Optional path to queue JSON file
        """
        self.event_bus = event_bus
        self.queue_file_path = queue_file_path or Path("tmp/queued_trades.json")
        self.queue_file_path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info("TradeQueueManager initialized", queue_file_path=str(self.queue_file_path))
    
    def queue_trade(self, trade_request: TradeRequest) -> None:
        """
        Queue a trade request for execution when market opens.
        
        Args:
            trade_request: Trade request to queue
        """
        try:
            # Load existing queue
            queued_trades = self._load_queue()
            
            # Add new trade to queue
            trade_entry = {
                "trade_request": trade_request.model_dump(),
                "queued_at": datetime.now().isoformat(),
                "target_premarket": get_next_premarket_time().isoformat(),
            }
            
            queued_trades.append(trade_entry)
            
            # Save queue
            self._save_queue(queued_trades)
            
            logger.info(
                "Trade queued for next premarket",
                ticker=trade_request.ticker,
                target_premarket=trade_entry["target_premarket"]
            )
            
            # Publish event (async, fire-and-forget)
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            if loop.is_running():
                asyncio.create_task(self._publish_queued_event(trade_request))
            else:
                loop.run_until_complete(self._publish_queued_event(trade_request))
        
        except Exception as e:
            logger.error("Failed to queue trade", ticker=trade_request.ticker, error=str(e))
            raise
    
    def get_queued_trades(self) -> List[Dict[str, Any]]:
        """
        Get all queued trades.
        
        Returns:
            List of queued trade dictionaries
        """
        return self._load_queue()
    
    def clear_queue(self) -> None:
        """Clear all queued trades."""
        self._save_queue([])
        logger.info("Trade queue cleared")
    
    def remove_queued_trade(self, index: int) -> bool:
        """
        Remove a queued trade by index.
        
        Args:
            index: Index of trade to remove
            
        Returns:
            True if removed, False if index out of range
        """
        queued_trades = self._load_queue()
        
        if index < 0 or index >= len(queued_trades):
            return False
        
        removed = queued_trades.pop(index)
        self._save_queue(queued_trades)
        
        logger.info(
            "Removed queued trade",
            ticker=removed["trade_request"].get("ticker"),
            index=index
        )
        
        return True
    
    def _load_queue(self) -> List[Dict[str, Any]]:
        """Load queue from file."""
        try:
            if not self.queue_file_path.exists():
                return []
            
            with open(self.queue_file_path, "r") as f:
                return json.load(f)
        
        except Exception as e:
            logger.error("Failed to load trade queue", error=str(e))
            return []
    
    def _save_queue(self, queued_trades: List[Dict[str, Any]]) -> None:
        """Save queue to file."""
        try:
            with open(self.queue_file_path, "w") as f:
                json.dump(queued_trades, f, indent=2)
        
        except Exception as e:
            logger.error("Failed to save trade queue", error=str(e))
            raise
    
    async def _publish_queued_event(self, trade_request: TradeRequest) -> None:
        """Publish TradeRequestQueuedEvent with typed infrastructure model."""
        # Convert shared TradeRequest to typed InfrastructureTradeRequestData
        infra_trade_request = build_infrastructure_trade_request_data(trade_request)
        
        event = TradeRequestQueuedEvent(
            trade_request=infra_trade_request,  # ✅ Typed infrastructure model
            queued_at=datetime.now(),
            target_premarket=get_next_premarket_time()
        )
        await self.event_bus.publish("TradeRequestQueued", event.model_dump())
        logger.debug("Published TradeRequestQueued event", ticker=trade_request.ticker)

