# Exit Trade Strategy Analysis

## How Exits Currently Work

### Files:
1. **`src/newsflash/infra/brokerage/service.py`** (lines 176-262): Routes trades to executors
2. **`src/newsflash/infra/brokerage/trade_executor_market_hours.py`**: Market hours executor
3. **`src/newsflash/infra/brokerage/trade_executor_extended_hours.py`**: Extended hours executor  
4. **`src/newsflash/utils/brokerage/ladder_algorithms.py`**: Ladder price calculation

### Current Strategy:

#### ✅ Market Hours Exits:
- **Strategy**: **MARKET orders**
- **File**: `trade_executor_market_hours.py` (line 127-132)
- **Execution**: Immediate fill at market price
- **Status**: ✅ **PERFECT** - Fast execution, no slippage concerns

#### ✅ Extended Hours Exits (IMPROVED):
- **Strategy**: **LADDER LIMIT orders** starting at **midprice**
- **File**: `trade_executor_extended_hours.py` (line 198-205)
- **How it works**:
  1. Base price = **midprice** (from NBBO) ✅
  2. Initial offset = **0 cents** (starts AT midprice) ✅
  3. Early steps = **-1 cent** per attempt (works DOWN toward bid)
  4. Late steps = **-3 cents** per attempt (after 6 attempts)
  5. Example: Mid = $40.00, Bid = $39.95 → $40.00 → $39.99 → $39.98 → ... → $39.95 → ...

#### ✅ Extended Hours Entries (IMPROVED):
- **Strategy**: **LADDER LIMIT orders** starting at **midprice**
- **File**: `trade_executor_extended_hours.py` (line 198-205)
- **How it works**:
  1. Base price = **midprice** (from NBBO) ✅
  2. Initial offset = **0 cents** (starts AT midprice) ✅
  3. Early steps = **+1 cent** per attempt (works UP toward ask)
  4. Late steps = **+3 cents** per attempt (after 6 attempts)
  5. Example: Mid = $40.00, Ask = $40.05 → $40.00 → $40.01 → $40.02 → ... → $40.05 → ...

### ✅ Improved Strategy:

**Both entries and exits now start at midprice:**

- **Entries (BUY)**: Start at midprice, work UP toward ask
- **Exits (SELL)**: Start at midprice, work DOWN toward bid
- **Why better**: Midprice is between bid/ask, giving better fills than starting at ask/bid directly

**Example with Bid = $39.95, Mid = $40.00, Ask = $40.05:**
- **SELL**: $40.00 → $39.99 → $39.98 → ... → $39.95 (bid) ✅
- **BUY**: $40.00 → $40.01 → $40.02 → ... → $40.05 (ask) ✅

## ✅ Implementation Complete

### Changes Made:

1. **Updated `calculate_ladder_base_price()`**:
   - Now accepts `mid` parameter (midprice from NBBO)
   - Uses midprice as base for both BUY and SELL orders
   - Falls back to ask/bid if mid unavailable

2. **Updated `calculate_ladder_parameters()`**:
   - Both BUY and SELL start at **0 cents offset** (at midprice)
   - BUY: Positive steps (work UP toward ask)
   - SELL: Negative steps (work DOWN toward bid)

3. **Updated `trade_executor_extended_hours.py`**:
   - Passes `mid` from NBBO snapshot to `calculate_ladder_base_price()`

## Current Ladder Parameters

From `settings.py`:
- `LADDER_INITIAL_CENTS = 1` (used for step size, not initial offset)
- `LADDER_STEP_CENTS = 1` (early step)
- `LADDER_STEP_CENTS_AFTER = 3` (late step)
- `LADDER_SWITCH_ATTEMPT = 6` (switch after 6 attempts)
- `LADDER_MAX_CENTS = 100` (max $1.00 from start)

**For BUY orders (entries):**
- Base: **Midprice** (e.g., $40.00)
- Initial: **0 cents** (at midprice)
- Early steps: **+1 cent** each ($40.01, $40.02, ...)
- Late steps: **+3 cents** each ($40.06, $40.09, ...)
- Target: **Ask price** ($40.05)

**For SELL orders (exits):**
- Base: **Midprice** (e.g., $40.00)
- Initial: **0 cents** (at midprice)
- Early steps: **-1 cent** each ($39.99, $39.98, ...)
- Late steps: **-3 cents** each ($39.97, $39.94, ...)
- Target: **Bid price** ($39.95)

## Benefits

**Why starting at midprice is better:**
- ✅ **Better fills**: Midprice is between bid/ask, more likely to fill
- ✅ **Better prices**: For entries, you pay less than ask. For exits, you get more than bid
- ✅ **Still protected**: Ladder progression protects against slippage
- ✅ **Works for both**: Same strategy for entries and exits (symmetrical)

**Example with Bid = $39.95, Mid = $40.00, Ask = $40.05:**
- **Entry**: Start at $40.00 (mid), work up to $40.05 (ask) - saves $0.05 vs starting at ask
- **Exit**: Start at $40.00 (mid), work down to $39.95 (bid) - gains $0.05 vs starting at bid
