"""
Position tracking service for automated trades.
Tracks open positions and manages scheduled exits.
"""
import json
import os
from typing import Dict, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Position:
    """Represents an open position from an auto-trade."""
    ticker: str
    shares: int
    entry_time: str  # ISO format datetime string
    entry_price: float
    article_id: str
    exit_scheduled: bool = False
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Position':
        """Create Position from dictionary."""
        return cls(**data)


class PositionTracker:
    """
    Tracks open positions from auto-trades and manages exit scheduling.
    
    Features:
    - In-memory position tracking
    - Persistent storage for crash recovery
    - Position lookup and management
    """
    
    def __init__(self, positions_file: str = "tmp/open_positions.json"):
        """
        Initialize position tracker.
        
        Args:
            positions_file: Path to persistent storage file
        """
        self.positions_file = positions_file
        self.positions: Dict[str, Position] = {}
        
        # Load existing positions from file (for crash recovery)
        self._load_positions()
        
        logger.info(
            "PositionTracker initialized",
            open_positions=len(self.positions),
            positions_file=self.positions_file
        )
    
    def _load_positions(self) -> None:
        """Load positions from persistent storage."""
        try:
            if os.path.exists(self.positions_file):
                with open(self.positions_file, 'r') as f:
                    data = json.load(f)
                    self.positions = {
                        ticker: Position.from_dict(pos_data)
                        for ticker, pos_data in data.items()
                    }
                logger.info("Loaded positions from file", count=len(self.positions))
        except Exception as e:
            logger.error("Failed to load positions from file", error=str(e))
    
    def _save_positions(self) -> None:
        """Save positions to persistent storage."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.positions_file), exist_ok=True)
            
            with open(self.positions_file, 'w') as f:
                json.dump(
                    {ticker: pos.to_dict() for ticker, pos in self.positions.items()},
                    f,
                    indent=2
                )
        except Exception as e:
            logger.error("Failed to save positions to file", error=str(e))
    
    def add_position(
        self,
        ticker: str,
        shares: int,
        entry_time: datetime,
        entry_price: float,
        article_id: str
    ) -> None:
        """
        Add a new position to tracking.
        
        Args:
            ticker: Stock ticker
            shares: Number of shares
            entry_time: Entry timestamp
            entry_price: Entry price per share
            article_id: Article ID that triggered the trade
        """
        position = Position(
            ticker=ticker,
            shares=shares,
            entry_time=entry_time.isoformat(),
            entry_price=entry_price,
            article_id=article_id,
            exit_scheduled=True  # Mark as scheduled when added
        )
        
        self.positions[ticker] = position
        self._save_positions()
        
        logger.info(
            "Position added",
            ticker=ticker,
            shares=shares,
            entry_price=entry_price,
            article_id=article_id
        )
    
    def remove_position(self, ticker: str) -> None:
        """
        Remove a position from tracking (after exit).
        
        Args:
            ticker: Stock ticker to remove
        """
        if ticker in self.positions:
            position = self.positions.pop(ticker)
            self._save_positions()
            logger.info(
                "Position removed",
                ticker=ticker,
                shares=position.shares,
                entry_price=position.entry_price
            )
        else:
            logger.warning("Attempted to remove non-existent position", ticker=ticker)
    
    def has_open_position(self, ticker: str) -> bool:
        """
        Check if there's an open position for a ticker.
        
        Args:
            ticker: Stock ticker to check
            
        Returns:
            True if position exists, False otherwise
        """
        return ticker in self.positions
    
    def get_entry_price(self, ticker: str) -> Optional[float]:
        """
        Get entry price for a position.
        
        Args:
            ticker: Stock ticker
            
        Returns:
            Entry price or None if position doesn't exist
        """
        if ticker in self.positions:
            return self.positions[ticker].entry_price
        return None
    
    def get_position(self, ticker: str) -> Optional[Position]:
        """
        Get position details.
        
        Args:
            ticker: Stock ticker
            
        Returns:
            Position object or None
        """
        return self.positions.get(ticker)
    
    def get_all_positions(self) -> Dict[str, Position]:
        """Get all open positions."""
        return self.positions.copy()
    
    def clear_all_positions(self) -> None:
        """Clear all positions (for testing/emergency)."""
        self.positions.clear()
        self._save_positions()
        logger.warning("All positions cleared")

