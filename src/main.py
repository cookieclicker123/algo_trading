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

from newsflash.services.composition_root import initialize_services
from newsflash.services.service_initialization import start_services, stop_services
from newsflash.utils.logging_config import get_logger
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)


class NewsFlashStandalone:
    """Standalone news polling application."""

    def __init__(self):
        self.services = None
        self.shutdown_event = asyncio.Event()
        self._lag_monitor_task = None

    async def _monitor_event_loop_lag(self):
        """
        Monitor event loop lag to diagnose WebSocket disconnections.

        If the event loop is blocked (by logging, GC, or other sync operations),
        this will detect it. High lag correlates with missed WebSocket pongs.
        """
        loop = asyncio.get_event_loop()
        consecutive_warnings = 0

        while not self.shutdown_event.is_set():
            try:
                start = loop.time()
                await asyncio.sleep(0.1)  # Should take ~100ms
                elapsed_ms = (loop.time() - start) * 1000

                if elapsed_ms > 500:
                    # Severe lag - this WILL cause WebSocket disconnections
                    logger.error(
                        "🚨 SEVERE event loop lag detected - WebSocket disconnections likely",
                        expected_ms=100,
                        actual_ms=round(elapsed_ms),
                        lag_ms=round(elapsed_ms - 100)
                    )
                    consecutive_warnings += 1
                elif elapsed_ms > 200:
                    # Warning - event loop is struggling
                    logger.warning(
                        "⚠️ Event loop lag detected",
                        expected_ms=100,
                        actual_ms=round(elapsed_ms),
                        lag_ms=round(elapsed_ms - 100)
                    )
                    consecutive_warnings += 1
                else:
                    # Reset counter on good tick
                    if consecutive_warnings > 0:
                        logger.info(
                            "✅ Event loop lag recovered",
                            consecutive_warnings=consecutive_warnings
                        )
                    consecutive_warnings = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Lag monitor error: {e}")
    
    async def start(self):
        """Start the standalone polling system."""
        logger.info("Starting NewsFlash standalone polling system")
        
        try:
            # Initialize services (async for future database connections)
            result = await initialize_services()
            if len(result) == 7:
                self.services, self.container, self.recall_engine, self.signal_engine, self.failed_trades_engine, self.scheduler, self.metadata_cache = result
            elif len(result) == 6:
                self.services, self.container, self.recall_engine, self.signal_engine, self.failed_trades_engine, self.scheduler = result
                self.metadata_cache = None
            elif len(result) == 5:
                self.services, self.container, self.recall_engine, self.signal_engine, self.failed_trades_engine = result
                self.scheduler = None
                self.metadata_cache = None
            elif len(result) == 4:
                self.services, self.container, self.recall_engine, self.signal_engine = result
                self.failed_trades_engine = None
                self.scheduler = None
                self.metadata_cache = None
            else:
                # Backward compatibility
                self.services, self.container = result
                self.recall_engine = None
                self.signal_engine = None
                self.failed_trades_engine = None
                self.scheduler = None
                self.metadata_cache = None
            
            # Start all services
            await start_services(self.services)

            # Start event loop lag monitor (diagnoses WebSocket disconnections)
            self._lag_monitor_task = asyncio.create_task(self._monitor_event_loop_lag())
            logger.info("Event loop lag monitor started")

            # Wait for shutdown signal
            await self.shutdown_event.wait()
                
        except Exception as e:
            logger.error("Error in standalone system", error=str(e))
            raise
        finally:
            # Stop lag monitor first
            if self._lag_monitor_task:
                self._lag_monitor_task.cancel()
                try:
                    await self._lag_monitor_task
                except asyncio.CancelledError:
                    pass
                logger.info("Event loop lag monitor stopped")

            # Stop scheduler first
            if hasattr(self, 'scheduler') and self.scheduler:
                try:
                    await self.scheduler.stop()
                    logger.info("MarketHoursScheduler stopped")
                except Exception as e:
                    logger.error("Error stopping MarketHoursScheduler", error=str(e))

            # Stop statistics engines
            if hasattr(self, 'recall_engine') and self.recall_engine:
                try:
                    await self.recall_engine.stop()
                    logger.info("RecallStatsEngine stopped")
                except Exception as e:
                    logger.error("Error stopping RecallStatsEngine", error=str(e))

            if hasattr(self, 'signal_engine') and self.signal_engine:
                try:
                    await self.signal_engine.stop()
                    logger.info("SignalStatsEngine stopped")
                except Exception as e:
                    logger.error("Error stopping SignalStatsEngine", error=str(e))

            if hasattr(self, 'failed_trades_engine') and self.failed_trades_engine:
                try:
                    await self.failed_trades_engine.stop()
                    logger.info("FailedTradeStatsEngine stopped")
                except Exception as e:
                    logger.error("Error stopping FailedTradeStatsEngine", error=str(e))

            # Stop metadata cache (saves to disk and stops scheduler)
            if hasattr(self, 'metadata_cache') and self.metadata_cache:
                try:
                    await self.metadata_cache.stop()
                    logger.info("MetadataCache stopped")
                except Exception as e:
                    logger.error("Error stopping MetadataCache", error=str(e))

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
