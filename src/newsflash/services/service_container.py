"""
Service container for dependency injection and lifecycle management.
Centralizes service creation and eliminates global state.
"""
from typing import Optional, Dict, Any
from ..utils.logging_config import get_logger
from .article_processor import get_article_processor
from .feed_manager import FeedManager
from .telegram_service import get_telegram_notifier
from .news_classifier import get_news_classifier
from .translation_service import get_translation_service
from .yfinance_service import get_yfinance_service
from .ibkr_trading_service import get_ibkr_trading_service
from .telegram_trade_handler import get_telegram_trade_handler
from .polling_state_manager import PollingStateManager

logger = get_logger(__name__)


class ServiceContainer:
    """
    Centralized service container for dependency injection.
    Manages service lifecycle and eliminates global state.
    """
    
    def __init__(self):
        """Initialize empty container."""
        self._services: Dict[str, Any] = {}
        self._initialized = False
        
        logger.info("ServiceContainer initialized")
    
    def initialize_services(self) -> None:
        """Initialize all services with proper dependency injection."""
        if self._initialized:
            logger.warning("Services already initialized")
            return
        
        try:
            # Initialize external services first (no dependencies)
            logger.info("Initializing external services...")
            
            # Translation service
            self._services['translator'] = get_translation_service()
            
            # YFinance service  
            self._services['yfinance'] = get_yfinance_service()
            
            # IBKR trading service
            self._services['trading'] = get_ibkr_trading_service()
            
            # Initialize dependent services
            logger.info("Initializing dependent services...")
            
            # Telegram trade handler (depends on trading service)
            from ..config.settings import get_telegram_config
            telegram_config = get_telegram_config()
            bot_token = telegram_config.get("bot_token", "dummy_token")
            
            self._services['trade_handler'] = get_telegram_trade_handler(
                bot_token=bot_token,
                trading_service=self._services['trading']
            )
            
            # Telegram notifier (depends on translator, yfinance, trade_handler)
            self._services['telegram'] = get_telegram_notifier(
                translator=self._services['translator'],
                yfinance_service=self._services['yfinance'],
                trade_handler=self._services['trade_handler']
            )
            
            # News classifier
            self._services['classifier'] = get_news_classifier()
            
            # Article processor (depends on telegram, classifier)
            self._services['article_processor'] = get_article_processor(
                telegram_notifier=self._services['telegram'],
                classifier=self._services['classifier']
            )
            
            # Polling state manager
            self._services['state_manager'] = PollingStateManager()
            
            # Feed manager (depends on article processor)
            self._services['feed_manager'] = FeedManager(
                article_processor=self._services['article_processor']
            )
            
            self._initialized = True
            logger.info("All services initialized successfully")
            
        except Exception as e:
            logger.error("Failed to initialize services", error=str(e))
            raise
    
    def get_service(self, service_name: str) -> Any:
        """Get a service by name."""
        if not self._initialized:
            raise RuntimeError("Services not initialized. Call initialize_services() first.")
        
        if service_name not in self._services:
            raise ValueError(f"Service '{service_name}' not found")
        
        return self._services[service_name]
    
    def get_feed_manager(self) -> FeedManager:
        """Get the feed manager service."""
        return self.get_service('feed_manager')
    
    def get_article_processor(self):
        """Get the article processor service."""
        return self.get_service('article_processor')
    
    def get_telegram_notifier(self):
        """Get the telegram notifier service."""
        return self.get_service('telegram')
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics from all services."""
        if not self._initialized:
            return {"error": "Services not initialized"}
        
        try:
            stats = {
                "feed_manager": self._services['feed_manager'].get_stats(),
                "article_processor": self._services['article_processor'].get_stats(),
                "telegram": {
                    "enabled_1": self._services['telegram'].enabled_1,
                    "enabled_2": self._services['telegram'].enabled_2,
                    "test_mode": self._services['telegram'].test_mode,
                },
                "classifier": self._services['classifier'].get_stats(),
                "trading": {
                    "enabled": self._services['trading'].enabled,
                }
            }
            return stats
        except Exception as e:
            logger.error("Failed to get service stats", error=str(e))
            return {"error": str(e)}
    
    async def start_all_services(self) -> None:
        """Start all services."""
        if not self._initialized:
            raise RuntimeError("Services not initialized")
        
        logger.info("Starting all services...")
        
        try:
            # Start feed manager (this will start all dependent services)
            await self._services['feed_manager'].start_all_feeds()
            logger.info("All services started successfully")
            
        except Exception as e:
            logger.error("Failed to start services", error=str(e))
            raise
    
    async def stop_all_services(self) -> None:
        """Stop all services."""
        if not self._initialized:
            logger.warning("Services not initialized, nothing to stop")
            return
        
        logger.info("Stopping all services...")
        
        try:
            # Stop feed manager (this will stop all dependent services)
            await self._services['feed_manager'].stop_all_feeds()
            logger.info("All services stopped successfully")
            
        except Exception as e:
            logger.error("Failed to stop services", error=str(e))
            raise
    
    def is_healthy(self) -> bool:
        """Check if all services are healthy."""
        if not self._initialized:
            return False
        
        try:
            # Check feed manager health
            feed_manager = self._services['feed_manager']
            return feed_manager.is_healthy()
            
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return False


# Global service container instance
_service_container: Optional[ServiceContainer] = None


def get_service_container() -> ServiceContainer:
    """Get the global service container instance."""
    global _service_container
    if _service_container is None:
        _service_container = ServiceContainer()
    return _service_container


def initialize_services() -> ServiceContainer:
    """Initialize and return the service container."""
    container = get_service_container()
    container.initialize_services()
    return container
