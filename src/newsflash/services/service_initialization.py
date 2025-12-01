"""
Simple service initialization - replaces ServiceContainer.
Temporary until proper dependency injection in Chapter 7.
"""
import asyncio
from typing import Dict, Any, Optional

from ..utils.bot_conflict_resolver import resolve_bot_conflicts
from ..utils.logging_config import get_logger
from ..config.settings import BENZINGA_API_KEY, BENZINGA_WEBSOCKET_ENABLED, get_telegram_config, get_telegram_config_2, GROQ_API_KEY, GROQ_MODEL, CLASSIFICATION_ENABLED

from .article_processor import get_article_processor
from .websocket.feed_manager import FeedManager
from .telegram_service import get_telegram_notifier
# NewsClassifier removed - now handled by classification microservice
# YFinance removed - no longer used
from .telegram_trade_handler import get_telegram_trade_handler
from .websocket.feed_health_monitor import FeedHealthMonitor
# ClassificationAuditTrail removed - all audit logging now goes through storage microservice

# New brokerage infrastructure and use cases
from ..infra.brokerage import IBKRBrokerageService

# Classification microservice
from ..infra.classification import ClassificationInfrastructureService
from ..domain.classification.listener import ClassificationDomainListener
from ..use_cases.classify_article_use_case import ClassifyArticleUseCase

# Storage microservice
from ..infra.storage import StorageInfrastructureService
from ..domain.storage.listener import StorageDomainListener
from ..services.storage import StorageQueryService
from ..use_cases.store_article_use_case import StoreArticleUseCase
from ..use_cases.store_audit_log_use_case import StoreAuditLogUseCase

logger = get_logger(__name__)


class Services:
    """Simple container to hold all services."""
    
    def __init__(self):
        self.yfinance = None
        self.brokerage = None  # New brokerage service
        self.trade_handler = None
        self.trade_handler_2 = None
        self.telegram = None
        self.audit_trail = None
        self.process_article_use_case = None  # WebSocket use case
        self.article_processor = None
        self.feed_manager = None
        self.benzinga_websocket = None
        self.health_monitor = None
        self._health_monitor_task: Optional[asyncio.Task] = None
        self.websocket_domain_listener = None
        self.brokerage_domain_listener = None
        
        # Classification microservice
        self.classification_infra = None
        self.classification_domain_listener = None
        self.classify_article_use_case = None
        
        # Storage microservice
        self.storage_infra = None
        self.storage_domain_listener = None
        self.storage_query_service = None
        self.store_article_use_case = None
        self.store_audit_log_use_case = None
        
        # Legacy compatibility (will be removed)
        self.trading = None  # Deprecated - use self.brokerage instead


def initialize_services() -> Services:
    """Initialize all services."""
    logger.info("Initializing services...")
    services = Services()
    
    try:
        # Initialize external services first (no dependencies)
        logger.info("Initializing external services...")
        
        # New brokerage service (infrastructure layer)
        services.brokerage = IBKRBrokerageService(paper_trading=True, client_id=5)
        logger.info("IBKR Brokerage Service initialized")
        
        # Legacy compatibility (for telegram handlers during migration)
        services.trading = services.brokerage
        
        # Initialize dependent services
        logger.info("Initializing dependent services...")
        
        # Telegram trade handlers (still using brokerage service)
        telegram_config_1 = get_telegram_config()
        telegram_config_2 = get_telegram_config_2()
        bot_token_1 = telegram_config_1.get("bot_token", "")
        bot_token_2 = telegram_config_2.get("bot_token", "")
        
        if telegram_config_1.get("enabled") and bot_token_1:
            services.trade_handler = get_telegram_trade_handler(
                bot_token=bot_token_1,
                trading_service=services.brokerage
            )
        
        if telegram_config_2.get("enabled") and bot_token_2:
            services.trade_handler_2 = get_telegram_trade_handler(
                bot_token=bot_token_2,
                trading_service=services.brokerage
            )
        
        # Telegram notifier
        services.telegram = get_telegram_notifier(
            yfinance_service=None,  # YFinance removed
            trade_handler=services.trade_handler,
            trade_handler_2=services.trade_handler_2
        )
        
        # Classification microservice - Infrastructure layer
        services.classification_infra = ClassificationInfrastructureService(
            api_key=GROQ_API_KEY,
            model=GROQ_MODEL,
            enabled=CLASSIFICATION_ENABLED
        )
        logger.info("ClassificationInfrastructureService initialized")
        
        # Classification microservice - Domain layer
        services.classification_domain_listener = ClassificationDomainListener()
        logger.info("ClassificationDomainListener initialized")
        
        # Classification microservice - Use cases layer
        services.classify_article_use_case = ClassifyArticleUseCase()
        logger.info("ClassifyArticleUseCase initialized")
        
        # Note: Audit logging is now handled by StoreAuditLogUseCase (storage microservice)
        
        # Storage microservice - Infrastructure layer
        services.storage_infra = StorageInfrastructureService()
        logger.info("StorageInfrastructureService initialized")
        
        # Storage microservice - Domain layer
        services.storage_domain_listener = StorageDomainListener()
        logger.info("StorageDomainListener initialized")
        
        # Storage microservice - Services layer
        services.storage_query_service = StorageQueryService()
        logger.info("StorageQueryService initialized")
        
        # Storage microservice - Use cases layer
        services.store_article_use_case = StoreArticleUseCase()
        logger.info("StoreArticleUseCase initialized")
        
        services.store_audit_log_use_case = StoreAuditLogUseCase(
            storage_query_service=services.storage_query_service
        )
        logger.info("StoreAuditLogUseCase initialized - uses StorageQueryService")
        
        # Auto-trade service (handles trading logic, subscribes to domain events)
        from ..services.brokerage.auto_trade_service import AutoTradeService
        services.auto_trade_service = AutoTradeService(
            storage_query_service=services.storage_query_service
        )
        logger.info("AutoTradeService initialized - uses storage query service")
        
        # Article processor (classification removed - handled by classification microservice)
        article_processor = get_article_processor(
            telegram_notifier=services.telegram,
            auto_trade_service=services.auto_trade_service  # Use trading service
        )
        services.article_processor = article_processor
        
        # Feed manager (no WebSocket management - just event subscription, no article_processor coupling)
        services.feed_manager = FeedManager()
        
        # Process article use case (orchestrates services and use cases)
        from ..use_cases.process_article_use_case import ProcessArticleUseCase
        services.process_article_use_case = ProcessArticleUseCase(notification_service=services.telegram)
        logger.info("ProcessArticleUseCase initialized - subscribes to Domain.ArticleClassified (event-driven)")
        logger.info("Note: Storage is handled by StoreArticleUseCase (event-driven)")
        
        # Benzinga WebSocket microservice (infrastructure layer - managed separately)
        if BENZINGA_WEBSOCKET_ENABLED and BENZINGA_API_KEY:
            from ..infra.websocket.service import BenzingaWebSocketMicroservice
            services.benzinga_websocket = BenzingaWebSocketMicroservice(token=BENZINGA_API_KEY)
            logger.info("Benzinga WebSocket microservice initialized")
        else:
            services.benzinga_websocket = None
            logger.info("Benzinga WebSocket microservice disabled")
        
        # Health monitor (no feed_manager dependency - just event subscription)
        services.health_monitor = FeedHealthMonitor(
            telegram_service=services.telegram
        )
        logger.info("Feed health monitor initialized")
        
        # Domain listeners - bridge between infrastructure and domain
        from ..domain.websocket.listener import WebSocketDomainListener
        from ..domain.brokerage.listener import BrokerageDomainListener
        
        services.websocket_domain_listener = WebSocketDomainListener()
        logger.info("WebSocket domain listener initialized")
        
        services.brokerage_domain_listener = BrokerageDomainListener()
        logger.info("Brokerage domain listener initialized")
        
        # Classification domain listener already initialized above
        logger.info("All domain listeners initialized")
        
        logger.info("All services initialized successfully")
        
    except Exception as e:
        logger.error("Failed to initialize services", error=str(e))
        raise
    
    return services


async def start_services(services: Services) -> None:
    """Start all services."""
    logger.info("Starting all services...")
    
    try:
        # Resolve bot conflicts
        telegram_config_1 = get_telegram_config()
        telegram_config_2 = get_telegram_config_2()
        
        bot_tokens = []
        if telegram_config_1.get("enabled") and telegram_config_1.get("bot_token"):
            bot_tokens.append(telegram_config_1.get("bot_token"))
        if telegram_config_2.get("enabled") and telegram_config_2.get("bot_token"):
            bot_tokens.append(telegram_config_2.get("bot_token"))
        
        if bot_tokens:
            conflict_resolved = await resolve_bot_conflicts(bot_tokens, aggressive=True)
            if not conflict_resolved:
                logger.warning("Bot conflicts detected but not resolved - services may fail to start")
        else:
            logger.info("No enabled bots found, skipping conflict resolution")
        
        # Start Telegram trade handlers
        telegram_config_1 = get_telegram_config()
        telegram_config_2 = get_telegram_config_2()
        
        if services.trade_handler and telegram_config_1.get("enabled"):
            await services.trade_handler.start()
            logger.info("Telegram trade handler 1 started")
        elif services.trade_handler:
            logger.info("Telegram trade handler 1 not started (bot 1 disabled)")
        
        if services.trade_handler_2 and telegram_config_2.get("enabled"):
            await services.trade_handler_2.start()
            logger.info("Telegram trade handler 2 started")
        elif services.trade_handler_2:
            logger.info("Telegram trade handler 2 not started (bot 2 disabled)")
        
        # Start WebSocket microservice FIRST (infrastructure layer)
        if services.benzinga_websocket:
            services.benzinga_websocket.start()
            logger.info("Benzinga WebSocket microservice started")
        
        # Start brokerage service (will connect automatically on startup)
        logger.info("About to start IBKR Brokerage Service...")
        await services.brokerage.start()
        logger.info("IBKR Brokerage Service started")
        
        # Start classification infrastructure service FIRST (infrastructure layer)
        await services.classification_infra.start()
        logger.info("ClassificationInfrastructureService started")
        
        # Start storage infrastructure service FIRST (infrastructure layer)
        await services.storage_infra.start()
        logger.info("StorageInfrastructureService started")
        
        # Start domain listeners (bridge infrastructure → domain)
        await services.websocket_domain_listener.start()
        logger.info("WebSocket domain listener started")
        
        await services.brokerage_domain_listener.start()
        logger.info("Brokerage domain listener started")
        
        await services.classification_domain_listener.start()
        logger.info("Classification domain listener started")
        
        await services.storage_domain_listener.start()
        logger.info("Storage domain listener started")
        
        # Start classification use cases
        await services.classify_article_use_case.start()
        logger.info("ClassifyArticleUseCase started")
        
        # Start storage services
        await services.storage_query_service.start()
        logger.info("StorageQueryService started")
        
        # Start storage use cases
        await services.store_article_use_case.start()
        logger.info("StoreArticleUseCase started")
        
        await services.store_audit_log_use_case.start()
        logger.info("StoreAuditLogUseCase started")
        
        # Start feed manager (non-blocking, event subscription only)
        asyncio.create_task(services.feed_manager.start_all_feeds())
        logger.info("Feed manager started")
        
        # Start process article use case (subscribes to Domain.ArticleReceived)
        await services.process_article_use_case.start()
        logger.info("Process article use case started")
        
        # Start health monitor as background task
        services._health_monitor_task = asyncio.create_task(
            services.health_monitor.start()
        )
        logger.info("Feed health monitor started")
        
        logger.info("All services started successfully")
        
    except Exception as e:
        logger.error("Failed to start services", error=str(e))
        raise


async def stop_services(services: Services) -> None:
    """Stop all services."""
    logger.info("Stopping all services...")
    
    try:
        # Stop storage use cases
        if services.store_audit_log_use_case:
            await services.store_audit_log_use_case.stop()
            logger.info("StoreAuditLogUseCase stopped")
        
        if services.store_article_use_case:
            await services.store_article_use_case.stop()
            logger.info("StoreArticleUseCase stopped")
        
        # Stop storage services
        if services.storage_query_service:
            await services.storage_query_service.stop()
            logger.info("StorageQueryService stopped")
        
        # Stop classification use cases
        if services.classify_article_use_case:
            await services.classify_article_use_case.stop()
            logger.info("ClassifyArticleUseCase stopped")
        
        # Stop domain listeners
        if services.storage_domain_listener:
            await services.storage_domain_listener.stop()
            logger.info("Storage domain listener stopped")
        
        if services.websocket_domain_listener:
            await services.websocket_domain_listener.stop()
            logger.info("WebSocket domain listener stopped")
        
        if services.brokerage_domain_listener:
            await services.brokerage_domain_listener.stop()
            logger.info("Brokerage domain listener stopped")
        
        if services.classification_domain_listener:
            await services.classification_domain_listener.stop()
            logger.info("Classification domain listener stopped")
        
        # Stop classification infrastructure service
        if services.storage_infra:
            await services.storage_infra.stop()
            logger.info("StorageInfrastructureService stopped")
        
        if services.classification_infra:
            await services.classification_infra.stop()
            logger.info("ClassificationInfrastructureService stopped")
        
        # Stop health monitor
        if services.health_monitor:
            await services.health_monitor.stop()
            if services._health_monitor_task:
                services._health_monitor_task.cancel()
                try:
                    await services._health_monitor_task
                except asyncio.CancelledError:
                    pass
            logger.info("Feed health monitor stopped")
        
        # Stop Benzinga WebSocket service
        if services.benzinga_websocket:
            services.benzinga_websocket.stop()
            logger.info("Benzinga WebSocket service stopped")
        
        # Stop brokerage service
        if services.brokerage:
            await services.brokerage.stop()
            logger.info("IBKR Brokerage Service stopped")
        
        # Stop feed manager
        if services.feed_manager:
            await services.feed_manager.stop_all_feeds()
            logger.info("Feed manager stopped")
        
        # Stop process article use case
        if services.process_article_use_case:
            await services.process_article_use_case.stop()
            logger.info("Process article use case stopped")
        
        # Stop Telegram trade handlers
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


def get_stats(services: Services) -> Dict[str, Any]:
    """Get statistics from all services."""
    try:
        stats = {
            "feed_manager": services.feed_manager.get_stats() if services.feed_manager else {},
            "article_processor": services.article_processor.get_stats() if services.article_processor else {},
            "telegram": {
                "enabled_1": services.telegram.enabled_1 if services.telegram else False,
                "enabled_2": services.telegram.enabled_2 if services.telegram else False,
                "test_mode": services.telegram.test_mode if services.telegram else False,
            },
            "classification_infra": services.classification_infra.get_stats() if services.classification_infra else {},
            "storage_infra": services.storage_infra.get_stats() if services.storage_infra else {},
            "brokerage": services.brokerage.get_stats() if services.brokerage else {},
        }
        return stats
    except Exception as e:
        logger.error("Failed to get service stats", error=str(e))
        return {"error": str(e)}


def is_healthy(services: Services) -> bool:
    """Check if all services are healthy."""
    try:
        if services.feed_manager:
            return services.feed_manager.is_healthy()
        return False
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return False

