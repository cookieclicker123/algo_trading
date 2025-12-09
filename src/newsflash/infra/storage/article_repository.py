"""
Article repository - handles file I/O for article storage.

Pure infrastructure - handles JSON file operations, rolling window, archiving.
"""
import json
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import aiofiles

# Config now injected via constructor - no direct import needed
from ...utils.logging_config import get_logger
from .types import StorageConfig

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
    
    def __init__(self, storage_config: StorageConfig):
        """
        Initialize article repository.
        
        Args:
            storage_config: Storage configuration dictionary
        """
        self.config = storage_config
        self.tmp_dir = Path(self.config["tmp_dir"])
        self.json_file = self.tmp_dir / self.config["articles_json_file"]
        self.rolling_window_hours = self.config["rolling_window_hours"]
        self.archive_window_hours = 24  # Archive articles after 24 hours
        
        # File operation lock to prevent race conditions in concurrent stores
        # This serializes access to file load/save operations
        self._file_lock = asyncio.Lock()
        
        # Ensure tmp directory exists
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
        
        logger.info(
            "ArticleRepository initialized",
            tmp_dir=str(self.tmp_dir),
            rolling_window_hours=self.rolling_window_hours
        )
    
    async def store_article(self, article_id: str, article_data: Dict[str, Any]) -> tuple[str, bool]:
        """
        Store an article.
        
        Uses file locking to prevent race conditions when multiple articles are stored concurrently.
        
        Args:
            article_id: Unique article identifier
            article_data: Serialized article data (dict)
            
        Returns:
            Tuple of (file_path, is_archived)
        """
        # CRITICAL: Use lock to serialize file operations and prevent race conditions
        # Multiple concurrent store_article calls will wait for each other
        async with self._file_lock:
            # Load existing articles
            existing_articles = await self._load_articles()
            
            # Check if already processed (deduplication)
            if any(self._get_article_id_from_data(a) == article_id for a in existing_articles):
                logger.debug("Article already processed, skipping", article_id=article_id)
                return (str(self.json_file), False)
            
            # Add new article
            existing_articles.append(article_data)
            
            # Save updated articles
            await self._save_articles(existing_articles)
            
            # Cleanup old articles (moves to 24-hour archives)
            # NOTE: Cleanup runs inside lock, so it sees the article we just saved
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
        logger.debug("Repository: Checking rolling window articles", 
                    article_id=article_id, articles_count=len(articles))
        for article in articles:
            # Try different ID formats
            if self._get_article_id_from_data(article) == article_id:
                logger.debug("Repository: Article found in rolling window", article_id=article_id)
                return article
        
        # Then check archives (recent dates first)
        current_date = datetime.now(timezone.utc)
        logger.debug("Repository: Article not in rolling window, checking archives", article_id=article_id)
        for days_back in range(7):  # Check last 7 days
            check_date = current_date - timedelta(days=days_back)
            archive_path = self._get_archive_path(check_date)
            
            if archive_path.exists():
                try:
                    async with aiofiles.open(archive_path, 'r') as f:
                        content = await f.read()
                        archived_articles = json.loads(content) if content.strip() else []
                    
                    logger.debug("Repository: Checking archive file", 
                                article_id=article_id, archive_path=str(archive_path), 
                                archived_articles_count=len(archived_articles))
                    for article in archived_articles:
                        if self._get_article_id_from_data(article) == article_id:
                            logger.debug("Repository: Article found in archive", 
                                        article_id=article_id, archive_path=str(archive_path))
                            return article
                except Exception as e:
                    logger.warning("Failed to read archive file", path=str(archive_path), error=str(e))
        
        logger.debug("Repository: Article not found in rolling window or archives", article_id=article_id)
        return None
    
    def _get_article_id_from_data(self, article_data: Dict[str, Any]) -> str:
        """Extract article ID from article data dict."""
        # Try different ID formats - prioritize article_id (standard format: "source:source_id")
        if 'article_id' in article_data:
            return str(article_data['article_id'])
        if 'id' in article_data:
            return str(article_data['id'])
        # Fallback: construct from source and source_id (use colon format to match domain model)
        if 'source_id' in article_data and 'source' in article_data:
            return f"{article_data['source']}:{article_data['source_id']}"
        if 'benzinga_id' in article_data:
            return str(article_data['benzinga_id'])
        return ""
    
    async def _load_articles(self) -> List[Dict[str, Any]]:
        """Load articles from rolling window JSON file."""
        if not self.json_file.exists():
            logger.debug("Articles JSON file does not exist", path=str(self.json_file))
            return []
        
        try:
            async with aiofiles.open(self.json_file, 'r') as f:
                content = await f.read()
                if not content.strip():
                    logger.debug("Articles JSON file is empty", path=str(self.json_file))
                    return []
                # Try to parse as single JSON array
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    # If that fails, try parsing as newline-delimited JSON (NDJSON)
                    logger.warning("Failed to parse articles as JSON array, trying NDJSON format", 
                                 path=str(self.json_file), error=str(e))
                    articles = []
                    for line_num, line in enumerate(content.strip().split('\n'), 1):
                        if line.strip():
                            try:
                                article = json.loads(line)
                                articles.append(article)
                            except json.JSONDecodeError as line_error:
                                logger.warning("Failed to parse article line", 
                                             path=str(self.json_file), line=line_num, error=str(line_error))
                    return articles
        except FileNotFoundError:
            logger.debug("Articles JSON file not found", path=str(self.json_file))
            return []
        except Exception as e:
            logger.error("Failed to load articles", path=str(self.json_file), error=str(e), exc_info=True)
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
        
        NOTE: This method should be called while holding _file_lock to prevent race conditions.
        The cleanup runs after store_article saves, so it will see the newly stored article.
        
        Returns:
            Path to archive file if articles were archived, None otherwise
        """
        current_time = datetime.now(timezone.utc)
        rolling_cutoff = current_time - timedelta(hours=self.rolling_window_hours)
        archive_cutoff = current_time - timedelta(hours=self.archive_window_hours)
        
        rolling_timestamp = rolling_cutoff.timestamp()
        archive_timestamp = archive_cutoff.timestamp()
        
        # Reload articles (we're inside lock, so this is safe)
        articles = await self._load_articles()
        logger.debug("Cleanup: Loaded articles for cleanup", articles_count=len(articles))
        current_articles = []
        articles_to_archive = []
        
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
                else:
                    # Too old - remove completely
                    pass
                    
            except (ValueError, TypeError) as e:
                # Keep articles with invalid timestamps
                logger.warning("Invalid timestamp in article", error=str(e))
                current_articles.append(article)
        
        # Archive articles if any
        archive_path = None
        if articles_to_archive:
            archive_path = await self._archive_articles(articles_to_archive)
        
        # Save current articles (inside lock, so safe)
        await self._save_articles(current_articles)
        
        if articles_to_archive:
            logger.info("Archived articles", count=len(articles_to_archive), path=str(archive_path))
        
        logger.debug("Cleanup: Completed", 
                    articles_kept=len(current_articles), 
                    articles_archived=len(articles_to_archive) if articles_to_archive else 0)
        
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

