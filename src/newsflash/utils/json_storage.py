"""
JSON storage utility for managing article data with rolling window and 24-hour archiving.
"""
import json
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Set, Optional
from pathlib import Path
import asyncio
import aiofiles

from ..config.settings import get_storage_config
from ..models.benzinga_models import BenzingaArticle


class ArticleStorage:
    """
    Manages storage of articles in JSON format with rolling window and 24-hour archiving.
    
    Features:
    - Stores unique articles in JSON format
    - Rolling window cleanup (removes articles older than 1 hour from current file)
    - 24-hour archival system (moves old articles to dated files)
    - Organized folder structure (year/month/week/date.json)
    - Thread-safe operations
    - Delta-based deduplication
    """
    
    def __init__(self):
        self.config = get_storage_config()
        self.tmp_dir = Path(self.config["tmp_dir"])
        self.json_file = self.tmp_dir / self.config["articles_json_file"]
        self.rolling_window_hours = self.config["rolling_window_hours"]
        self.archive_window_hours = 24  # Archive articles after 24 hours
        
        # Ensure tmp directory exists
        self.tmp_dir.mkdir(exist_ok=True)
        
        # Track processed article IDs to avoid duplicates
        self.processed_ids: Set[int] = set()
        self._load_existing_ids()
    
    def _load_existing_ids(self):
        """Load existing article IDs from JSON file to avoid duplicates."""
        # Don't load any existing IDs - start fresh every time
        # The delta approach with updated_gt should handle deduplication
        self.processed_ids = set()
        print("Starting with empty processed IDs set - delta approach will handle deduplication")
    
    async def store_articles(self, articles: List[BenzingaArticle]) -> List[BenzingaArticle]:
        """
        Store new articles in JSON format, avoiding duplicates.
        
        Args:
            articles: List of articles to store
            
        Returns:
            List of newly stored articles (not duplicates)
        """
        new_articles = []
        
        for article in articles:
            # Skip if we've already processed this article
            if article.benzinga_id in self.processed_ids:
                continue
            
            # Add to processed set
            self.processed_ids.add(article.benzinga_id)
            new_articles.append(article)
        
        if not new_articles:
            return []
        
        # Load existing articles
        existing_articles = await self._load_articles()
        
        # Add new articles
        for article in new_articles:
            existing_articles.append(article.to_dict())
        
        # Save updated articles
        await self._save_articles(existing_articles)
        
        # Cleanup old articles (moves to 24-hour archives)
        await self._cleanup_and_archive_articles()
        
        return new_articles
    
    async def _load_articles(self) -> List[Dict[str, Any]]:
        """Load articles from JSON file."""
        if not self.json_file.exists():
            return []
        
        try:
            async with aiofiles.open(self.json_file, 'r') as f:
                content = await f.read()
                return json.loads(content) if content.strip() else []
        except (json.JSONDecodeError, FileNotFoundError):
            return []
    
    async def _save_articles(self, articles: List[Dict[str, Any]]):
        """Save articles to JSON file."""
        async with aiofiles.open(self.json_file, 'w') as f:
            await f.write(json.dumps(articles, indent=2, default=str))
    
    async def _cleanup_and_archive_articles(self):
        """Remove articles older than rolling window and archive them."""
        current_time = datetime.now(timezone.utc)
        rolling_cutoff = current_time - timedelta(hours=self.rolling_window_hours)
        archive_cutoff = current_time - timedelta(hours=self.archive_window_hours)
        
        rolling_timestamp = rolling_cutoff.timestamp()
        archive_timestamp = archive_cutoff.timestamp()
        
        articles = await self._load_articles()
        current_articles = []
        articles_to_archive = []
        removed_ids = set()
        
        for article in articles:
            try:
                # Parse published timestamp
                if 'published' in article:
                    if isinstance(article['published'], str):
                        pub_time = datetime.fromisoformat(article['published'].replace('Z', '+00:00'))
                        pub_timestamp = pub_time.timestamp()
                    else:
                        pub_timestamp = float(article['published'])
                    
                    # Determine what to do with this article
                    if pub_timestamp >= rolling_timestamp:
                        # Keep in current file (within rolling window)
                        current_articles.append(article)
                    elif pub_timestamp >= archive_timestamp:
                        # Archive (older than rolling window, but within 24 hours)
                        articles_to_archive.append(article)
                        if 'benzinga_id' in article:
                            removed_ids.add(article['benzinga_id'])
                    else:
                        # Too old - remove completely
                        if 'benzinga_id' in article:
                            removed_ids.add(article['benzinga_id'])
                else:
                    # Keep articles without timestamp
                    current_articles.append(article)
                    
            except (ValueError, TypeError):
                # Keep articles with invalid timestamps
                current_articles.append(article)
        
        # Archive articles if any
        if articles_to_archive:
            await self._archive_articles(articles_to_archive)
        
        # Update processed IDs set
        self.processed_ids -= removed_ids
        
        # Save current articles
        await self._save_articles(current_articles)
        
        if articles_to_archive:
            print(f"Archived {len(articles_to_archive)} articles")
        if removed_ids and not articles_to_archive:
            print(f"Cleaned up {len(removed_ids)} old articles")
    
    def _get_archive_path(self, article_date: datetime) -> Path:
        """Get the archive path for articles from a specific date."""
        year = article_date.strftime("%Y")
        month = article_date.strftime("%m")
        week = article_date.strftime("%W")  # Week of year
        date_str = article_date.strftime("%Y-%m-%d")
        
        # Create archive directory structure: year/month/week/
        archive_dir = self.tmp_dir / "archive" / year / month / f"week_{week}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        
        # Archive file: YYYY-MM-DD.json
        return archive_dir / f"{date_str}.json"
    
    async def _archive_articles(self, articles: List[Dict[str, Any]]):
        """Archive articles to dated files."""
        # Group articles by date
        articles_by_date: Dict[str, List[Dict[str, Any]]] = {}
        
        for article in articles:
            try:
                if 'published' in article:
                    if isinstance(article['published'], str):
                        pub_time = datetime.fromisoformat(article['published'].replace('Z', '+00:00'))
                    else:
                        pub_time = datetime.fromtimestamp(float(article['published']), tz=timezone.utc)
                    
                    date_key = pub_time.strftime("%Y-%m-%d")
                    if date_key not in articles_by_date:
                        articles_by_date[date_key] = []
                    articles_by_date[date_key].append(article)
            except (ValueError, TypeError):
                # Skip articles with invalid timestamps
                continue
        
        # Save articles to their respective date files
        for date_key, date_articles in articles_by_date.items():
            archive_path = self._get_archive_path(datetime.strptime(date_key, "%Y-%m-%d").replace(tzinfo=timezone.utc))
            
            # Load existing archived articles for this date
            existing_articles = []
            if archive_path.exists():
                try:
                    async with aiofiles.open(archive_path, 'r') as f:
                        content = await f.read()
                        existing_articles = json.loads(content) if content.strip() else []
                except (json.JSONDecodeError, FileNotFoundError):
                    existing_articles = []
            
            # Add new articles (avoid duplicates)
            existing_ids = {a.get('benzinga_id') for a in existing_articles if 'benzinga_id' in a}
            new_articles = [a for a in date_articles if a.get('benzinga_id') not in existing_ids]
            
            if new_articles:
                all_articles = existing_articles + new_articles
                async with aiofiles.open(archive_path, 'w') as f:
                    await f.write(json.dumps(all_articles, indent=2, default=str))
    
    async def get_recent_articles(self, hours: int = 1) -> List[Dict[str, Any]]:
        """Get articles from the last N hours."""
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_timestamp = cutoff_time.timestamp()
        
        articles = await self._load_articles()
        recent_articles = []
        
        for article in articles:
            try:
                if 'published' in article:
                    if isinstance(article['published'], str):
                        pub_time = datetime.fromisoformat(article['published'].replace('Z', '+00:00'))
                        pub_timestamp = pub_time.timestamp()
                    else:
                        pub_timestamp = float(article['published'])
                    
                    if pub_timestamp >= cutoff_timestamp:
                        recent_articles.append(article)
            except (ValueError, TypeError):
                continue
        
        return recent_articles
    
    async def get_archived_articles(self, date: str) -> List[Dict[str, Any]]:
        """
        Get archived articles for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format
            
        Returns:
            List of archived articles for that date
        """
        try:
            archive_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            archive_path = self._get_archive_path(archive_date)
            
            if not archive_path.exists():
                return []
            
            async with aiofiles.open(archive_path, 'r') as f:
                content = await f.read()
                return json.loads(content) if content.strip() else []
        except (ValueError, json.JSONDecodeError, FileNotFoundError):
            return []
    
    async def get_archive_stats(self) -> Dict[str, Any]:
        """Get statistics about archived articles."""
        archive_dir = self.tmp_dir / "archive"
        if not archive_dir.exists():
            return {"total_archived_dates": 0, "total_archived_files": 0}
        
        total_dates = 0
        total_files = 0
        
        # Walk through archive directory structure
        for year_dir in archive_dir.iterdir():
            if year_dir.is_dir():
                for month_dir in year_dir.iterdir():
                    if month_dir.is_dir():
                        for week_dir in month_dir.iterdir():
                            if week_dir.is_dir():
                                for file_path in week_dir.glob("*.json"):
                                    if file_path.is_file():
                                        total_files += 1
                                        total_dates += 1
        
        return {
            "total_archived_dates": total_dates,
            "total_archived_files": total_files,
            "archive_directory": str(archive_dir)
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        return {
            "total_processed": len(self.processed_ids),
            "json_file_exists": self.json_file.exists(),
            "json_file_size": self.json_file.stat().st_size if self.json_file.exists() else 0,
            "rolling_window_hours": self.rolling_window_hours,
            "archive_window_hours": self.archive_window_hours,
        }
