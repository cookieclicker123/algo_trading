"""
Lifecycle manager for starting and stopping services.

This service orchestrates the startup and shutdown sequence of all microservices.
It's created via dependency injection to ensure all dependencies are properly injected.

STATELESS PRINCIPLE:
- LifecycleManager is the SINGLE SOURCE OF TRUTH for service running state
- Services don't need is_running flags - lifecycle manager tracks this
- Services are idempotent (safe to call start/stop multiple times)
"""
from ..utils.bot_conflict_resolver import resolve_bot_conflicts
from ..utils.logging_config import get_logger
from .service_initialization import Services
from typing import Set

logger = get_logger(__name__)


class LifecycleManager:
    """
    Manages the lifecycle of all services.
    
    This service orchestrates startup and shutdown sequences.
    All dependencies (like config) are injected via DI.
    
    SINGLE SOURCE OF TRUTH:
    - Tracks which services are currently running
    - Services don't need is_running flags - check lifecycle manager instead
    - Services are idempotent (safe to call start/stop multiple times)
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
        
        # Track which services are currently running
        # This is the SINGLE SOURCE OF TRUTH for service state
        self._running_services: Set[str] = set[str]()
    
    def is_service_running(self, service_name: str) -> bool:
        """
        Check if a service is currently running.
        
        This is the SINGLE SOURCE OF TRUTH for service running state.
        Services should use this instead of maintaining their own is_running flags.
        
        Args:
            service_name: Name of the service to check
            
        Returns:
            True if service is running, False otherwise
        """
        return service_name in self._running_services
    
    def _mark_service_running(self, service_name: str) -> None:
        """Mark a service as running."""
        self._running_services.add(service_name)
        logger.debug(f"Service '{service_name}' marked as running")
    
    def _mark_service_stopped(self, service_name: str) -> None:
        """Mark a service as stopped."""
        self._running_services.discard(service_name)
        logger.debug(f"Service '{service_name}' marked as stopped")
    
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
                self._mark_service_running("trade_handler_1")
                logger.info("Telegram trade handler 1 started")
            
            if services.trade_handler_2 and self.telegram_config_2.get("enabled"):
                await services.trade_handler_2.start()
                self._mark_service_running("trade_handler_2")
                logger.info("Telegram trade handler 2 started")
            
            # Start each microservice (they are idempotent - safe to call multiple times)
            await services.storage.start()
            self._mark_service_running("storage")
            
            await services.classification.start()
            self._mark_service_running("classification")
            
            await services.notification.start()
            self._mark_service_running("notification")
            
            await services.brokerage.start()
            self._mark_service_running("brokerage")
            
            await services.websocket.start()
            self._mark_service_running("websocket")
            
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
            import asyncio
            
            # Stop shared services first (Telegram bots can block)
            if services.trade_handler:
                try:
                    await asyncio.wait_for(
                        services.trade_handler.stop(),
                        timeout=5.0  # 5 second timeout for Telegram bot
                    )
                    self._mark_service_stopped("trade_handler_1")
                    logger.info("Telegram trade handler 1 stopped")
                except asyncio.TimeoutError:
                    logger.warning("Telegram trade handler 1 stop timed out")
                    self._mark_service_stopped("trade_handler_1")
            
            if services.trade_handler_2:
                try:
                    await asyncio.wait_for(
                        services.trade_handler_2.stop(),
                        timeout=5.0  # 5 second timeout for Telegram bot
                    )
                    self._mark_service_stopped("trade_handler_2")
                    logger.info("Telegram trade handler 2 stopped")
                except asyncio.TimeoutError:
                    logger.warning("Telegram trade handler 2 stop timed out")
                    self._mark_service_stopped("trade_handler_2")
            
            # Stop microservices in reverse order (they are idempotent - safe to call multiple times)
            await services.websocket.stop()
            self._mark_service_stopped("websocket")
            
            await services.brokerage.stop()
            self._mark_service_stopped("brokerage")
            
            await services.notification.stop()
            self._mark_service_stopped("notification")
            
            await services.classification.stop()
            self._mark_service_stopped("classification")
            
            await services.storage.stop()
            self._mark_service_stopped("storage")
            
            logger.info("All services stopped successfully")
            
        except Exception as e:
            logger.error("Failed to stop services", error=str(e))
            # Don't raise - we want to ensure we try to stop everything

