"""
Lifecycle manager for starting and stopping services.

This service orchestrates the startup and shutdown sequence of all microservices.
It's created via dependency injection to ensure all dependencies are properly injected.
"""
from ..utils.bot_conflict_resolver import resolve_bot_conflicts
from ..utils.logging_config import get_logger
from .service_initialization import Services

logger = get_logger(__name__)


class LifecycleManager:
    """
    Manages the lifecycle of all services.
    
    This service orchestrates startup and shutdown sequences.
    All dependencies (like config) are injected via DI.
    """
    
    def __init__(
        self,
        telegram_config_1: dict,
        telegram_config_2: dict,
    ):
        """
        Initialize lifecycle manager.
        
        Args:
            telegram_config_1: Primary Telegram bot configuration
            telegram_config_2: Secondary Telegram bot configuration
        """
        self.telegram_config_1 = telegram_config_1
        self.telegram_config_2 = telegram_config_2
    
    async def start_services(self, services: Services) -> None:
        """
        Start all services in the correct order.
        
        Args:
            services: Services container with all microservices
        """
        logger.info("Starting all services...")
        
        try:
            # Resolve bot conflicts (shared concern)
            bot_tokens = []
            if self.telegram_config_1.get("enabled") and self.telegram_config_1.get("bot_token"):
                bot_tokens.append(self.telegram_config_1.get("bot_token"))
            if self.telegram_config_2.get("enabled") and self.telegram_config_2.get("bot_token"):
                bot_tokens.append(self.telegram_config_2.get("bot_token"))
            
            if bot_tokens:
                conflict_resolved = await resolve_bot_conflicts(bot_tokens, aggressive=True)
                if not conflict_resolved:
                    logger.warning("Bot conflicts detected but not resolved - services may fail to start")
            else:
                logger.info("No enabled bots found, skipping conflict resolution")
            
            # Start Telegram trade handlers (shared services)
            if services.trade_handler and self.telegram_config_1.get("enabled"):
                await services.trade_handler.start()
                logger.info("Telegram trade handler 1 started")
            
            if services.trade_handler_2 and self.telegram_config_2.get("enabled"):
                await services.trade_handler_2.start()
                logger.info("Telegram trade handler 2 started")
            
            # Start each microservice (they manage their own lifecycle!)
            await services.storage.start()
            await services.classification.start()
            await services.notification.start()
            await services.brokerage.start()
            await services.websocket.start()
            
            logger.info("All services started successfully")
            
        except Exception as e:
            logger.error("Failed to start services", error=str(e))
            raise
    
    async def stop_services(self, services: Services) -> None:
        """
        Stop all services in the correct order.
        
        Args:
            services: Services container with all microservices
        """
        logger.info("Stopping all services...")
        
        try:
            # Stop microservices in reverse order (they manage their own lifecycle!)
            await services.websocket.stop()
            await services.brokerage.stop()
            await services.notification.stop()
            await services.classification.stop()
            await services.storage.stop()
            
            # Stop shared services
            if services.trade_handler:
                await services.trade_handler.stop()
                logger.info("Telegram trade handler 1 stopped")
            
            if services.trade_handler_2:
                await services.trade_handler_2.stop()
                logger.info("Telegram trade handler 2 stopped")
            
            logger.info("All services stopped successfully")
            
        except Exception as e:
            logger.error("Failed to stop services", error=str(e))
            raise

