"""
Article repository - handles file I/O for article storage.

Pure infrastructure - handles JSON file operations, rolling window, archiving.
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
import aiofiles

from ...config.settings import get_storage_config
from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class ArticleRepository:
    """
    Repository for article storage operations.
    
    Handles:
    - Rolling window storage (articles.json - 1 hour)
    - Daily archiving (archive/YYYY/MM/week_XX/YYYY-MM-DD.json)
    - Deduplication
    - File I/O operations
    """
    
    def __init__(self):
        """Initialize article repository."""
        self.config = get_storage_config()
        self.tmp_dir = Path(self.config["tmp_dir"])
        self.json_file = self.tmp_dir / self.config["articles_json_file"]
        self.rolling_window_hours = self.config["rolling_window_hours"]
        self.archive_window_hours = 24  # Archive articles after 24 hours
        
        # Ensure tmp directory exists
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
        # Track processed article IDs to avoid duplicates
        self.processed_ids: set[str] = set[str]()
        
        logger.info(
            "ArticleRepository initialized",
            tmp_dir=str(self.tmp_dir),
            rolling_window_hours=self.rolling_window_hours
        )
    
    async def store_article(self, article_id: str, article_data: Dict[str, Any]) -> tuple[str, bool]:
        """
        Store an article.
        
        Args:
            article_id: Unique article identifier
            article_data: Serialized article data (dict)
            
        Returns:
            Tuple of (file_path, is_archived)
        """
        # Check if already processed (deduplication)
        if article_id in self.processed_ids:
            logger.debug("Article already processed, skipping", article_id=article_id)
            return (str(self.json_file), False)
        
        # Add to processed set
        self.processed_ids.add(article_id)
        
        # Load existing articles
        existing_articles = await self._load_articles()
        
        # Add new article
        existing_articles.append(article_data)
        
        # Save updated articles
        await self._save_articles(existing_articles)
        
        # Cleanup old articles (moves to 24-hour archives)
        archived_path = await self._cleanup_and_archive_articles()
        
        if archived_path:
            return (str(archived_path), True)
        
        return (str(self.json_file), False)
    
    async def fetch_article(self, article_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch an article by ID.
        
        Args:
            article_id: Article ID to fetch
            
        Returns:
            Article data if found, None otherwise
        """
        # First check rolling window
        articles = await self._load_articles()
        for article in articles:
            # Try different ID formats
            if self._get_article_id_from_data(article) == article_id:
                return article
        
        # Then check archives (recent dates first)
        current_date = datetime.now(timezone.utc)
        for days_back in range(7):  # Check last 7 days
            check_date = current_date - timedelta(days=days_back)
            archive_path = self._get_archive_path(check_date)
            
            if archive_path.exists():
                try:
                    async with aiofiles.open(archive_path, 'r') as f:
                        content = await f.read()
                        archived_articles = json.loads(content) if content.strip() else []
                    
                    for article in archived_articles:
                        if self._get_article_id_from_data(article) == article_id:
                            return article
                except Exception as e:
                    logger.warning("Failed to read archive file", path=str(archive_path), error=str(e))
        
        return None
    
    def _get_article_id_from_data(self, article_data: Dict[str, Any]) -> str:
        """Extract article ID from article data dict."""
        # Try different ID formats
        if 'benzinga_id' in article_data:
            return str(article_data['benzinga_id'])
        if 'source_id' in article_data and 'source' in article_data:
            return f"{article_data['source']}_{article_data['source_id']}"
        if 'article_id' in article_data:
            return str(article_data['article_id'])
        if 'id' in article_data:
            return str(article_data['id'])
        return ""
    
    async def _load_articles(self) -> List[Dict[str, Any]]:
        """Load articles from rolling window JSON file."""
        if not self.json_file.exists():
            return []
        
        try:
            async with aiofiles.open(self.json_file, 'r') as f:
                content = await f.read()
                return json.loads(content) if content.strip() else []
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning("Failed to load articles", error=str(e))
            return []
    
    async def _save_articles(self, articles: List[Dict[str, Any]]):
        """Save articles to rolling window JSON file."""
        try:
            async with aiofiles.open(self.json_file, 'w') as f:
                await f.write(json.dumps(articles, indent=2, default=str))
        except Exception as e:
            logger.error("Failed to save articles", error=str(e))
            raise
    
    async def _cleanup_and_archive_articles(self) -> Optional[Path]:
        """
        Remove articles older than rolling window and archive them.
        
        Returns:
            Path to archive file if articles were archived, None otherwise
        """
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
                pub_timestamp = self._get_published_timestamp(article)
                
                if pub_timestamp is None:
                    # Keep articles without timestamp
                    current_articles.append(article)
                    continue
                
                # Determine what to do with this article
                if pub_timestamp >= rolling_timestamp:
                    # Keep in current file (within rolling window)
                    current_articles.append(article)
                elif pub_timestamp >= archive_timestamp:
                    # Archive (older than rolling window, but within 24 hours)
                    articles_to_archive.append(article)
                    article_id = self._get_article_id_from_data(article)
                    if article_id:
                        removed_ids.add(article_id)
                else:
                    # Too old - remove completely
                    article_id = self._get_article_id_from_data(article)
                    if article_id:
                        removed_ids.add(article_id)
                    
            except (ValueError, TypeError) as e:
                # Keep articles with invalid timestamps
                logger.warning("Invalid timestamp in article", error=str(e))
                current_articles.append(article)
        
        # Archive articles if any
        archive_path = None
        if articles_to_archive:
            archive_path = await self._archive_articles(articles_to_archive)
        
        # Update processed IDs set
        self.processed_ids -= removed_ids
        
        # Save current articles
        await self._save_articles(current_articles)
        
        if articles_to_archive:
            logger.info("Archived articles", count=len(articles_to_archive), path=str(archive_path))
        
        return archive_path
    
    def _get_published_timestamp(self, article: Dict[str, Any]) -> Optional[float]:
        """Extract published timestamp from article data."""
        if 'published' in article:
            if isinstance(article['published'], str):
                try:
                    pub_time = datetime.fromisoformat(article['published'].replace('Z', '+00:00'))
                    return pub_time.timestamp()
                except ValueError:
                    return None
            else:
                return float(article['published'])
        if 'published_at' in article:
            if isinstance(article['published_at'], str):
                try:
                    pub_time = datetime.fromisoformat(article['published_at'].replace('Z', '+00:00'))
                    return pub_time.timestamp()
                except ValueError:
                    return None
            else:
                return float(article['published_at'])
        return None
    
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
    
    async def _archive_articles(self, articles: List[Dict[str, Any]]) -> Path:
        """Archive articles to dated files."""
        # Group articles by date
        articles_by_date: Dict[str, List[Dict[str, Any]]] = {}
        
        for article in articles:
            try:
                pub_timestamp = self._get_published_timestamp(article)
                if pub_timestamp is None:
                    continue
                
                pub_time = datetime.fromtimestamp(pub_timestamp, tz=timezone.utc)
                date_key = pub_time.strftime("%Y-%m-%d")
                
                if date_key not in articles_by_date:
                    articles_by_date[date_key] = []
                articles_by_date[date_key].append(article)
            except (ValueError, TypeError):
                # Skip articles with invalid timestamps
                continue
        
        # Save articles to their respective date files
        archive_path = None
        for date_key, date_articles in articles_by_date.items():
            archive_path = self._get_archive_path(
                datetime.strptime(date_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            )
            
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
            existing_ids = {self._get_article_id_from_data(a) for a in existing_articles}
            new_articles = [a for a in date_articles if self._get_article_id_from_data(a) not in existing_ids]
            
            if new_articles:
                all_articles = existing_articles + new_articles
                async with aiofiles.open(archive_path, 'w') as f:
                    await f.write(json.dumps(all_articles, indent=2, default=str))
        
        return archive_path if archive_path else Path(self.tmp_dir / "archive")
    
    async def get_recent_articles(self, hours: int = 1) -> List[Dict[str, Any]]:
        """Get articles from the last N hours."""
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_timestamp = cutoff_time.timestamp()
        
        articles = await self._load_articles()
        recent_articles = []
        
        for article in articles:
            pub_timestamp = self._get_published_timestamp(article)
            if pub_timestamp and pub_timestamp >= cutoff_timestamp:
                recent_articles.append(article)
        
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
        except (ValueError, json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning("Failed to get archived articles", date=date, error=str(e))
            return []

