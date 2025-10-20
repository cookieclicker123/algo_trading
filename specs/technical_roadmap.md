# NewsFlash Trading System - Technical Roadmap

## Project Overview
Transform the current dual-source news aggregation system into a fully automated trading notification and execution platform that combines real-time news analysis with quantitative signals for optimal trading opportunities.

## Phase 1: Telegram Integration (Immediate - Week 1-2)

### 1.1 Telegram Bot Setup
- [ ] Create Telegram bot via BotFather
- [ ] Configure bot token in environment variables
- [ ] Add `python-telegram-bot` dependency to `pyproject.toml`
- [ ] Create Telegram notification service

### 1.2 Notification Service Architecture
```python
src/newsflash/services/telegram_service.py
```
- [ ] Implement `TelegramNotifier` class
- [ ] Add message formatting for news articles
- [ ] Support for both Benzinga and Finlight sources
- [ ] Rate limiting and error handling
- [ ] Message queuing for high-volume scenarios

### 1.3 Integration with Feed Manager
- [ ] Modify `FeedManager` to include Telegram notifications
- [ ] Add configuration for Telegram settings
- [ ] Implement notification triggers for new articles
- [ ] Add user management (single user initially, multi-user ready)

### 1.4 Message Format
```
🚨 IMMINENT | HIGH CONFIDENCE
AAPL, GOOGL
Apple Announces $50B Partnership With Google
🔗 https://benzinga.com/article/12345

📈 NOTEWORTHY | MEDIUM CONFIDENCE  
TSLA
Tesla Plans Giga Factory Expansion in Texas
🔗 https://finlight.me/article/67890

⚠️ IMMINENT | MEDIUM CONFIDENCE
MRNA
Moderna FDA Approval Expected Within 48 Hours
🔗 https://benzinga.com/article/54321
```

**Message Structure:**
- Classification emoji (🚨 IMMINENT or 📈 NOTEWORTHY)
- Confidence level (HIGH/MEDIUM)
- Ticker(s) on dedicated line
- Clean headline
- Article URL for full details

## Phase 2: AI-Powered News Classification (Week 3-4)

### 2.1 LLM Integration Setup
- [ ] Add Groq API integration (`groq` package)
- [ ] Configure Llama 3 model via Groq
- [ ] Configure API keys and rate limits
- [ ] Create classification service architecture with async support

### 2.2 Classification Categories
```python
class NewsClassification(str, Enum):
    IMMINENT = "imminent"          # Immediate trading opportunity (10%+ intraday moves)
    NOTEWORTHY = "noteworthy"      # Worth monitoring but less time-sensitive
    IGNORE = "ignore"              # Filter out - no trading signal
```

### 2.3 Classification Response Model
```python
class ClassificationResult(BaseModel):
    """Pydantic model for LLM classification output"""
    classification: NewsClassification
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reasoning: str = Field(..., max_length=200)
```

### 2.4 Classification Service
```python
src/newsflash/services/news_classifier.py
```
- [ ] Implement `NewsClassifier` class with Groq Llama 3 integration
- [ ] Add async batch processing for multiple headlines
- [ ] Implement structured output with Pydantic model enforcement
- [ ] Add rate limiting and error handling
- [ ] Add classification caching for performance

### 2.5 LLM System Prompt

**Production Prompt for Llama 3 via Groq:**

```
You are a professional financial news classifier specializing in identifying headlines that will cause significant stock price movements (10%+ intraday). Your role is to filter real-time news headlines and distinguish between market-moving news ("IMMINENT" price movers) and noteworthy developments ("NOTEWORTHY" but less time-sensitive).

**CLASSIFICATION CRITERIA:**

**IMMINENT MOVERS (Urgent Action Required):**
Headlines containing these elements typically cause 10%+ price movements within hours:
- Merger & Acquisition announcements with specific deal values/terms
- Major partnership deals with big financial numbers ($100M+)
- Unexpected earnings beats/misses (>20% variance from estimates)
- FDA approvals/rejections for biotech/pharma
- Major government contracts with disclosed values
- Activist investor stake disclosures (>5%)
- Share buyback programs with significant $ amounts
- Dividend cuts/increases (>25% change)
- Executive departures (CEO/CFO) - unplanned
- Product launches with revenue projections
- Major litigation outcomes/settlements
- Bankruptcy filings/debt restructuring announcements
- Supply chain disruptions affecting major contracts
- Regulatory approvals for new markets/products
- Production halts or major facility closures

**NOTEWORTHY (Worth Monitoring):**
Potentially significant but less immediately actionable:
- Analyst upgrades/downgrades with significant price target changes (>20%)
- Conference presentations by management with material guidance
- Industry trend announcements affecting multiple stocks
- Infrastructure spending affecting relevant sectors
- Technology partnerships without disclosed financials
- Market share gains/losses in key segments
- New facility openings/expansions
- Patent approvals/disputes with major competitors
- Clinical trial updates (Phase 2/3 results)
- Credit rating changes (upgrade/downgrade)
- Insider trading activity (10%+ stake changes)
- Strategic initiatives with long-term impact

**EXCLUSION CRITERIA (Ignore These):**
- General market commentary without specific stock focus
- Routine quarterly guidance reaffirmations
- Minor analyst note revisions
- Industry conference schedules/attendance
- General economic data unless directly impacting mentioned ticker
- Social media sentiment without material news
- Technical analysis or chart pattern discussions
- Historical performance reviews
- General company profile updates
- Routine regulatory filings without material changes
- News older than 24 hours being rehashed
- Opinion pieces without new factual developments

**ANALYSIS RULES:**
1. Focus ONLY on headlines mentioning specific tickers (provided separately)
2. Prioritize headlines with concrete financial figures, percentages, or deal terms
3. Consider timing context - "announces," "reports," "files" = immediate; "plans to," "considering" = future
4. If multiple tickers mentioned: classify based on the primary company being affected
5. Unknown/unclear impact = classify as NOTEWORTHY rather than IMMINENT
6. If unsure between IMMINENT/NOTEWORTHY, choose NOTEWORTHY (better safe than missing a trade)
7. Brand new information takes priority over rehashed/follow-up stories
8. Pay attention to magnitude: $10M deal for small cap = IMMINENT, same deal for mega cap = NOTEWORTHY

**CLASSIFICATION FREQUENCY EXPECTATIONS:**
- **IGNORE**: 85-90% of all headlines - Most news is routine market commentary, rehashed information, or lacks specific catalysts
- **NOTEWORTHY**: 8-12% of headlines - Occurs multiple times per week, sometimes daily during heavy news cycles
- **IMMINENT**: 1-3% of headlines - Rare but critical. These are the needle-movers that require immediate attention

Your classification precision is critical. We measure precision/recall to find the optimal balance between capturing all relevant trading opportunities (recall) and avoiding notification fatigue from noise (precision). When in doubt, err toward IGNORE - it's better to miss a marginal signal than to flood with false positives.

**CONFIDENCE LEVELS:**
- HIGH: Clear catalyst with specific financial/strategic details and immediate market impact expected
- MEDIUM: Meaningful development with some details but timing or impact unclear
- LOW: Potentially relevant but high uncertainty or limited information

**OUTPUT INSTRUCTIONS:**
You must respond with a valid JSON object matching this exact structure:
{
  "classification": "IMMINENT" | "NOTEWORTHY" | "IGNORE",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "reasoning": "Brief 1-2 sentence explanation (max 200 chars)"
}

Process the headline provided, then return your classification in the exact JSON format specified above. No additional text or explanation outside the JSON structure.
```

### 2.6 Integration with Notification System
- [ ] Route classified articles through LLM
- [ ] Filter to only IMMINENT and NOTEWORTHY (HIGH/MEDIUM confidence)
- [ ] Add classification labels to Telegram messages
- [ ] Store classification metadata in articles.json
- [ ] Add classification performance metrics and monitoring

## Phase 3: Quantitative Signal Integration (Week 5-6)

**Note:** This phase begins after the headline classification system is fully operational and tested. Price/volume data will complement the news-driven signals.

### 3.1 Price Action Monitoring
```python
src/newsflash/services/quant_signal_service.py
```
- [ ] Integrate Polygon.io real-time price feeds
- [ ] Monitor volume spikes (>200% average)
- [ ] Detect price movements (>5% in 15 minutes)
- [ ] Track unusual options activity

### 3.2 Signal Correlation Engine
- [ ] Correlate price movements with news events
- [ ] Identify news-first vs price-first scenarios
- [ ] Build confidence scoring based on signal alignment
- [ ] Create signal strength indicators

### 3.3 Market Pattern Recognition
- [ ] Track sector-specific patterns
- [ ] Identify time-of-day patterns
- [ ] Monitor market cap correlations
- [ ] Build historical performance metrics

## Phase 4: Automated Trading Integration (Week 7-8)

### 4.1 Trading Bot Framework
```python
src/newsflash/services/trading_bot.py
```
- [ ] Create trading execution service
- [ ] Implement position sizing logic
- [ ] Add risk management controls
- [ ] Support for multiple broker APIs

### 4.2 Signal-to-Trade Pipeline
- [ ] Define trade trigger conditions
- [ ] Implement order placement logic
- [ ] Add confirmation workflows
- [ ] Create trade tracking and reporting

### 4.3 Telegram Trading Interface
- [ ] Add trading commands to bot
- [ ] Implement manual override capabilities
- [ ] Create portfolio monitoring
- [ ] Add performance reporting

## Technical Architecture

### Core Services
```
src/newsflash/
├── services/
│   ├── feed_manager.py          # Multi-source news orchestration
│   ├── telegram_service.py      # Telegram notifications
│   ├── news_classifier.py       # Groq Llama 3 classification (async)
│   ├── quant_signal_service.py  # Price/volume monitoring
│   └── trading_bot.py           # Automated trading
├── models/
│   ├── classification_models.py # Classification enums/models
│   └── trading_models.py        # Trading signal models
└── config/
    └── trading_config.py        # Trading parameters
```

### Data Flow
```
News Sources → Feed Manager → Classifiers → Telegram → Trading Bot
     ↓              ↓            ↓           ↓          ↓
  Storage ←→ Quant Signals ←→ Correlation ←→ Alerts ←→ Execution
```

### Performance Targets
- **News Classification**: <2s per article (Groq Llama 3 with async batching)
- **Telegram Delivery**: <5s from news receipt to notification
- **Quant Signal Detection**: <30s from market data (Phase 3)
- **End-to-End Latency**: <10s from news ingestion to Telegram alert

### Risk Management
- [ ] Position size limits
- [ ] Daily loss limits
- [ ] Manual override capabilities
- [ ] Audit logging for all trades
- [ ] Backtesting framework

## Success Metrics
- **Signal Quality**: >70% accuracy on breakout moves
- **Speed**: <10s end-to-end latency
- **Volume**: Handle 1000+ articles/day
- **Performance**: Positive ROI within 30 days
- **Reliability**: 99.9% uptime

## Future Enhancements
- [ ] Multi-broker support
- [ ] Options strategy integration
- [ ] Cryptocurrency support
- [ ] International market expansion
- [ ] Machine learning model improvements
- [ ] Social sentiment integration

---

This roadmap creates a systematic approach to building a high-performance news-driven trading system that combines the best of real-time news analysis, AI classification, and quantitative signals for optimal trading opportunities.
