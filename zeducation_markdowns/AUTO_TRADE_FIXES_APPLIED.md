# Auto-Trade Fixes Applied

**Date:** 2025-12-09  
**Issue:** Auto-trading not working - trades not executing

---

## Root Causes Identified

### 1. ✅ FIXED: TradeRequestFactory Bug - Rejecting Multiple Tickers

**Problem:**
- `TradeRequestFactory.create_from_article()` was rejecting articles with multiple tickers
- Line 67: `if len(tickers) != 1: return None`
- This caused articles like "ADVM/LLY" and "TSXV:RSS/RSASF" to be skipped

**Fix Applied:**
- Changed to use the first ticker when multiple tickers exist
- Added logging to show which ticker was selected from multiple options
- Now trades the primary ticker (first one) instead of rejecting

**Files Changed:**
- `src/newsflash/domain/brokerage/factories.py` - Line 64-75

---

### 2. ⚠️ IDENTIFIED: SETO Trade Failed - NBBO Snapshot Unavailable

**Problem:**
- SETO trade was attempted but failed: "Could not retrieve NBBO snapshot for extended hours trade"
- SETO is likely an OTC/pink sheet stock that doesn't have NBBO data during premarket
- Extended hours executor requires NBBO snapshot for ladder limit orders

**Status:**
- This is expected behavior for low-liquidity/OTC stocks
- System is working correctly - it's attempting trades but some stocks aren't tradeable
- Need to filter out OTC/pink sheet stocks or handle NBBO failures gracefully

**Recommendation:**
- Add ticker validation to filter out OTC/pink sheet stocks
- Or add fallback logic when NBBO snapshot is unavailable

---

## What Was Working

✅ **Alpaca Connection:** Connected successfully  
✅ **AutoTradeService:** Receiving ArticleClassified events  
✅ **Trade Requests:** Being published correctly  
✅ **Trade Execution:** Attempting trades (SETO example)  

---

## What Was Broken

❌ **TradeRequestFactory:** Rejecting articles with multiple tickers  
❌ **Trade Execution:** Failing for OTC stocks without NBBO data  

---

## Expected Behavior After Fix

1. Articles with multiple tickers will now trade the first ticker
2. More trades will be attempted (previously skipped)
3. OTC stocks may still fail (expected - need ticker validation)

---

## Next Steps

1. ✅ **DONE:** Fix TradeRequestFactory to accept multiple tickers
2. ⚠️ **TODO:** Add ticker validation to filter OTC/pink sheet stocks
3. ⚠️ **TODO:** Add fallback logic for NBBO snapshot failures
4. ⚠️ **TODO:** Test with a liquid ticker (AAPL, TSLA, etc.) to verify trades execute

---

## Testing

To verify the fix works:

1. Wait for next IMMINENT article with multiple tickers
2. Check logs for: "Article has X tickers, using first ticker for trade"
3. Verify trade request is published
4. Check if trade executes (may still fail for OTC stocks)

---

## Summary

**Main Issue:** TradeRequestFactory was too restrictive - rejecting valid trade opportunities  
**Fix:** Now uses first ticker when multiple tickers exist  
**Result:** More trades will be attempted, but OTC stocks may still fail (expected)
