"""
Finlight.me WebSocket service for real-time news streaming.
"""
import asyncio
from typing import Callable, Dict, Any, Optional
from finlight_client import FinlightApi, ApiConfig
from finlight_client.models import GetArticlesWebSocketParams

from ..config.settings import get_api_key
from ..utils.logging_config import get_logger
from ..models.finlight_models import FinlightArticleProcessor
from ..models.base_models import StandardizedArticle

logger = get_logger(__name__)


class FinlightWebSocketService:
    """Service for handling Finlight.me WebSocket connections."""
    
    def __init__(self, article_callback: Callable[[StandardizedArticle], None]):
        """
        Initialize Finlight WebSocket service.
        
        Args:
            article_callback: Function to call when new articles arrive
        """
        self.article_callback = article_callback
        self.client: Optional[FinlightApi] = None
        self.is_connected = False
        self.is_running = False
        self.processor = FinlightArticleProcessor()
        
        # Connection state
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5  # seconds
        
    def _on_article(self, raw_data):
        """Handle incoming article from WebSocket."""
        try:
            logger.info("Received article from Finlight", data=str(raw_data)[:200])
            
            # Convert Article object to dict if needed
            if hasattr(raw_data, '__dict__'):
                # It's an Article object, convert to dict
                logger.info("Converting Article object to dict")
                # Extract tickers from Finlight's AI companies field
                tickers = []
                
                # Check for companies field (main field for ticker data)
                if hasattr(raw_data, 'companies') and raw_data.companies:
                    companies = raw_data.companies
                    if isinstance(companies, list):
                        # Extract ticker from each company object
                        for company in companies:
                            if isinstance(company, dict) and 'ticker' in company:
                                ticker = company['ticker']
                                if ticker and ticker.strip():
                                    tickers.append(ticker.strip().upper())
                            elif hasattr(company, 'ticker') and company.ticker:
                                ticker = company.ticker
                                if ticker and str(ticker).strip():
                                    tickers.append(str(ticker).strip().upper())
                    elif isinstance(companies, str):
                        tickers = [companies.strip().upper()] if companies.strip() else []
                
                # Check for tickers field directly (fallback)
                elif hasattr(raw_data, 'tickers') and raw_data.tickers:
                    tickers_data = raw_data.tickers
                    if isinstance(tickers_data, list):
                        tickers = [str(ticker).strip().upper() for ticker in tickers_data if ticker and str(ticker).strip()]
                    elif isinstance(tickers_data, str):
                        tickers = [tickers_data.strip().upper()] if tickers_data.strip() else []
                
                article_dict = {
                    'id': getattr(raw_data, 'id', None),
                    'title': getattr(raw_data, 'title', ''),
                    'content': getattr(raw_data, 'content', ''),
                    'summary': getattr(raw_data, 'summary', ''),
                    'author': getattr(raw_data, 'author', None),
                    'published_at': getattr(raw_data, 'publishDate', None),
                    'updated_at': None,
                    'url': getattr(raw_data, 'link', None),
                    'tickers': tickers,
                    'tags': [],
                    'category': None,
                    'source': getattr(raw_data, 'source', None)
                }
                logger.info("Article dict created", title=article_dict.get('title', 'No title'), tickers=tickers)
            else:
                # It's already a dict
                logger.info("Data is already a dict")
                article_dict = raw_data
            
            # Convert to standardized format
            standardized_article = self.processor.process_raw_article(article_dict)
            
            # Call the callback
            self.article_callback(standardized_article)
            
            logger.info(
                "Processed Finlight article",
                source_id=standardized_article.source_id,
                title=standardized_article.title[:100],
                tickers=standardized_article.tickers
            )
            
        except Exception as e:
            logger.error("Failed to process Finlight article", error=str(e), data=raw_data)
    
    async def connect(self):
        """Connect to Finlight WebSocket."""
        try:
            api_key = get_api_key("FINLIGHT_API_KEY")
            if not api_key:
                raise ValueError("FINLIGHT_API_KEY not found in environment variables")
            
            # Initialize client with websocket takeover to handle multiple connections
            self.client = FinlightApi(
                config=ApiConfig(api_key=api_key),
                websocket_takeover=True
            )
            
            # Create payload with includeEntities to get ticker data
            payload = GetArticlesWebSocketParams()
            # Add includeEntities parameter to get company/ticker information
            payload.includeEntities = True
            
            # Connect
            await self.client.websocket.connect(
                request_payload=payload,
                on_article=self._on_article
            )
            
            self.is_connected = True
            self.reconnect_attempts = 0
            
            logger.info("Successfully connected to Finlight WebSocket")
            
        except Exception as e:
            logger.error("Failed to connect to Finlight WebSocket", error=str(e))
            self.is_connected = False
            raise
    
    async def disconnect(self):
        """Disconnect from Finlight WebSocket."""
        try:
            if self.client and self.is_connected:
                await self.client.websocket.disconnect()
                self.is_connected = False
                logger.info("Disconnected from Finlight WebSocket")
        except Exception as e:
            logger.error("Error disconnecting from Finlight WebSocket", error=str(e))
    
    async def start(self):
        """Start the WebSocket service."""
        self.is_running = True
        logger.info("Starting Finlight WebSocket service")
        
        while self.is_running:
            try:
                await self.connect()
                
                # Keep connection alive
                while self.is_connected and self.is_running:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error("Finlight WebSocket error", error=str(e))
                self.is_connected = False
                
                # Handle rate limiting (429) with longer backoff
                if "429" in str(e) or "rate limit" in str(e).lower():
                    self.reconnect_delay = min(self.reconnect_delay * 2, 300)  # Max 5 minutes
                    logger.warning(f"Rate limited, increasing backoff to {self.reconnect_delay}s")
                
                if self.is_running and self.reconnect_attempts < self.max_reconnect_attempts:
                    self.reconnect_attempts += 1
                    logger.info(
                        f"Attempting to reconnect to Finlight (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})",
                        delay=self.reconnect_delay
                    )
                    await asyncio.sleep(self.reconnect_delay)
                else:
                    if self.reconnect_attempts >= self.max_reconnect_attempts:
                        logger.error("Max reconnection attempts reached for Finlight WebSocket")
                    break
    
    async def stop(self):
        """Stop the WebSocket service."""
        logger.info("Stopping Finlight WebSocket service")
        self.is_running = False
        await self.disconnect()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            "is_connected": self.is_connected,
            "is_running": self.is_running,
            "reconnect_attempts": self.reconnect_attempts,
            "max_reconnect_attempts": self.max_reconnect_attempts,
            "source": "finlight"
        }
