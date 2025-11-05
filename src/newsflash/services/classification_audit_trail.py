"""
Classification Audit Trail Service

Enhanced audit logging for IMMINENT classifications with detailed timing and price tracking.
Creates daily JSON files in tmp/classification_audit_trail/YYYY/MM/week_XX/YYYY-MM-DD.json

New format includes:
- Timing fields: news_received_at, classified_at, auto_trade_placed_at
- Metadata: market_cap, sector, industry
- Price history: 20-minute price tracking (minute_1 through minute_20)
- Trade details: entry_price, exit_price, shares, P/L
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from ..utils.logging_config import get_logger
from ..models.classification_models import ClassificationResult
from ..models.base_models import StandardizedArticle

logger = get_logger(__name__)

class ClassificationAuditTrail:
    """Audit trail for IMMINENT classifications."""
    
    def __init__(self, base_dir: str = "tmp/classification_audit_trail"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Classification audit trail initialized", base_dir=str(self.base_dir))
    
    def _get_daily_file_path(self, date: datetime) -> Path:
        """Get the file path for a specific date."""
        year = date.year
        month = date.month
        week_num = date.isocalendar()[1]
        day_str = date.strftime("%Y-%m-%d")
        
        # Create directory structure: YYYY/MM/week_XX/
        dir_path = self.base_dir / str(year) / f"{month:02d}" / f"week_{week_num:02d}"
        dir_path.mkdir(parents=True, exist_ok=True)
        
        # Return file path: YYYY-MM-DD.json
        return dir_path / f"{day_str}.json"
    
    def _load_daily_classifications(self, file_path: Path) -> list:
        """Load existing classifications from daily file."""
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Failed to load existing audit file", file_path=str(file_path), error=str(e))
                return []
        return []
    
    def _save_daily_classifications(self, file_path: Path, classifications: list):
        """Save classifications to daily file."""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(classifications, f, indent=2, ensure_ascii=False)
            logger.debug("Saved audit trail", file_path=str(file_path), count=len(classifications))
        except Exception as e:
            logger.error("Failed to save audit trail", file_path=str(file_path), error=str(e))
    
    def log_imminent_classification(
        self, 
        article: StandardizedArticle, 
        classification: ClassificationResult,
        news_received_at: Optional[datetime] = None,
        classified_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Log an IMMINENT classification to the audit trail with enhanced timing and metadata.
        
        Args:
            article: The classified article
            classification: The classification result
            news_received_at: When news was received (defaults to now)
            classified_at: When classification occurred (defaults to now)
            metadata: Optional metadata (market_cap, sector, industry, etc.)
        """
        if not classified_at:
            classified_at = datetime.now()
        if not news_received_at:
            news_received_at = classified_at  # Fallback if not provided
        
        # Only log IMMINENT classifications
        if classification.classification.value.lower() != "imminent":
            return
        
        article_id = self._get_article_id(article)
        
        # Create enhanced audit entry
        audit_entry = {
            # Timing fields (NEW)
            "news_received_at": news_received_at.isoformat(),
            "classified_at": classified_at.isoformat(),
            "auto_trade_placed_at": None,  # Will be updated when trade is placed
            
            # Legacy timestamp for backward compatibility
            "timestamp": classified_at.isoformat(),
            
            # Article info
            "article_id": article_id,
            "article_title": article.title,
            "article_tickers": article.tickers,
            "article_published": article.published.isoformat() if article.published else None,
            
            # Classification info
            "classification": classification.classification.value,
            "confidence": classification.confidence,
            "reasoning": classification.reasoning,
            "source": article.source.value if hasattr(article.source, 'value') else str(article.source),
            
            # Metadata (NEW)
            "metadata": metadata or {},
            
            # Trade details (NEW - will be updated)
            "trade_details": {
                "ticker": article.tickers[0] if article.tickers else None,
                "entry_price": None,
                "exit_price": None,
                "shares": None,
                "pnl": None,
                "pnl_percent": None,
                "entry_time": None,
                "exit_time": None,
                "session": None,  # market_hours, extended_hours, closed
                "order_type": None,  # MARKET, LIMIT
                "entry_volume": None,  # Volume at entry time
                "exit_volume": None  # Volume at exit time
            },
            
            # Timing calculations (NEW - calculated from timestamps)
            "timing_stats": {
                "news_to_classification_ms": None,  # Time from news receipt to classification
                "classification_to_trade_ms": None,  # Time from classification to trade placement
                "news_to_trade_ms": None,  # Total time from news receipt to trade placement
                "trade_to_exit_ms": None  # Time from trade entry to exit (should be ~5 minutes)
            },
            
            # Price history (NEW - will be updated over 20 minutes)
            "price_history": {}
        }
        
        # Get daily file path
        file_path = self._get_daily_file_path(classified_at)
        
        # Load existing classifications
        classifications = self._load_daily_classifications(file_path)
        
        # Add new entry
        classifications.append(audit_entry)
        
        # Save back to file
        self._save_daily_classifications(file_path, classifications)
        
        logger.info(
            "Logged IMMINENT classification to audit trail",
            article_id=article_id,
            file_path=str(file_path),
            total_entries=len(classifications),
            news_received_at=news_received_at.isoformat(),
            classified_at=classified_at.isoformat()
        )
        
        return article_id
    
    def update_auto_trade_placed(
        self,
        article_id: str,
        trade_placed_at: datetime,
        ticker: str,
        entry_price: Optional[float] = None,
        shares: Optional[int] = None,
        session: Optional[str] = None,
        order_type: Optional[str] = None
    ) -> bool:
        """
        Update audit entry with auto-trade placement information.
        
        Args:
            article_id: Article ID to update
            trade_placed_at: When trade was placed
            ticker: Stock ticker
            entry_price: Entry price (optional)
            shares: Number of shares (optional)
            session: Market session (optional)
            order_type: Order type (optional)
            
        Returns:
            True if updated, False if entry not found
        """
        file_path = self._get_daily_file_path(trade_placed_at)
        classifications = self._load_daily_classifications(file_path)
        
        # Find the entry (most recent matching article_id)
        for entry in reversed(classifications):
            if entry.get("article_id") == article_id:
                entry["auto_trade_placed_at"] = trade_placed_at.isoformat()
                
                # Calculate timing stats
                if entry.get("news_received_at") and entry.get("classified_at"):
                    news_received = datetime.fromisoformat(entry["news_received_at"])
                    classified = datetime.fromisoformat(entry["classified_at"])
                    entry["timing_stats"] = entry.get("timing_stats", {})
                    entry["timing_stats"]["news_to_classification_ms"] = (classified - news_received).total_seconds() * 1000
                    entry["timing_stats"]["classification_to_trade_ms"] = (trade_placed_at - classified).total_seconds() * 1000
                    entry["timing_stats"]["news_to_trade_ms"] = (trade_placed_at - news_received).total_seconds() * 1000
                
                if entry.get("trade_details"):
                    entry["trade_details"]["ticker"] = ticker
                    if entry_price is not None:
                        entry["trade_details"]["entry_price"] = entry_price
                    if shares is not None:
                        entry["trade_details"]["shares"] = shares
                    entry["trade_details"]["entry_time"] = trade_placed_at.isoformat()
                    if session:
                        entry["trade_details"]["session"] = session
                    if order_type:
                        entry["trade_details"]["order_type"] = order_type
                        
                self._save_daily_classifications(file_path, classifications)
                logger.info("Updated audit entry with auto-trade info", article_id=article_id)
                return True
        
        logger.warning("Could not find audit entry to update", article_id=article_id)
        return False
    
    def update_metadata(
        self,
        article_id: str,
        metadata: Dict[str, Any],
        date: Optional[datetime] = None
    ) -> bool:
        """
        Update audit entry with metadata (market cap, sector, industry).
        
        Args:
            article_id: Article ID to update
            metadata: Metadata dictionary
            date: Date of the entry (defaults to today)
            
        Returns:
            True if updated, False if entry not found
        """
        if not date:
            date = datetime.now()
        
        file_path = self._get_daily_file_path(date)
        classifications = self._load_daily_classifications(file_path)
        
        # Find the entry (most recent matching article_id)
        for entry in reversed(classifications):
            if entry.get("article_id") == article_id:
                entry["metadata"] = metadata
                self._save_daily_classifications(file_path, classifications)
                logger.debug("Updated audit entry with metadata", article_id=article_id)
                return True
        
        logger.warning("Could not find audit entry to update metadata", article_id=article_id)
        return False
    
    def update_price_history(
        self,
        article_id: str,
        ticker: str,
        price_history: Dict[str, Any],
        date: Optional[datetime] = None
    ) -> bool:
        """
        Update audit entry with price history data.
        This is called repeatedly over 20 minutes by the price tracking service.
        
        Args:
            article_id: Article ID to update
            ticker: Stock ticker
            price_history: Dictionary of minute_X: {price, timestamp, minutes_since_trade}
            date: Date of the entry (defaults to today)
            
        Returns:
            True if updated, False if entry not found
        """
        if not date:
            date = datetime.now()
        
        file_path = self._get_daily_file_path(date)
        classifications = self._load_daily_classifications(file_path)
        
        # Find the entry (most recent matching article_id)
        for entry in reversed(classifications):
            if entry.get("article_id") == article_id:
                # Merge price history
                if "price_history" not in entry:
                    entry["price_history"] = {}
                entry["price_history"].update(price_history)
                
                self._save_daily_classifications(file_path, classifications)
                logger.debug(
                    "Updated price history",
                    article_id=article_id,
                    minutes_count=len(price_history)
                )
                return True
        
        logger.warning("Could not find audit entry to update price history", article_id=article_id)
        return False
    
    def update_trade_exit(
        self,
        article_id: str,
        exit_price: float,
        exit_time: datetime,
        pnl: Optional[float] = None,
        pnl_percent: Optional[float] = None,
        session: Optional[str] = None,
        order_type: Optional[str] = None
    ) -> bool:
        """
        Update audit entry with trade exit information.
        
        Args:
            article_id: Article ID to update
            exit_price: Exit price
            exit_time: When trade was exited
            pnl: Profit/loss amount (optional)
            pnl_percent: Profit/loss percentage (optional)
            session: Market session (optional)
            order_type: Order type (optional)
            
        Returns:
            True if updated, False if entry not found
        """
        file_path = self._get_daily_file_path(exit_time)
        classifications = self._load_daily_classifications(file_path)
        
        # Find the entry (most recent matching article_id)
        for entry in reversed(classifications):
            if entry.get("article_id") == article_id:
                if entry.get("trade_details"):
                    entry["trade_details"]["exit_price"] = exit_price
                    entry["trade_details"]["exit_time"] = exit_time.isoformat()
                    if pnl is not None:
                        entry["trade_details"]["pnl"] = pnl
                    if pnl_percent is not None:
                        entry["trade_details"]["pnl_percent"] = pnl_percent
                    if session:
                        entry["trade_details"]["session"] = session
                    if order_type:
                        entry["trade_details"]["order_type"] = order_type
                
                # Calculate trade_to_exit timing
                if entry.get("trade_details", {}).get("entry_time"):
                    entry_time = datetime.fromisoformat(entry["trade_details"]["entry_time"])
                    entry["timing_stats"] = entry.get("timing_stats", {})
                    entry["timing_stats"]["trade_to_exit_ms"] = (exit_time - entry_time).total_seconds() * 1000
                
                self._save_daily_classifications(file_path, classifications)
                logger.info("Updated audit entry with trade exit info", article_id=article_id)
                return True
        
        logger.warning("Could not find audit entry to update exit info", article_id=article_id)
        return False
    
    def _get_article_id(self, article: StandardizedArticle) -> str:
        """Extract article ID from various article types."""
        if hasattr(article, 'benzinga_id'):
            return str(article.benzinga_id)
        elif hasattr(article, 'id'):
            return str(article.id)
        else:
            return "unknown"

def get_classification_audit_trail() -> ClassificationAuditTrail:
    """Get classification audit trail instance."""
    return ClassificationAuditTrail()
