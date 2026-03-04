# Trading Pipeline: Complete Filter Chain

## Stage 1: Prefilters (service.py — before AI classification)

All prefilters apply identically regardless of trade type. Mega trade detection happens later.

| # | Filter | Threshold | What it blocks |
|---|--------|-----------|----------------|
| 1 | Latency check | >15s since publication | Late articles that missed initial momentum |
| 2 | NBBO availability | No bid/ask available | Stock not actively trading in current session |
| 3 | Headline blacklist | ~30 regex patterns | Law firms, class actions, conferences, dividends, non-binding, restructuring, patent allowance, line extensions |
| 4 | Market cap max | >$500M | Large-caps don't move enough. EXCEPTION: Healthcare Biotech + Medical Devices exempt |
| 5 | Market cap min | <$1.5M | Sub-$1.5M = manipulation risk. EXCEPTION: Transformational headline ($ amount >5x market cap) bypasses |
| 6 | Price min | <$0.05 | Sub-penny territory |
| 7 | Spread (prefilter) | >4.5% of mid | Wide spreads eat profits on entry/exit |

## Stage 2: Universal Catalyst Check (service.py — bypasses AI)

Regex patterns that match universally bullish catalysts skip the LLM entirely and go straight to TRADE:
- Debt elimination/retirement
- Definitive agreements (M&A finalized)
- "To be acquired" / acquisition offers
- Strategic investment received

## Stage 3: AI Classification (service.py -> Groq LLM)

1. Sector/industry looked up via MetadataCache (FMP data)
2. Industry mapped to specific prompt file (e.g., prompts/healthcare/biotechnology.txt)
3. Headline + market cap context sent to Groq LLM
4. LLM returns: SKIP, TRADE SMALL, TRADE MODERATE, TRADE LARGE, or TRADE MAX
5. If SKIP -> done, no trade

## Stage 4: Activity Confirmation (auto_trade.py — postfilter gate)

After AI says TRADE, the system waits for real market activity to confirm:

| Check | Window | Criteria |
|-------|--------|----------|
| Min trades | 2s confluence | >=3 trades required (1-2 = not real activity) |
| STRENGTH | 2s | Score >=1 AND excursion >=0.5% |
| HIGH CONFLUENCE | 2s | Score >=3 AND excursion >=0.5% |
| SURGE | 8s | Strict criteria (monitored if no STRENGTH) |
| LATE ENTRY | up to 90s | Monitored if no STRENGTH or SURGE |

If none trigger -> SKIP (no activity confirmation)

## Stage 5: Mega Trade Detection (auto_trade.py — after activity confirmation)

Detected here but only affects downstream filters. ALL criteria must be true:

| Criterion | Threshold | Why |
|-----------|-----------|-----|
| ai_position_size | LARGE or MAX | LLM identified strong headline |
| confluence_score | >=5 | All 5+ criteria met (ADIL had 4 and lost -5.3%) |
| confluence_has_volume_surge | True | Extreme volume confirmed |
| confluence_has_price_excursion | True | Strong price move confirmed |
| max_excursion_pct | >=2.0% | Meaningful movement, not noise |
| confluence_buying_pressure_pct | >=70% | Majority buying (MOBX 78.4% yes, ADIL 66.6% no) |

## Stage 6: Postfilters (auto_trade.py — safety filters before execution)

| # | Filter | Regular | Mega | What it catches |
|---|--------|---------|------|-----------------|
| 1 | Market cap (post) | <$1.5M = skip | Same | Duplicates prefilter (catches late-discovered data) |
| 2 | Spread (post) | >4.5% = skip | Same | Wide spread at confluence time |
| 3 | Selling pressure | Imbalance < -0.3 (>65% selling) = skip | Same | Heavy selling = someone knows something |
| 4 | Zero volume | 0 trades since pub = skip | Same | Dead market, no interest |
| 5 | Fill-time spread | >4.5% = skip (with deterioration tolerance) | Same | Spread widened during SURGE monitoring (APUS protection). If initial spread was tight (<3%) and widening <3pp, allows temporary volatility |
| 6 | Front-running LEG 1 (pub->recv) | >3% AND >$0.05 = skip | >8% AND >$0.05 = skip | Ask already moved before we received article |
| 7 | Front-running LEG 2 (recv->fill) | >3% AND >$0.05 = skip | >8% AND >$0.05 = skip | Ask moving during our checks |
| 8 | Pump-and-dump | Fill ask >5.5% above VWAP AND >$0.08 absolute gap = skip | Same | Paying inflated ask while trades at lower VWAP |
| 9 | Pre-news runup | >5% move in prior 30 min = skip | Same | News already priced in / leaked |
| 10 | Momentum exhaustion | Max confluence price >5% above entry ask = skip | Same | Buying at the top |
| 11 | Late entry timing | >15s (normal) / >95s (late trade) = skip | Same | Too late to the party |

Only filters 6 and 7 differ for mega trades (8% vs 3% threshold). The $0.05 absolute floor applies to filters 6, 7. The $0.08 floor applies to filter 8.

## Stage 6b: Execution-Time Slippage Guard (trade_executor_extended_hours.py — final safety net)

After postfilters pass and the trade event reaches the executor, a fresh NBBO is fetched. If the ask has spiked since the decision-time NBBO (race condition), this guard aborts.

| Check | Regular | Mega | What it catches |
|-------|---------|------|-----------------|
| Decision-to-execution slippage | >5% AND >$0.05 = abort | >7.5% AND >$0.05 = abort | Ask spiked between postfilter check and execution (FBGL: $0.879→$0.9683 = 10.16%, instant -10.78% loss) |

The threshold equals the stop loss: if slippage alone would trigger your stop, the trade is mathematically guaranteed to lose.

## Stage 7: Execution and Position Management

| Aspect | Regular Trade | Mega Trade |
|--------|--------------|------------|
| Position size | $4 base x confluence multiplier | Same $4 base x confluence multiplier |
| Stop loss | 5% hard, 5s grace, 1.25s confirm | 7.5% hard, 5s grace, 1s confirm |
| Breakeven | At +5% -> lifts stop to entry | At +20% -> lifts stop to entry |
| Tiered exits | +15%: 50%, +20%: 50%, +30%: 100% | +20%: 50%, +25%: 50%, +30%: 100% |
| Floor rule | Active (+5% after T1, +10% after T2) | Active (+10% after T1, +15% after T2) |
| Early exit | +10% after 5 min | Same |
| 10-min scheduled exit | Active (ExitTradeUseCase) | Active |
| Session-end force exit | Active | Active |
| Manual /exit | Available | Available |
| Manual /hold | Extends to 30 min | Extends to 30 min |
