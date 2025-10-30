"""
IBKR Keep-Alive Service to maintain persistent connection to IB Gateway.
Prevents timeouts and ensures Gateway stays alive for algo trading.
"""
import asyncio
from typing import Optional
from datetime import datetime
from ib_insync import IB
from ..utils.logging_config import get_logger
from ..config import settings

logger = get_logger(__name__)


class IBKRKeepAliveService:
    """
    Maintains a persistent IBKR connection to keep Gateway alive.
    
    Features:
    - Persistent connection that stays open
    - Periodic keep-alive requests (account data)
    - Automatic reconnection on disconnect
    - Lightweight - uses minimal resources
    """
    
    def __init__(self, paper_trading: bool = False, telegram_service=None):
        """
        Initialize keep-alive service.
        
        Args:
            paper_trading: Whether to use paper trading port
        """
        self.paper_trading = paper_trading
        self.ib: Optional[IB] = None
        self.is_running = False
        self.is_connected = False
        self.keep_alive_interval = 60  # Send keep-alive every 60 seconds
        self.client_id = 99  # Use high client ID to avoid conflicts with trading service
        self.reconnect_delay = 5  # Wait 5 seconds before reconnecting
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10  # Stop trying after 10 failed attempts
        self.telegram_service = telegram_service
        self.next_connect_time: Optional[datetime] = None  # gate reconnects until a specific time
        
        port = settings.IBKR_PAPER_TRADING_PORT if paper_trading else settings.IBKR_LIVE_TRADING_PORT
        mode = "Paper Trading" if paper_trading else "Live Trading"
        logger.info(
            f"IBKR KeepAlive Service initialized for {mode}",
            port=port,
            client_id=self.client_id
        )
    
    async def start(self):
        """Start the keep-alive service."""
        if self.is_running:
            logger.warning("Keep-alive service already running")
            return
        
        self.is_running = True
        logger.info("Starting IBKR Keep-Alive service...")
        
        # Start connection loop in background
        asyncio.create_task(self._connection_loop())
        
        logger.info("IBKR Keep-Alive service started")
        # Start daily restart notifier/watchdog
        asyncio.create_task(self._daily_restart_watchdog())
    
    async def stop(self):
        """Stop the keep-alive service."""
        self.is_running = False
        
        if self.ib and self.ib.isConnected():
            try:
                self.ib.disconnect()
                logger.info("Disconnected from IB Gateway (keep-alive)")
            except Exception as e:
                logger.error("Error disconnecting keep-alive", error=str(e))
        
        self.is_connected = False
        logger.info("IBKR Keep-Alive service stopped")
    
    async def _connection_loop(self):
        """Main connection loop - maintains persistent connection."""
        while self.is_running:
            try:
                if not self.is_connected:
                    # Respect any scheduled next connect time (e.g., daily restart + 2 minutes)
                    if self.next_connect_time is not None:
                        now = datetime.now()
                        if now < self.next_connect_time:
                            await asyncio.sleep(min(1, (self.next_connect_time - now).total_seconds()))
                            continue
                        else:
                            self.next_connect_time = None
                    await self._connect()
                
                if self.is_connected:
                    # Wait for keep-alive interval
                    await asyncio.sleep(self.keep_alive_interval)
                    
                    # Send keep-alive request
                    await self._send_keepalive()
                else:
                    # Not connected, wait before retrying
                    await asyncio.sleep(self.reconnect_delay)
                    
            except asyncio.CancelledError:
                logger.info("Keep-alive connection loop cancelled")
                break
            except Exception as e:
                logger.error("Error in keep-alive connection loop", error=str(e))
                self.is_connected = False
                await asyncio.sleep(self.reconnect_delay)
    
    async def _connect(self):
        """Establish connection to IB Gateway."""
        try:
            port = settings.IBKR_PAPER_TRADING_PORT if self.paper_trading else settings.IBKR_LIVE_TRADING_PORT
            mode = "Paper Trading" if self.paper_trading else "Live Trading"
            
            # Create new IB instance if needed
            if self.ib is None or not self.ib.isConnected():
                if self.ib and not self.ib.isConnected():
                    try:
                        self.ib.disconnect()
                    except:
                        pass
                
                self.ib = IB()
                
                logger.info(
                    f"Connecting keep-alive to IBKR {mode} Gateway",
                    port=port,
                    client_id=self.client_id
                )
                
                await self.ib.connectAsync('127.0.0.1', port, clientId=self.client_id)
                self.is_connected = True
                self.reconnect_attempts = 0
                
                logger.info(
                    f"✅ Keep-alive connected to IBKR Gateway",
                    port=port,
                    client_id=self.client_id
                )
                # Notify via Telegram on successful (re)connection
                await self._notify_telegram("✅ IB Gateway keep-alive connected")
                
                # Set up disconnect handler
                self.ib.disconnectedEvent += self._on_disconnect
                
        except Exception as e:
            self.is_connected = False
            self.reconnect_attempts += 1
            
            if self.reconnect_attempts >= self.max_reconnect_attempts:
                logger.error(
                    f"Max reconnection attempts reached ({self.max_reconnect_attempts}) - stopping keep-alive",
                    error=str(e)
                )
                self.is_running = False
            else:
                logger.warning(
                    f"Failed to connect keep-alive (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})",
                    error=str(e)
                )
    
    def _on_disconnect(self):
        """Handle disconnect event."""
        logger.warning("Keep-alive connection lost - will reconnect")
        self.is_connected = False
        # Fire and forget Telegram notice
        asyncio.create_task(self._notify_telegram("⚠️ IB Gateway disconnected (daily restart or network). Reconnecting..."))
        # If this is the scheduled daily restart window, delay reconnect until +2 minutes
        try:
            from datetime import timedelta
            import pytz
            local_tz = pytz.timezone('US/Eastern')
            now_local = datetime.now(local_tz)
            hh, mm = [int(x) for x in settings.IBKR_DAILY_RESTART_TIME.split(":", 1)]
            restart_today = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            # Choose the most recent restart reference (today if not ahead by > 12h)
            if now_local < restart_today - timedelta(hours=12):
                restart_today = restart_today - timedelta(days=1)
            # Window: within +/- 5 minutes of configured restart
            window_start = restart_today - timedelta(minutes=5)
            window_end = restart_today + timedelta(minutes=5)
            if window_start <= now_local <= window_end:
                planned = (restart_today + timedelta(minutes=2)).astimezone(local_tz)
                # Convert to naive local time for comparison with datetime.now() (system tz)
                self.next_connect_time = datetime.now() + (planned - now_local)
                asyncio.create_task(self._notify_telegram(f"⏳ Delaying reconnect until {planned.strftime('%I:%M %p %Z')} (2 min after daily restart)"))
        except Exception as e:
            logger.debug("Could not schedule delayed reconnect window", error=str(e))
    
    async def _send_keepalive(self):
        """Send keep-alive request to prevent timeout."""
        if not self.ib or not self.ib.isConnected():
            self.is_connected = False
            return
        
        try:
            # Request account data as keep-alive (lightweight operation)
            # This prevents Gateway from timing out due to inactivity
            accounts = self.ib.accountValues()
            
            logger.debug(
                f"Keep-alive sent - connection active",
                accounts_count=len(accounts) if accounts else 0,
                timestamp=datetime.now().isoformat()
            )
            
        except Exception as e:
            logger.warning("Keep-alive request failed", error=str(e))
            self.is_connected = False
            await self._notify_telegram("⚠️ Keep-alive ping failed; attempting reconnect...")
    
    async def _daily_restart_watchdog(self):
        """Send heads-up before the configured daily restart and confirm after reconnect.
        Assumes Gateway/TWS is configured to auto-restart around settings.IBKR_DAILY_RESTART_TIME.
        """
        try:
            while self.is_running:
                try:
                    # Compute seconds until next restart minus 2 minutes for heads-up
                    from datetime import datetime, timedelta
                    import pytz
                    local_tz = pytz.timezone('US/Eastern')
                    now_local = datetime.now(local_tz)
                    hh, mm = [int(x) for x in settings.IBKR_DAILY_RESTART_TIME.split(":", 1)]
                    restart_today = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    if now_local >= restart_today:
                        restart_today = restart_today + timedelta(days=1)
                    heads_up_time = restart_today - timedelta(minutes=2)
                    seconds_until_heads_up = max(1, int((heads_up_time - now_local).total_seconds()))
                    await asyncio.sleep(seconds_until_heads_up)
                    # Send heads up
                    await self._notify_telegram("⏰ IB Gateway daily restart in ~2 minutes. Trading will auto-reconnect.")
                    # Sleep until shortly after expected restart time, then we rely on disconnect handler
                    now_local = datetime.now(local_tz)
                    seconds_until_restart = max(1, int((restart_today - now_local).total_seconds()))
                    await asyncio.sleep(seconds_until_restart + 30)
                    # After window, if connected, confirm; if not, connection loop will handle
                    if self.is_connected:
                        await self._notify_telegram("✅ IB Gateway appears healthy post daily restart.")
                except Exception as inner:
                    logger.warning("Daily restart watchdog error", error=str(inner))
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
    
    async def _notify_telegram(self, message: str):
        """Helper to send status to Telegram if service injected."""
        try:
            if self.telegram_service and hasattr(self.telegram_service, '_send_message_to_all_bots'):
                await self.telegram_service._send_message_to_all_bots(message)
        except Exception as e:
            logger.warning("Failed to send keep-alive Telegram notification", error=str(e))
    
    def get_status(self) -> dict:
        """Get current status of keep-alive service."""
        return {
            "is_running": self.is_running,
            "is_connected": self.is_connected,
            "paper_trading": self.paper_trading,
            "reconnect_attempts": self.reconnect_attempts,
            "client_id": self.client_id
        }

