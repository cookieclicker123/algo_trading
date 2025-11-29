"""
Simple service initialization - replaces ServiceContainer.
Temporary until proper dependency injection in Chapter 7.
"""
import asyncio
from typing import Dict, Any, Optional

from ..utils.bot_conflict_resolver import resolve_bot_conflicts
from ..utils.logging_config import get_logger
from ..config.settings import BENZINGA_API_KEY, BENZINGA_WEBSOCKET_ENABLED, get_telegram_config, get_telegram_config_2, GROQ_API_KEY, GROQ_MODEL

from .article_processor import get_article_processor
from .feed_manager import FeedManager
from .telegram_service import get_telegram_notifier
from .news_classifier import get_news_classifier
from .translation_service import TranslationService
from .yfinance_service import YFinanceService
from .ibkr_trading_service import get_ibkr_trading_service
from .telegram_trade_handler import get_telegram_trade_handler
from .benzinga_websocket_service import BenzingaWebSocketService
from .feed_health_monitor import FeedHealthMonitor
from .auto_trade_service import AutoTradeService
from .position_tracker import PositionTracker
from .price_tracking_service import PriceTrackingService
from .classification_audit_trail import ClassificationAuditTrail
from ..config.settings import get_classification_config

logger = get_logger(__name__)


class Services:
    """Simple container to hold all services."""
    
    def __init__(self):
        self.translator = None
        self.yfinance = None
        self.trading = None
        self.trade_handler = None
        self.trade_handler_2 = None
        self.telegram = None
        self.classifier = None
        self.position_tracker = None
        self.audit_trail = None
        self.price_tracking = None
        self.auto_trade_service = None
        self.article_processor = None
        self.feed_manager = None
        self.benzinga_websocket = None
        self.health_monitor = None
        self._health_monitor_task: Optional[asyncio.Task] = None


def initialize_services() -> Services:
    """Initialize all services."""
    logger.info("Initializing services...")
    services = Services()
    
    try:
        # Initialize external services first (no dependencies)
        logger.info("Initializing external services...")
        
        # Translation service (inline instead of factory)
        translation_config = get_classification_config()
        services.translator = TranslationService(
            api_key=translation_config["api_key"],
            enabled=True
        )
        
        # YFinance service (inline instead of factory)
        services.yfinance = YFinanceService()
        services.trading = get_ibkr_trading_service(paper_trading=True)
        
        # Initialize dependent services
        logger.info("Initializing dependent services...")
        
        # Telegram trade handlers
        telegram_config_1 = get_telegram_config()
        telegram_config_2 = get_telegram_config_2()
        bot_token_1 = telegram_config_1.get("bot_token", "")
        bot_token_2 = telegram_config_2.get("bot_token", "")
        
        if telegram_config_1.get("enabled") and bot_token_1:
            services.trade_handler = get_telegram_trade_handler(
                bot_token=bot_token_1,
                trading_service=services.trading
            )
        
        if telegram_config_2.get("enabled") and bot_token_2:
            services.trade_handler_2 = get_telegram_trade_handler(
                bot_token=bot_token_2,
                trading_service=services.trading
            )
        
        # Telegram notifier
        services.telegram = get_telegram_notifier(
            translator=services.translator,
            yfinance_service=services.yfinance,
            trade_handler=services.trade_handler,
            trade_handler_2=services.trade_handler_2
        )
        
        # Inject telegram notifier into trading service
        services.trading.telegram_service = services.telegram
        
        # News classifier
        services.classifier = get_news_classifier(
            api_key=GROQ_API_KEY,
            model=GROQ_MODEL
        )
        
        # Position tracker
        services.position_tracker = PositionTracker()
        logger.info("PositionTracker initialized")
        
        # Share tracker with trading service
        services.trading.position_tracker = services.position_tracker
        
        # Classification audit trail
        services.audit_trail = ClassificationAuditTrail()
        logger.info("ClassificationAuditTrail initialized")
        
        # Price tracking service
        services.price_tracking = PriceTrackingService(
            ibkr_service=services.trading,
            audit_trail=services.audit_trail
        )
        logger.info("PriceTrackingService initialized")
        
        # Auto-trade service
        services.auto_trade_service = AutoTradeService(
            trading_service=services.trading,
            position_tracker=services.position_tracker,
            telegram_service=services.telegram,
            audit_trail=services.audit_trail,
            price_tracking_service=services.price_tracking
        )
        logger.info("AutoTradeService initialized")
        
        # Article processor
        article_processor = get_article_processor(
            telegram_notifier=services.telegram,
            classifier=services.classifier,
            auto_trade_service=services.auto_trade_service
        )
        # Inject shared audit trail
        article_processor.audit_trail = services.audit_trail
        services.article_processor = article_processor
        
        # Feed manager
        benzinga_token = BENZINGA_API_KEY if BENZINGA_WEBSOCKET_ENABLED else None
        services.feed_manager = FeedManager(
            article_processor=services.article_processor,
            benzinga_token=benzinga_token
        )
        
        # Benzinga WebSocket service
        if BENZINGA_WEBSOCKET_ENABLED and BENZINGA_API_KEY:
            services.benzinga_websocket = BenzingaWebSocketService(
                article_processor=services.article_processor,
                token=BENZINGA_API_KEY
            )
            logger.info("Benzinga WebSocket service initialized")
        else:
            logger.info("Benzinga WebSocket service disabled or no API key")
        
        # Health monitor
        services.health_monitor = FeedHealthMonitor(
            feed_manager=services.feed_manager,
            telegram_service=services.telegram
        )
        logger.info("Feed health monitor initialized")
        
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
        
        # Start IBKR trading service
        logger.info("About to start IBKR Trading Service...")
        await services.trading.start()
        logger.info("IBKR Trading Service started and connected")
        
        # Start feed manager
        await services.feed_manager.start_all_feeds()
        
        # Start Benzinga WebSocket service
        if services.benzinga_websocket:
            services.benzinga_websocket.start()
            logger.info("Benzinga WebSocket service started")
        
        # Start health monitor
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
        
        # Stop IBKR trading service
        if services.trading:
            await services.trading.stop()
            logger.info("IBKR Trading Service stopped")
        
        # Stop feed manager
        if services.feed_manager:
            await services.feed_manager.stop_all_feeds()
        
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
            "classifier": services.classifier.get_stats() if services.classifier else {},
            "trading": {
                "enabled": services.trading.enabled if services.trading else False,
            }
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

