# Final Automation Plan - Fully Autonomous Trading System

**Date:** October 29, 2025  
**Status:** Ready for Implementation  
**Current State:** Dual-source news system operational, health monitoring active, manual trading via Telegram working

---

## 🎯 Overview

Transform the current semi-automated system into a **fully autonomous trading system** that:
1. Automatically trades IMMINENT news without user intervention
2. Exits positions after 5 minutes using market orders
3. Maintains persistent connection to IBKR Gateway
4. Preserves manual trading capabilities via Telegram

**Why Now:** WebSocket is confirmed faster (typically 100-500ms advantage) and captures all major corporate announcements. Perfect timing for automation.

---

## 📋 Three Core Features to Implement

### 1. **Automated Trade Execution on IMMINENT News** (Priority 1)

#### Current State
- ✅ News classification working (IMMINENT detection)
- ✅ Manual trade execution via Telegram commands (`/buy TICKER $100`)
- ✅ IBKRTradingService fully functional
- ✅ Paper trading mode active (port 4001)
- ✅ Health monitoring active

#### Implementation Plan

**A. Ticker Selection Logic**
```python
def select_ticker_for_trade(article: StandardizedArticle) -> Optional[str]:
    """
    Select which ticker to trade from an article.
    
    Rules:
    - If article has NO tickers: skip (no trade)
    - If article has 1 ticker: trade that ticker
    - If article has multiple tickers: trade the FIRST ticker in the list
      (usually the primary company mentioned in news)
    """
    if not article.tickers:
        return None
    return article.tickers[0]  # First ticker is primary
```

**B. Auto-Trade Service**
- **Location:** `src/newsflash/services/auto_trade_service.py` (new file)
- **Purpose:** Orchestrate automatic trade execution on IMMINENT articles
- **Integration Point:** Called from `ArticleProcessor` after classification

**C. Configuration**
- Add to `settings.py`:
  ```python
  AUTO_TRADING_ENABLED: bool = True  # Master switch
  AUTO_TRADE_AMOUNT_USD: float = 100.0
  AUTO_TRADE_EXIT_DELAY_MINUTES: int = 5
  ```

---

### 2. **Automated Position Exit After 5 Minutes** (Priority 2)

#### Strategy
**Exit Rule:** Sell entire position 5 minutes after entry using a market order

---

### 3. **Keep IBKR Gateway Permanently Alive** (Priority 3)

#### Recommended Solution: **IBCAlpha (IB Controller)**
- Auto-login to IBKR Gateway on startup
- Handle 2FA prompts automatically
- Auto-restart on connection loss

---

## 📝 Implementation Order

### Step 1: Auto-Trading Core (Priority 1) - **START HERE**
- [ ] Create `AutoTradeService`
- [ ] Implement ticker selection logic
- [ ] Add configuration flags to `settings.py`
- [ ] Integrate with `ArticleProcessor`
- [ ] Test with paper account

### Step 2: Position Exit (Priority 2)
- [ ] Create `PositionTracker`
- [ ] Implement 5-minute exit scheduler
- [ ] Add persistent storage
- [ ] Handle crash recovery

### Step 3: Connection Management (Priority 3)
- [ ] Research IBCAlpha setup
- [ ] Implement keep-alive or use IBCAlpha
- [ ] Add reconnect logic

---

## 🎯 Success Criteria

### Week 1 (Paper Trading)
- ✅ Auto-trading executes on IMMINENT news
- ✅ Positions exit after exactly 5 minutes
- ✅ No system crashes or connection losses
- ✅ All trades logged to audit trail

### Week 2 (Live Trading - 1 Share)
- ✅ Trades execute on live account
- ✅ Win rate > 60%
- ✅ Average profit per trade > $0 (after commissions)
- ✅ System runs 24/7 without intervention

---

## 🚀 Timeline Estimate

- **Step 1 (Auto-Trading Core):** 4-6 hours
- **Step 2 (Position Exit):** 3-4 hours
- **Step 3 (Connection Management):** 4-6 hours

**Total Development Time:** ~15-20 hours  
**Total Testing Time:** 2 weeks (1 week paper, 1 week live)

---

## 🎉 End Goal

**A fully autonomous trading system that:**
1. Monitors two news sources 24/7
2. Classifies news in <1 second
3. Automatically trades the best opportunities
4. Exits positions after 5 minutes
5. Runs indefinitely without intervention

**Ready to start!** Begin with **Step 1: Auto-Trading Core**.

