Phase 6: The Triple Confluence Gate (Hard Gating)
Goal: Transition from Shadow Tracking to Hard Gating. No trade is executed unless move_type == "SURGE".
Logic: Modify 
AutoTradeService
 (or signal engine) to verify the 4-pillar shock result before publishing a TradeRequested event.
Phase 7: Sector-Specific Tuning & Risk Optimization
Goal: Tailor thresholds to industry volatility.
Tuning:
Tech/Biotech: Higher volume/momentum thresholds (e.g., 5.0x vol, 1.0% pop).
Finance/Utilities: Lower thresholds for stealth institutional moves.
Risk: Track Max Adverse Excursion (MAE) across all trades to optimize stop-loss placement per industry.
Sizing: Implement an "Alpha Score" (Headline + Microstructure + Industry) to determine dynamic leverage (1x, 2x, or 3x).
