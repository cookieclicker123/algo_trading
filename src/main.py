"""
Main entry point for standalone news polling.
Run with: python -m src.main
"""
import asyncio
import signal
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from newsflash.services.service_initialization import initialize_services, start_services, stop_services
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)


class NewsFlashStandalone:
    """Standalone news polling application."""
    
    def __init__(self):
        self.services = None
        self.shutdown_event = asyncio.Event()
    
    async def start(self):
        """Start the standalone polling system."""
        logger.info("Starting NewsFlash standalone polling system")
        
        try:
            # Initialize services
            self.services = initialize_services()
            
            # Start all services
            await start_services(self.services)
            
            # Wait for shutdown signal
            await self.shutdown_event.wait()
                
        except Exception as e:
            logger.error("Error in standalone system", error=str(e))
            raise
        finally:
            if self.services:
                await stop_services(self.services)
            logger.info("NewsFlash standalone system stopped")
    
    def stop(self):
        """Stop the system gracefully."""
        logger.info("Shutdown signal received")
        self.shutdown_event.set()


async def main():
    """Main function."""
    app = NewsFlashStandalone()
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        app.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await app.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        app.stop()
    except Exception as e:
        logger.error("Fatal error", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
