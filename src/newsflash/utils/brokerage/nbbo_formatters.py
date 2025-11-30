"""
NBBO formatting utilities.
Pure functions for formatting bid/ask/spread data.
"""
from typing import Optional, Dict, Any


def build_nbbo_info(
    bid: Optional[float],
    ask: Optional[float],
    *,
    spread: Optional[float] = None,
    source: str = "ladder",
    fallback: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Compose NBBO telemetry from bid/ask with optional fallback data.
    
    Args:
        bid: Bid price
        ask: Ask price
        spread: Optional explicit spread value
        source: Source identifier for the NBBO data
        fallback: Optional fallback NBBO dictionary
        
    Returns:
        Dictionary with bid, ask, mid, spread, source, or None if no valid data
    """
    nbbo: Dict[str, Any] = {}
    
    def _valid(value: Optional[float]) -> bool:
        return value is not None and isinstance(value, (int, float)) and value > 0

    if _valid(bid):
        nbbo["bid"] = float(bid)
    if _valid(ask):
        nbbo["ask"] = float(ask)

    if "bid" in nbbo and "ask" in nbbo:
        nbbo["mid"] = round((nbbo["bid"] + nbbo["ask"]) / 2.0, 4)
        nbbo["spread"] = round(nbbo["ask"] - nbbo["bid"], 4)
    else:
        if fallback:
            for key in ("bid", "ask", "mid", "spread"):
                if key not in nbbo and fallback.get(key) is not None:
                    nbbo[key] = fallback.get(key)
    
    if spread is not None:
        nbbo["spread"] = float(spread)
    
    if "spread" not in nbbo and "bid" in nbbo and "ask" in nbbo:
        nbbo["spread"] = round(nbbo["ask"] - nbbo["bid"], 4)
    
    nbbo["source"] = source
    
    if fallback and not nbbo:
        return fallback
    
    if fallback:
        for key, value in fallback.items():
            nbbo.setdefault(key, value)
    
    return nbbo or None

