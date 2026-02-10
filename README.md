# NewsFlash Trading System

## Quick Setup

### 1. Clone and Navigate
```bash
git clone <repository-url>
cd newsflash
```

### 2. Create Virtual Environment
```bash
# Using uv (recommended)
uv venv .venv
source .venv/bin/activate

# Or using venv
python -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
# Using uv (recommended)
uv pip install -e .

# Or using pip
pip install -e .
```

### 4. Environment Setup
Create a `.env` file in the project root:
```bash
# Copy the example file
cp .env.example .env

# Edit with your API key
nano .env
```

### 5. Optional: Configure Telegram Notifications

To receive news alerts on Telegram:

1. **Create a Bot**:
   - Open Telegram and message `@BotFather`
   - Send: `/newbot`
   - Follow the instructions to name your bot
   - Copy the bot token provided

2. **Get Your Chat ID**:
   - Message `@userinfobot` on Telegram
   - It will reply with your chat ID
   - Copy the numeric ID

3. **Update .env**:
   ```env
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   TELEGRAM_CHAT_ID=123456789
   ```


### FastAPI Server
Run the FastAPI server with integrated polling:

**Important**: Make sure to activate your virtual environment first!

```bash
# Run server (using python -m to ensure correct environment)
python -m uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload
ŹZW1E1WEWEWE3E
# Alternative: Direct path to uvicorn in virtual environment
.venv/bin/uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload
```


## Testing

### unit tests

#### Statistics
```bash
python -m pytest tests/unit/statistics/test_repository.py -v
python -m pytest tests/unit/statistics/test_recall_engine.py -v
python -m pytest tests/unit/statistics/test_signal_engine.py -v
```


### integration tests

#### Statistics
```bash
python -m pytest tests/integration/statistics/test_repository_integration.py -v
python -m pytest tests/integration/statistics/test_recall_engine_integration.py -v
python -m pytest tests/integration/statistics/test_signal_engine_integration.py -v
```

### Code Formatting
```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Type checking
mypy src/
```

---

## Trade Classification Pipeline (ML Data Factory)

The system automatically classifies all trading decisions into a confusion matrix for ML training.

### Categories

| Category | Definition | Data Source |
|----------|------------|-------------|
| **True Positive (TP)** | Trades we made that were profitable (>= +2%) | Alpaca order history |
| **False Positive (FP)** | Trades we made that lost money (<= -2%) | Alpaca order history |
| **False Negative (FN)** | IMMINENT articles we didn't trade with +10% peak, <5% MAE | Recall records |
| **True Negative (TN)** | Correctly ignored (wouldn't have been profitable) | Recall records |

### Automatic Jobs (Scheduler)

These run automatically when the server is running:

| Schedule | Job | Output |
|----------|-----|--------|
| **Daily 8pm ET** | TradeClassificationJob | `tmp/trade_classification/daily/{date}/` |
| **Friday 8pm ET** | WeeklyAggregationJob | `tmp/trade_classification/weekly/{year}_week_{n}/` |

**Daily output:**
```
tmp/trade_classification/daily/2026-02-03/
├── true_positive.txt      # Human-readable list
├── false_positive.txt     # Human-readable list
├── false_negative.txt     # Human-readable list
├── true_negative.txt      # Human-readable list
└── summary.json           # Counts + metrics (precision, recall, F1)
```

**Weekly output:**
```
tmp/trade_classification/weekly/2026_week_6/
├── true_positive.txt      # Week's TP trades
├── false_positive.txt     # Week's FP trades
├── false_negative.txt     # Week's FN missed opportunities
├── true_negative.txt      # Week's TN correctly ignored
├── aggregated_stats.json  # Week's metrics
└── training_data.json     # Labeled data for ML (label=1: should trade, label=0: should not)
```

### Manual Scripts

Run these as needed for analysis and ML training:

```bash
# Check status of combined training data
python scripts/combine_training_data.py --status

# Combine all available weeks into single training set (incremental)
python scripts/combine_training_data.py

# Rebuild training set from scratch
python scripts/combine_training_data.py --rebuild

# Combine only last 4 weeks
python scripts/combine_training_data.py --weeks 4
```

**Classification scripts (for backfill/debugging):**
```bash
# Classify specific date
python scripts/run_trade_classification.py 2026-02-03

# Classify last 7 days
python scripts/run_trade_classification.py --days 7

# Run weekly aggregation for specific week
python scripts/run_weekly_aggregation.py 2026-02-07

# Backfill missing exit data from Alpaca (one-time fix)
python scripts/backfill_exit_data.py --dry-run
python scripts/backfill_exit_data.py
```

### State Management

The combine script maintains persistent state in `tmp/trade_classification/combined_state.json`:
- Tracks which weeks have been processed
- Only adds new weeks on subsequent runs (incremental)
- First run processes all available weeks

**Output:**
```
tmp/trade_classification/
├── daily/{date}/          # Daily classification files
├── weekly/{year}_week_{n}/ # Weekly aggregation files
├── combined_state.json    # Tracks processed weeks
└── combined_training_data.json  # All weeks combined for ML
```

### ML Training Labels

In `training_data.json`:
- `label=1`: Should trade (TP + FN) - profitable or would have been
- `label=0`: Should not trade (FP + TN) - lost money or correctly ignored

### Metrics

- **Precision**: TP / (TP + FP) - What % of our trades were winners?
- **Recall**: TP / (TP + FN) - What % of winners did we catch?
- **F1 Score**: Harmonic mean of precision and recall

### Actionable Insights from Each Category

All four categories provide different actionable insights:

| Category | Meaning | Action |
|----------|---------|--------|
| **True Positive (TP)** | What we're doing RIGHT | CONTINUE these patterns |
| **False Positive (FP)** | What we're doing WRONG | STOP - add filters to exclude these |
| **False Negative (FN)** | Winners we're MISSING | START - remove filters blocking these |
| **True Negative (TN)** | What we correctly IGNORE | KEEP ignoring these patterns |

**How to use all four:**
- **TP + FN** = All winners → tells you what TO DO (continue + start)
- **FP + TN** = All non-winners → tells you what NOT to do (stop + keep ignoring)
- **TP vs FP** = Among trades we make, what distinguishes winners from losers?
- **FN vs TN** = Among trades we skip, what distinguishes missed winners from correctly ignored?

### Pattern Analysis Scripts

Once you have enough data (50+ samples), run these to find optimal trading patterns:

**Segment Analysis** - Compare win rates across feature buckets:
```bash
python scripts/analyze_patterns.py                    # Full analysis
python scripts/analyze_patterns.py --feature industry # Focus on one feature
python scripts/analyze_patterns.py --min-samples 10   # Require more data
```

Example output:
```
MARKET CAP ANALYSIS
Segment                     TP    FP    FN  Precision  Recommendation
----------------------------------------------------------------------
$50-100M                     8     2     1        80%          TRADE
$25-50M                      5     3     2        63%
<$25M                        2     6     0        25%          AVOID
```

**Find Optimal Signal** - Search filter combinations for best precision/recall:
```bash
python scripts/find_optimal_signal.py                     # Best overall signal
python scripts/find_optimal_signal.py --by-industry       # Best signal PER industry
python scripts/find_optimal_signal.py --min-precision 0.7 # Require 70%+ precision
python scripts/find_optimal_signal.py --export rules.json # Export for trading system
```

Example output:
```
OPTIMAL SIGNAL BY INDUSTRY
Industry                    TP   FP   Prec  Recall   F1  Best Filter
---------------------------------------------------------------------
Biotechnology               12    3    80%    75%   77%  market_cap <= 100 AND volume_ratio >= 5
Software                     8    2    80%    67%   73%  confluence_score >= 4
Medical Devices              5    1    83%    71%   77%  market_cap <= 200
```

### Recommended Workflow

1. **Collect data** - Let the system run for 2-4 weeks
2. **Analyze patterns** - Run `analyze_patterns.py` weekly to see trends
3. **Find optimal signal** - Run `find_optimal_signal.py --by-industry` when you have 50+ trades
4. **Tune thresholds** - Apply discovered patterns as filters in trading logic
5. **Re-evaluate monthly** - Market conditions change, re-run analysis to adapt

### Test Environment Issues

4. **Tests Using Wrong Python Environment**
   ```
   ModuleNotFoundError: No module named 'pytz'
   ```
   Or pytest shows system Python path instead of venv:
   ```
   /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
   ```
   
   **Solution:**
   ```bash
   # 1. Deactivate current environment
   deactivate
   
   # 2. Remove old venv
   rm -rf .venv
   
   # 3. Create fresh venv with uv
   uv venv .venv
   
   # 4. Activate it
   source .venv/bin/activate
   
   # 5. Install dependencies (including dev)
   uv pip install -e ".[dev]"
   
   # 6. Verify you're using venv Python
   which python
   # Should show: /path/to/newsflash/.venv/bin/python
   
   # 7. Run tests with python -m pytest (not just pytest)
   python -m pytest tests/unit/statistics/ -v
   ```

5. **Async Fixture Errors in Tests**
   ```
   pytest.PytestRemovedIn9Warning: 'test_name' requested an async fixture 'test_tmp_dir'
   ```
   
   **Solution:** This happens when sync tests use async fixtures. The fixture should be sync:
   - Use `@pytest.fixture` (not `@pytest.fixture async`)
   - Use `time.sleep()` instead of `await asyncio.sleep()` in fixtures
   - Only use `async def` for fixtures that are actually async operations

6. **Pytest Not Found in Venv**
   ```
   which pytest
   # Shows: /Library/Frameworks/Python.framework/.../pytest (system path)
   ```
   
   **Solution:**
   ```bash
   # Install pytest in venv
   uv pip install -e ".[dev]"
   
   # Always use python -m pytest instead of just pytest
   python -m pytest tests/ -v
   ```