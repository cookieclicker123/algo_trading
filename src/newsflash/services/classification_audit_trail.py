"""
Classification Audit Trail Service

Simple audit logging for IMMINENT classifications to track what gets sent to Telegram.
Creates daily JSON files in tmp/classification_audit_trail/YYYY/MM/week_XX/YYYY-MM-DD.json
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
        timestamp: Optional[datetime] = None
    ):
        """
        Log an IMMINENT classification to the audit trail.
        
        Args:
            article: The classified article
            classification: The classification result
            timestamp: When this classification occurred (defaults to now)
        """
        if not timestamp:
            timestamp = datetime.now()
        
        # Only log IMMINENT classifications
        if classification.classification.value.lower() != "imminent":
            return
        
        # Create audit entry
        audit_entry = {
            "timestamp": timestamp.isoformat(),
            "article_id": self._get_article_id(article),
            "article_title": article.title,
            "article_tickers": article.tickers,
            "article_published": article.published.isoformat() if article.published else None,
            "classification": classification.classification.value,
            "confidence": classification.confidence,
            "reasoning": classification.reasoning,
            "source": article.source.value if hasattr(article.source, 'value') else str(article.source)
        }
        
        # Get daily file path
        file_path = self._get_daily_file_path(timestamp)
        
        # Load existing classifications
        classifications = self._load_daily_classifications(file_path)
        
        # Add new entry
        classifications.append(audit_entry)
        
        # Save back to file
        self._save_daily_classifications(file_path, classifications)
        
        logger.info(
            "Logged IMMINENT classification to audit trail",
            article_id=audit_entry["article_id"],
            file_path=str(file_path),
            total_entries=len(classifications)
        )
    
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
