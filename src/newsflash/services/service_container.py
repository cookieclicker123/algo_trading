"""
Service container for dependency injection and lifecycle management.
Centralizes service creation and eliminates global state.
"""
from typing import Optional, Dict, Any
from ..utils.bot_conflict_resolver import resolve_bot_conflicts
from ..utils.logging_config import get_logger
from ..config.settings import BENZINGA_API_KEY, BENZINGA_WEBSOCKET_ENABLED
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
            self._services['trading'] = get_ibkr_trading_service(paper_trading=True)
            
            # Initialize dependent services
            logger.info("Initializing dependent services...")
            
            # Telegram trade handlers (one per bot) - depend on trading service
            from ..config.settings import get_telegram_config, get_telegram_config_2
            telegram_config_1 = get_telegram_config()
            telegram_config_2 = get_telegram_config_2()
            bot_token_1 = telegram_config_1.get("bot_token", "")
            bot_token_2 = telegram_config_2.get("bot_token", "")

            self._services['trade_handler'] = None
            self._services['trade_handler_2'] = None

            if telegram_config_1.get("enabled") and bot_token_1:
                self._services['trade_handler'] = get_telegram_trade_handler(
                    bot_token=bot_token_1,
                    trading_service=self._services['trading']
                )
            if telegram_config_2.get("enabled") and bot_token_2:
                self._services['trade_handler_2'] = get_telegram_trade_handler(
                    bot_token=bot_token_2,
                    trading_service=self._services['trading']
                )
            
            # Telegram notifier (depends on translator, yfinance, trade_handler)
            self._services['telegram'] = get_telegram_notifier(
                translator=self._services['translator'],
                yfinance_service=self._services['yfinance'],
                trade_handler=self._services['trade_handler'],
                trade_handler_2=self._services['trade_handler_2']
            )
            
            # News classifier
            from ..config.settings import GROQ_API_KEY, GROQ_MODEL
            self._services['classifier'] = get_news_classifier(
                api_key=GROQ_API_KEY,
                model=GROQ_MODEL
            )
            
            # Article processor (depends on telegram, classifier)
            self._services['article_processor'] = get_article_processor(
                telegram_notifier=self._services['telegram'],
                classifier=self._services['classifier']
            )
            
            # Polling state manager
            self._services['state_manager'] = PollingStateManager()
            
            # Feed manager (depends on article processor)
            benzinga_token = BENZINGA_API_KEY if BENZINGA_WEBSOCKET_ENABLED else None
            self._services['feed_manager'] = FeedManager(
                article_processor=self._services['article_processor'],
                benzinga_token=benzinga_token
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
            # Resolve bot conflicts before starting services
            from ..config.settings import get_telegram_config, get_telegram_config_2
            config_1 = get_telegram_config()
            config_2 = get_telegram_config_2()
            bot_tokens = [
                config_1.get("bot_token", ""),
                config_2.get("bot_token", "")
            ]
            
            # Resolve conflicts non-aggressively for production
            conflict_resolved = await resolve_bot_conflicts(bot_tokens, aggressive=False)
            if not conflict_resolved:
                logger.warning("Bot conflicts detected but not resolved - services may fail to start")
            
            # Start Telegram trade handlers first
            if self._services['trade_handler']:
                await self._services['trade_handler'].start()
                logger.info("Telegram trade handler 1 started")
            
            if self._services['trade_handler_2']:
                await self._services['trade_handler_2'].start()
                logger.info("Telegram trade handler 2 started")
            
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
            
            # Stop Telegram trade handlers
            if self._services['trade_handler']:
                await self._services['trade_handler'].stop()
                logger.info("Telegram trade handler 1 stopped")
            
            if self._services['trade_handler_2']:
                await self._services['trade_handler_2'].stop()
                logger.info("Telegram trade handler 2 stopped")
            
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
