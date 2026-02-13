# Momentum Trailing Stop

## Overview

A momentum-based trailing stop system that activates after hitting +15% profit, designed to capture more upside on runners while protecting gains.

**Current System**: Fixed tiers (+10%, +15%, +20%) with floors after each tier exit.

**Proposed Enhancement**: After Tier 2 (+15%), replace fixed +20% exit with momentum-aware trailing stop.

---

## The Problem

Fixed tiers leave money on the table:
- Stock hits +15%, you exit 25% of position
- Stock continues to +35%, but you exit remaining at +20%
- You captured +20% on 75% when +35% was achievable

Conversely, fixed tiers can be too slow:
- Stock hits +15%, momentum dies
- You wait for +20% that never comes
- Price reverses to floor (+5%), you exit remaining there

---

## Proposed Solution

### When It Activates
- After Tier 2 exit (+15%)
- Only on remaining position (25% of original)

### How It Works

**1. Trailing Floor (Always Active)**
```
floor_price = peak_price - (entry_price * 0.05)
min_floor = entry_price * 1.05  # Never below +5%
trailing_floor = max(floor_price, min_floor)
```

As price rises, floor rises. Never falls below +5% from entry.

**Example trajectory:**
| Peak Price | Floor Price | Protection |
|------------|-------------|------------|
| +15% | +10% | 10% locked |
| +20% | +15% | 15% locked |
| +30% | +25% | 25% locked |
| +25% (drops) | +25% (holds) | EXIT at +25% |

**2. Momentum Detection (Early Exit Signal)**

Momentum = price velocity over rolling 3-second window

```python
velocity = (current_price - price_3s_ago) / 3  # $/second

# Exponential smoothing
smoothed_velocity = 0.3 * velocity + 0.7 * prev_smoothed_velocity
```

**Exit conditions:**
1. Price hits trailing floor, OR
2. Smoothed velocity negative for 3+ consecutive seconds (momentum exhaustion)

---

## Market Physics Rationale

**Why velocity matters:**
- Positive velocity = buyers still aggressive, price pushing higher
- Zero velocity = equilibrium, likely near local top
- Negative velocity = sellers taking over, trend reversing

**Why 3-second confirmation:**
- Sub-second dips are noise (spread fluctuation, odd-lot trades)
- 3 seconds of selling pressure indicates real directional change
- News trades move fast - 3s is enough to confirm reversal

**What professionals do:**
1. **Volume-confirmed momentum**: High volume + positive velocity = hold. Low volume + positive velocity = suspicious.
2. **VWAP relationship**: Price above VWAP = bullish flow. Below = distribution.
3. **Tape reading**: Watch for large sell prints vs small buy nibbles.

For retail without Level 2: velocity + trailing floor is a reasonable proxy.

---

## Implementation Plan

### Phase 1: Data Collection (Track Only) - IMPLEMENTED

Data collection is now active in `position_manager.py`. After Tier 2 (+15%) triggers:

**Position dataclass tracks:**
```python
momentum_tracking_active: bool           # True once Tier 2 triggered
momentum_tier_2_time: datetime           # When +15% tier triggered
momentum_tier_2_price: float             # Price at +15% trigger
momentum_peak_after_tier_2: float        # Highest profit % after Tier 2
momentum_peak_price: float               # Price at peak
momentum_peak_time: datetime             # Time of peak
momentum_trajectory: List[Dict]          # Price samples (every ~500ms)
```

**Each trajectory sample contains:**
```python
{
    "time": "2024-02-13T10:30:45.123",
    "price": 12.85,
    "profit_pct": 18.5,              # Current profit %
    "seconds_since_tier_2": 12.5,    # Time since +15%
    "velocity_pct_per_sec": 0.25,    # Rate of change (% per second)
}
```

**Saved to SignalRecord on exit:**
- Last 50 trajectory samples (avoids excessive data)
- Peak price/time after Tier 2
- Can be analyzed to simulate what momentum trailing would have done

**Analyze after 50+ Tier 2+ exits:**
1. What % of trades would have benefited from momentum exit?
2. What % would have been hurt?
3. Optimal velocity threshold for exit signal?

### Phase 2: Shadow Mode

Run momentum trailing alongside current fixed tiers. Log what decision momentum would have made vs what actually happened. After 50+ trades, compare outcomes.

### Phase 3: Live Implementation (After Validation)

Replace Tier 3 (+20%) with momentum trailing after +15%.

---

## Parameters to Tune

| Parameter | Default | Range to Test |
|-----------|---------|---------------|
| Trail distance | 5% of entry | 3%, 4%, 5%, 6% |
| Velocity window | 3 seconds | 2s, 3s, 5s |
| Exhaustion threshold | 3s negative | 2s, 3s, 4s |
| Minimum floor | +5% from entry | +3%, +5%, +7% |

---

## Risk Considerations

**Upside**: Capture more on runners. ELBM-type trades that go +30%+ captured better.

**Downside**:
- Premature exit on volatile chop (velocity spikes negative briefly)
- More complex logic = more potential bugs
- Extended hours have wider spreads, velocity readings noisier

**Mitigation**:
- Require 3s confirmation (not instant)
- Shadow mode first
- Keep fixed floor as safety net

---

## Current Status

**Phase 1 is LIVE** - Data collection active since 2024-02-13.

Every trade that reaches Tier 2 (+15%) now records:
- Price trajectory with velocity calculations
- Peak price/time after Tier 2
- All data saved to SignalRecord for end-of-day analysis

**Next Steps:**
1. Review momentum_tracking data in signal files after a few weeks of trades
2. Calculate what momentum trailing would have done vs fixed tier exits
3. If momentum consistently outperforms, move to Phase 2 (shadow mode)
