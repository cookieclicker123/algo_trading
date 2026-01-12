# OSRH Trade Latency Breakdown

## Production Timing (2026-01-12)

**Real Trade Data:**
- **Published**: `2026-01-12T13:05:00Z` (8:05:00 AM ET)
- **Received**: `2026-01-12T13:05:17.595747Z` (17.6s delay from publication)
- **Surge Detected**: `2026-01-12T13:05:19.744983Z` (2.15s after received)
- **Trade Executed**: `2026-01-12T13:05:20.909389Z` (3.31s after received)
- **Entry Price**: `0.880452`

## Latency Breakdown

### Total: 3.31 seconds

#### Phase 1: Article Received → Surge Detection (2.15 seconds)

**Components:**
1. **Article Processing & Tracking** (~0.1s)
   - Event bus publishing
   - Recall engine event handler
   - Article validation and tracking

2. **Metadata Fetching** (~0.5s)
   - Yahoo Finance API call for industry/sector/market_cap
   - NBBO snapshot from Alpaca
   - Exchange info from Alpaca

3. **Surge Monitoring Cycles** (~1.5s)
   - Multiple 4-second monitoring cycles
   - Volume analysis and surge detection
   - Surge detected early in cycle (not waiting full 4s)

**Total Phase 1: 2.15 seconds**

#### Phase 2: Surge Detection → Trade Executed (1.16 seconds)

**Components:**
1. **Trade Request Creation** (~0.1s)
   - Trade request factory
   - Trade request validation
   - Event publishing

2. **Brokerage Service Processing** (~0.3s)
   - Event bus routing
   - Brokerage domain listener
   - Trade executor selection
   - NBBO validation

3. **Order Execution (Alpaca)** (~0.7s)
   - Alpaca API call
   - Order placement
   - Fill confirmation
   - Event publishing

**Total Phase 2: 1.16 seconds**

## Optimization Opportunities

### Phase 1 Optimizations (Target: < 1.5s)
- **Non-blocking repository updates**: Save ~0.1-0.2s
- **Parallel metadata fetching**: Save ~0.2-0.3s
- **Faster surge detection**: Catch-up window analysis could save ~0.5-1.0s
- **WebSocket real-time data**: Reduce polling delays

### Phase 2 Optimizations (Target: < 0.8s)
- **Faster trade request creation**: Already optimized
- **Parallel NBBO validation**: Save ~0.1s
- **Optimized Alpaca API calls**: Save ~0.2-0.3s

### Total Potential: 2.0-2.5 seconds (from 3.31s)

## High Load Considerations

During 12pm/1pm bulk delivery (7am/8am ET):
- **Yahoo Finance rate limiting**: May add 1-5s delays
- **Repository file locking**: May add 0.5-2s delays
- **Event bus congestion**: May add 0.1-0.5s delays
- **Alpaca API rate limiting**: May add 0.5-2s delays

**Expected high load latency: 5-10 seconds**

This is why the baseline test runs in isolation - to establish the "best case" scenario before testing under load.
