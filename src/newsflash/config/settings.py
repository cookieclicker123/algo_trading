"""
Configuration management for the news trading system.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
API_BASE_URL = "https://api.polygon.io"
BENZINGA_NEWS_ENDPOINT = "/benzinga/v2/news"

# Polling Configuration
POLLING_INTERVAL_SECONDS = 0.05  # 50ms = 20 requests per second
MAX_REQUESTS_PER_SECOND = 20
SAFETY_BUFFER_SECONDS = 5

# Rate Limiting
RATE_LIMIT_REQUESTS_PER_SECOND = 100  # Polygon.io limit
BACKOFF_BASE_DELAY = 1.0
BACKOFF_MAX_DELAY = 60.0

# File Storage
TMP_DIR = "tmp"
ARTICLES_JSON_FILE = "articles.json"
ROLLING_WINDOW_HOURS = 1  # Keep articles for 1 hour

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

def get_api_key(key_name: str = "POLYGON_API_KEY") -> str:
    """Get API key from environment variables."""
    api_key = os.getenv(key_name)
    if not api_key:
        raise ValueError(f"{key_name} environment variable is required")
    return api_key

def get_polling_config() -> dict:
    """Get polling configuration."""
    return {
        "interval_seconds": POLLING_INTERVAL_SECONDS,
        "max_requests_per_second": MAX_REQUESTS_PER_SECOND,
        "safety_buffer_seconds": SAFETY_BUFFER_SECONDS,
    }

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
