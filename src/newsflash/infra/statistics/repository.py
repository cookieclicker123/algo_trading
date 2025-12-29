"""
Statistics repository - handles file I/O for statistics records.
Pure infrastructure - stateless, uses BaseRepository pattern.
"""
from typing import Dict, Any
import json
import asyncio
from pathlib import Path
from datetime import datetime
import aiofiles
import pytz

from ...utils.logging_config import get_logger
from ...shared.statistics.models import (
    RecallSessionFile,
    SignalSessionFile,
    FailedTradeSessionFile,
    RecallRecord,
    SignalRecord,
    FailedTradeRecord,
)
from ...domain.brokerage.models import MarketSession

logger = get_logger(__name__)


class StatisticsRepository:
    """
    Repository for statistics file operations.
    
    Responsibilities:
    - Append records to session JSON files in real-time
    - Load existing session files
    - Update summary statistics
    - Handle file path calculation
    
    Stateless: All state in files, no in-memory storage.
    """
    
    def __init__(self, tmp_dir: Path):
        """
        Initialize statistics repository.
        
        Args:
            tmp_dir: Base tmp directory (e.g., Path("tmp"))
        """
        self.tmp_dir = tmp_dir
        self.statistics_dir = tmp_dir / "statistics"
        self._file_lock = asyncio.Lock()  # Serialize file access
        
        # Ensure statistics directory exists
        self.statistics_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("StatisticsRepository initialized", tmp_dir=str(tmp_dir))
    
    def _get_session_file_path(
        self,
        engine_type: str,  # "recall", "signal", or "failed_trades"
        session: str,  # "premarket", "market_hours", "postmarket"
        date: datetime
    ) -> Path:
        """
        Calculate file path for a session file.
        
        Path: tmp/statistics/{engine_type}/{year}/{month}/week_{week}/{day}/{session}/{session}.json
        
        Args:
            engine_type: "recall" or "signal"
            session: Session name (from session_detector: "premarket", "market_hours", "postmarket")
            date: Date to use for path calculation
            
        Returns:
            Path to JSON file
        """
        et_tz = pytz.timezone("US/Eastern")
        date_et = date.astimezone(et_tz) if date.tzinfo else et_tz.localize(date)
        
        year = date_et.year
        month = date_et.month
        day = date_et.day
        
        # Calculate week number (ISO week)
        week = date_et.isocalendar()[1]
        
        # Map session name to directory name
        # session_detector returns: "premarket", "market_hours", "postmarket"
        # File structure uses: "premarket", "market_hours", "postmarket"
        session_dir_map = {
            "premarket": "premarket",
            "market_hours": "market_hours",
            "postmarket": "postmarket"
        }
        session_dir = session_dir_map.get(session, session)
        
        file_path = (
            self.statistics_dir /
            engine_type /
            str(year) /
            f"{month:02d}" /
            f"week_{week}" /
            f"{day:02d}" /
            session_dir /
            f"{session_dir}.json"
        )
        
        return file_path
    
    def _map_session_to_enum(self, session: str) -> MarketSession:
        """
        Map session_detector session string to MarketSession enum.
        
        Args:
            session: Session string from session_detector ("premarket", "market_hours", "postmarket", "closed")
            
        Returns:
            MarketSession enum value
        """
        mapping = {
            "premarket": MarketSession.PREMARKET,
            "market_hours": MarketSession.MARKET,
            "postmarket": MarketSession.POSTMARKET,
            "closed": MarketSession.CLOSED
        }
        return mapping.get(session, MarketSession.MARKET)
    
    def _calculate_session_times(self, session: str, date: datetime) -> tuple[datetime, datetime]:
        """
        Calculate session start and end times for a given date and session.
        
        Args:
            session: Session name
            date: Date to calculate times for
            
        Returns:
            Tuple of (session_start, session_end) in ET timezone
        """
        et_tz = pytz.timezone("US/Eastern")
        date_et = date.astimezone(et_tz) if date.tzinfo else et_tz.localize(date)
        
        if session == "premarket":
            session_start = date_et.replace(hour=4, minute=0, second=0, microsecond=0)
            session_end = date_et.replace(hour=9, minute=30, second=0, microsecond=0)
        elif session == "market_hours":
            session_start = date_et.replace(hour=9, minute=30, second=0, microsecond=0)
            session_end = date_et.replace(hour=16, minute=0, second=0, microsecond=0)
        elif session == "postmarket":
            session_start = date_et.replace(hour=16, minute=0, second=0, microsecond=0)
            session_end = date_et.replace(hour=20, minute=0, second=0, microsecond=0)
        else:
            # Default to market hours if unknown
            session_start = date_et.replace(hour=9, minute=30, second=0, microsecond=0)
            session_end = date_et.replace(hour=16, minute=0, second=0, microsecond=0)
        
        return session_start, session_end
    
    async def append_recall_record(
        self,
        record: RecallRecord,
        session: str,
        date: datetime
    ) -> None:
        """
        Append a recall record to the session file and update summary.
        
        Real-time operation: Loads file, appends record, updates summary, saves.
        
        Args:
            record: RecallRecord to append
            session: Session name (from session_detector)
            date: Date for file path calculation
        """
        async with self._file_lock:
            file_path = self._get_session_file_path("recall", session, date)
            
            # Load existing file or create new
            session_file = await self._load_recall_file(file_path, session, date)
            
            # Append record
            session_file.records.append(record)
            
            # Update summary
            session_file.summary["total_articles_tracked"] = len(session_file.records)
            
            # Update 1% move count if price check completed
            if record.price_check_5min and record.price_check_5min.get("moved_1_percent"):
                session_file.summary["articles_with_1_percent_move"] += 1
                # If filtered (has filter_reason), it's a missed opportunity
                if record.filter_reason:
                    session_file.summary["missed_opportunities"] += 1

            # Update filter breakdown
            if record.filter_reason:
                session_file.summary["filter_breakdown"][record.filter_reason] = \
                    session_file.summary["filter_breakdown"].get(record.filter_reason, 0) + 1
            
            # Update ticker breakdown
            for ticker in record.tickers:
                session_file.summary["ticker_breakdown"][ticker] = \
                    session_file.summary["ticker_breakdown"].get(ticker, 0) + 1
            
            # Update last_updated_at
            session_file.last_updated_at = datetime.now()
            
            # Save file
            await self._save_recall_file(file_path, session_file)
            
            logger.debug(
                "Appended recall record",
                article_id=record.article_id,
                file_path=str(file_path)
            )
    
    async def remove_recall_record(
        self,
        article_id: str,
        session: str,
        date: datetime
    ) -> bool:
        """
        Remove a recall record by article_id (best-effort).
        
        Used when a trade attempt is made (even if failed) - removes from recall.
        
        Returns:
            True if record was found and removed, False otherwise
        """
        try:
            file_path = self._get_session_file_path("recall", session, date)
            
            if not file_path.exists():
                return False
            
            async with self._file_lock:
                # Load file (need session and date for _load_recall_file signature)
                session_file = await self._load_recall_file(file_path, session, date)
                
                # Find and remove record
                original_count = len(session_file.records)
                session_file.records = [
                    r for r in session_file.records
                    if r.article_id != article_id
                ]
                
                if len(session_file.records) < original_count:
                    # Record was removed - recalculate summary
                    session_file.summary = self._calculate_recall_summary(session_file.records)
                    
                    # Save file
                    await self._save_recall_file(file_path, session_file)
                    logger.debug(
                        "Recall: Removed record for attempted trade",
                        article_id=article_id,
                        session=session
                    )
                    return True
                
                return False
        except Exception as e:
            logger.debug(
                "Recall: Error removing record (may not exist)",
                article_id=article_id,
                error=str(e)
            )
            return False
    
    async def update_recall_record(
        self,
        article_id: str,
        updates: dict,
        session: str,
        date: datetime
    ) -> bool:
        """
        Update an existing recall record (e.g., after 5-minute price check).
        
        Args:
            article_id: Article ID to find and update
            updates: Dictionary of fields to update
            session: Session name
            date: Date for file path calculation
            
        Returns:
            True if record found and updated, False otherwise
        """
        async with self._file_lock:
            file_path = self._get_session_file_path("recall", session, date)
            
            # Load existing file
            session_file = await self._load_recall_file(file_path, session, date)
            
            record_found = False
            # Find and update record
            for record in session_file.records:
                if record.article_id == article_id:
                    record_found = True
                    # Store old values BEFORE updating (for summary recalculation)
                    old_price_check = record.price_check_5min
                    old_was_counted = old_price_check and old_price_check.get("moved_1_percent")
                    old_filter_reason = record.filter_reason
                    
                    # Update fields
                    for key, value in updates.items():
                        if hasattr(record, key):
                            # Special handling for filter_reason - set directly (singular, one reason per article)
                            if key == "filter_reason":
                                # Only set if not already set (first reason wins, or explicit override)
                                if not record.filter_reason or value is None:
                                    setattr(record, key, value)
                                # If updating with a new reason, log it but keep first one (unless explicitly overriding)
                                elif value and value != record.filter_reason:
                                    logger.warning(
                                        "Recall: Filter reason already set, keeping original",
                                        article_id=article_id,
                                        existing_reason=record.filter_reason,
                                        new_reason=value
                                    )
                            # Special handling for ticker_metadata - merge dictionaries (for multiple tickers)
                            elif key == "ticker_metadata" and isinstance(value, dict):
                                existing_metadata = record.ticker_metadata.copy() if record.ticker_metadata else {}
                                # Merge new metadata into existing (don't overwrite, just add/update)
                                existing_metadata.update(value)
                                setattr(record, key, existing_metadata)
                            # Special handling for metadata_errors - merge dictionaries
                            elif key == "metadata_errors" and isinstance(value, dict):
                                existing_errors = record.metadata_errors.copy() if record.metadata_errors else {}
                                existing_errors.update(value)
                                setattr(record, key, existing_errors)
                            else:
                                setattr(record, key, value)
                    
                    # Recalculate summary if price check was updated
                    if "price_check_5min" in updates:
                        new_price_check = updates["price_check_5min"]
                        new_was_counted = new_price_check and new_price_check.get("moved_1_percent")
                        
                        # If transitioning from not counted to counted
                        if new_was_counted and not old_was_counted:
                            session_file.summary["articles_with_1_percent_move"] += 1
                            if record.filter_reason:
                                session_file.summary["missed_opportunities"] += 1
                        # If transitioning from counted to not counted
                        elif old_was_counted and not new_was_counted:
                            session_file.summary["articles_with_1_percent_move"] = max(0, session_file.summary["articles_with_1_percent_move"] - 1)
                            if old_filter_reason:
                                session_file.summary["missed_opportunities"] = max(0, session_file.summary["missed_opportunities"] - 1)
                    
                    # Update filter breakdown if filter_reason was updated
                    if "filter_reason" in updates:
                        # Remove old reason from breakdown
                        if old_filter_reason and old_filter_reason in session_file.summary["filter_breakdown"]:
                            session_file.summary["filter_breakdown"][old_filter_reason] = max(0, session_file.summary["filter_breakdown"][old_filter_reason] - 1)
                        # Add new reason to breakdown
                        if record.filter_reason:
                            session_file.summary["filter_breakdown"][record.filter_reason] = \
                                session_file.summary["filter_breakdown"].get(record.filter_reason, 0) + 1
                    
                    break
            
            if record_found:
                # Update last_updated_at
                session_file.last_updated_at = datetime.now()
                
                # Save file
                await self._save_recall_file(file_path, session_file)
                
                logger.debug(
                    "Updated recall record",
                    article_id=article_id,
                    file_path=str(file_path)
                )
                return True
            else:
                logger.warning(
                    "Recall: Record not found for update",
                    article_id=article_id,
                    file_path=str(file_path),
                    session=session,
                    date_iso=date.isoformat()
                )
                return False
    
    async def update_signal_record(
        self,
        trade_id: str,
        updates: Dict[str, Any],
        session: str,
        date: datetime
    ) -> None:
        """
        Update an existing signal record in the session file.
        
        Args:
            trade_id: Trade ID to update
            updates: Dictionary of fields to update
            session: Session name
            date: Date for file path calculation
        """
        async with self._file_lock:
            file_path = self._get_session_file_path("signal", session, date)
            
            # Load existing file
            session_file = await self._load_signal_file(file_path, session, date)
            
            # Find and update record
            for record in session_file.records:
                if record.trade_id == trade_id:
                    # Store old values for summary recalculation
                    old_profit_loss = record.profit_loss_usd
                    old_was_profitable = old_profit_loss is not None and old_profit_loss > 0
                    old_was_losing = old_profit_loss is not None and old_profit_loss < 0
                    old_metadata = record.ticker_metadata
                    
                    # Update fields
                    for key, value in updates.items():
                        if hasattr(record, key):
                            setattr(record, key, value)
                    
                    # Recalculate summary if profit/loss was updated
                    if "profit_loss_usd" in updates:
                        new_profit_loss = updates["profit_loss_usd"]
                        new_was_profitable = new_profit_loss is not None and new_profit_loss > 0
                        new_was_losing = new_profit_loss is not None and new_profit_loss < 0
                        
                        # Update profit/loss counts
                        if new_was_profitable and not old_was_profitable:
                            session_file.summary["profitable_trades"] += 1
                            if old_was_losing:
                                session_file.summary["losing_trades"] = max(0, session_file.summary["losing_trades"] - 1)
                        elif new_was_losing and not old_was_losing:
                            session_file.summary["losing_trades"] += 1
                            if old_was_profitable:
                                session_file.summary["profitable_trades"] = max(0, session_file.summary["profitable_trades"] - 1)
                        
                        # Update total P&L
                        if old_profit_loss is not None:
                            session_file.summary["total_profit_loss_usd"] -= old_profit_loss
                        if new_profit_loss is not None:
                            session_file.summary["total_profit_loss_usd"] += new_profit_loss
                    
                    # Update industry/sector breakdown if metadata was updated
                    if "ticker_metadata" in updates:
                        new_metadata = updates["ticker_metadata"]
                        
                        # Remove old industry/sector from breakdown
                        if old_metadata:
                            old_industry = old_metadata.get("industry")
                            old_sector = old_metadata.get("sector")
                            if old_industry and old_industry in session_file.summary["industry_breakdown"]:
                                session_file.summary["industry_breakdown"][old_industry] = max(0, session_file.summary["industry_breakdown"][old_industry] - 1)
                            if old_sector and old_sector in session_file.summary["sector_breakdown"]:
                                session_file.summary["sector_breakdown"][old_sector] = max(0, session_file.summary["sector_breakdown"][old_sector] - 1)
                        
                        # Add new industry/sector to breakdown
                        if new_metadata:
                            industry = new_metadata.get("industry")
                            sector = new_metadata.get("sector")
                            if industry:
                                session_file.summary["industry_breakdown"][industry] = \
                                    session_file.summary["industry_breakdown"].get(industry, 0) + 1
                            if sector:
                                session_file.summary["sector_breakdown"][sector] = \
                                    session_file.summary["sector_breakdown"].get(sector, 0) + 1
                    
                    break
            
            # Update last_updated_at
            session_file.last_updated_at = datetime.now()
            
            # Save file
            await self._save_signal_file(file_path, session_file)
            
            logger.debug(
                "Updated signal record",
                trade_id=trade_id,
                file_path=str(file_path)
            )
    
    async def append_signal_record(
        self,
        record: SignalRecord,
        session: str,
        date: datetime
    ) -> None:
        """
        Append a signal record to the session file and update summary.
        
        Real-time operation: Loads file, appends record, updates summary, saves.
        
        Args:
            record: SignalRecord to append
            session: Session name (from session_detector)
            date: Date for file path calculation
        """
        async with self._file_lock:
            file_path = self._get_session_file_path("signal", session, date)
            
            # Load existing file or create new
            session_file = await self._load_signal_file(file_path, session, date)
            
            # Append record
            session_file.records.append(record)
            
            # Update summary
            session_file.summary["total_trades"] = len(session_file.records)
            
            # Update profit/loss counts
            if record.profit_loss_usd is not None:
                if record.profit_loss_usd > 0:
                    session_file.summary["profitable_trades"] += 1
                else:
                    session_file.summary["losing_trades"] += 1
                session_file.summary["total_profit_loss_usd"] += record.profit_loss_usd
            
            # Update average spread
            if record.entry_nbbo and record.entry_nbbo.get("spread"):
                spreads = [
                    r.entry_nbbo.get("spread")
                    for r in session_file.records
                    if r.entry_nbbo and r.entry_nbbo.get("spread")
                ]
                if spreads:
                    session_file.summary["average_spread_at_entry"] = sum(spreads) / len(spreads)
            
            # Update ticker breakdown
            session_file.summary["ticker_breakdown"][record.ticker] = \
                session_file.summary["ticker_breakdown"].get(record.ticker, 0) + 1
            
            # Update industry/sector breakdown if metadata available
            if record.ticker_metadata:
                industry = record.ticker_metadata.get("industry")
                sector = record.ticker_metadata.get("sector")
                if industry:
                    session_file.summary["industry_breakdown"][industry] = \
                        session_file.summary["industry_breakdown"].get(industry, 0) + 1
                if sector:
                    session_file.summary["sector_breakdown"][sector] = \
                        session_file.summary["sector_breakdown"].get(sector, 0) + 1
            
            # Update last_updated_at
            session_file.last_updated_at = datetime.now()
            
            # Save file
            await self._save_signal_file(file_path, session_file)
            
            logger.debug(
                "Appended signal record",
                trade_id=record.trade_id,
                file_path=str(file_path)
            )
    
    async def _load_recall_file(
        self,
        file_path: Path,
        session: str,
        date: datetime
    ) -> RecallSessionFile:
        """Load recall session file or create new if doesn't exist."""
        if file_path.exists():
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    if content.strip():
                        data = json.loads(content)
                        # Convert session string to enum
                        data["session"] = self._map_session_to_enum(data.get("session", session))
                        return RecallSessionFile(**data)
            except Exception as e:
                logger.warning(
                    "Failed to load recall file, creating new",
                    file_path=str(file_path),
                    error=str(e)
                )
        
        # Create new file
        session_start, session_end = self._calculate_session_times(session, date)
        et_tz = pytz.timezone("US/Eastern")
        date_et = date.astimezone(et_tz) if date.tzinfo else et_tz.localize(date)
        
        return RecallSessionFile(
            session=self._map_session_to_enum(session),
            date=date_et.strftime("%Y-%m-%d"),
            session_start=session_start,
            session_end=session_end
        )
    
    async def _load_signal_file(
        self,
        file_path: Path,
        session: str,
        date: datetime
    ) -> SignalSessionFile:
        """Load signal session file or create new if doesn't exist."""
        if file_path.exists():
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    if content.strip():
                        data = json.loads(content)
                        # Convert session string to enum
                        data["session"] = self._map_session_to_enum(data.get("session", session))
                        return SignalSessionFile(**data)
            except Exception as e:
                logger.warning(
                    "Failed to load signal file, creating new",
                    file_path=str(file_path),
                    error=str(e)
                )
        
        # Create new file
        session_start, session_end = self._calculate_session_times(session, date)
        et_tz = pytz.timezone("US/Eastern")
        date_et = date.astimezone(et_tz) if date.tzinfo else et_tz.localize(date)
        
        return SignalSessionFile(
            session=self._map_session_to_enum(session),
            date=date_et.strftime("%Y-%m-%d"),
            session_start=session_start,
            session_end=session_end
        )
    
    async def _save_recall_file(self, file_path: Path, session_file: RecallSessionFile) -> None:
        """Save recall session file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            json_str = json.dumps(
                session_file.model_dump(mode='json'),
                indent=2,
                ensure_ascii=False,
                default=str
            )
            await f.write(json_str)
    
    async def _save_signal_file(self, file_path: Path, session_file: SignalSessionFile) -> None:
        """Save signal session file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            json_str = json.dumps(
                session_file.model_dump(mode='json'),
                indent=2,
                ensure_ascii=False,
                default=str
            )
            await f.write(json_str)
    
    # ===== Failed Trades Methods =====
    
    async def append_failed_trade_record(
        self,
        record: FailedTradeRecord,
        session: str,
        date: datetime
    ) -> None:
        """
        Append a failed trade record to the session file and update summary.
        
        Real-time operation: Loads file, appends record, updates summary, saves.
        
        Args:
            record: FailedTradeRecord to append
            session: Session name (from session_detector)
            date: Date for file path calculation
        """
        async with self._file_lock:
            file_path = self._get_session_file_path("failed_trades", session, date)
            
            # Load existing file or create new
            session_file = await self._load_failed_trade_file(file_path, session, date)
            
            # Append record
            session_file.records.append(record)
            
            # Update summary
            session_file.summary["total_failed_trades"] = len(session_file.records)
            
            # Update failure reasons breakdown
            reason = record.failure_reason
            session_file.summary["failure_reasons_breakdown"][reason] = \
                session_file.summary["failure_reasons_breakdown"].get(reason, 0) + 1
            
            # Update ticker breakdown
            session_file.summary["ticker_breakdown"][record.ticker] = \
                session_file.summary["ticker_breakdown"].get(record.ticker, 0) + 1
            
            # Update time of day breakdown (by hour)
            hour_key = f"{record.hour:02d}:00"
            session_file.summary["time_of_day_breakdown"][hour_key] = \
                session_file.summary["time_of_day_breakdown"].get(hour_key, 0) + 1
            
            # Update session breakdown
            session_str = record.session.value if hasattr(record.session, 'value') else str(record.session)
            session_file.summary["session_breakdown"][session_str] = \
                session_file.summary["session_breakdown"].get(session_str, 0) + 1
            
            # Update average spread, bid_size, ask_size at failure
            if record.failure_nbbo:
                spreads = [
                    r.failure_nbbo.get("spread")
                    for r in session_file.records
                    if r.failure_nbbo and r.failure_nbbo.get("spread") is not None
                ]
                bid_sizes = [
                    r.failure_nbbo.get("bid_size")
                    for r in session_file.records
                    if r.failure_nbbo and r.failure_nbbo.get("bid_size") is not None
                ]
                ask_sizes = [
                    r.failure_nbbo.get("ask_size")
                    for r in session_file.records
                    if r.failure_nbbo and r.failure_nbbo.get("ask_size") is not None
                ]
                
                if spreads:
                    session_file.summary["avg_spread_at_failure"] = sum(spreads) / len(spreads)
                if bid_sizes:
                    session_file.summary["avg_bid_size_at_failure"] = sum(bid_sizes) / len(bid_sizes)
                if ask_sizes:
                    session_file.summary["avg_ask_size_at_failure"] = sum(ask_sizes) / len(ask_sizes)
            
            # Update last_updated_at
            session_file.last_updated_at = datetime.now()
            
            # Save file
            await self._save_failed_trade_file(file_path, session_file)
            
            logger.debug(
                "Appended failed trade record",
                trade_id=record.trade_id,
                file_path=str(file_path)
            )
    
    async def update_failed_trade_record(
        self,
        trade_id: str,
        updates: Dict[str, Any],
        session: str,
        date: datetime
    ) -> None:
        """
        Update an existing failed trade record in the session file.
        
        Args:
            trade_id: Trade ID to update
            updates: Dictionary of fields to update
            session: Session name
            date: Date for file path calculation
        """
        async with self._file_lock:
            file_path = self._get_session_file_path("failed_trades", session, date)
            
            # Load existing file
            session_file = await self._load_failed_trade_file(file_path, session, date)
            
            # Find and update record
            for record in session_file.records:
                if record.trade_id == trade_id:
                    # Update fields
                    for key, value in updates.items():
                        if hasattr(record, key):
                            setattr(record, key, value)
                    
                    # Update industry/sector breakdown if metadata was updated
                    if "ticker_metadata" in updates:
                        new_metadata = updates["ticker_metadata"]
                        if new_metadata:
                            # Recalculate breakdowns (simplified - just update counts)
                            # In practice, we'd need to track old vs new, but for now just ensure it's counted
                            pass  # Breakdowns are updated on append, not on metadata update
                    
                    break
            
            # Update last_updated_at
            session_file.last_updated_at = datetime.now()
            
            # Save file
            await self._save_failed_trade_file(file_path, session_file)
            
            logger.debug(
                "Updated failed trade record",
                trade_id=trade_id,
                file_path=str(file_path)
            )
    
    async def _load_failed_trade_file(
        self,
        file_path: Path,
        session: str,
        date: datetime
    ) -> FailedTradeSessionFile:
        """Load failed trade session file or create new if doesn't exist."""
        if file_path.exists():
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    
                    # Convert records to FailedTradeRecord models
                    records = [FailedTradeRecord(**r) for r in data.get("records", [])]
                    
                    # Map session string to MarketSession enum
                    session_str = data.get("session")
                    if isinstance(session_str, str):
                        session_enum_map = {
                            "MARKET": MarketSession.MARKET,
                            "PREMARKET": MarketSession.PREMARKET,
                            "POSTMARKET": MarketSession.POSTMARKET,
                        }
                        session_enum = session_enum_map.get(session_str, MarketSession.MARKET)
                    else:
                        session_enum = MarketSession.MARKET
                    
                    return FailedTradeSessionFile(
                        session=session_enum,
                        date=data.get("date", date.strftime("%Y-%m-%d")),
                        session_start=datetime.fromisoformat(data.get("session_start", datetime.now().isoformat())),
                        session_end=datetime.fromisoformat(data.get("session_end", datetime.now().isoformat())),
                        file_created_at=datetime.fromisoformat(data.get("file_created_at", datetime.now().isoformat())),
                        last_updated_at=datetime.fromisoformat(data.get("last_updated_at", datetime.now().isoformat())),
                        summary=data.get("summary", {}),
                        records=records
                    )
            except Exception as e:
                logger.warning(
                    "Error loading failed trade file, creating new",
                    file_path=str(file_path),
                    error=str(e)
                )
        
        # Create new file
        et_tz = pytz.timezone("US/Eastern")
        date_et = date.astimezone(et_tz) if date.tzinfo else et_tz.localize(date)
        
        # Map session string to MarketSession enum
        session_enum_map = {
            "premarket": MarketSession.PREMARKET,
            "market_hours": MarketSession.MARKET,
            "postmarket": MarketSession.POSTMARKET
        }
        session_enum = session_enum_map.get(session, MarketSession.MARKET)
        
        return FailedTradeSessionFile(
            session=session_enum,
            date=date_et.strftime("%Y-%m-%d"),
            session_start=date_et,
            session_end=date_et,
            records=[]
        )
    
    async def _save_failed_trade_file(self, file_path: Path, session_file: FailedTradeSessionFile) -> None:
        """Save failed trade session file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            json_str = json.dumps(
                session_file.model_dump(mode='json'),
                indent=2,
                ensure_ascii=False,
                default=str
            )
            await f.write(json_str)
