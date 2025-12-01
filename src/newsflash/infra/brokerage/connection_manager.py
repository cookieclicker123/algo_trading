"""
IBKR connection manager - handles connection lifecycle and keepalive.
Pure infrastructure - publishes events, no business logic.
"""
import asyncio
import threading
import time
from typing import Optional
from datetime import datetime, timedelta

import pytz
from ib_insync import IB

from ...utils.logging_config import get_logger
from ...config import settings
from ...shared.event_bus import AsyncEventBus
from .events import ConnectionStatusChangedEvent

logger = get_logger(__name__)


class IBKRConnectionManager:
    """
    Manages IBKR Gateway connection lifecycle.
    
    Responsibilities:
    - Connection establishment and verification
    - Keepalive pings
    - Reconnection handling
    - Daily restart handling
    - Publishing connection status events
    
    Does NOT:
    - Execute trades
    - Know about business logic
    - Send Telegram notifications (publishes events instead)
    """
    
    def __init__(self, event_bus: AsyncEventBus, paper_trading: bool = False, client_id: int = 5):
        """
        Initialize connection manager.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            paper_trading: Whether to use paper trading port
            client_id: IBKR client ID (default 5 for trading service)
        """
        self.paper_trading = paper_trading
        self.client_id = client_id
        self.ib: Optional[IB] = None
        self.is_connected = False
        self.is_running = False
        
        # Event bus for publishing events
        self.event_bus = event_bus
        
        # Connection state
        self._connection_lock: Optional[asyncio.Lock] = None
        self._main_event_loop: Optional[asyncio.AbstractEventLoop] = None
        
        # IB connection thread (dedicated thread with its own event loop)
        self._ib_thread: Optional[threading.Thread] = None
        self._ib_event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._connection_ready = threading.Event()  # Signal when connection is ready
        self._connection_error: Optional[Exception] = None
        
        # Background tasks
        self._connection_verification_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        
        # Configuration
        self.keep_alive_interval = 60  # seconds
        self.reconnect_backoff_seconds = 5
        self.verification_interval = 15  # seconds
        
        # Daily restart handling
        self.next_connect_time: Optional[datetime] = None
        
        # Statistics
        self.stats = {
            "connection_attempts": 0,
            "reconnect_attempts": 0,
            "last_connection_time": None,
            "last_disconnection_time": None,
            "last_keepalive_time": None,
        }
        
        port = settings.IBKR_PAPER_TRADING_PORT if paper_trading else settings.IBKR_LIVE_TRADING_PORT
        mode = "Paper Trading" if paper_trading else "Live Trading"
        logger.info(
            f"IBKR Connection Manager initialized for {mode}",
            port=port,
            client_id=client_id
        )
    
    def get_ib_connection(self) -> Optional[IB]:
        """Get the current IB connection instance."""
        return self.ib if self.is_connected and self.ib and self.ib.isConnected() else None
    
    async def start(self) -> None:
        """Start the connection manager and establish connection."""
        if self.is_running:
            logger.warning("Connection manager already running")
            return
        
        logger.info("🚀 Starting IBKR Connection Manager")
        self.is_running = True
        
        # Initialize connection lock (will be set in async context)
        if self._connection_lock is None:
            try:
                self._main_event_loop = asyncio.get_running_loop()
                self._connection_lock = asyncio.Lock()
            except RuntimeError:
                self._main_event_loop = asyncio.get_event_loop()
                self._connection_lock = asyncio.Lock()
        
        # Schedule connection in background task (after startup completes)
        # This avoids event loop conflicts during FastAPI startup
        async def _connect_after_startup():
            """Connect after a short delay to let startup complete."""
            # Wait a bit longer for Gateway to be ready (it may take a few seconds to start)
            await asyncio.sleep(3)  # Increased delay to let Gateway fully initialize
            try:
                await self.ensure_connected(timeout_seconds=60.0)  # Increased timeout
                logger.info("✅ IBKR Connection Manager connected - Gateway ready")
            except Exception as e:
                logger.warning(
                    "Failed to establish initial connection (will retry)",
                    error=str(e),
                    note="Gateway may still be starting up - connection will retry automatically"
                )
                # Connection will retry via verification loop
        
        # Start connection in background
        if self._main_event_loop:
            self._main_event_loop.create_task(_connect_after_startup())
        
        logger.info("IBKR Connection Manager started (connection scheduled in background)")
    
    async def stop(self) -> None:
        """Stop the connection manager."""
        logger.info("🛑 Stopping IBKR Connection Manager")
        self.is_running = False
        
        # Cancel background tasks
        tasks = [
            self._connection_verification_task,
            self._keepalive_task,
        ]
        
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        self._connection_verification_task = None
        self._keepalive_task = None
        
        # Disconnect
        if self.ib:
            try:
                self.ib.disconnect()
            except Exception:
                pass
            finally:
                self.ib = None
                self.is_connected = False
        
        # Publish disconnection event
        await self._publish_connection_status(False, "Connection manager stopped")
        
        logger.info("IBKR Connection Manager stopped")
    
    async def ensure_connected(self, timeout_seconds: Optional[float] = None) -> IB:
        """
        Ensure a warm persistent IB connection is available.
        
        Args:
            timeout_seconds: Optional timeout for connection
            
        Returns:
            IB connection instance
            
        Raises:
            TimeoutError: If connection times out
        """
        if self._connection_lock is None:
            self._connection_lock = asyncio.Lock()
        
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        
        def remaining_time() -> Optional[float]:
            if deadline is None:
                return None
            return deadline - time.monotonic()
        
        async with self._connection_lock:
            if self.ib and self.is_connected:
                try:
                    if self.ib.isConnected():
                        # Light ping to confirm the API client is responsive
                        self.ib.accountValues()
                        return self.ib
                except Exception:
                    logger.warning("⚠️ Existing IB connection became unresponsive – reconnecting")
                    self.is_connected = False
            
            remaining = remaining_time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Connection timeout reached before attempting IB reconnect")
            
            return await self._connect_with_confirmation(remaining)
    
    async def _connect_with_confirmation(self, timeout_seconds: Optional[float] = None) -> IB:
        """
        Connect to IB Gateway and confirm the API client is responsive.
        
        Args:
            timeout_seconds: Optional timeout for connection
            
        Returns:
            IB connection instance
            
        Raises:
            TimeoutError: If connection times out
        """
        # Clean up existing connection
        if self.ib:
            try:
                self.ib.disconnect()
            except Exception:
                pass
            self.ib = None
        
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        
        def remaining_time() -> Optional[float]:
            if deadline is None:
                return None
            return deadline - time.monotonic()
        
        # Ensure we have the current event loop
        if self._main_event_loop is None:
            try:
                self._main_event_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._main_event_loop = asyncio.get_event_loop()
        
        # Connect in a thread to avoid event loop conflicts
        # Create IB instance inside the thread where it will run
        port = settings.IBKR_PAPER_TRADING_PORT if self.paper_trading else settings.IBKR_LIVE_TRADING_PORT
        logger.info(f"🔌 Connecting to IB Gateway (port {port}, clientId {self.client_id})...")
        
        remaining = remaining_time()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Connection timeout reached before contacting IB Gateway")
        
        # Start dedicated IB connection thread if not already running
        # This thread runs its own event loop and manages the IB connection
        if self._ib_thread is None or not self._ib_thread.is_alive():
            self._connection_ready.clear()
            self._connection_error = None
            
            self._ib_thread = threading.Thread(
                target=self._run_ib_connection_thread,
                daemon=True,
                args=(port,)
            )
            self._ib_thread.start()
            logger.info("Started dedicated IB connection thread")
        
        # Wait for connection to be ready (or error)
        timeout = remaining if remaining is not None else 30.0
        if self._connection_ready.wait(timeout=timeout):
            # Connection successful
            if self._connection_error:
                error_msg = f"IB Gateway connection failed: {self._connection_error}"
                await self._publish_connection_status(False, error_msg)
                raise ConnectionError(error_msg)
            
            if not self.ib or not self.is_connected:
                error_msg = "IB Gateway connection failed - no connection established"
                await self._publish_connection_status(False, error_msg)
                raise ConnectionError(error_msg)
            
            logger.info("✅ IB Gateway connected")
        else:
            # Timeout waiting for connection
            error_msg = f"IB Gateway connection timed out after {timeout}s"
            logger.error(error_msg)
            await self._publish_connection_status(False, error_msg)
            raise TimeoutError(error_msg)
        
        # Verify connection
        try:
            remaining = remaining_time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Connection timeout reached before verification")
            
            accounts = self.ib.accountValues()
            logger.info(
                f"✅ Gateway API client verified via accountValues() ({len(accounts) if accounts else 0} accounts)"
            )
            
            self.is_connected = True
            self.stats["connection_attempts"] += 1
            self.stats["last_connection_time"] = datetime.now()
            
            # Request real-time market data
            try:
                self.ib.reqMarketDataType(1)
            except Exception as exc:
                logger.warning("⚠️ Failed to request real-time market data type", error=str(exc))
            
            # Publish connection event
            await self._publish_connection_status(True, "Connected and verified")
            
            # Start background tasks now that we have a connection
            self._start_connection_verification()
            self._start_keepalive()
            
        except Exception as exc:
            error_msg = f"Connection verification failed: {exc}"
            logger.error(f"❌ {error_msg}")
            self.is_connected = False
            # Publish failure event
            await self._publish_connection_status(False, error_msg)
            if self.ib:
                try:
                    self.ib.disconnect()
                except Exception:
                    pass
                self.ib = None
            raise
        
        return self.ib
    
    def _on_disconnect(self) -> None:
        """Handle Gateway-initiated disconnects."""
        logger.warning("⚠️ Gateway disconnected API client - scheduling reconnect")
        self.is_connected = False
        self.stats["last_disconnection_time"] = datetime.now()
        
        # Handle daily restart window
        self._handle_daily_restart_window()
        
        # Publish disconnection event
        if self._main_event_loop:
            self._main_event_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._publish_connection_status(False, "Gateway disconnected"))
            )
        
        # Schedule reconnection
        if self._main_event_loop:
            self._main_event_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._reconnect_after_disconnect())
            )
    
    def _handle_daily_restart_window(self) -> None:
        """Handle daily restart window - delay reconnection if needed."""
        try:
            local_tz = pytz.timezone('US/Eastern')
            now_local = datetime.now(local_tz)
            hh, mm = [int(x) for x in settings.IBKR_DAILY_RESTART_TIME.split(":", 1)]
            restart_today = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            
            # Choose the most recent restart reference
            if now_local < restart_today - timedelta(hours=12):
                restart_today = restart_today - timedelta(days=1)
            
            # Window: within +/- 5 minutes of configured restart
            window_start = restart_today - timedelta(minutes=5)
            window_end = restart_today + timedelta(minutes=5)
            
            if window_start <= now_local <= window_end:
                planned = (restart_today + timedelta(minutes=2)).astimezone(local_tz)
                # Convert to naive local time for comparison
                self.next_connect_time = datetime.now() + (planned - now_local)
                logger.info(
                    f"⏳ Delaying reconnect until {planned.strftime('%I:%M %p %Z')} (2 min after daily restart)"
                )
        except Exception as e:
            logger.debug("Could not schedule delayed reconnect window", error=str(e))
    
    async def _reconnect_after_disconnect(self) -> None:
        """Reconnect to IB Gateway after a disconnect."""
        await asyncio.sleep(1)
        
        while self.is_running and not self.is_connected:
            # Check if we should delay reconnection
            if self.next_connect_time is not None:
                now = datetime.now()
                if now < self.next_connect_time:
                    await asyncio.sleep(min(1, (self.next_connect_time - now).total_seconds()))
                    continue
                else:
                    self.next_connect_time = None
            
            self.stats["reconnect_attempts"] += 1
            try:
                await self._connect_with_confirmation()
                logger.info("🔄 Reconnected to IB Gateway", attempts=self.stats["reconnect_attempts"])
                break
            except Exception as exc:
                logger.error(f"❌ Reconnect attempt failed: {exc}", attempts=self.stats["reconnect_attempts"])
                await asyncio.sleep(self.reconnect_backoff_seconds)
    
    def _start_connection_verification(self) -> None:
        """Start connection verification task."""
        if self._main_event_loop is None:
            return
        if self._connection_verification_task and not self._connection_verification_task.done():
            return
        
        self._connection_verification_task = self._main_event_loop.create_task(self._verify_connection())
    
    async def _verify_connection(self) -> None:
        """Periodically verify that the Gateway API client responds."""
        try:
            while self.is_running:
                await asyncio.sleep(self.verification_interval)
                
                if not self.is_running:
                    break
                
                if not self.ib:
                    continue
                
                try:
                    if not self.ib.isConnected():
                        logger.warning("⚠️ ib.isConnected() returned False - triggering reconnection")
                        self.is_connected = False
                        await self._reconnect_after_disconnect()
                        continue
                    
                    # Verify API client is responsive
                    self.ib.accountValues()
                    
                    if not self.is_connected:
                        self.is_connected = True
                        await self._publish_connection_status(True, "Reconnected and verified")
                
                except Exception as exc:
                    logger.warning(f"⚠️ Gateway API client verification failed: {exc}")
                    self.is_connected = False
                    await self._reconnect_after_disconnect()
        
        except asyncio.CancelledError:
            logger.info("Connection verification task cancelled")
    
    def _start_keepalive(self) -> None:
        """Start keepalive task."""
        if self._main_event_loop is None:
            try:
                self._main_event_loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("No event loop available for keepalive")
                return
        
        if self._keepalive_task and not self._keepalive_task.done():
            return
        
        self._keepalive_task = self._main_event_loop.create_task(self._keepalive_loop())
    
    async def _keepalive_loop(self) -> None:
        """Send lightweight keepalive pings to avoid idle disconnects."""
        try:
            while self.is_running:
                await asyncio.sleep(self.keep_alive_interval)
                
                if not self.is_running:
                    break
                
                try:
                    ib = await self.ensure_connected()
                    ib.accountValues()
                    self.stats["last_keepalive_time"] = datetime.now()
                    logger.debug("🔁 Keepalive ping successful")
                except Exception as exc:
                    logger.warning(f"⚠️ Keepalive ping failed: {exc}")
                    self.is_connected = False
                    await self._reconnect_after_disconnect()
        
        except asyncio.CancelledError:
            logger.info("Keepalive task cancelled")
    
    def _run_ib_connection_thread(self, port: int) -> None:
        """Run IB connection in dedicated thread with its own event loop."""
        try:
            # Create and set event loop for this thread
            self._ib_event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._ib_event_loop)
            
            # Schedule connection as a task
            self._ib_event_loop.create_task(self._connect_async(port))
            
            # Run event loop forever - this keeps connection alive
            self._ib_event_loop.run_forever()
            
        except Exception as e:
            logger.error(f"Thread setup failed: {e}", exc_info=True)
            error_msg = str(e)
            self._connection_error = e
            self._connection_ready.set()
            
            # Publish error on main loop
            if self._main_event_loop:
                def _publish_setup_error():
                    asyncio.create_task(
                        self._publish_connection_status(False, f"Thread setup failed: {error_msg}")
                    )
                self._main_event_loop.call_soon_threadsafe(_publish_setup_error)
        finally:
            # Clean up event loop
            if self._ib_event_loop:
                try:
                    self._ib_event_loop.close()
                except Exception:
                    pass
                self._ib_event_loop = None
    
    async def _connect_async(self, port: int) -> None:
        """Async connection logic - runs in the dedicated thread's event loop."""
        try:
            # Create IB instance in this thread
            ib = IB()
            ib.disconnectedEvent += self._on_disconnect
            ib.errorEvent += self._on_ib_error
            
            # Increase connection timeout
            ib.RequestTimeout = 60  # 60 seconds timeout
            
            # Connect using async method (like working tests do - line 73 in test_market_hours_trading.py)
            logger.info(f"Attempting IB connection in thread (port {port}, clientId {self.client_id}, timeout={ib.RequestTimeout}s)...")
            await ib.connectAsync("127.0.0.1", port, clientId=self.client_id)
            
            # Store instance
            self.ib = ib
            self.is_connected = True
            self.stats["last_connection_time"] = datetime.now()
            self.stats["connection_attempts"] += 1
            
            logger.info("✅ IB Gateway connected successfully in dedicated thread")
            
            # Signal connection ready
            self._connection_ready.set()
            
            # Publish connection status (schedule on main loop)
            if self._main_event_loop:
                self._main_event_loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        self._publish_connection_status(True, "Connected and verified")
                    )
                )
            
            # Keep event loop running to maintain connection
            # Wait until we're told to stop
            while self.is_connected and self.is_running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"IB connection failed in thread: {e}", exc_info=True)
            error_msg = str(e)
            self._connection_error = e
            self.ib = None
            self.is_connected = False
            
            # Signal ready (with error)
            self._connection_ready.set()
            
            # Publish error on main loop
            if self._main_event_loop:
                def _publish_error():
                    asyncio.create_task(
                        self._publish_connection_status(False, f"Connection failed: {error_msg}")
                    )
                self._main_event_loop.call_soon_threadsafe(_publish_error)
    
    def _on_ib_error(self, req_id: int, error_code: int, error_message: str, misc: str) -> None:
        """Handle IB error events."""
        # High-frequency informational errors - ignore
        if error_code in {2104, 2106, 2107, 2157, 2158}:
            logger.debug(
                "IBKR informational message",
                req_id=req_id,
                error_code=error_code,
                error_message=error_message
            )
            return
        
        # Log other errors
        logger.warning(
            "IBKR error event",
            req_id=req_id,
            error_code=error_code,
            error_message=error_message,
            misc=misc
        )
    
    async def _publish_connection_status(self, is_connected: bool, reason: str) -> None:
        """Publish connection status change event."""
        event = ConnectionStatusChangedEvent(
            is_connected=is_connected,
            paper_trading=self.paper_trading,
            changed_at=datetime.now(),
            reason=reason
        )
        await self.event_bus.publish("ConnectionStatusChanged", event.model_dump())
        logger.debug("Published ConnectionStatusChanged event", is_connected=is_connected, reason=reason)
    
    def get_stats(self) -> dict:
        """Get connection statistics."""
        return {
            "is_connected": self.is_connected,
            "is_running": self.is_running,
            "paper_trading": self.paper_trading,
            "client_id": self.client_id,
            **self.stats
        }
    
    def is_healthy(self) -> bool:
        """Check if connection manager is healthy."""
        return self.is_running and self.is_connected

