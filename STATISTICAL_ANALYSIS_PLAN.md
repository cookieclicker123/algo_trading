# Statistical Analysis & Optimization Plan

## Objective
Transition from a heuristic-based "One Size Fits All" model to a quantitatively optimized, "Feature-Driven" trading system. By collecting granular data over the next 4 weeks, we will train specific filter sets for different market conditions.

## 1. The "Golden Dataset"
We will enrich the `recall_record` (and `surge_detection_window_stats`) with the following feature sets to enable backtesting.

### A. Fundamental Features (New)
*   **Float**: Number of tradeable shares. (Critical for volume context).
*   **Market Cap**: Size context.
*   **Relative Volume (Float Turnover)**: `Window Volume / Float`. A 100k surge is huge for a 1M float, noise for AAPL.

### B. Microstructure Features ("Tape Quality")
*   **Tape Quality Score (0-100)**:
    *   **Purity**: % of trades on Ask vs Bid (Buying Pressure).
    *   **Linearity**: Correlation of Price vs Time ($R^2$ of linear regression in surge window).
    *   **Blockiness**: % of volume from trades > $10k.
*   **Liveness**: `max_trade_gap` (Already implemented).
*   **Spread Stability**: Avg spread during surge vs baseline.

### C. Catalyst Features
*   **Keywords**: Extract stems from headlines (e.g., "Grant", "Buyout", "Phase", "Beat").
*   **Source Authority**: Benzinga vs SEC vs Press Release.
*   **Timing**: Time of day (Pre-market vs Open vs Mid-day).

## 2. Statistical Clustering Strategy
Once data is collected, we will segment the market into **Clusters** and tune thresholds for each.

### Proposed Clusters
1.  **"Nano-Float Supernovas"**
    *   *Float < 2M, Biotech/Health*.
    *   *Strategy*: Relaxed Volume (speed is key), Strict Price Action (must halt or moon).
2.  **"Mid-Cap Machines"**
    *   *Float 20M-100M, Tech/Finance*.
    *   *Strategy*: Strict Volume (>50k required), Leniency on Price Action (slower grind).
3.  **"Blue Chip Flow"**
    *   *Float > 200M*.
    *   *Strategy*: Require Massive Block Flow, fading retail noise.

## 3. Optimization Targets
We will optimize for **Expectancy (Win Rate * Reward/Risk)**, not just Win Rate.
*   **Win Rate Target**: > 40% (for Risk:Reward 1:3).
*   **Break-Even Stop**: Identify "Invalidation Points" earlier (e.g., if Tape Quality drops < 50, exit immediately).

## 4. Implementation Phase 1 (Data Collection)
**Action Items:**
1.  [ ] **Float Integration**: Fetch `shares_outstanding` from Alpaca/Poly/YF and save to `surge_stats`.
2.  [ ] **Tape Quality**: Implement `calculate_tape_quality()` and save score.
3.  [ ] **Keywords**: Save cleaned headline tokens list to record.

## 5. Statistical Analysis Script (Python)
In 4 weeks, we will write a script to:
1.  Load all `recall_records`.
2.  Cluster by `Float` + `Sector`.
3.  Run "Grid Search" on Thresholds (e.g., test Volume Multiplier 2.0x to 10.0x).
4.  Output optimal parameters for each Cluster.
