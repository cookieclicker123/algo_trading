"""
Market Hours Scheduler - Manages service lifecycle based on market sessions.

Shuts down heavy services (websocket) during off-hours to prevent rate limits
and restarts them before market opens.

Schedule:
- Premarket shutdown: 9:30 AM ET (market open - system inactive during market hours)
- Postmarket startup: 3:45 PM ET (15 minutes before postmarket at 4:00 PM ET)
- Overnight shutdown: 1:00 AM ET (after postmarket, before dead hours)
- Premarket startup: 3:55 AM ET (5 minutes before premarket at 4:00 AM ET)
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional
import pytz

from ...utils.logging_config import get_logger
from ...utils.brokerage.session_detector import get_market_session
from ..service_initialization import Services

logger = get_logger(__name__)


class MarketHoursScheduler:
    """
    Scheduler that manages service lifecycle based on market hours.
    
    Responsibilities:
    - Shutdown websocket at 9:30 AM ET (market open - system inactive during market hours)
    - Restart websocket at 3:45 PM ET (15 min before postmarket)
    - Shutdown websocket at 1:00 AM ET (off-hours)
    - Restart websocket at 3:55 AM ET (5 min before premarket)
    - Monitor market session to determine shutdown/startup times
    - Handle graceful shutdown and startup
    
    Stateless: Uses session_detector for time calculations.
    """
    
    def __init__(
        self,
        services: Services,
        telegram_notifier=None,  # TelegramNotifier for sending notifications
        shutdown_hour: int = 20,  # 8:00 PM ET (postmarket ends)
        shutdown_minute: int = 0,  # Exactly 8:00 PM
        startup_hour: int = 3,  # 3:55 AM ET
        startup_minute: int = 55  # 5 minutes before premarket at 4:00 AM
    ):
        """
        Initialize market hours scheduler.
        
        Args:
            services: Services container with websocket and other services
            telegram_notifier: Optional TelegramNotifier for sending session transition notifications
            shutdown_hour: Hour to shutdown (ET, default 20 = 8:00 PM, right after postmarket ends)
            shutdown_minute: Minute to shutdown (default 0 = exactly 8:00 PM)
            startup_hour: Hour to startup (ET, default 3 = 3:55 AM)
            startup_minute: Minute to startup (default 55 = 5 minutes before premarket)
        """
        self.services = services
        self.telegram_notifier = telegram_notifier
        self.shutdown_hour = shutdown_hour
        self.shutdown_minute = shutdown_minute
        self.startup_hour = startup_hour
        self.startup_minute = startup_minute
        
        # Market hours shutdown/startup times
        self.market_open_shutdown_hour = 9  # 9:30 AM ET (market open)
        self.market_open_shutdown_minute = 30
        self.postmarket_startup_hour = 15  # 3:45 PM ET (15 min before postmarket at 4:00 PM)
        self.postmarket_startup_minute = 45
        
        # Track scheduler state
        self._scheduler_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._websocket_shutdown = False  # Track if websocket was manually shut down
        
        logger.info(
            "MarketHoursScheduler initialized",
            shutdown_time=f"{shutdown_hour:02d}:{shutdown_minute:02d} ET",
            startup_time=f"{startup_hour:02d}:{startup_minute:02d} ET",
            dead_period_hours="8:00 PM - 3:55 AM ET (8 hours)",
            telegram_notifications="enabled" if telegram_notifier else "disabled"
        )
    
    async def start(self) -> None:
        """
        Start the scheduler loop.
        
        IMPORTANT: Also checks if we should be running WebSocket right now.
        If outside trading hours, shuts down the websocket immediately.
        """
        if self._is_running:
            logger.debug("MarketHoursScheduler already running")
            return
        
        # Check current state - determine if websocket should be running
        et_tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(et_tz)
        time_str = now_et.strftime("%Y-%m-%d %H:%M:%S %Z")
        is_weekend = now_et.weekday() >= 5  # Saturday = 5, Sunday = 6
        
        if is_weekend:
            # It's weekend - websocket should be DOWN
            logger.info(
                "MarketHoursScheduler: Detected weekend - checking websocket state",
                day=now_et.strftime("%A")
            )
            
            # If websocket is running, shut it down
            if self.services.websocket and self.services.websocket.infra:
                if hasattr(self.services.websocket.infra, '_threads_should_run') and self.services.websocket.infra._threads_should_run:
                    await self._send_notification(
                        f"⏸️ WebSocket NOT started (weekend)\n"
                        f"📍 Detected: {now_et.strftime('%A')}\n"
                        f"🕐 Time: {time_str}\n"
                        f"⏰ Will start: Monday 3:55 AM ET"
                    )
                    await self.services.websocket.stop()
                    logger.info("MarketHoursScheduler: Websocket shut down for weekend")
            
            self._websocket_shutdown = True
        else:
            # Weekday - check market session
            session, _ = get_market_session()
            
            if session == "market_hours":
                # Market hours - websocket should be DOWN (system inactive during market hours)
                if self.services.websocket and self.services.websocket.infra:
                    if hasattr(self.services.websocket.infra, '_threads_should_run') and self.services.websocket.infra._threads_should_run:
                        await self._send_notification(
                            f"⏸️ WebSocket NOT started (market hours)\n"
                            f"📍 Detected: Market Hours\n"
                            f"🕐 Time: {time_str}\n"
                            f"⏰ Will start: 3:45 PM ET (15 min before postmarket)"
                        )
                        await self.services.websocket.stop()
                        logger.info("MarketHoursScheduler: Websocket shut down for market hours")
                self._websocket_shutdown = True
            elif session in ["premarket", "postmarket"]:
                # We're in premarket or postmarket - websocket should be running
                if self.services.websocket and self.services.websocket.infra:
                    if hasattr(self.services.websocket.infra, '_threads_should_run') and self.services.websocket.infra._threads_should_run:
                        self._websocket_shutdown = False
                        next_shutdown = "9:30 AM ET" if session == "premarket" else "1:00 AM ET"
                        await self._send_notification(
                            f"✅ WebSocket running ({session})\n"
                            f"🕐 Time: {time_str}\n"
                            f"⏰ Will shutdown: {next_shutdown}"
                        )
                        logger.info(
                            "MarketHoursScheduler: Detected trading session, websocket is running",
                            session=session
                        )
                    else:
                        # Websocket should be running but isn't - warn
                        logger.warning(
                            "MarketHoursScheduler: Detected trading session but websocket is not running",
                            session=session
                        )
            else:
                # We're in closed hours (1am-3:55am) - websocket should be shut down
                logger.info(
                    "MarketHoursScheduler: Detected closed hours - checking websocket state",
                    session=session
                )
                
                # If websocket is running, shut it down
                if self.services.websocket and self.services.websocket.infra:
                    if hasattr(self.services.websocket.infra, '_threads_should_run') and self.services.websocket.infra._threads_should_run:
                        await self._send_notification(
                            f"⏸️ WebSocket NOT started (closed hours)\n"
                            f"📍 Session: {session}\n"
                            f"🕐 Time: {time_str}\n"
                            f"⏰ Will start: {self.startup_hour:02d}:{self.startup_minute:02d} ET"
                        )
                        await self.services.websocket.stop()
                        logger.info("MarketHoursScheduler: Websocket shut down for closed hours")
                
                self._websocket_shutdown = True
        
        self._is_running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("MarketHoursScheduler started")
    
    async def stop(self) -> None:
        """Stop the scheduler loop."""
        if not self._is_running:
            return
        
        self._is_running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        
        logger.info("MarketHoursScheduler stopped")
    
    async def _send_notification(self, message: str) -> None:
        """Send Telegram notification if notifier is configured."""
        if self.telegram_notifier:
            try:
                await self.telegram_notifier.send_system_message(message)
            except Exception as e:
                logger.error(f"Failed to send scheduler notification (Telegram): {e}")
    
    async def _scheduler_loop(self) -> None:
        """
        Main scheduler loop - checks time and manages service lifecycle.
        
        Runs every minute to check if it's time to shutdown/startup.
        """
        logger.info("MarketHoursScheduler loop started")
        
        while self._is_running:
            try:
                et_tz = pytz.timezone("US/Eastern")
                now_et = datetime.now(et_tz)
                current_hour = now_et.hour
                current_minute = now_et.minute
                
                # Determine day type
                weekday = now_et.weekday()  # Monday=0, Tuesday=1, ..., Friday=4, Saturday=5, Sunday=6
                is_friday = weekday == 4
                is_monday = weekday == 0
                is_weekday = weekday < 5  # Monday-Friday = 0-4
                is_weekend = weekday >= 5  # Saturday=5, Sunday=6
                
                # Check if it's Friday and shutdown time (8:00 PM ET) - weekend shutdown
                if is_friday and current_hour == self.shutdown_hour and current_minute == self.shutdown_minute:
                    await self._handle_shutdown_time(now_et, is_weekend_shutdown=True)
                
                # Check if it's Monday and startup time (3:55 AM ET) - weekend startup
                elif is_monday and current_hour == self.startup_hour and current_minute == self.startup_minute:
                    await self._handle_startup_time(now_et, is_weekend_startup=True)
                
                # Check if it's a weekday (Mon-Thu) and shutdown time (8:00 PM ET) - daily shutdown
                elif is_weekday and not is_friday and current_hour == self.shutdown_hour and current_minute == self.shutdown_minute:
                    await self._handle_shutdown_time(now_et, is_weekend_shutdown=False)
                
                # Check if it's a weekday (Tue-Fri) and startup time (3:55 AM ET) - daily startup
                elif is_weekday and not is_monday and current_hour == self.startup_hour and current_minute == self.startup_minute:
                    await self._handle_startup_time(now_et, is_weekend_startup=False)
                
                # Check if it's a weekday and market open shutdown time (9:30 AM ET) - market hours shutdown
                elif is_weekday and current_hour == self.market_open_shutdown_hour and current_minute == self.market_open_shutdown_minute:
                    await self._handle_market_hours_shutdown(now_et)
                
                # Check if it's a weekday and postmarket startup time (3:45 PM ET) - postmarket startup
                elif is_weekday and current_hour == self.postmarket_startup_hour and current_minute == self.postmarket_startup_minute:
                    await self._handle_postmarket_startup(now_et)
                
                # Safety check: If it's Saturday or Sunday and websocket is running, shut it down
                is_weekend = now_et.weekday() >= 5  # Saturday = 5, Sunday = 6
                if is_weekend and not self._websocket_shutdown:
                    logger.warning(
                        "MarketHoursScheduler: Detected weekend but websocket is running - shutting down",
                        day=now_et.strftime("%A")
                    )
                    await self._handle_shutdown_time(now_et, is_weekend_shutdown=True)
                
                # Check if we're in a trading session and websocket is down (shouldn't happen, but recover)
                # BUT: Skip this check on weekends AND during market hours (we intentionally shut down during market hours)
                is_weekend = now_et.weekday() >= 5  # Saturday = 5, Sunday = 6
                if not is_weekend:
                    session, _ = get_market_session()
                    # Only recover if in premarket or postmarket (NOT market_hours - we shut down intentionally)
                    if session in ["premarket", "postmarket"]:
                        if self._websocket_shutdown:
                            logger.warning(
                                "MarketHoursScheduler: Detected trading session but websocket is down - restarting",
                                session=session
                            )
                            await self._startup_websocket()
                
                # Sleep for 60 seconds before next check
                await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                logger.info("MarketHoursScheduler loop cancelled")
                break
            except Exception as e:
                logger.error(
                    "MarketHoursScheduler: Error in scheduler loop",
                    error=str(e),
                    exc_info=True
                )
                await asyncio.sleep(60)  # Continue after error
    
    async def _handle_shutdown_time(self, now_et: datetime, is_weekend_shutdown: bool = False) -> None:
        """
        Handle shutdown time - gracefully stop websocket after postmarket ends.
        
        Args:
            now_et: Current time in ET
            is_weekend_shutdown: True if this is Friday shutdown (weekend), False if daily shutdown
        """
        if self._websocket_shutdown:
            logger.debug("MarketHoursScheduler: Websocket already shut down, skipping")
            return
        
        time_str = now_et.strftime("%Y-%m-%d %H:%M:%S %Z")
        
        if is_weekend_shutdown:
            logger.info(
                "MarketHoursScheduler: Friday postmarket ended - shutting down websocket for weekend",
                time=time_str,
                next_startup="Monday 3:55 AM ET"
            )
            # Send notification - attempting shutdown
            await self._send_notification(
                f"🔴 Shutting down WebSocket (weekend)\n"
                f"📍 Friday postmarket ended\n"
                f"🕐 Time: {time_str}\n"
                f"⏰ Will restart: Monday 3:55 AM ET"
            )
        else:
            logger.info(
                "MarketHoursScheduler: Postmarket ended - shutting down websocket for off-hours",
                time=time_str,
                next_startup=f"{self.startup_hour:02d}:{self.startup_minute:02d} ET"
            )
            # Send notification - attempting shutdown
            await self._send_notification(
                f"🔴 Shutting down WebSocket (overnight)\n"
                f"📍 Postmarket ended at 8:00 PM ET\n"
                f"🕐 Time: {time_str}\n"
                f"⏰ Will restart: {self.startup_hour:02d}:{self.startup_minute:02d} ET"
            )
        
        try:
            # Gracefully stop websocket
            if self.services.websocket:
                await self.services.websocket.stop()
                self._websocket_shutdown = True
                logger.info("MarketHoursScheduler: Websocket stopped for off-hours")
                
                # Send success notification
                await self._send_notification(
                    f"✅ WebSocket shutdown complete\n"
                    f"🕐 Time: {time_str}"
                )
        except Exception as e:
            logger.error(
                "MarketHoursScheduler: Error stopping websocket",
                error=str(e),
                exc_info=True
            )
            # Send failure notification
            await self._send_notification(
                f"❌ WebSocket shutdown FAILED\n"
                f"🚨 Error: {str(e)}"
            )
    
    async def _handle_startup_time(self, now_et: datetime, is_weekend_startup: bool = False) -> None:
        """
        Handle startup time - restart websocket before premarket.
        
        Args:
            now_et: Current time in ET
            is_weekend_startup: True if this is Monday startup (after weekend), False if daily startup
        """
        if not self._websocket_shutdown:
            logger.debug("MarketHoursScheduler: Websocket already running, skipping")
            return
        
        time_str = now_et.strftime("%Y-%m-%d %H:%M:%S %Z")
        
        if is_weekend_startup:
            logger.info(
                "MarketHoursScheduler: Monday premarket approaching - restarting websocket after weekend",
                time=time_str,
                premarket_starts_in="5 minutes",
                weekend_downtime="~64 hours (Friday 8pm - Monday 3:55am)"
            )
            # Send notification - attempting startup
            await self._send_notification(
                f"🟢 Starting WebSocket (Monday)\n"
                f"📍 Premarket opens in 5 minutes\n"
                f"🕐 Time: {time_str}\n"
                f"⏰ Weekend downtime: ~64 hours"
            )
        else:
            logger.info(
                "MarketHoursScheduler: Startup time reached - restarting websocket",
                time=time_str,
                premarket_starts_in="5 minutes"
            )
            # Send notification - attempting startup
            await self._send_notification(
                f"🟢 Starting WebSocket (daily)\n"
                f"📍 Premarket opens in 5 minutes\n"
                f"🕐 Time: {time_str}"
            )
        
        try:
            await self._startup_websocket()
            # Send success notification
            await self._send_notification(
                f"✅ WebSocket started successfully\n"
                f"📍 Ready for premarket trading\n"
                f"🕐 Time: {time_str}"
            )
        except Exception as e:
            logger.error(
                "MarketHoursScheduler: Error starting websocket",
                error=str(e),
                exc_info=True
            )
            # Send failure notification
            await self._send_notification(
                f"❌ WebSocket startup FAILED\n"
                f"🚨 Error: {str(e)}"
            )
    
    async def _handle_market_hours_shutdown(self, now_et: datetime) -> None:
        """
        Handle market hours shutdown - stop websocket at market open (9:30 AM ET).
        
        Args:
            now_et: Current time in ET
        """
        if self._websocket_shutdown:
            logger.debug("MarketHoursScheduler: Websocket already shut down, skipping")
            return
        
        time_str = now_et.strftime("%Y-%m-%d %H:%M:%S %Z")
        
        logger.info(
            "MarketHoursScheduler: Market open - shutting down websocket (system inactive during market hours)",
            time=time_str,
            next_startup="3:45 PM ET (15 min before postmarket)"
        )
        
        # Send notification - attempting shutdown
        await self._send_notification(
            f"🔴 Shutting down WebSocket (market hours)\n"
            f"📍 Market opened at 9:30 AM ET\n"
            f"🕐 Time: {time_str}\n"
            f"⏰ Will restart: 3:45 PM ET (15 min before postmarket)\n"
            f"💡 System inactive during market hours (edge only in pre/post market)"
        )
        
        try:
            # Gracefully stop websocket
            if self.services.websocket:
                await self.services.websocket.stop()
                self._websocket_shutdown = True
                logger.info("MarketHoursScheduler: Websocket stopped for market hours")
                
                # Send success notification
                await self._send_notification(
                    f"✅ WebSocket shutdown complete (market hours)\n"
                    f"🕐 Time: {time_str}\n"
                    f"⏰ Will restart: 3:45 PM ET"
                )
        except Exception as e:
            logger.error(
                "MarketHoursScheduler: Error stopping websocket for market hours",
                error=str(e),
                exc_info=True
            )
            # Send failure notification
            await self._send_notification(
                f"❌ WebSocket shutdown FAILED (market hours)\n"
                f"🚨 Error: {str(e)}"
            )
    
    async def _handle_postmarket_startup(self, now_et: datetime) -> None:
        """
        Handle postmarket startup - restart websocket at 3:45 PM ET (15 min before postmarket).
        
        Args:
            now_et: Current time in ET
        """
        if not self._websocket_shutdown:
            logger.debug("MarketHoursScheduler: Websocket already running, skipping")
            return
        
        time_str = now_et.strftime("%Y-%m-%d %H:%M:%S %Z")
        
        logger.info(
            "MarketHoursScheduler: Postmarket approaching - restarting websocket",
            time=time_str,
            postmarket_starts_in="15 minutes",
            market_hours_downtime="~6.5 hours (9:30 AM - 3:45 PM)"
        )
        
        # Send notification - attempting startup
        await self._send_notification(
            f"🟢 Starting WebSocket (postmarket)\n"
            f"📍 Postmarket opens in 15 minutes\n"
            f"🕐 Time: {time_str}\n"
            f"⏰ Market hours downtime: ~6.5 hours (9:30 AM - 3:45 PM)"
        )
        
        try:
            await self._startup_websocket()
            # Send success notification
            await self._send_notification(
                f"✅ WebSocket started successfully\n"
                f"📍 Ready for postmarket trading\n"
                f"🕐 Time: {time_str}"
            )
        except Exception as e:
            logger.error(
                "MarketHoursScheduler: Error starting websocket for postmarket",
                error=str(e),
                exc_info=True
            )
            # Send failure notification
            await self._send_notification(
                f"❌ WebSocket startup FAILED (postmarket)\n"
                f"🚨 Error: {str(e)}"
            )
    
    async def _startup_websocket(self) -> None:
        """Startup websocket service."""
        if self.services.websocket:
            # Check if websocket infrastructure is already running
            # (it might have been started manually or by another process)
            if self.services.websocket.infra and self.services.websocket.infra._threads_should_run:
                logger.debug("MarketHoursScheduler: Websocket already running, skipping startup")
                self._websocket_shutdown = False
                return
            
            await self.services.websocket.start()
            self._websocket_shutdown = False
            logger.info("MarketHoursScheduler: Websocket restarted")
    
    def get_next_shutdown_time(self) -> Optional[datetime]:
        """
        Get the next shutdown time (8:00 PM ET).
        
        Returns:
            Next shutdown datetime in ET, or None if unable to calculate
        """
        try:
            et_tz = pytz.timezone("US/Eastern")
            now_et = datetime.now(et_tz)
            
            # Today's shutdown time
            today_shutdown = now_et.replace(
                hour=self.shutdown_hour,
                minute=self.shutdown_minute,
                second=0,
                microsecond=0
            )
            
            # If we're before today's shutdown, return today's
            if now_et < today_shutdown:
                return today_shutdown
            
            # Otherwise, return tomorrow's shutdown
            tomorrow_shutdown = today_shutdown + timedelta(days=1)
            
            # Handle weekends - skip to Monday
            while tomorrow_shutdown.weekday() >= 5:  # Saturday = 5, Sunday = 6
                tomorrow_shutdown += timedelta(days=1)
            
            return tomorrow_shutdown
        except Exception as e:
            logger.error("Failed to calculate next shutdown time", error=str(e))
            return None
    
    def get_next_startup_time(self) -> Optional[datetime]:
        """
        Get the next startup time (3:55 AM ET).
        
        Returns:
            Next startup datetime in ET, or None if unable to calculate
        """
        try:
            et_tz = pytz.timezone("US/Eastern")
            now_et = datetime.now(et_tz)
            
            # Today's startup time
            today_startup = now_et.replace(
                hour=self.startup_hour,
                minute=self.startup_minute,
                second=0,
                microsecond=0
            )
            
            # If we're before today's startup, return today's
            if now_et < today_startup:
                return today_startup
            
            # Otherwise, return tomorrow's startup
            tomorrow_startup = today_startup + timedelta(days=1)
            
            # Handle weekends - skip to Monday
            while tomorrow_startup.weekday() >= 5:  # Saturday = 5, Sunday = 6
                tomorrow_startup += timedelta(days=1)
            
            return tomorrow_startup
        except Exception as e:
            logger.error("Failed to calculate next startup time", error=str(e))
            return None
