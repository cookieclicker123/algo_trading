# Trading System Mechanics

## Overview
News-based trading system that monitors Benzinga WebSocket for IMMINENT headlines, validates with microstructure signals, and executes via Alpaca with extended-hours support.

**Core Principle**: AI only trades strong headlines. Weak headlines are rejected as false positives and used to refine prompts. Position sizing comes from microstructure confirmation, not headline quality assessment.

**Primary Goal**: Faithful statistics collection + gap-and-trap prevention. Over time, data will reveal optimal thresholds and feature combinations.

---

## 1. ENTRY CONDITIONS (In Order)

### Pre-Trade Checks (Blocking)

| # | Check | Threshold | Rationale |
|---|-------|-----------|-----------|
| 1 | **Circuit Breaker** | Daily P&L ≤ -$5 | Shuts down system if daily losses exceed threshold |
| 2 | **Auto-Trade Enabled** | Config flag | Master switch for trading |
| 3 | **Classification = IMMINENT** | AI classifier | Only trade AI-confirmed strong catalysts |
| 4 | **Active Position** | No duplicate | Cannot enter same ticker twice |
| 5 | **Ticker Cooldown** | 5 min (profit) / 30 min (loss) | Dynamic cooldown based on exit outcome |
| 6 | **Ticker Blacklist** | 3 consecutive FPs | Auto-blacklist serial pump-and-dump tickers |

### Microstructure Gating (Must Pass ONE)

**Path A: STRENGTH** (2-second window after publication):
- Confluence score ≥ 1 (of 5 criteria below)
- Price excursion ≥ 0.5% (any direction)

**5 Confluence Criteria:**
| Criterion | Threshold | Description |
|-----------|-----------|-------------|
| Dollar Volume | $2,500 in 2s | Meaningful activity |
| Trade Count | 5+ trades | Real activity, not one big order |
| Imbalance Ratio | ≥ 0.5 (60%+ buy) | Buy pressure dominance |
| Price Excursion | 1%+ from recv_ask | Price moving |
| First Trade Latency | < 1.5s from pub | Fast reaction = informed traders |

**Path B: SURGE** (8-second window, if STRENGTH fails):
- ALL 4 criteria must be met:
  - Volume ≥ max(2000 shares, 3× prior 10min avg)
  - Trade count ≥ max(20 trades, 3× prior 10min avg)
  - Price excursion ≥ 2% (positive direction only)
  - Buying pressure ≥ 80%

### Safety Filters (Applied to ALL trades - STRENGTH, SURGE, or monitoring)

**These filters apply regardless of which gate was passed. No exceptions.**

| # | Filter | Threshold | Lesson |
|---|--------|-----------|--------|
| 1 | **Market Cap** | ≥ $2M | Avoid manipulated micro-caps |
| 2 | **Biotech Price** | ≥ $30 (if biotech) | Sub-$30 biotechs have poor risk/reward |
| 3 | **Spread** | ≤ 5% of mid | Wide spread = instant loss |
| 4 | **Selling Pressure** | Imbalance > -0.3 | Block if >65% selling (someone knows something) |
| 5 | **Pub→Recv Ask Change** | ≤ 3% | Front-running detection (VRME) |
| 6 | **Recv→Fill Ask Change** | ≤ 3% | Chase/volatility filter |
| 7 | **Ask vs First Trade** | ≤ 3% premium | Pump-and-dump pattern (EPOW) |
| 8 | **Pre-News Runup** | ≤ 5% in prior 30 min | News may be priced in or leaked |
| 9 | **Confluence Runup** | ≤ 5% | Momentum exhaustion - entering at top |
| 10 | **Entry Delay** | ≤ 10s from publication | Fights late WebSocket deliveries |

---

## 2. POSITION SIZING

### Base Size
**$4 base for all trades.**

The AI only trades strong headlines. If a weak headline slips through, that's a false positive to be fixed in prompt refinement. Therefore:
- No "SMALL" or "MODERATE" sizing based on headline weakness
- Base is always $4 (will scale to $4k-$20k when validated)

### Confluence Multiplier (Microstructure Confirmation)
| Level | Criteria Met | Multiplier | Result |
|-------|--------------|------------|--------|
| Full | 4-5 of 5 | 1.0× | $4.00 |
| Partial | 2-3 of 5 | 0.75× | $3.00 |
| No Volume | 0-1 of 5 | 0.5× | $2.00 (await scale-in confirmation) |

### Final Position Size
```
Position = $4 × Confluence_Multiplier
```

### Surge Trades
Surge trades use the **same sizing logic** as confluence trades:
- Same $4 base
- Same confluence multiplier based on criteria met
- Same safety filters
- No special "STANDARD" override

*Rationale: If safety filters pass, the ticker hasn't run away. Even if max excursion lasts a second, that's enough to exit with profit.*

### Risk Adjustments
- **Recently Skipped Ticker**: If headline for same ticker was skipped within 10 min, cap at $5k (second-wave risk)

---

## 3. EXIT CONDITIONS

### Stop Loss System

**Base Stop**: -5% from entry price (fixed, caps max loss per trade)

**Grace Period (First 5 seconds)**:
- Brief spikes are noise, not signal
- Require 1.25s confirmation before executing stop
- *SMTK lesson*: Hit -7% at 1.8s, then +37% at 2.0s - recovered in 0.2s
- *KIDZ lesson*: 1.046s breach at -7.4% but recovered to +40%

**After Grace Period**: Immediate execution if stop breached

### Breakeven Trigger (Protects Winners Before Any Exit)

| Condition | Effect |
|-----------|--------|
| Price hits +5% | Start 0.5s confirmation timer |
| Stays at +5% for 0.5s | Stop moves from -5% to 0% (breakeven) |

**Why Breakeven + Floor Rules?**

They serve different purposes at different stages:

```
Entry ──────► +5% (Breakeven activates) ──────► +10% (Tier 1 exit) ──────►
              │                                  │
              │ Stop: -5% → 0%                   │ Floor: +2.5%
              │ Protects BEFORE any exit         │ Protects AFTER partial exit
              │                                  │
              └─ If drops to 0%, exit all        └─ If drops to +2.5%, exit remaining
```

- **Breakeven**: Protects full position from turning winner into loser. Activates at +5%, before any tiered exits.
- **Floor Rule**: Protects remaining position after you've already taken partial profits. Each tier has its own floor.

*Example*: You're up +8%. Breakeven is active (stop at 0%). Price drops to +1%. You exit at +1% profit instead of -5% loss. Without breakeven, you'd have held hoping for +10% tier and potentially hit -5% stop.

### Tiered Profit-Taking
| Tier | Profit Level | Exit % | Floor After Exit |
|------|--------------|--------|------------------|
| 1 | +10% | 50% of position | +2.5% |
| 2 | +15% | 50% of remaining | +5.0% |
| 3 | +20% | 100% of remaining | N/A (fully exited) |

### Floor Rule
After taking a tiered exit, if price drops to floor level, exit remaining position immediately.

*Example*: Exit 50% at +10%. Floor is now +2.5%. If price drops from +10% to +2.5%, exit remaining 50% at +2.5% profit (not wait for -5% stop).

### Early Exit
| Condition | Behavior |
|-----------|----------|
| After 5 minutes AND profit ≥ 10% | Exit entire remaining position |

*Rationale: ELBM hit +11% at 6:55 but only +6% at 10 min. Capture the move early.*

### Forced Exits (Overnight Risk)
| Condition | Behavior |
|-----------|----------|
| **Session End** | Force exit 10 min before extended hours close |
| Premarket | Exit by 9:20 AM ET (market opens 9:30) |
| Postmarket | Exit by 7:50 PM ET (session ends 8:00) |

*Rationale: If still holding at close, stuck until next session. Overnight gap risk is unacceptable.*

---

## 4. TIMING SUMMARY

```
[Article Published] ─────────────────────────────────────────────────────────►

 0s        2s        10s                 5min                    Session End
 │         │         │                    │                            │
 │◄───────►│         │                    │                            │
 │ STRENGTH│         │                    │                            │
 │  check  │         │                    │                            │
 │         │◄───────►│                    │                            │
 │         │ SURGE   │                    │                            │
 │         │ window  │                    │                            │
 │         │ (8s)    │                    │                            │
 │         │         │◄── Max entry ─────►│                            │
 │         │         │    delay (10s      │                            │
 │         │         │    from pub)       │                            │
 │         │         │                    │◄─── Early exit check ─────►│
 │         │         │                    │     (if +10% after 5min)   │
 │         │         │                    │                            │◄─ Force exit
 │         │         │                    │                            │   (10 min buffer)

 SAFETY FILTERS APPLY AT ANY ENTRY POINT ─────────────────────────────────────
```

---

## 5. ORDER EXECUTION

### Extended Hours Strategy
- **Order Type**: Limit orders (required for extended hours)
- **Time in Force**: DAY
- **Chase Logic**: Chase the ask with limit order updates
- **Parallel Submission**: New order submitted while old cancels (50ms gap vs 200-500ms)

### Fill Timeout
Orders that don't fill are cancelled and retried at current ask.

---

## 6. PROTECTION MECHANISMS

| Protection | Trigger | Effect |
|------------|---------|--------|
| Circuit Breaker | Daily P&L ≤ -$5 | Block all new trades |
| Duplicate Guard | Active position exists | Block entry |
| Cooldown | 5 min (profit) / 30 min (loss) | Dynamic re-entry prevention |
| Session End | 10 min before close | Force exit all |
| Spread Filter | >5% spread | Block entry |
| Selling Pressure | Imbalance < -0.3 | Block entry (>65% selling) |
| Price Runaway | >3% move per leg | Block entry |
| Breakeven Stop | After +5% confirmed | Stop moves to 0% |
| Floor Rule | After tiered exit | Protect remaining gains |

---

## 7. DATA FLOW

```
Benzinga WebSocket
       │
       ▼
 ArticleReceived
       │
       ▼
  Groq AI Classifier ──► NOT IMMINENT ──► Skip (or refine prompt if false negative)
       │
       ▼ (IMMINENT - strong headline only)
  Auto-Trade Service
       │
       ├── Circuit Breaker Check
       ├── Duplicate Position Check
       ├── Cooldown Check
       ├── STRENGTH/SURGE Gate (microstructure confirmation)
       ├── Safety Filters (ALL 9, no exceptions)
       │
       ▼ (All Pass)
  TradeRequested Event
       │
       ▼
  Trade Executor (Extended Hours)
       │
       ▼
  TradeExecuted Event
       │
       ├── Position Manager (monitors exits)
       ├── Signal Stats Engine (records TP/FP for analysis)
       └── Recall Stats Engine (records FN - missed opportunities)
```

---

## 8. STATISTICS COLLECTION

### What We Track (All 4 Outcome Types)

**For every outcome (TP, FP, FN, TN), we collect:**
- **Session**: premarket or postmarket
- **Exact time**: HH:MM:SS for optimal trading time analysis
- **All filter checkpoint values**: For feature predictiveness ranking

| Metric | Purpose |
|--------|---------|
| **Slippage from Decision** | TRUE slippage: fill_price vs decision-time ask |
| **Order vs Depth Ratio** | Market impact: your order size / ask_size |
| **Confluence Criteria** | Which of 5 criteria were met |
| **Entry Delay** | Seconds from publication to fill |
| **Exit Reason** | Stop, tier, floor, early, session_end |
| **P&L per Trade** | Actual profit/loss |
| **Filter Values** | Every checkpoint value for hit rate analysis |

### Confusion Matrix
| Outcome | Definition | File |
|---------|------------|------|
| **True Positive (TP)** | Traded, made money | signal_*.json |
| **False Positive (FP)** | Traded, lost money | signal_*.json |
| **False Negative (FN)** | Didn't trade, would have made money | recall_*.json |
| **True Negative (TN)** | Didn't trade, would have lost money | recall_*.json |

### Filter Hit Rate Analysis

**Daily aggregation** compares TP vs FP distributions for each filter:
```json
"filter_analysis": {
    "spread_pct": {
        "tp_avg": 1.2, "tp_max": 3.0,
        "fp_avg": 2.8, "fp_max": 4.5,
        "discriminates": true
    }
}
```

**Weekly aggregation** ranks filters by predictiveness to identify:
1. Which features discriminate best between TP and FP
2. Optimal thresholds for each filter
3. Feature combinations that maximize win rate AND profit

*Over hundreds of samples, this reveals the optimal configuration.*

### Daily Analytics
End-of-day job aggregates all trades into:
- Confusion matrix counts
- Slippage analysis (avg, max, by confluence level)
- Depth analysis (orders exceeding available depth)
- Missed opportunities (FN analysis from recall engine)
- Filter hit rate comparison (TP vs FP distributions)
- Session/time breakdown (premarket vs postmarket, optimal trading times)

---

## 9. GAP AND TRAP PREVENTION

The system's primary defensive goal is preventing gap-and-trap scenarios:

| Trap Type | Filter | How It Catches |
|-----------|--------|----------------|
| Front-running | Pub→Recv >3% | Price moved before we saw article |
| Chase trap | Recv→Fill >3% | Price moving during our checks |
| Pump-and-dump | Ask vs First Trade >3% | Ask inflated above trade prices |
| Pre-news leak | Pre-news runup >5% | Stock already ran before news (insider buying, leaked) |
| Momentum exhaustion | Confluence runup >5% | Move already happened in window |
| Late delivery | Entry Delay >10s | WebSocket delivered article late |
| Selling pressure | Imbalance < -0.3 | Smart money exiting, we'd be exit liquidity |
| Wide spread | Spread >5% | Instant loss on entry |

**Philosophy**: We cannot prevent all losses, but we CAN prevent gap-and-trap scenarios where the deck is stacked against us before we even enter.
