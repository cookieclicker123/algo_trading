"""
Audit repository - handles file I/O for audit trail storage.

Pure infrastructure - handles JSON file operations for classification audit logs.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class AuditRepository:
    """
    Repository for audit trail storage operations.
    
    Handles:
    - Daily audit log files (classification_audit_trail/YYYY/MM/week_XX/YYYY-MM-DD.json)
    - Updating existing audit entries
    - File I/O operations
    """
    
    def __init__(self, base_dir: str = "tmp/classification_audit_trail"):
        """
        Initialize audit repository.
        
        Args:
            base_dir: Base directory for audit trail files
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("AuditRepository initialized", base_dir=str(self.base_dir))
    
    async def store_audit_entry(
        self,
        article_id: str,
        audit_data: Dict[str, Any],
        logged_at: datetime
    ) -> str:
        """
        Store a new audit entry.
        
        Args:
            article_id: Article ID for audit entry
            audit_data: Serialized audit entry data (dict)
            logged_at: When audit entry was logged
            
        Returns:
            File path where entry was stored
        """
        file_path = self._get_daily_file_path(logged_at)
        
        # Load existing classifications
        classifications = await self._load_daily_classifications(file_path)
        
        # Add new entry
        classifications.append(audit_data)
        
        # Save back to file
        await self._save_daily_classifications(file_path, classifications)
        
        logger.info(
            "Stored audit entry",
            article_id=article_id,
            file_path=str(file_path),
            total_entries=len(classifications)
        )
        
        return str(file_path)
    
    async def update_audit_entry(
        self,
        article_id: str,
        updates: Dict[str, Any],
        date: Optional[datetime] = None
    ) -> bool:
        """
        Update an existing audit entry.
        
        Args:
            article_id: Article ID to update
            updates: Dictionary of fields to update
            date: Date of the entry (defaults to today)
            
        Returns:
            True if updated, False if entry not found
        """
        if not date:
            date = datetime.now()
        
        file_path = self._get_daily_file_path(date)
        classifications = await self._load_daily_classifications(file_path)
        
        # Find the entry (most recent matching article_id)
        for entry in reversed(classifications):
            if entry.get("article_id") == article_id:
                # Update entry with new data
                entry.update(updates)
                
                await self._save_daily_classifications(file_path, classifications)
                logger.debug("Updated audit entry", article_id=article_id)
                return True
        
        logger.warning("Could not find audit entry to update", article_id=article_id)
        return False
    
    async def get_audit_entry(
        self,
        article_id: str,
        date: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get an audit entry by article ID.
        
        Args:
            article_id: Article ID to fetch
            date: Date of the entry (defaults to today)
            
        Returns:
            Audit entry if found, None otherwise
        """
        if not date:
            date = datetime.now()
        
        file_path = self._get_daily_file_path(date)
        classifications = await self._load_daily_classifications(file_path)
        
        # Find the entry (most recent matching article_id)
        for entry in reversed(classifications):
            if entry.get("article_id") == article_id:
                return entry
        
        return None
    
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
    
    async def _load_daily_classifications(self, file_path: Path) -> List[Dict[str, Any]]:
        """Load existing classifications from daily file."""
        if file_path.exists():
            try:
                import aiofiles
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    return json.loads(content) if content.strip() else []
            except Exception as e:
                logger.warning("Failed to load existing audit file", file_path=str(file_path), error=str(e))
                return []
        return []
    
    async def _save_daily_classifications(self, file_path: Path, classifications: List[Dict[str, Any]]):
        """Save classifications to daily file."""
        try:
            import aiofiles
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(classifications, indent=2, ensure_ascii=False, default=str))
            logger.debug("Saved audit trail", file_path=str(file_path), count=len(classifications))
        except Exception as e:
            logger.error("Failed to save audit trail", file_path=str(file_path), error=str(e))
            raise

