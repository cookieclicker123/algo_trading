"""
Configuration container - provides configuration values.
"""
from dependency_injector import containers, providers

from ...config import settings


class ConfigurationContainer(containers.DeclarativeContainer):
    """Configuration container for application settings."""
    
    wiring_config = containers.WiringConfiguration(
        modules=[
            # Route handlers can use @inject decorator for automatic injection
            "newsflash.api.routes.health",
            "newsflash.api.routes.storage.articles",
            "newsflash.api.routes.websocket.feeds",
        ]
    )
    
    # Configuration providers
    storage_config = providers.Callable(settings.get_storage_config)
    telegram_config_1 = providers.Callable(settings.get_telegram_config)
    telegram_config_2 = providers.Callable(settings.get_telegram_config_2)
    classification_config = providers.Callable(settings.get_classification_config)
    server_config = providers.Callable(settings.get_server_config)
    
    # Direct config values
    groq_api_key = providers.Callable(lambda: settings.GROQ_API_KEY)
    groq_triage_model = providers.Callable(lambda: settings.GROQ_TRIAGE_MODEL)
    anthropic_api_key = providers.Callable(lambda: settings.ANTHROPIC_API_KEY)
    anthropic_model = providers.Callable(lambda: settings.ANTHROPIC_MODEL)
    classification_enabled = providers.Callable(lambda: settings.CLASSIFICATION_ENABLED)
    benzinga_api_key = providers.Callable(lambda: settings.BENZINGA_API_KEY)
    benzinga_websocket_enabled = providers.Callable(lambda: settings.BENZINGA_WEBSOCKET_ENABLED)
    
    # Brokerage Configuration
    paper_trading = providers.Callable(lambda: settings.PAPER_TRADING)
    
    # Auto-Trading Configuration
    auto_trading_enabled = providers.Callable(lambda: settings.AUTO_TRADING_ENABLED)
    auto_trade_exit_delay_minutes = providers.Callable(lambda: settings.AUTO_TRADE_EXIT_DELAY_MINUTES)
    
    # Ladder Configuration
    ladder_initial_cents = providers.Callable(lambda: settings.LADDER_INITIAL_CENTS)
    ladder_step_cents = providers.Callable(lambda: settings.LADDER_STEP_CENTS)
    ladder_step_cents_after = providers.Callable(lambda: settings.LADDER_STEP_CENTS_AFTER)
    ladder_switch_attempt = providers.Callable(lambda: settings.LADDER_SWITCH_ATTEMPT)
    ladder_interval_ms = providers.Callable(lambda: settings.LADDER_INTERVAL_MS)
    ladder_interval_ms_late = providers.Callable(lambda: settings.LADDER_INTERVAL_MS_LATE)
    ladder_max_cents = providers.Callable(lambda: settings.LADDER_MAX_CENTS)

