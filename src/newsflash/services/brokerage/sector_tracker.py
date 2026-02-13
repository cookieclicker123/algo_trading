"""
Sector Correlation Tracker - Track FPs per sector for "hot sector" detection.

Tracks FPs per sector during the trading session.
If a sector has 3+ FPs today, it's considered "hot" and potentially pump-and-dump prone.

Currently: Tracking only (no filtering). Will analyze data before deciding on filtering.
"""
import asyncio
from datetime import datetime, date
from typing import Optional, Dict, Any, Set

from ...utils.logging_config import get_logger

logger = get_logger(__name__)

# Configuration
SECTOR_HOT_THRESHOLD = 3  # 3+ FPs = sector is "hot"


class SectorTracker:
    """
    In-memory tracker for sector-level FP rates.

    Resets daily. Tracks:
    - FP count per sector
    - TP count per sector
    - Win rate by sector
    """

    def __init__(self):
        self._sector_fps: Dict[str, int] = {}  # sector -> FP count today
        self._sector_tps: Dict[str, int] = {}  # sector -> TP count today
        self._current_date: Optional[date] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize the tracker."""
        self._current_date = date.today()
        logger.info("SectorTracker started")

    async def stop(self) -> None:
        """Stop the tracker."""
        logger.info(
            "SectorTracker stopped",
            sectors_tracked=len(self._sector_fps)
        )

    async def _check_date_reset(self) -> None:
        """Reset counters if day changed."""
        today = date.today()
        if self._current_date != today:
            async with self._lock:
                self._sector_fps.clear()
                self._sector_tps.clear()
                self._current_date = today
                logger.info("SectorTracker: Daily reset")

    async def is_sector_hot(self, sector: Optional[str]) -> bool:
        """
        Check if a sector is "hot" (many FPs today).

        Returns True if sector has >= SECTOR_HOT_THRESHOLD FPs today.
        """
        if not sector:
            return False

        await self._check_date_reset()

        async with self._lock:
            fp_count = self._sector_fps.get(sector, 0)
            return fp_count >= SECTOR_HOT_THRESHOLD

    async def get_sector_stats(self, sector: Optional[str]) -> Dict[str, Any]:
        """Get statistics for a sector."""
        if not sector:
            return {"fps": 0, "tps": 0, "is_hot": False}

        await self._check_date_reset()

        async with self._lock:
            fps = self._sector_fps.get(sector, 0)
            tps = self._sector_tps.get(sector, 0)
            total = fps + tps
            win_rate = tps / total if total > 0 else None

            return {
                "sector": sector,
                "fps": fps,
                "tps": tps,
                "total_trades": total,
                "win_rate": win_rate,
                "is_hot": fps >= SECTOR_HOT_THRESHOLD
            }

    async def record_outcome(self, sector: Optional[str], profitable: bool) -> None:
        """
        Record a trade outcome for a sector.

        Updates FP/TP counts for the sector.
        """
        if not sector:
            return

        await self._check_date_reset()

        async with self._lock:
            if profitable:
                self._sector_tps[sector] = self._sector_tps.get(sector, 0) + 1
                logger.debug(
                    "SectorTracker: Recorded TP",
                    sector=sector,
                    tps=self._sector_tps[sector]
                )
            else:
                self._sector_fps[sector] = self._sector_fps.get(sector, 0) + 1
                fp_count = self._sector_fps[sector]

                if fp_count >= SECTOR_HOT_THRESHOLD:
                    logger.warning(
                        f"🔥 SECTOR HOT: {sector} has {fp_count} FPs today",
                        sector=sector,
                        fps=fp_count,
                        threshold=SECTOR_HOT_THRESHOLD
                    )
                else:
                    logger.info(
                        "SectorTracker: Recorded FP",
                        sector=sector,
                        fps=fp_count,
                        threshold=SECTOR_HOT_THRESHOLD
                    )

    async def get_all_hot_sectors(self) -> Set[str]:
        """Get all sectors that are currently "hot"."""
        await self._check_date_reset()

        async with self._lock:
            return {
                sector for sector, fps in self._sector_fps.items()
                if fps >= SECTOR_HOT_THRESHOLD
            }

    async def get_daily_summary(self) -> Dict[str, Any]:
        """Get full daily summary of sector performance."""
        await self._check_date_reset()

        async with self._lock:
            all_sectors = set(self._sector_fps.keys()) | set(self._sector_tps.keys())

            sector_stats = {}
            for sector in all_sectors:
                fps = self._sector_fps.get(sector, 0)
                tps = self._sector_tps.get(sector, 0)
                total = fps + tps
                sector_stats[sector] = {
                    "fps": fps,
                    "tps": tps,
                    "total": total,
                    "win_rate": tps / total if total > 0 else None,
                    "is_hot": fps >= SECTOR_HOT_THRESHOLD
                }

            hot_sectors = [s for s, d in sector_stats.items() if d.get("is_hot")]

            return {
                "date": self._current_date.isoformat() if self._current_date else None,
                "sectors_tracked": len(all_sectors),
                "hot_sectors": hot_sectors,
                "hot_sector_count": len(hot_sectors),
                "sector_breakdown": sector_stats
            }

    def get_stats(self) -> Dict[str, Any]:
        """Get quick stats (synchronous)."""
        return {
            "date": self._current_date.isoformat() if self._current_date else None,
            "sectors_tracked": len(self._sector_fps),
            "hot_sectors": len([s for s, fps in self._sector_fps.items() if fps >= SECTOR_HOT_THRESHOLD])
        }


# Global instance
_tracker: Optional[SectorTracker] = None


def get_sector_tracker() -> SectorTracker:
    """Get or create the global sector tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = SectorTracker()
    return _tracker


async def is_sector_hot(sector: Optional[str]) -> bool:
    """Convenience function to check if sector is hot."""
    return await get_sector_tracker().is_sector_hot(sector)


async def record_sector_outcome(sector: Optional[str], profitable: bool) -> None:
    """Convenience function to record outcome."""
    await get_sector_tracker().record_outcome(sector, profitable)
