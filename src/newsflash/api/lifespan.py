"""
FastAPI lifespan event handlers for service initialization and cleanup.

Replaces deprecated @app.on_event("startup") and @app.on_event("shutdown").
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from ..services.composition_root import initialize_services
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Global flag to stop lag monitor
_lag_monitor_stop = False


async def _monitor_event_loop_lag() -> None:
    """
    Monitor event loop lag to diagnose WebSocket disconnections.

    If the event loop is blocked (by logging, GC, or other sync operations),
    this will detect it. High lag correlates with missed WebSocket pongs.
    """
    global _lag_monitor_stop
    _lag_monitor_stop = False
    loop = asyncio.get_event_loop()
    consecutive_warnings = 0

    while not _lag_monitor_stop:
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


async def cleanup_background_tasks() -> None:
    """
    Cancel and wait for background tasks properly.
    
    Handles nested tasks without recursion error.
    Uses asyncio.wait() instead of gather() to avoid recursion.
    """
    # Get all tasks except current one
    current_task = asyncio.current_task()
    tasks = [
        task for task in asyncio.all_tasks()
        if task != current_task and not task.done()
    ]
    
    if not tasks:
        return
    
    logger.info(f"Cancelling {len(tasks)} remaining background tasks")
    
    # Cancel all tasks (non-recursive)
    for task in tasks:
        if not task.done():
            task.cancel()
    
    # Wait for tasks with timeout (using wait() not gather())
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=5.0,  # 5 second timeout
            return_when=asyncio.ALL_COMPLETED
        )
        
        # Log any tasks that didn't complete
        if pending:
            logger.warning(f"{len(pending)} tasks did not complete within timeout")
            for task in pending:
                logger.warning(f"Pending task: {task.get_name() if hasattr(task, 'get_name') else 'unknown'}")
        
        # Check for exceptions (ignore CancelledError - expected)
        for task in done:
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected for cancelled tasks
            except Exception as e:
                logger.error(f"Task exception during cleanup", error=str(e))
                
    except Exception as e:
        logger.error(f"Error during task cleanup", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan context manager for FastAPI application.
    
    Handles:
    - Service initialization on startup
    - Service startup
    - Service shutdown on application exit
    - Proper cleanup of background tasks and connections
    
    This ensures graceful shutdown when:
    - Control+C is pressed
    - Server is stopped via signal
    - Application is terminated
    
    Args:
        app: FastAPI application instance
        
    Yields:
        None - application runs during yield
    """
    # Startup
    logger.info("Starting NewsFlash API server")
    
    try:
        # Initialize services (async for future database connections)
        result = await initialize_services()
        if len(result) == 7:
            services, container, recall_engine, signal_engine, failed_trades_engine, scheduler, metadata_cache = result
        elif len(result) == 6:
            services, container, recall_engine, signal_engine, failed_trades_engine, scheduler = result
            metadata_cache = None
        elif len(result) == 5:
            services, container, recall_engine, signal_engine, failed_trades_engine = result
            scheduler = None
            metadata_cache = None
        elif len(result) == 4:
            services, container, recall_engine, signal_engine = result
            failed_trades_engine = None
            scheduler = None
            metadata_cache = None
        else:
            # Backward compatibility if return signature changes
            services, container = result
            recall_engine = None
            signal_engine = None
            failed_trades_engine = None
            scheduler = None
            metadata_cache = None
        
        # Get lifecycle manager from DI container
        lifecycle_manager = container.lifecycle_manager()
        
        # Start all services via lifecycle manager (DI-managed)
        await lifecycle_manager.start_services(services)
        
        # Start scheduler AFTER websocket is running so it can check trading hours
        # and shut down websocket if we're outside trading hours (weekend/overnight)
        if scheduler:
            await scheduler.start()
            logger.info("MarketHoursScheduler started - managing websocket lifecycle")

        # Start event loop lag monitor to diagnose WebSocket disconnections
        lag_monitor_task = asyncio.create_task(_monitor_event_loop_lag())
        logger.info("Event loop lag monitor started")

        # Store services, container, statistics engines, scheduler, and cache in app.state
        app.state.lag_monitor_task = lag_monitor_task
        app.state.services = services
        app.state.container = container
        app.state.recall_engine = recall_engine
        app.state.signal_engine = signal_engine
        app.state.failed_trades_engine = failed_trades_engine
        app.state.scheduler = scheduler
        app.state.metadata_cache = metadata_cache
        
        logger.info("API server startup completed successfully")
        
    except Exception as e:
        logger.error("Failed to start API server", error=str(e))
        raise
    
    # Application runs here
    yield
    
    # Shutdown
    logger.info("Shutting down NewsFlash API server")

    # Stop lag monitor first
    global _lag_monitor_stop
    _lag_monitor_stop = True
    lag_monitor_task = getattr(app.state, "lag_monitor_task", None)
    if lag_monitor_task:
        lag_monitor_task.cancel()
        try:
            await lag_monitor_task
        except asyncio.CancelledError:
            pass
        logger.info("Event loop lag monitor stopped")

    try:
        # Get services and container from app.state
        services = getattr(app.state, "services", None)
        container = getattr(app.state, "container", None)
        recall_engine = getattr(app.state, "recall_engine", None)
        signal_engine = getattr(app.state, "signal_engine", None)
        failed_trades_engine = getattr(app.state, "failed_trades_engine", None)
        scheduler = getattr(app.state, "scheduler", None)
        metadata_cache = getattr(app.state, "metadata_cache", None)
        
        # Stop scheduler first (it manages websocket lifecycle)
        if scheduler:
            try:
                await scheduler.stop()
                logger.info("MarketHoursScheduler stopped")
            except Exception as e:
                logger.error("Error stopping MarketHoursScheduler", error=str(e))
        
        # Stop statistics engines (they have background monitoring tasks)
        if recall_engine:
            try:
                await recall_engine.stop()
                logger.info("RecallStatsEngine stopped")
            except Exception as e:
                logger.error("Error stopping RecallStatsEngine", error=str(e))
        
        if signal_engine:
            try:
                await signal_engine.stop()
                logger.info("SignalStatsEngine stopped")
            except Exception as e:
                logger.error("Error stopping SignalStatsEngine", error=str(e))
        
        if failed_trades_engine:
            try:
                await failed_trades_engine.stop()
                logger.info("FailedTradeStatsEngine stopped")
            except Exception as e:
                logger.error("Error stopping FailedTradeStatsEngine", error=str(e))

        # Stop metadata cache (saves to disk and stops scheduler)
        if metadata_cache:
            try:
                await metadata_cache.stop()
                logger.info("MetadataCache stopped")
            except Exception as e:
                logger.error("Error stopping MetadataCache", error=str(e))

        if services and container:
            # Get lifecycle manager from DI container
            lifecycle_manager = container.lifecycle_manager()
            
            # Stop all services via lifecycle manager (DI-managed)
            await lifecycle_manager.stop_services(services)
        
        # Cancel any remaining background tasks
        # This ensures all tasks are cleaned up even if stop_services missed some
        await cleanup_background_tasks()
        
        # Unwire container on shutdown
        container = getattr(app.state, "container", None)
        if container:
            container.unwire()
            logger.info("DI container unwired")
        
        logger.info("API server shutdown completed")
        
    except Exception as e:
        logger.error("Error during API server shutdown", error=str(e))
        # Don't raise - we want to ensure cleanup completes even if there are errors

