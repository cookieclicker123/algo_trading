"""
FastAPI server entry point for the news trading system.
Run with: uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload
"""
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from newsflash.api.app import create_app
from newsflash.utils.logging_config import setup_logging

# Setup logging
setup_logging()

# Create FastAPI app
app = create_app()

if __name__ == "__main__":
    import uvicorn
    from newsflash.config.settings import get_server_config
    
    config = get_server_config()
    uvicorn.run(
        "server:app",
        host=config["host"],
        port=config["port"],
        reload=True,
        log_level="info"
    )
