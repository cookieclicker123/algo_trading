# Exit Strategy Part 2: Chart-Based Momentum Alerts

## Status: PLANNED (not yet implemented)

## Core Insight

Traditional chart indicators (RSI, MACD, Stochastic) assume mean-reverting, range-bound price action. News trades are regime changes — RSI hits 80+ within seconds as normal operating state, not overbought. These indicators produce constant false triggers on high-volatility news trades.

## What Actually Works: Microstructure Signals

The useful signals detect when the mechanical fuel behind the move is gone — not price oscillators, but structural tape data.

### Signal 1: Volume Rate Collapse
- The move runs on buying volume. When volume rate drops >60% from peak rate, no more marginal buyers exist.
- Already captured by TradeAnalyticsEngine at 100ms: `volume_rate`, `cumulative_volume`
- False trigger risk: **Low.** Volume doesn't lie. Rate of decline matters — gradual taper differs from cliff.

### Signal 2: Spread Widening
- Market makers widen spreads when move is over or they're getting picked off. Spread doubling from entry = liquidity withdrawal.
- Already captured: `spread`, `spread_pct` in tape snapshots
- False trigger risk: **Low-medium.** Can widen briefly on single large trade, but sustained widening (3+ consecutive 5-second windows) is highly reliable.

### Signal 3: Tape Imbalance Flip
- `buying_pressure` ratio dropping from >0.6 to <0.4 = sellers dominating.
- Already captured: `imbalance_ratio`, `buying_pressure`
- False trigger risk: **Medium.** Brief imbalance flips during consolidation are common. Needs 2-3 window persistence.

## Why Combine All Three

The core question: "How do we know exhaustion is real when relaxed states always happen before sudden jumps?"

- Volume cliff alone: ~70% reliable (volume can resume)
- Spread widening alone: ~65% reliable (can be transient)
- Imbalance flip alone: ~60% reliable (brief flips are noise)
- **All three together**: Very high confidence. Move is structurally over — new wave of buyers arriving against widened spreads into seller-dominated tape with no volume is rare.

For 30-second trades: use 1-2 second measurement windows (not 5) and require **2 of 3** signals.

## VWAP

VWAP cross (price < session VWAP) is a **confirmation** signal, not primary. If microstructure signals fire AND price is below VWAP = very strong. But VWAP alone is too noisy — price whipsaws around VWAP during consolidation.

## Telegram Notification Format

During active position, fire-and-forget via FastTradeNotifier:

```
MOMENTUM FADING on $CETX
Volume: down 72% from peak
Spread: 2.1% -> 4.8% (widened 2.3x)
Imbalance: 0.71 -> 0.34 (sellers dominating)
Current: +18.4% | Peak: +22.1%

Profile: military_contract peaks ~+25% at ~4min, fades to +16%

/exit CETX to exit now
```

No automated exits. User decides. System surfaces tape state.

## Integration Points

- Runs inside `position_manager._process_price_update()` (WebSocket quote handler, sub-100ms)
- Computes volume rate, spread ratio, imbalance from existing tape data (no new sources)
- Fires alert via existing `fast_notifier` (FastTradeNotifier) — zero blocking
- Cooldown: one alert per position per signal state change (no spam)
- All data already captured by TradeAnalyticsEngine — no new infrastructure needed

## Implementation Steps (when ready)

1. Add microstructure signal scoring to position manager (volume rate, spread ratio, imbalance flip)
2. Add Telegram momentum alerts via FastTradeNotifier with `/exit` command
3. Include headline profile context in the alert ("this type typically peaks at X")
