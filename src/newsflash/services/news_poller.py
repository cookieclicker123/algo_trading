"""
Core news polling service for Polygon.io Benzinga API.
Real-time event-driven polling system with 50ms intervals.
"""
import asyncio
import time
import httpx
from typing import List, Dict, Any, Optional
from ..config.settings import get_api_key, API_BASE_URL, get_polling_config
from ..models.benzinga_models import BenzingaArticle
from ..utils.logging_config import get_logger
from .polling_state_manager import PollingStateManager

logger = get_logger(__name__)


class NewsPoller:
    """
    Real-time news polling engine for Benzinga articles.
    
    Features:
    - 50ms polling intervals (20 req/s)
    - Delta-based fetching using updated_gt
    - Event-driven article processing
    - Exponential backoff on errors
    - State persistence across restarts
    """
    
    def __init__(self, article_processor, state_manager: Optional[PollingStateManager] = None):
        """
        Initialize news poller.
        
        Args:
            article_processor: Article processor for handling new articles
            state_manager: Optional state manager (injected dependency)
        """
        self.article_processor = article_processor
        self.state_manager = state_manager or PollingStateManager()
        self.client = None
        self.is_running = False
        self.polling_task = None
        self.config = get_polling_config()
        
        # Error handling constants
        self.max_consecutive_errors = 5
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.client = httpx.AsyncClient(timeout=10.0)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
        if self.client:
            await self.client.aclose()
    
    async def fetch_new_articles(self) -> List[BenzingaArticle]:
        """
        Fetch latest articles and filter for truly new ones.
        
        Returns:
            List of new BenzingaArticle objects
        """
        if not self.client:
            raise RuntimeError("HTTP client not initialized. Use async context manager.")
        
        # Get current state
        state = self.state_manager.get_state()
        
        # Simple approach: get latest articles, no time filtering
        params = {
            "apiKey": get_api_key(),
            "limit": 50  # Get latest 50 articles
        }
        
        try:
            response = await self.client.get(
                f"{API_BASE_URL}/benzinga/v2/news",
                params=params
            )
            response.raise_for_status()
            
            data = response.json()
            all_articles = data.get("results", [])
            
            # Filter for articles we haven't seen before and convert to BenzingaArticle objects
            new_articles = []
            highest_id_seen = state.last_seen_article_id
            
            for article_data in all_articles:
                article_id = article_data.get("benzinga_id", 0)
                if article_id > state.last_seen_article_id:
                    try:
                        # Convert raw data to BenzingaArticle object
                        article = BenzingaArticle.model_validate(article_data)
                        new_articles.append(article)
                        highest_id_seen = max(highest_id_seen, article_id)
                    except Exception as e:
                        logger.error("Failed to parse article", error=str(e), article_id=article_id)
            
            # Update state if we found new articles
            if new_articles:
                self.state_manager.update_last_seen_id(highest_id_seen)
                logger.info(f"Found {len(new_articles)} new articles, updated last seen ID to {highest_id_seen}")
            
            # Reset error counter on success
            self.state_manager.reset_errors()
            
            return new_articles
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("Rate limited, backing off", status_code=429)
                await self._handle_rate_limit()
            else:
                logger.error("HTTP error fetching news", status_code=e.response.status_code)
            
            self.state_manager.increment_errors()
            return []
            
        except Exception as e:
            logger.error("Error fetching news", error=str(e))
            self.state_manager.increment_errors()
            return []
    
    async def _handle_rate_limit(self):
        """Handle rate limiting with exponential backoff."""
        state = self.state_manager.get_state()
        await asyncio.sleep(state.backoff_delay)
        self.state_manager.increase_backoff()
    
    async def _polling_loop(self):
        """Main polling loop - runs every 50ms."""
        logger.info("Starting news polling loop", interval_ms=self.config["interval_seconds"] * 1000)
        
        while self.is_running:
            loop_start = time.time()
            
            try:
                # Check if we've hit too many consecutive errors
                state = self.state_manager.get_state()
                if state.consecutive_errors >= self.max_consecutive_errors:
                    logger.error("Too many consecutive errors, backing off", errors=state.consecutive_errors)
                    await asyncio.sleep(30)  # 30 second backoff
                    self.state_manager.reset_errors()  # Reset after backoff
                    continue
                
                # Fetch and process new articles
                new_articles = await self.fetch_new_articles()
                if new_articles:
                    # Process articles through the processor (handles storage, handlers, etc.)
                    await self.article_processor.process_articles(new_articles)
                
            except Exception as e:
                logger.error("Unexpected error in polling loop", error=str(e))
                self.state_manager.increment_errors()
            
            # Calculate sleep time to maintain 50ms intervals
            loop_duration = time.time() - loop_start
            sleep_time = max(0, self.config["interval_seconds"] - loop_duration)
            
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            else:
                logger.warning("Polling loop taking longer than interval", duration_ms=loop_duration * 1000)
    
    async def start(self):
        """Start the polling loop and keep it running."""
        if self.is_running:
            logger.warning("Polling already running")
            return
        
        self.is_running = True
        logger.info("News poller started")
        
        try:
            # Run the polling loop directly instead of creating a task
            await self._polling_loop()
        except asyncio.CancelledError:
            logger.info("News poller cancelled")
            raise
        except Exception as e:
            logger.error("Error in news poller", error=str(e))
            raise
        finally:
            self.is_running = False
            logger.info("News poller stopped")
    
    async def stop(self):
        """Stop the polling loop gracefully."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self.polling_task:
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
        
        logger.info("News poller stopped")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current polling statistics."""
        return {
            "is_running": self.is_running,
            "last_seen_article_id": self.last_seen_article_id,
            "consecutive_errors": self.consecutive_errors,
            "backoff_delay": self.backoff_delay,
            "polling_interval_ms": self.config["interval_seconds"] * 1000
        }
