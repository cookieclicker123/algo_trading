"""
Configuration management for the news trading system.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# File Storage
TMP_DIR = "tmp"
ARTICLES_JSON_FILE = "articles.json"
ROLLING_WINDOW_HOURS = 1  # Keep articles for 1 hour
ARTICLE_FETCH_TIMEOUT_SECONDS = float(os.getenv("ARTICLE_FETCH_TIMEOUT_SECONDS", "10.0"))  # Increased from 5.0 to handle race conditions

# Server Configuration
HOST = "0.0.0.0"
PORT = 8000

# Telegram Configuration
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Telegram Configuration (Second Bot)
TELEGRAM_ENABLED_2 = os.getenv("TELEGRAM_ENABLED_2", "false").lower() == "true"
TELEGRAM_BOT_TOKEN_2 = os.getenv("TELEGRAM_BOT_TOKEN_2", "")
TELEGRAM_CHAT_ID_2 = os.getenv("TELEGRAM_CHAT_ID_2", "")

# AI Classification Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
CLASSIFICATION_ENABLED = os.getenv("CLASSIFICATION_ENABLED", "true").lower() == "true"

# Benzinga Direct WebSocket Configuration
BENZINGA_API_KEY = os.getenv("BENZINGA_API_KEY", "")
BENZINGA_WEBSOCKET_ENABLED = os.getenv("BENZINGA_WEBSOCKET_ENABLED", "false").lower() == "true"
FEED_AUTORESTART_WEBSOCKET = os.getenv("FEED_AUTORESTART_WEBSOCKET", "true").lower() == "true"
# Skip articles older than this many minutes when WebSocket first starts (prevents processing backlog)
WEBSOCKET_STARTUP_SKIP_OLD_MESSAGES_MINUTES = int(os.getenv("WEBSOCKET_STARTUP_SKIP_OLD_MESSAGES_MINUTES", "10"))

# Auto-Trading Configuration
AUTO_TRADING_ENABLED = os.getenv("AUTO_TRADING_ENABLED", "true").lower() == "true"
AUTO_TRADE_EXIT_DELAY_MINUTES = int(os.getenv("AUTO_TRADE_EXIT_DELAY_MINUTES", "10"))  # Exit after 10 minutes

# Brokerage Configuration
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"

# Extended-hours ladder tuning (cents and milliseconds)
LADDER_INITIAL_CENTS = int(os.getenv("LADDER_INITIAL_CENTS", "1"))            # first step from NBBO
LADDER_STEP_CENTS = int(os.getenv("LADDER_STEP_CENTS", "1"))                  # step for early attempts
LADDER_STEP_CENTS_AFTER = int(os.getenv("LADDER_STEP_CENTS_AFTER", "3"))      # step after switch
LADDER_SWITCH_ATTEMPT = int(os.getenv("LADDER_SWITCH_ATTEMPT", "6"))          # after N attempts switch to larger step
LADDER_INTERVAL_MS = int(os.getenv("LADDER_INTERVAL_MS", "30"))               # early check interval
LADDER_INTERVAL_MS_LATE = int(os.getenv("LADDER_INTERVAL_MS_LATE", "50"))     # later check interval
LADDER_MAX_CENTS = int(os.getenv("LADDER_MAX_CENTS", "100"))                  # max range from start ($1)

def get_server_config() -> dict:
    """Get server configuration."""
    return {
        "host": HOST,
        "port": PORT,
    }

def get_storage_config() -> dict:
    """Get storage configuration."""
    return {
        "tmp_dir": TMP_DIR,
        "articles_json_file": ARTICLES_JSON_FILE,
        "rolling_window_hours": ROLLING_WINDOW_HOURS,
        "article_fetch_timeout_seconds": ARTICLE_FETCH_TIMEOUT_SECONDS,
    }

def get_telegram_config() -> dict:
    """Get Telegram configuration."""
    return {
        "enabled": TELEGRAM_ENABLED,
        "bot_token": TELEGRAM_BOT_TOKEN,
        "chat_id": TELEGRAM_CHAT_ID,
    }

def get_telegram_config_2() -> dict:
    """Get Telegram configuration for second bot."""
    return {
        "enabled": TELEGRAM_ENABLED_2,
        "bot_token": TELEGRAM_BOT_TOKEN_2,
        "chat_id": TELEGRAM_CHAT_ID_2,
    }

def get_classification_config() -> dict:
    """Get AI classification configuration."""
    return {
        "enabled": CLASSIFICATION_ENABLED,
        "api_key": GROQ_API_KEY,
        "model": GROQ_MODEL,
    }
