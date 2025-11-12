"""
Position tracking service for automated trades.
Tracks open positions and manages scheduled exits.
"""
import json
import os
from typing import Dict, Optional, List, Any
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
    instrument: str = "stock"
    instrument_details: Optional[Dict[str, Any]] = None
    leverage: Optional[float] = None
    
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
        # Store positions as {ticker: {article_id: Position}}
        self.positions: Dict[str, Dict[str, Position]] = {}
        
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
                    normalized: Dict[str, Dict[str, Position]] = {}
                    for ticker, value in data.items():
                        if isinstance(value, dict):
                            # Detect legacy format where value is a single Position dict
                            if "shares" in value and "article_id" in value:
                                article_id = value.get("article_id") or f"legacy_{value.get('entry_time', '')}"
                                normalized.setdefault(ticker, {})[article_id] = Position.from_dict(value)
                            else:
                                # New format: mapping of article_id -> position dict
                                normalized[ticker] = {
                                    article_id: Position.from_dict(pos_data)
                                    for article_id, pos_data in value.items()
                                }
                        else:
                            logger.warning("Unexpected position data format", ticker=ticker)
                    self.positions = normalized
                logger.info("Loaded positions from file", count=len(self.positions))
        except Exception as e:
            logger.error("Failed to load positions from file", error=str(e))
    
    def _save_positions(self) -> None:
        """Save positions to persistent storage."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.positions_file), exist_ok=True)
            
            with open(self.positions_file, 'w') as f:
                serialized: Dict[str, Dict[str, dict]] = {
                    ticker: {
                        article_id: pos.to_dict()
                        for article_id, pos in positions.items()
                    }
                    for ticker, positions in self.positions.items()
                }
                json.dump(serialized, f, indent=2)
        except Exception as e:
            logger.error("Failed to save positions to file", error=str(e))
    
    def add_position(
        self,
        ticker: str,
        shares: int,
        entry_time: datetime,
        entry_price: float,
        article_id: str,
        instrument: Optional[str] = "stock",
        instrument_details: Optional[Dict[str, Any]] = None,
        leverage: Optional[float] = None,
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
            exit_scheduled=True,  # Mark as scheduled when added
            instrument=instrument or "stock",
            instrument_details=instrument_details,
            leverage=leverage,
        )

        ticker_positions = self.positions.setdefault(ticker, {})
        ticker_positions[article_id] = position
        self._save_positions()
        
        logger.info(
            "Position added",
            ticker=ticker,
            shares=shares,
            entry_price=entry_price,
            article_id=article_id,
            instrument=position.instrument,
        )
    
    def remove_position(self, ticker: str, article_id: Optional[str] = None) -> None:
        """
        Remove a position or all positions for a ticker.

        Args:
            ticker: Stock ticker to remove.
            article_id: Specific article/position identifier. If omitted, all
                tracked positions for the ticker are removed.
        """
        if ticker not in self.positions:
            logger.warning("Attempted to remove non-existent position", ticker=ticker)
            return

        if article_id is None:
            removed_positions = self.positions.pop(ticker)
            self._save_positions()
            total_shares = sum(pos.shares for pos in removed_positions.values())
            logger.info(
                "Removed all positions for ticker",
                ticker=ticker,
                total_shares=total_shares,
                positions=len(removed_positions)
            )
            return

        position = self.positions[ticker].pop(article_id, None)
        if not position:
            logger.warning(
                "Attempted to remove non-existent position for article",
                ticker=ticker,
                article_id=article_id
            )
            return

        if not self.positions[ticker]:
            self.positions.pop(ticker, None)

        self._save_positions()
        logger.info(
            "Position removed",
            ticker=ticker,
            article_id=article_id,
            shares=position.shares,
            entry_price=position.entry_price
        )
    
    def has_open_position(self, ticker: str, article_id: Optional[str] = None) -> bool:
        """
        Check if there's an open position for a ticker or specific article.

        Args:
            ticker: Stock ticker to check
            article_id: Optional article identifier

        Returns:
            True if a matching position exists, False otherwise
        """
        if ticker not in self.positions:
            return False
        if article_id is None:
            return bool(self.positions[ticker])
        return article_id in self.positions[ticker]
    
    def get_entry_price(self, ticker: str, article_id: Optional[str] = None) -> Optional[float]:
        """
        Get entry price for a position.

        Args:
            ticker: Stock ticker
            article_id: Optional article identifier

        Returns:
            Entry price or None if position doesn't exist
        """
        if ticker not in self.positions:
            return None

        if article_id:
            position = self.positions[ticker].get(article_id)
            return position.entry_price if position else None

        # Fallback: return entry price of the most recent position
        latest = self._get_latest_position(self.positions[ticker].values())
        return latest.entry_price if latest else None
    
    def get_position(self, ticker: str, article_id: Optional[str] = None) -> Optional[Position]:
        """
        Get position details.

        Args:
            ticker: Stock ticker
            article_id: Optional article identifier

        Returns:
            Position object or None
        """
        if ticker not in self.positions:
            return None

        if article_id:
            return self.positions[ticker].get(article_id)

        return self._get_latest_position(self.positions[ticker].values())
    
    def get_positions_for_ticker(self, ticker: str) -> Dict[str, Position]:
        """Return all recorded positions for a ticker."""
        return self.positions.get(ticker, {}).copy()

    def get_all_positions(self) -> Dict[str, Dict[str, Position]]:
        """Get all open positions."""
        return {ticker: positions.copy() for ticker, positions in self.positions.items()}

    def get_total_shares(self, ticker: str) -> int:
        """Return the total shares tracked for a ticker."""
        return sum(pos.shares for pos in self.positions.get(ticker, {}).values())
    
    def clear_all_positions(self) -> None:
        """Clear all positions (for testing/emergency)."""
        self.positions.clear()
        self._save_positions()
        logger.warning("All positions cleared")

    @staticmethod
    def _get_latest_position(positions: List[Position]) -> Optional[Position]:
        if not positions:
            return None
        return max(
            positions,
            key=lambda pos: datetime.fromisoformat(pos.entry_time) if isinstance(pos.entry_time, str) else pos.entry_time
        )

