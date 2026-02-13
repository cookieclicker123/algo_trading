# Complex Features Implementation Plan

## Overview

This document outlines the implementation plan for complex features that require careful design and validation before deployment.

---

## 1. Float-Normalized Volume - IMPLEMENTED (Phase 1: Data Collection)

### Current Status: LIVE

Float-normalized volume tracking is now active. Data being collected:

**SignalRecord fields (executed trades):**
- `float_shares` - From metadata_cache (FMP source)
- `confluence_volume_float_pct` - Confluence volume as % of float
- `surge_volume_float_pct` - Surge volume as % of float
- `volume_1min_float_pct` - First minute volume as % of float

**RecallRecord fields (missed opportunities):**
- `float_shares` - From metadata_cache (FMP source)
- `confluence_volume_float_pct` - Confluence volume as % of float
- `surge_volume_float_pct` - Surge volume as % of float

### Why This Matters

- 2000 shares on a 2M float stock = 0.1% of float (massive)
- 2000 shares on a 50M float stock = 0.004% of float (noise)
- Same raw threshold, very different meanings

Float-normalized volume helps compare activity across different sized companies.

### Data Sources

- Float fetched from FMP API (via metadata_cache)
- Daily refresh at 4am UK time
- Instant lookups from cache (~0ms)

### Analysis Plan

After 100+ samples, analyze:
1. Do TPs have higher confluence_volume_float_pct than FPs?
2. Is there an optimal float_pct threshold that discriminates?
3. Should we use float-normalized thresholds instead of fixed 2000 shares?

### Future: Normalized Thresholds (Phase 2)

If data supports, replace fixed thresholds with:
```python
BASE_FLOAT = 10_000_000  # 10M float as "standard"
BASE_VOLUME_THRESHOLD = 2000  # shares

def get_normalized_volume_threshold(float_shares: int) -> int:
    multiplier = float_shares / BASE_FLOAT
    normalized = int(BASE_VOLUME_THRESHOLD * multiplier)
    return max(200, min(normalized, 50000))
```

---

## 2. Ticker Blacklist

### Current State
No blacklist mechanism. Same pump-and-dump ticker can trap us repeatedly.

### Proposed Solution

**Auto-Blacklist Logic:**
```python
# data/blacklist.json
{
    "EPOW": {
        "consecutive_fps": 3,
        "last_fp_date": "2024-02-10",
        "permanent": true,
        "reason": "3 consecutive false positives"
    }
}
```

**Rules:**
- 3 consecutive FPs on same ticker → permanent blacklist
- "Consecutive" = no TP in between
- Blacklist resets only manually (require human review)

**Why permanent?**
- Serial pump-and-dump tickers rarely change character
- Better to miss one good trade than catch 5 bad ones
- Manual review can whitelist if circumstances change

### Implementation

```python
async def check_blacklist(ticker: str) -> bool:
    """Returns True if ticker is blacklisted."""
    blacklist = await load_blacklist()
    entry = blacklist.get(ticker.upper())
    return entry is not None and entry.get("permanent", False)

async def update_blacklist(ticker: str, profitable: bool):
    """Update blacklist based on trade outcome."""
    blacklist = await load_blacklist()
    ticker = ticker.upper()

    if profitable:
        # Reset consecutive count (but don't unblacklist)
        if ticker in blacklist and not blacklist[ticker].get("permanent"):
            blacklist[ticker]["consecutive_fps"] = 0
    else:
        if ticker not in blacklist:
            blacklist[ticker] = {"consecutive_fps": 0, "permanent": False}

        blacklist[ticker]["consecutive_fps"] += 1
        blacklist[ticker]["last_fp_date"] = date.today().isoformat()

        if blacklist[ticker]["consecutive_fps"] >= 3:
            blacklist[ticker]["permanent"] = True
            blacklist[ticker]["reason"] = "3 consecutive false positives"
            logger.warning(f"TICKER BLACKLISTED: {ticker}")

    await save_blacklist(blacklist)
```

---

## 3. Sector Correlation

### Current State
No sector-level tracking. Hot sectors (lots of FPs today) can trap us repeatedly.

### Proposed Solution

**In-Memory Tracking (Reset Daily):**
```python
# Track FPs per sector today
_sector_fp_count: Dict[str, int] = {}
SECTOR_HOT_THRESHOLD = 3  # 3+ FPs = sector is "hot"

async def check_sector_hot(ticker: str) -> Optional[str]:
    """Returns sector name if hot, None if OK."""
    metadata = await get_ticker_metadata(ticker)
    sector = metadata.get("sector")

    if not sector:
        return None

    if _sector_fp_count.get(sector, 0) >= SECTOR_HOT_THRESHOLD:
        return sector

    return None

async def record_sector_outcome(ticker: str, profitable: bool):
    """Update sector tracking based on trade outcome."""
    metadata = await get_ticker_metadata(ticker)
    sector = metadata.get("sector")

    if sector and not profitable:
        _sector_fp_count[sector] = _sector_fp_count.get(sector, 0) + 1
```

**Usage in auto_trade.py:**
```python
hot_sector = await check_sector_hot(ticker)
if hot_sector:
    logger.info(f"Sector {hot_sector} is hot today ({_sector_fp_count[hot_sector]} FPs)")
    # Option A: Skip entirely
    # Option B: Require higher confluence (4-5 instead of 1)
    # Option C: Log only (track for stats)
```

### Decision Required

- **Option A**: Hard block after 3 sector FPs (aggressive)
- **Option B**: Raise confluence requirement (moderate)
- **Option C**: Track only, filter later (conservative)

Recommendation: **Option C** first, gather data, then decide.

---

## 4. Pre-News 30-Minute Price Trend

### Current State
- Confluence runup filter only checks 2-second window
- No check for pre-news momentum

### Problem
If stock already ran 10% in the 30 minutes before news, the "news pop" may already be priced in.

### Proposed Solution

```python
PRE_NEWS_LOOKBACK_SECONDS = 1800  # 30 minutes
PRE_NEWS_RUNUP_THRESHOLD = 5.0  # 5%

async def check_pre_news_runup(ticker: str, published_at: datetime) -> Optional[float]:
    """
    Check if stock already moved significantly before news.

    Returns: Pre-news change %, or None if can't determine.
    """
    # Get historical quotes from WebSocket cache
    stream_manager = get_stream_manager()
    historical_quotes = await stream_manager.get_recent_quotes(
        ticker,
        max_quotes=3000  # ~30 min at ~100 quotes/min
    )

    if not historical_quotes:
        return None

    # Find price from 30 min ago
    target_time = published_at - timedelta(seconds=PRE_NEWS_LOOKBACK_SECONDS)
    price_30min_ago = find_closest_quote_price(historical_quotes, target_time)

    if not price_30min_ago:
        return None

    # Current price (at publication)
    pub_quote = find_closest_quote_price(historical_quotes, published_at)
    if not pub_quote:
        return None

    change_pct = ((pub_quote - price_30min_ago) / price_30min_ago) * 100
    return change_pct
```

**Usage:**
```python
pre_news_change = await check_pre_news_runup(ticker, published_at)
if pre_news_change and pre_news_change > PRE_NEWS_RUNUP_THRESHOLD:
    await _record_postfilter_skip(
        article_id,
        f"postfilter_pre_news_runup:{pre_news_change:.1f}%"
    )
    return

# Track for statistics
filter_values["pre_news_30min_change_pct"] = pre_news_change
```

### Implementation Notes

- Requires sufficient quote history in WebSocket cache
- May not have 30 min of history for new tickers
- Could use Alpaca historical API as fallback (slower)

---

## 5. SPY Regime Filter

### Current State
SPY daily change is tracked in daily stats but not used for live filtering.

### Proposed Solution

**Step 1: Track Regime**
```python
async def get_market_regime() -> str:
    """Returns 'bullish', 'bearish', or 'neutral'."""
    spy_change = await get_spy_daily_change()

    if spy_change > 0.5:
        return "bullish"
    elif spy_change < -0.5:
        return "bearish"
    return "neutral"
```

**Step 2: Track Correlation**

For each trade, record:
```python
market_regime: str  # bullish/bearish/neutral
spy_daily_change: float  # % at time of trade
```

Analyze: Win rate by regime after 100+ trades.

**Step 3: Decide on Filtering**

If data shows bearish regime has significantly lower win rate:
- Option A: Block marginal trades in bearish regime
- Option B: Require higher confluence in bearish regime
- Option C: Continue tracking (no filtering)

Recommendation: Track regime in stats, don't filter yet.

---

## Implementation Priority

| Feature | Status | Impact |
|---------|--------|--------|
| Pre-news 30min check | ✅ LIVE | High |
| Ticker blacklist | ✅ LIVE | High |
| Sector correlation | ✅ TRACKING | Medium |
| Float normalization | ✅ TRACKING | Medium |
| SPY regime | Pending | Medium |
| Momentum trailing | ✅ TRACKING | High |

### Phase 1 - COMPLETE
1. Pre-news 30min filter (hard filter) ✅
2. Ticker blacklist (auto-blacklist after 3 FPs) ✅
3. Dynamic cooldown (5 min profit / 30 min loss) ✅
4. Hour of day tracking ✅
5. News source tracking ✅

### Phase 2 - IN PROGRESS (Data Collection)
6. Sector hot detection (tracking only, logging when hot) ✅
7. Float normalization (tracking volume as % of float) ✅
8. Momentum trailing (price trajectory after +15%) ✅

### Phase 3 (After Validation)
9. Momentum trailing stop (if data supports)
10. Float-normalized thresholds (if data supports)
11. SPY regime filtering (if data supports)

---

## Open Questions

1. **Blacklist permanence**: 3 consecutive FPs = permanent, or reset after N days?
2. **Sector threshold**: 3 FPs enough, or 5?
3. **Pre-news lookback**: 30 min optimal, or test 15/60?
4. **Float normalization**: Track only, or make hard filter?

Your input on these would help finalize the implementation.
