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