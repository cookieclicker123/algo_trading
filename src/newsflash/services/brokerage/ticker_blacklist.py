"""
Ticker Blacklist - Auto-blacklist tickers after consecutive false positives.

Rules:
- 3 consecutive FPs on same ticker → permanent blacklist
- "Consecutive" = no TP in between
- Blacklist resets only manually (require human review)

File stored at: data/blacklist.json
"""
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Set

from ...utils.logging_config import get_logger

logger = get_logger(__name__)

# Configuration
BLACKLIST_FILE = Path("data/blacklist.json")
CONSECUTIVE_FPS_THRESHOLD = 3  # Auto-blacklist after this many consecutive FPs


class TickerBlacklist:
    """
    Manages ticker blacklist for preventing trades on serial pump-and-dump tickers.

    Structure:
    {
        "EPOW": {
            "consecutive_fps": 3,
            "last_fp_date": "2024-02-10",
            "permanent": true,
            "reason": "3 consecutive false positives",
            "blacklisted_at": "2024-02-10T12:00:00"
        }
    }
    """

    def __init__(self, blacklist_path: Path = BLACKLIST_FILE):
        self.blacklist_path = blacklist_path
        self._blacklist: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    async def start(self) -> None:
        """Load blacklist from disk."""
        await self._load()
        logger.info(
            "TickerBlacklist started",
            blacklisted_count=len([t for t, d in self._blacklist.items() if d.get("permanent")]),
            tracked_count=len(self._blacklist)
        )

    async def stop(self) -> None:
        """Save blacklist to disk."""
        await self._save()
        logger.info("TickerBlacklist stopped")

    async def _load(self) -> None:
        """Load blacklist from disk."""
        async with self._lock:
            if self.blacklist_path.exists():
                try:
                    with open(self.blacklist_path, "r") as f:
                        self._blacklist = json.load(f)
                    logger.debug("Loaded blacklist", tickers=len(self._blacklist))
                except Exception as e:
                    logger.error("Error loading blacklist", error=str(e))
                    self._blacklist = {}
            else:
                self._blacklist = {}
                # Ensure directory exists
                self.blacklist_path.parent.mkdir(parents=True, exist_ok=True)
            self._loaded = True

    async def _save(self) -> None:
        """Save blacklist to disk."""
        async with self._lock:
            try:
                self.blacklist_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.blacklist_path, "w") as f:
                    json.dump(self._blacklist, f, indent=2)
                logger.debug("Saved blacklist", tickers=len(self._blacklist))
            except Exception as e:
                logger.error("Error saving blacklist", error=str(e))

    async def is_blacklisted(self, ticker: str) -> bool:
        """
        Check if ticker is blacklisted.

        Returns True if permanently blacklisted.
        """
        if not self._loaded:
            await self._load()

        ticker = ticker.upper()
        async with self._lock:
            entry = self._blacklist.get(ticker)
            if entry and entry.get("permanent"):
                return True
            return False

    async def get_blacklist_info(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get full blacklist entry for a ticker."""
        if not self._loaded:
            await self._load()

        ticker = ticker.upper()
        async with self._lock:
            return self._blacklist.get(ticker)

    async def record_trade_outcome(self, ticker: str, profitable: bool) -> bool:
        """
        Record trade outcome and update blacklist accordingly.

        Returns True if ticker was newly blacklisted.
        """
        if not self._loaded:
            await self._load()

        ticker = ticker.upper()
        newly_blacklisted = False

        async with self._lock:
            if profitable:
                # Profitable trade - reset consecutive FP count (but don't unblacklist)
                if ticker in self._blacklist:
                    entry = self._blacklist[ticker]
                    if not entry.get("permanent"):
                        # Reset the counter since we had a win
                        entry["consecutive_fps"] = 0
                        entry["last_win_date"] = datetime.now().isoformat()
                        logger.debug(
                            "Blacklist: Reset FP counter after win",
                            ticker=ticker
                        )
            else:
                # Losing trade - increment consecutive FP count
                if ticker not in self._blacklist:
                    self._blacklist[ticker] = {
                        "consecutive_fps": 0,
                        "permanent": False,
                        "created_at": datetime.now().isoformat()
                    }

                entry = self._blacklist[ticker]

                # Don't modify if already permanently blacklisted
                if entry.get("permanent"):
                    return False

                entry["consecutive_fps"] = entry.get("consecutive_fps", 0) + 1
                entry["last_fp_date"] = datetime.now().date().isoformat()

                logger.info(
                    "Blacklist: Recorded false positive",
                    ticker=ticker,
                    consecutive_fps=entry["consecutive_fps"],
                    threshold=CONSECUTIVE_FPS_THRESHOLD
                )

                # Check if threshold reached
                if entry["consecutive_fps"] >= CONSECUTIVE_FPS_THRESHOLD:
                    entry["permanent"] = True
                    entry["reason"] = f"{CONSECUTIVE_FPS_THRESHOLD} consecutive false positives"
                    entry["blacklisted_at"] = datetime.now().isoformat()
                    newly_blacklisted = True

                    logger.warning(
                        f"🚫 TICKER BLACKLISTED: {ticker} after {CONSECUTIVE_FPS_THRESHOLD} consecutive FPs",
                        ticker=ticker,
                        consecutive_fps=entry["consecutive_fps"]
                    )

        # Save after any modification
        await self._save()

        return newly_blacklisted

    async def get_all_blacklisted(self) -> Set[str]:
        """Get all permanently blacklisted tickers."""
        if not self._loaded:
            await self._load()

        async with self._lock:
            return {
                ticker for ticker, entry in self._blacklist.items()
                if entry.get("permanent")
            }

    async def manual_unblacklist(self, ticker: str, reason: str = "Manual review") -> bool:
        """
        Manually remove a ticker from blacklist.

        Requires explicit human action - not automated.
        Returns True if ticker was unblacklisted.
        """
        ticker = ticker.upper()

        async with self._lock:
            if ticker in self._blacklist:
                entry = self._blacklist[ticker]
                entry["permanent"] = False
                entry["consecutive_fps"] = 0
                entry["unblacklisted_at"] = datetime.now().isoformat()
                entry["unblacklist_reason"] = reason

                logger.info(
                    f"Ticker manually unblacklisted: {ticker}",
                    ticker=ticker,
                    reason=reason
                )

                await self._save()
                return True

            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get blacklist statistics."""
        permanent_count = len([t for t, d in self._blacklist.items() if d.get("permanent")])
        return {
            "total_tracked": len(self._blacklist),
            "permanently_blacklisted": permanent_count,
            "blacklist_file": str(self.blacklist_path)
        }


# Global instance
_blacklist: Optional[TickerBlacklist] = None


def get_ticker_blacklist() -> TickerBlacklist:
    """Get or create the global blacklist instance."""
    global _blacklist
    if _blacklist is None:
        _blacklist = TickerBlacklist()
    return _blacklist


async def is_ticker_blacklisted(ticker: str) -> bool:
    """Convenience function to check if ticker is blacklisted."""
    return await get_ticker_blacklist().is_blacklisted(ticker)


async def record_trade_outcome(ticker: str, profitable: bool) -> bool:
    """Convenience function to record trade outcome."""
    return await get_ticker_blacklist().record_trade_outcome(ticker, profitable)
